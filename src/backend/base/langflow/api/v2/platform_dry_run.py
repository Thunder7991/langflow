"""Platform-only synchronous dry run of saved text or delegated model graphs."""

import asyncio
from copy import deepcopy
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from langflow.api.utils import CurrentActiveUser, DbSession, release_db_transaction
from langflow.api.v1.flow_conflict import parse_if_match
from langflow.services.auth import platform_dry_run as policy
from langflow.services.auth import platform_execution
from langflow.services.auth.platform_workflows import (
    context,
    require_action,
    require_personal_destination,
    validate_fields,
)
from langflow.services.authorization.guards import FlowAction, ensure_flow_permission
from langflow.services.database.models.flow.model import Flow

router = APIRouter(prefix="/platform-flows", tags=["Platform text dry run"])
models_router = APIRouter(prefix="/platform-models", tags=["Platform models"])


@models_router.get("")
async def model_catalog(request: Request, current_user: CurrentActiveUser):
    identity = context(request)
    if identity is None or identity["user_id"] != str(current_user.id):
        raise HTTPException(403, "Platform models require platform SSO")
    require_action(request, "read")
    try:
        from langflow.services.auth.platform_models import ensure_inventory

        await ensure_inventory(request)
        return await identity["model_call"]("catalog", {"catalogVersion": 2})
    except HTTPException as exc:
        from langflow.services.auth.platform_models import session_failure

        return await session_failure(identity, exc)


class DryRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str | None = Field(default=None, max_length=65536)
    run_id: UUID = Field(default_factory=uuid4)


