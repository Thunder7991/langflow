"""Chat and safe graph progress over the existing platform execution handlers."""

import asyncio
import json
from contextlib import suppress
from typing import Literal
from uuid import UUID

import anyio
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from langflow.api.utils import CurrentActiveUser
from langflow.api.v1.flow_conflict import parse_if_match
from langflow.api.v2.platform_dry_run import DryRunBody, dry_run
from langflow.api.v2.platform_workflow import RunBody, run
from langflow.services.auth import platform_execution
from langflow.services.auth.platform_workflows import context, require_action
from langflow.services.deps import session_scope

router = APIRouter(prefix="/platform-playground", tags=["Platform Playground"])


class PlaygroundBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["draft", "published"]
    input: str | None = Field(default=None, max_length=65536)
    run_id: UUID


@router.post("/{flow_id}")
async def playground(flow_id: UUID, body: PlaygroundBody, request: Request, current_user: CurrentActiveUser):
    identity = context(request)
    if identity is None or identity["user_id"] != str(current_user.id):
        raise HTTPException(403, "Platform SSO required")
    require_action(request, "read")
    require_action(request, "dry_run" if body.mode == "draft" else "run")
    if request.query_params:
        raise HTTPException(422, "Custom runtime options are not enabled")
    if parse_if_match(request.headers.get("if-match")) is None:
        raise HTTPException(428, "Playground requires a confirmed saved or published version")
    queue: asyncio.Queue = asyncio.Queue()
    run_id = str(body.run_id)

    def send(event):
        queue.put_nowait({**event, "run_id": run_id})

    def error_event(status_code, detail):
        message = detail.get("message", "运行失败, 请核对节点及权限。") if isinstance(detail, dict) else detail
        return {
            "type": "run_error",
            "status_code": status_code,
            "detail": message,
            "code": "PLATFORM_SSO_REQUIRED"
            if status_code == status.HTTP_401_UNAUTHORIZED
            else "PLATFORM_EXECUTION_ERROR",
            "hostOrigin": identity["host_origin"],
        }

    async def produce():
        token = platform_execution.EVENT_SINK.set(send)
        try:
            payload = {"input": body.input, "run_id": body.run_id}
            # 复用受控入口, 草稿归属和发布快照、防重、企业模型权限不另写一套。
            # 普通 DbSession 在响应头发出前关闭; 流任务必须持有自己的会话完成运行台账。
            async with session_scope() as execution_session:
                result = await (
                    dry_run(flow_id, DryRunBody(**payload), request, current_user, execution_session)
                    if body.mode == "draft"
                    else run(flow_id, RunBody(**payload), request, current_user, execution_session)
                )
            if isinstance(result, JSONResponse):
                failure = json.loads(result.body)
                send(error_event(result.status_code, failure.get("detail", "运行失败。")))
            elif result["status"] == "cancelled":
                send({"type": "run_cancelled"})
            else:
                send({"type": "run_finished", **result})
        except HTTPException as exc:
            send(error_event(exc.status_code, exc.detail))
        except Exception:  # noqa: BLE001 - SSE errors must never expose internal component data.
            send(error_event(500, "运行失败, 请检查节点与连线。"))
        finally:
            platform_execution.EVENT_SINK.reset(token)

    task = asyncio.create_task(produce())
    try:
        # 所有者、版本、图合法性与受理防重完成后才建立 SSE, 错误仍返回真实 HTTP 状态。
        first = await queue.get()
    except BaseException:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        raise
    if first["type"] != "run_started":
        await task
        return JSONResponse(first, status_code=first.get("status_code", 500))

    async def stream():
        try:
            yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in {"run_finished", "run_error", "run_cancelled"}:
                    break
        finally:
            # ASGI 断线会取消流的作用域, 屏蔽外层取消以完成本地任务及发布台账清理。
            with anyio.CancelScope(shield=True):
                key = (str(current_user.id), str(flow_id), run_id)
                try:
                    if not task.done() and key in platform_execution.ACTIVE_MODELS:
                        # 桥接服务异常也不能阻断本 worker 的取消; 已受理请求仍可能计费。
                        with suppress(Exception):
                            await asyncio.wait_for(identity["model_call"]("cancel", {"runId": run_id}), timeout=3)
                finally:
                    if not task.done():
                        task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )
