"""Platform operations with explicit permissions, personal ownership and inert imports."""

from copy import deepcopy
from typing import Annotated
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from langflow.api.utils import CurrentActiveUser, DbSession
from langflow.api.utils.core import strip_secret_field_values
from langflow.services.auth.platform_workflows import (
    context,
    require_action,
    require_personal_destination,
    validate_create,
    validate_fields,
)
from langflow.services.authorization import FlowAction, ensure_flow_permission
from langflow.services.database.models.flow.model import Flow, FlowCreate, FlowRead
from langflow.services.deps import get_storage_service
from langflow.services.storage.service import StorageService

MAX_IMPORT_FLOWS = 100
MAX_INPUT_BYTES = 65536

router = APIRouter(prefix="/platform-workflow", tags=["Platform workflow operations"])
Storage = Annotated[StorageService, Depends(get_storage_service)]


async def owned(request, session, flow_id, user, action):
    identity = context(request)
    if identity is None or identity["user_id"] != str(user.id):
        raise HTTPException(403, "Platform SSO required")
    require_action(request, "read")
    require_action(request, action)
    flow = await session.get(Flow, flow_id)
    if flow is None or flow.user_id != user.id or flow.workspace_id is not None:
        raise HTTPException(404, "Flow not found")
    await require_personal_destination(session, flow.folder_id, user.id)
    await ensure_flow_permission(
        user, FlowAction.READ, flow_id=flow.id, flow_user_id=flow.user_id, folder_id=flow.folder_id
    )
    return flow


def export_flow(flow):
    result = FlowRead.model_validate(flow, from_attributes=True).model_dump(mode="json")
    result["data"] = strip_secret_field_values(result.get("data"))
    return result


@router.get("/flows/{flow_id}/export")
async def export(flow_id: UUID, request: Request, current_user: CurrentActiveUser, session: DbSession):
    return export_flow(await owned(request, session, flow_id, current_user, "export"))


@router.get("/flows/{flow_id}/versions/{version_id}/export")
async def export_version(
    flow_id: UUID, version_id: UUID, request: Request, current_user: CurrentActiveUser, session: DbSession
):
    from langflow.services.database.models.flow_version.crud import get_flow_version_entry_or_raise

    flow = await owned(request, session, flow_id, current_user, "version_export")
    require_action(request, "version_read")
    entry = await get_flow_version_entry_or_raise(session, version_id, current_user.id, flow_id=flow_id)
    result = export_flow(flow)
    result["data"] = strip_secret_field_values(entry.data)
    return result


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flows: list[dict] = Field(min_length=1, max_length=100)
    folder_id: UUID | None = None
    project_name: str | None = Field(default=None, min_length=1, max_length=200)


async def import_flows(body, request, user, session, storage):
    from langflow.api.v1.authz_route_dependencies import require_flow_create_permission
    from langflow.api.v1.flows import create_flow

    require_action(request, "create")
    await require_personal_destination(session, body.folder_id, user.id)
    pending = []
    for raw in body.flows:
        # 导入只创建新副本;不接受外部 owner、ID、公开访问、webhook、路径或凭据副作用。
        try:
            flow = FlowCreate(
                name=raw.get("name", "Imported workflow"),
                description=raw.get("description"),
                data=deepcopy(raw.get("data")),
                folder_id=body.folder_id,
                icon=raw.get("icon"),
                gradient=raw.get("gradient"),
                tags=raw.get("tags"),
                mcp_enabled=False,
            )
        except ValidationError as exc:
            # 不返回导入原文或校验器上下文, 防止文件中的凭据被带到错误提示中。
            raise HTTPException(
                422,
                f"第 {len(pending) + 1} 个流程格式不正确，请检查名称、描述、标签及节点数据。",  # noqa: RUF001
            ) from exc
        await validate_create(request, session, flow, user.id)
        pending.append(flow)
    results = []
    # 整个批次同一事务,后续任一图失败不能留下部分流程。
    async with session.begin_nested():
        for flow in pending:
            # 直接调用处理器不会运行 FastAPI Depends, 必须执行与普通新建相同的原生授权。
            destination = await require_flow_create_permission(request, user, flow, session)
            results.append(
                await create_flow(
                    request=request,
                    session=session,
                    flow=flow,
                    current_user=user,
                    _create=destination,
                    storage_service=storage,
                )
            )
    return results