@router.post("/{flow_id}/dry-run")
async def dry_run(
    flow_id: UUID, _body: DryRunBody, request: Request, current_user: CurrentActiveUser, session: DbSession
):
    identity = context(request)
    if identity is None or identity["user_id"] != str(current_user.id):
        raise HTTPException(403, "Text dry runs require platform SSO")
    require_action(request, "dry_run")
    if request.query_params:
        raise HTTPException(422, "Stream, background and custom runtime options are not enabled")
    flow = await session.get(Flow, flow_id)
    if flow is None or flow.user_id != current_user.id or flow.workspace_id is not None:
        raise HTTPException(404, "Flow not found")
    validate_fields(flow, existing=True)
    await require_personal_destination(session, flow.folder_id, current_user.id)
    await ensure_flow_permission(
        current_user,
        FlowAction.EXECUTE,
        flow_id=flow.id,
        flow_user_id=flow.user_id,
        folder_id=flow.folder_id,
        workspace_id=flow.workspace_id,
    )
    expected = parse_if_match(request.headers.get("if-match"))
    if expected is None:
        raise HTTPException(428, "Dry run requires If-Match of the saved version")
    if flow.version_token is None or expected != flow.version_token:
        raise HTTPException(409, "Saved flow version changed; reload before running")
    try:
        # 草稿与发布快照使用相同的受控节点适配, 但只读取本次已核对修订的草稿。
        if _body.input is not None and len(_body.input.encode()) > policy.MAX_TEXT_BYTES:
            raise HTTPException(422, "文本输入不能超过 64 KiB。")
        saved_graph = platform_execution.prepared_graph(deepcopy(flow.data), input_text=_body.input)
        output_ids = {
            node["id"] for node in saved_graph["nodes"] if node["data"]["type"] in {"TextOutput", "ChatOutput"}
        }
        inputs = [node for node in saved_graph["nodes"] if node["data"]["type"] in {"TextInput", "ChatInput"}]
        connected = {edge["target"] for edge in saved_graph["edges"]}
        if len(inputs) > 1 or not output_ids or not output_ids.issubset(connected):
            raise HTTPException(422, "草稿调试最多支持一个文本入口, 且必须连接明确文本输出。")
        if _body.input is not None and not inputs:
            raise HTTPException(422, "该草稿没有可覆盖的文本入口。")
        uses_model = any(node["data"]["type"] == "PlatformModel" for node in saved_graph["nodes"])
    except (AttributeError, KeyError, TypeError) as exc:
        raise HTTPException(422, "Saved graph structure is invalid for text dry runs") from exc
    version = str(flow.version_token)
    user_id = str(current_user.id)
    await release_db_transaction(session)
    if user_id in policy.ACTIVE_USERS:
        raise HTTPException(429, "A text dry run is already active for this user")
    run_id = str(_body.run_id)
    key = (user_id, str(flow_id), run_id)
    platform_execution.claim_draft_run(key)
    policy.ACTIVE_USERS.add(user_id)
    sequence = 0
    try:

        async def invoke(**parameters):
            nonlocal sequence
            if "dry_run" not in await identity["recheck"]():
                raise HTTPException(403, "Text dry run permission was revoked")
            # 模型节点共用执行上下文, 每次供应商调用单独防重, 避免第二个节点被当成重复请求。
            sequence += 1
            model_run_id = str(uuid5(UUID(run_id), str(sequence)))
            result = await identity["model_call"](
                "invoke",
                {
                    **parameters,
                    "flowId": str(flow_id),
                    "versionToken": version,
                    "runId": model_run_id,
                    "executionId": run_id,
                },
            )
            if result.get("runId") != model_run_id:
                raise HTTPException(502, "平台模型运行标识不匹配。")
            return result

        execution = platform_execution.execute(
            saved_graph, invoke=invoke, flow_id=str(flow_id), user_id=user_id, run_id=run_id
        )
        task = asyncio.create_task(execution)
        platform_execution.ACTIVE[key] = task
        if uses_model:
            platform_execution.ACTIVE_MODELS.add(key)
        platform_execution.emit("run_started", version_token=version, flow_id=str(flow_id))
        results = await asyncio.wait_for(task, timeout=45 if uses_model else policy.TIMEOUT_SECONDS)
        outputs = [item for item in results if item["node_id"] in output_ids]
        if {item["node_id"] for item in outputs} != output_ids or any(
            not isinstance(item["text"], str) or len(item["text"].encode()) > policy.MAX_TEXT_BYTES for item in outputs
        ):
            raise HTTPException(422, "草稿未返回完整文本输出或输出超过 64 KiB。")
        # 不把运行期间已经失效的会话结果重新交给浏览器。
        try:
            current_actions = await identity["recheck"]()
        except HTTPException as exc:
            return JSONResponse(
                {
                    "detail": exc.detail,
                    "code": "PLATFORM_SSO_REQUIRED"
                    if exc.status_code == status.HTTP_401_UNAUTHORIZED
                    else "PLATFORM_SSO_ERROR",
                    "hostOrigin": identity["host_origin"],
                },
                status_code=exc.status_code,
            )
        if "dry_run" not in current_actions:
            raise HTTPException(403, "Text dry run permission was revoked")
        return {
            "run_id": run_id,
            "flow_id": str(flow_id),
            "version_token": version,
            "status": "succeeded",
            "outputs": outputs,
        }
    except asyncio.CancelledError:
        return {"status": "cancelled", "run_id": run_id, "outputs": []}
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            504, {"code": "platform_dry_run_timeout", "message": "纯文本调试超时。请稍后重试。", "run_id": run_id}
        ) from exc
    except HTTPException as exc:
        if uses_model and exc.status_code != status.HTTP_401_UNAUTHORIZED:
            detail = (
                exc.detail
                if isinstance(exc.detail, dict)
                else {"code": "platform_model_failed", "message": str(exc.detail)}
            )
            raise HTTPException(exc.status_code, {**detail, "run_id": run_id}) from None
        from langflow.services.auth.platform_models import session_failure

        return await session_failure(identity, exc)
    except Exception as exc:
        # 错误响应不携带组件内部参数、源码、环境变量或堆栈。
        raise HTTPException(
            422,
            {"code": "platform_dry_run_failed", "message": "纯文本调试执行失败。请检查节点与连线。", "run_id": run_id},
        ) from exc
    finally:
        platform_execution.ACTIVE.pop(key, None)
        platform_execution.ACTIVE_MODELS.discard(key)
        policy.ACTIVE_USERS.discard(user_id)
