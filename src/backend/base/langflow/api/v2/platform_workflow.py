"""Platform operations with explicit permissions, personal ownership and inert imports."""

from copy import deepcopy
from typing import Annotated
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

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
    from langflow.api.v1.flows import create_flow

    require_action(request, "create")
    await require_personal_destination(session, body.folder_id, user.id)
    pending = []
    for raw in body.flows:
        # 导入只创建新副本;不接受外部 owner、ID、公开访问、webhook、路径或凭据副作用。
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
        await validate_create(request, session, flow, user.id)
        pending.append(flow)
    results = []
    # 整个批次同一事务,后续任一图失败不能留下部分流程。
    async with session.begin_nested():
        for flow in pending:
            results.append(  # noqa: PERF401 - transactional async writes
                await create_flow(
                    request=request,
                    session=session,
                    flow=flow,
                    current_user=user,
                    _create=(None, flow.folder_id),
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


@router.post("/flows/{flow_id}/publish")
async def publish(
    flow_id: UUID, body: PublishBody, request: Request, current_user: CurrentActiveUser, session: DbSession
):
    from langflow.services.database.models.flow.model import AccessTypeEnum

    flow = await owned(request, session, flow_id, current_user, "publish")
    await ensure_flow_permission(
        current_user, FlowAction.WRITE, flow_id=flow.id, flow_user_id=flow.user_id, folder_id=flow.folder_id
    )
    # 发布采用 Langflow 现有 PUBLIC 标识;实例 SSO 仍生效,不提供匿名执行旁路。
    flow.access_type = AccessTypeEnum.PUBLIC if body.published else AccessTypeEnum.PRIVATE
    session.add(flow)
    await session.flush()
    return export_flow(flow)


@router.get("/flows/{flow_id}/share")
async def share(flow_id: UUID, request: Request, current_user: CurrentActiveUser, session: DbSession):
    flow = await owned(request, session, flow_id, current_user, "share")
    settings = request.app.state.platform_sso if hasattr(request.app.state, "platform_sso") else None
    # 分享编辑器入口保留平台登录和资源归属;不生成持有即授权的长期访问凭据。
    base = settings.public_url if settings else str(request.base_url)
    return {"url": f"{base.rstrip('/')}/flow/{flow.id}", "authentication": "platform_sso", "scope": "personal"}


@router.get("/flows/{flow_id}/api")
async def api_configuration(flow_id: UUID, request: Request, current_user: CurrentActiveUser, session: DbSession):
    flow = await owned(request, session, flow_id, current_user, "api_access")
    return {
        "method": "POST",
        "path": f"{request.scope.get('root_path', '')}/api/v2/platform-workflow/flows/{flow.id}/run",
        "authentication": "platform_sso",
        "credentials": "include",
        "required_action": "run",
        "headers": {"If-Match": str(flow.version_token)},
        "body": {"input": "待调试的文本"},
    }


class RunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID = Field(default_factory=uuid4)
    input: str | None = Field(default=None, max_length=65536)


@router.post("/flows/{flow_id}/run")
async def run(flow_id: UUID, body: RunBody, request: Request, current_user: CurrentActiveUser, session: DbSession):
    import asyncio

    from langflow.api.utils import release_db_transaction
    from langflow.api.v1.flow_conflict import parse_if_match
    from langflow.services.auth import platform_execution
    from langflow.services.auth.platform_dry_run import ACTIVE_USERS

    flow = await owned(request, session, flow_id, current_user, "run")
    await ensure_flow_permission(
        current_user, FlowAction.EXECUTE, flow_id=flow.id, flow_user_id=flow.user_id, folder_id=flow.folder_id
    )
    expected = parse_if_match(request.headers.get("if-match"))
    if expected is None:
        raise HTTPException(428, "Run requires If-Match")
    if expected != flow.version_token:
        raise HTTPException(409, "Saved version changed; reload before running")
    if body.input is not None and len(body.input.encode()) > MAX_INPUT_BYTES:
        raise HTTPException(422, "Input exceeds 64 KiB")
    graph = platform_execution.prepared_graph(deepcopy(flow.data), input_text=body.input)
    identity = context(request)
    actor, version, run_id = str(current_user.id), str(flow.version_token), str(body.run_id)
    await release_db_transaction(session)
    if actor in ACTIVE_USERS:
        raise HTTPException(429, "A workflow run is already active")
    key = (actor, str(flow_id), run_id)
    ACTIVE_USERS.add(actor)
    sequence = 0

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
        if "run" not in await identity["recheck"]():
            raise HTTPException(403, "Run permission revoked")
        return {
            "status": "succeeded",
            "run_id": run_id,
            "flow_id": str(flow_id),
            "version_token": version,
            "outputs": outputs,
        }
    except asyncio.CancelledError:
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
