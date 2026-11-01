"""Platform-only synchronous dry run of saved text or delegated model graphs."""

import asyncio
from copy import deepcopy
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from langflow.api.utils import CurrentActiveUser, DbSession, release_db_transaction
from langflow.api.v1.flow_conflict import parse_if_match
from langflow.services.auth import platform_dry_run as policy
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
        return await identity["model_call"]("catalog", {})
    except HTTPException as exc:
        from langflow.services.auth.platform_models import session_failure

        return await session_failure(identity, exc)


class DryRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    model_spec = None
    try:
        saved_graph = deepcopy(flow.data)
        if isinstance(saved_graph, dict) and len(saved_graph.get("nodes", [])) == 3:  # noqa: PLR2004 - input, model, output
            source, target, text, model_id, configuration = policy.model_chain(saved_graph)
            model_spec = (model_id, configuration)
        else:
            source, target, text = policy.text_pair(saved_graph)
    except (AttributeError, KeyError, TypeError) as exc:
        raise HTTPException(422, "Saved graph structure is invalid for text dry runs") from exc
    version = str(flow.version_token)
    user_id = str(current_user.id)
    await release_db_transaction(session)
    if user_id in policy.ACTIVE_USERS:
        raise HTTPException(429, "A text dry run is already active for this user")
    policy.ACTIVE_USERS.add(user_id)
    run_id = str(uuid4())
    try:
        if model_spec:

            async def invoke(**parameters):
                result = await identity["model_call"](
                    "invoke", {**parameters, "flowId": str(flow_id), "versionToken": version, "runId": run_id}
                )
                if result.get("runId") != run_id:
                    raise HTTPException(502, "平台模型运行标识不匹配。")
                return result

            execution = policy.execute_model_chain(
                source, target, text, *model_spec, invoke=invoke, flow_id=str(flow_id), user_id=user_id, run_id=run_id
            )
        else:
            execution = policy.execute_text_pair(
                source, target, text, flow_id=str(flow_id), user_id=user_id, run_id=run_id
            )
        output = await asyncio.wait_for(execution, timeout=45 if model_spec else policy.TIMEOUT_SECONDS)
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
            "outputs": [{"node_id": target, "text": output}],
        }
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            504, {"code": "platform_dry_run_timeout", "message": "纯文本调试超时。请稍后重试。", "run_id": run_id}
        ) from exc
    except HTTPException as exc:
        if model_spec and exc.status_code != status.HTTP_401_UNAUTHORIZED:
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
        policy.ACTIVE_USERS.discard(user_id)
