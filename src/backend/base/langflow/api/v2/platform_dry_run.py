"""Platform-only synchronous dry run of one saved, owned text graph version."""

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
    try:
        source, target, text = policy.text_pair(deepcopy(flow.data))
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
        output = await asyncio.wait_for(
            policy.execute_text_pair(source, target, text, flow_id=str(flow_id), user_id=user_id, run_id=run_id),
            timeout=policy.TIMEOUT_SECONDS,
        )
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
    except HTTPException:
        raise
    except Exception as exc:
        # 错误响应不携带组件内部参数、源码、环境变量或堆栈。
        raise HTTPException(
            422,
            {"code": "platform_dry_run_failed", "message": "纯文本调试执行失败。请检查节点与连线。", "run_id": run_id},
        ) from exc
    finally:
        policy.ACTIVE_USERS.discard(user_id)