async def import_upload(file, request, user, session, storage, *, folder_id=None, project=False):
    import io
    import json
    import zipfile

    import anyio

    require_action(request, "project_import" if project else "import")
    if file is None:
        raise HTTPException(422, "Choose a JSON or ZIP file")
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "Import exceeds 20 MiB")

    def decode():
        if (file.filename or "").lower().endswith(".zip"):
            flows, total = [], 0
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_IMPORT_FLOWS:
                    raise ValueError
                for entry in entries:
                    if entry.is_dir():
                        continue
                    if not entry.filename.lower().endswith(".json"):
                        raise ValueError
                    with archive.open(entry) as source:
                        raw = source.read(512 * 1024 + 1)
                    total += len(raw)
                    if len(raw) > 512 * 1024 or total > 20 * 1024 * 1024:
                        raise ValueError
                    flows.append(json.loads(raw))
            return {"flows": flows}
        raw = json.loads(content)
        return raw if "flows" in raw else {"flows": [raw]}

    try:
        decoded = await anyio.to_thread.run_sync(decode)
        body = ImportBody(
            flows=decoded["flows"],
            folder_id=folder_id,
            project_name=(decoded.get("folder_name") or (file.filename or "Imported project").rsplit(".", 1)[0])
            if project
            else None,
        )
    except (ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        raise HTTPException(422, "Invalid import; no data was imported") from exc
    if project:
        return (await import_project(body, request, user, session, storage))["flows"]
    return await import_flows(body, request, user, session, storage)


@router.post("/import", status_code=201)
async def import_graphs(
    body: ImportBody, request: Request, current_user: CurrentActiveUser, session: DbSession, storage: Storage
):
    require_action(request, "import")
    return await import_flows(body, request, current_user, session, storage)


@router.post("/projects/import", status_code=201)
async def import_project(
    body: ImportBody, request: Request, current_user: CurrentActiveUser, session: DbSession, storage: Storage
):
    from langflow.api.v1.projects import create_project
    from langflow.services.database.models.folder.model import FolderCreate

    require_action(request, "project_import")
    require_action(request, "project_create")
    require_action(request, "create")
    if not body.project_name or body.folder_id:
        raise HTTPException(422, "Project import requires a new project name")
    async with session.begin_nested():
        project = await create_project(
            request=request, session=session, project=FolderCreate(name=body.project_name), current_user=current_user
        )
        body.folder_id = project.id
        flows = await import_flows(body, request, current_user, session, storage)
    return {"project": project, "flows": flows}


class DuplicateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: UUID | None = None


@router.post("/flows/{flow_id}/duplicate", status_code=201)
async def duplicate(
    flow_id: UUID,
    body: DuplicateBody,
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage: Storage,
):
    flow = await owned(request, session, flow_id, current_user, "duplicate")
    validate_fields(flow, existing=True)
    data = export_flow(flow)
    data["name"] = f"{flow.name} copy"
    return (
        await import_flows(
            ImportBody(flows=[data], folder_id=body.folder_id or flow.folder_id),
            request,
            current_user,
            session,
            storage,
        )
    )[0]


class PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    published: bool
    publication_token: UUID | None


class PublicationSwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publication_token: UUID


@router.get("/flows/{flow_id}/publication")
async def publication_status(flow_id: UUID, request: Request, current_user: CurrentActiveUser, session: DbSession):
    from langflow.services.auth.platform_publications import state_response

    return await state_response(session, await owned(request, session, flow_id, current_user, "read"))


@router.get("/flows/{flow_id}/publications")
async def publications(
    flow_id: UUID,
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    from sqlalchemy import func
    from sqlmodel import select

    from langflow.services.auth.platform_publications import metadata
    from langflow.services.database.models.workflow_publication import WorkflowPublication

    await owned(request, session, flow_id, current_user, "read")
    query = select(WorkflowPublication).where(WorkflowPublication.flow_id == flow_id)
    rows = (
        await session.exec(query.order_by(WorkflowPublication.version_number.desc()).offset(offset).limit(limit))
    ).all()
    total = (
        await session.exec(
            select(func.count()).select_from(WorkflowPublication).where(WorkflowPublication.flow_id == flow_id)
        )
    ).one()
    return {"items": [metadata(row) for row in rows], "total": total}


@router.post("/flows/{flow_id}/publish")
async def publish(
    flow_id: UUID, body: PublishBody, request: Request, current_user: CurrentActiveUser, session: DbSession
):
    from langflow.api.v1.flow_conflict import parse_if_match
    from langflow.services.auth import platform_publications
    from langflow.services.auth.platform_actions import validate_graph_references
    from langflow.services.database.models.workflow_publication import WorkflowPublication

    flow = await owned(request, session, flow_id, current_user, "publish")
    await ensure_flow_permission(
        current_user, FlowAction.WRITE, flow_id=flow.id, flow_user_id=flow.user_id, folder_id=flow.folder_id
    )
    await platform_publications.lock_flow(session, flow)
    state = await platform_publications.check_state(session, flow, body.publication_token)
    if not body.published:
        if state is None or state.publication_id is None:
            raise HTTPException(409, "流程当前没有线上版本。")
        if "publish" not in await context(request)["recheck"]():
            raise HTTPException(403, "Publish permission revoked")
        return await platform_publications.switch(session, flow, current_user, state, None, "offline")
    expected = parse_if_match(request.headers.get("if-match"))
    if expected is None:
        raise HTTPException(428, "发布必须指定已保存的草稿修订。")
    if expected != flow.version_token:
        raise HTTPException(409, "草稿已变化, 请保存并刷新后再发布。")
    schemas = platform_publications.validate_release(flow.data)
    await validate_graph_references(request, session, flow.data, current_user.id, require_model_use=True)
    if "publish" not in await context(request)["recheck"]():
        raise HTTPException(403, "Publish permission revoked")
    release = WorkflowPublication(
        flow_id=flow.id,
        version_number=(state.latest_version if state else 0) + 1,
        source_version_token=flow.version_token,
        data=deepcopy(flow.data),
        digest=platform_publications.digest(flow.data),
        input_schema=schemas[0],
        output_schema=schemas[1],
        dependencies=schemas[2],
        created_by=current_user.id,
    )
    session.add(release)
    await session.flush()
    return await platform_publications.switch(session, flow, current_user, state, release, "publish")


@router.post("/flows/{flow_id}/publications/{publication_id}/activate")
async def rollback_publication(
    flow_id: UUID,
    publication_id: UUID,
    body: PublicationSwitchBody,
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
):
    from langflow.services.auth import platform_publications
    from langflow.services.auth.platform_actions import validate_graph_references
    from langflow.services.database.models.workflow_publication import WorkflowPublication

    flow = await owned(request, session, flow_id, current_user, "publish")
    await ensure_flow_permission(
        current_user, FlowAction.WRITE, flow_id=flow.id, flow_user_id=flow.user_id, folder_id=flow.folder_id
    )
    await platform_publications.lock_flow(session, flow)
    state = await platform_publications.check_state(session, flow, body.publication_token)
    release = await session.get(WorkflowPublication, publication_id)
    if release is None or release.flow_id != flow.id:
        raise HTTPException(404, "Publication not found")
    platform_publications.validate_release(release.data)
    await validate_graph_references(request, session, release.data, current_user.id, require_model_use=True)
    if "publish" not in await context(request)["recheck"]():
        raise HTTPException(403, "Publish permission revoked")
    return await platform_publications.switch(session, flow, current_user, state, release, "rollback")


@router.get("/flows/{flow_id}/share")
async def share(flow_id: UUID, request: Request, current_user: CurrentActiveUser, session: DbSession):
    flow = await owned(request, session, flow_id, current_user, "share")
    settings = request.app.state.platform_sso if hasattr(request.app.state, "platform_sso") else None
    # 分享编辑器入口保留平台登录和资源归属;不生成持有即授权的长期访问凭据。
    base = settings.public_url if settings else str(request.base_url)
    return {"url": f"{base.rstrip('/')}/flow/{flow.id}", "authentication": "platform_sso", "scope": "personal"}


@router.get("/flows/{flow_id}/api")
async def api_configuration(flow_id: UUID, request: Request, current_user: CurrentActiveUser, session: DbSession):
    from langflow.services.auth.platform_publications import state_response

    flow = await owned(request, session, flow_id, current_user, "api_access")
    publication = await state_response(session, flow)
    example = {"run_id": str(uuid4())}
    if publication["current"] and publication["current"]["input_schema"]["nodes"]:
        example["input"] = "业务文本输入"
    return {
        "method": "POST",
        "path": f"{request.scope.get('root_path', '')}/api/v2/platform-workflow/flows/{flow.id}/run",
        "authentication": "platform_sso",
        "credentials": "include",
        "required_action": "run",
        "publication": publication,
        "body": example,
        "run_id_policy": "每次运行使用新的 UUID, 相同 ID 不重复执行。",
    }


class RunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID = Field(default_factory=uuid4)
    input: str | None = Field(default=None, max_length=65536)


@router.post("/flows/{flow_id}/run")
async def run(flow_id: UUID, body: RunBody, request: Request, current_user: CurrentActiveUser, session: DbSession):
    import asyncio

    from sqlalchemy.exc import IntegrityError

    from langflow.api.utils import release_db_transaction
    from langflow.api.v1.flow_conflict import parse_if_match
    from langflow.services.auth import platform_execution
    from langflow.services.auth.platform_dry_run import ACTIVE_USERS
    from langflow.services.auth.platform_publications import lock_flow
    from langflow.services.database.models.workflow_publication import (
        WorkflowPublication,
        WorkflowPublicationRun,
        WorkflowPublicationState,
    )

    flow = await owned(request, session, flow_id, current_user, "run")
    await ensure_flow_permission(
        current_user, FlowAction.EXECUTE, flow_id=flow.id, flow_user_id=flow.user_id, folder_id=flow.folder_id
    )
    await lock_flow(session, flow)
    state = await session.get(WorkflowPublicationState, flow.id, populate_existing=True)
    if state is None or state.publication_id is None:
        raise HTTPException(409, "流程未发布或已下线, 不能业务执行。")
    release = await session.get(WorkflowPublication, state.publication_id)
    if release is None or release.flow_id != flow.id:
        raise HTTPException(409, "线上版本不可用, 不能回退到草稿。")
    expected = parse_if_match(request.headers.get("if-match"))
    if expected is not None and expected != release.id:
        raise HTTPException(409, "线上版本已变化, 请重新确认后执行。")
    if body.input is not None and len(body.input.encode()) > MAX_INPUT_BYTES:
        raise HTTPException(422, "Input exceeds 64 KiB")
    if body.input is not None and not release.input_schema["nodes"]:
        raise HTTPException(422, "该发布版本没有可覆盖的文本入口。")
    graph = platform_execution.prepared_graph(deepcopy(release.data), input_text=body.input)
    identity = context(request)
    actor, version, run_id = str(current_user.id), str(release.id), str(body.run_id)
    publication_version = release.version_number
    output_ids = set(release.output_schema["nodes"])
    if actor in ACTIVE_USERS:
        raise HTTPException(429, "A workflow run is already active")
    # 检查和占用之间不等待数据库, 避免同一 worker 的并发请求都通过限流检查。
    ACTIVE_USERS.add(actor)
    session.add(WorkflowPublicationRun(id=body.run_id, publication_id=release.id, created_by=current_user.id))
    try:
        # 先持久化受理记录和快照引用, 再执行模型; 跨 worker 重复 ID 也不能再次扣费。
        await release_db_transaction(session)
    except IntegrityError as exc:
        ACTIVE_USERS.discard(actor)
        await session.rollback()
        raise HTTPException(
            409,
            {
                "code": "workflow_run_accepted",
                "message": "运行 ID 已受理, 请查询原运行, 不要自动重试。",
                "run_id": run_id,
            },
        ) from exc
    except BaseException:
        ACTIVE_USERS.discard(actor)
        raise
    key = (actor, str(flow_id), run_id)
    sequence = 0
    outcome = "failed"

    async def invoke(**parameters):
        nonlocal sequence
        actions = await identity["recheck"]()
        if "run" not in actions:
            raise HTTPException(403, "Run permission revoked")
        # 每个模型调用独立防重,executionId 将多个节点关联到同一次可停止的运行。
        sequence += 1
        return await identity["model_call"](
            "invoke",
            {
                **parameters,
                "flowId": str(flow_id),
                "versionToken": version,
                "runId": str(uuid5(body.run_id, str(sequence))),
                "executionId": run_id,
            },
        )

    task = asyncio.create_task(
        platform_execution.execute(graph, flow_id=str(flow_id), user_id=actor, run_id=run_id, invoke=invoke)
    )
    platform_execution.ACTIVE[key] = task
    if any(node["data"]["type"] == "PlatformModel" for node in graph["nodes"]):
        platform_execution.ACTIVE_MODELS.add(key)
    try:
        outputs = await asyncio.wait_for(task, timeout=45)
        if not output_ids.issubset({output["node_id"] for output in outputs}):
            raise HTTPException(422, "发布流程未返回约定的输出, 不能认定运行成功。")
        if "run" not in await identity["recheck"]():
            raise HTTPException(403, "Run permission revoked")
        outcome = "succeeded"
        return {
            "status": "succeeded",
            "run_id": run_id,
            "flow_id": str(flow_id),
            "version_token": version,
            "publication_id": version,
            "publication_version": publication_version,
            "outputs": [output for output in outputs if output["node_id"] in output_ids],
        }
    except asyncio.CancelledError:
        outcome = "cancelled"
        return {"status": "cancelled", "run_id": run_id, "outputs": []}
    except TimeoutError as exc:
        raise HTTPException(504, "Workflow execution timed out; model billing may already have occurred") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, "Workflow execution failed; check inputs and connections") from exc
    finally:
        platform_execution.ACTIVE.pop(key, None)
        platform_execution.ACTIVE_MODELS.discard(key)
        ACTIVE_USERS.discard(actor)
        from datetime import datetime, timezone

        from sqlmodel import update

        await session.exec(
            update(WorkflowPublicationRun)
            .where(WorkflowPublicationRun.id == body.run_id)
            .values(status=outcome, finished_at=datetime.now(timezone.utc))
        )
        await session.commit()


@router.get("/flows/{flow_id}/runs/{run_id}")
async def run_status(
    flow_id: UUID, run_id: UUID, request: Request, current_user: CurrentActiveUser, session: DbSession
):
    from langflow.services.database.models.workflow_publication import WorkflowPublication, WorkflowPublicationRun

    await owned(request, session, flow_id, current_user, "run")
    record = await session.get(WorkflowPublicationRun, run_id)
    release = await session.get(WorkflowPublication, record.publication_id) if record else None
    if record is None or release is None or record.created_by != current_user.id or release.flow_id != flow_id:
        raise HTTPException(404, "Run not found")
    return {**record.model_dump(mode="json"), "flow_id": str(flow_id), "publication_version": release.version_number}


@router.post("/flows/{flow_id}/runs/{run_id}/stop")
async def stop(flow_id: UUID, run_id: UUID, request: Request, current_user: CurrentActiveUser, session: DbSession):
    from langflow.services.auth.platform_execution import ACTIVE, ACTIVE_MODELS

    await owned(request, session, flow_id, current_user, "stop")
    key = (str(current_user.id), str(flow_id), str(run_id))
    task = ACTIVE.get(key)
    if task is None or task.done():
        raise HTTPException(404, "No active run on this worker; do not assume a remote task was stopped")
    try:
        if key in ACTIVE_MODELS:
            await context(request)["model_call"]("cancel", {"runId": str(run_id)})
    finally:
        task.cancel()
    return {"status": "cancel_requested", "run_id": str(run_id)}
