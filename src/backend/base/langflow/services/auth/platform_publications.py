"""Publication transactions; draft writes never change the online pointer."""

import hashlib
import json
from copy import deepcopy
from datetime import timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError
from sqlmodel import select, update

from langflow.services.auth import platform_execution
from langflow.services.database.lock_retry import is_database_lock_error
from langflow.services.database.models.audit_event.model import AuditEvent
from langflow.services.database.models.flow.model import Flow
from langflow.services.database.models.workflow_publication import WorkflowPublication, WorkflowPublicationState


def digest(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def metadata(publication):
    if publication is None:
        return None
    result = publication.model_dump(mode="json", exclude={"data", "dependencies"})
    created = publication.created_at
    result["created_at"] = (created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created).isoformat()
    return result


async def state_response(session, flow):
    state = await session.get(WorkflowPublicationState, flow.id)
    current = await session.get(WorkflowPublication, state.publication_id) if state and state.publication_id else None
    return {
        "status": "published" if current else "offline" if state else "unpublished",
        "publication_token": str(state.token) if state else None,
        "current": metadata(current),
        "latest_version": state.latest_version if state else 0,
        "has_unpublished_changes": current is not None and current.digest != digest(flow.data),
    }


async def lock_flow(session, flow):
    # 与原生保存共用 Flow 行锁。无变化 UPDATE 也让 SQLite 取得写锁, 避免首次发布竞态。
    owner = flow.user_id
    try:
        if session.get_bind().dialect.name == "postgresql":
            await session.exec(select(Flow.id).where(Flow.id == flow.id).with_for_update(nowait=True, key_share=True))
        result = await session.exec(update(Flow).where(Flow.id == flow.id).values(version_token=Flow.version_token))
    except DBAPIError as exc:
        code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if is_database_lock_error(exc) or code in {"55P03", "40001", "40P01"}:
            raise HTTPException(503, "流程正在保存或发布, 请刷新后重试。", headers={"Retry-After": "1"}) from exc
        raise
    if result.rowcount != 1:
        raise HTTPException(404, "Flow not found")
    await session.refresh(flow)
    if flow.user_id != owner or flow.workspace_id is not None:
        raise HTTPException(404, "Flow not found")
    from langflow.services.auth.platform_workflows import require_personal_destination

    await require_personal_destination(session, flow.folder_id, owner)


async def check_state(session, flow, expected):
    state = await session.get(WorkflowPublicationState, flow.id, populate_existing=True)
    if (state.token if state else None) != expected:
        raise HTTPException(409, "线上版本已变化, 请刷新发布状态后重试。")
    return state


def validate_release(data):
    graph = platform_execution.prepared_graph(deepcopy(data))
    inputs = [node for node in graph["nodes"] if node["data"]["type"] in {"TextInput", "ChatInput"}]
    outputs = [node for node in graph["nodes"] if node["data"]["type"] in {"TextOutput", "ChatOutput"}]
    if len(inputs) > 1 or not outputs:
        raise HTTPException(422, "首期发布最多支持一个文本入口, 且必须有明确的文本输出。")
    connected = {edge["target"] for edge in graph["edges"]}
    if any(node["id"] not in connected for node in outputs):
        raise HTTPException(422, "输出节点尚未连接, 不能发布。")
    models = []
    for node in graph["nodes"]:
        if node["data"]["type"] == "PlatformModel":
            fields = node["data"]["node"]["template"]
            from langflow.services.auth.platform_models import configuration

            config = configuration(fields)
            models.append({key: value for key, value in config.items() if key not in {"temperature", "max_tokens"}})
    input_schema = {"nodes": [node["id"] for node in inputs]}
    if inputs:
        input_schema["input"] = {"type": "string", "required": False, "nullable": True, "max_bytes": 65536}
    return (
        input_schema,
        {"nodes": [node["id"] for node in outputs], "type": "text_outputs"},
        {"models": models},
    )


async def switch(session, flow, user, state, publication, operation):
    previous = state.publication_id if state else None
    if state is None:
        state = WorkflowPublicationState(flow_id=flow.id)
    state.publication_id = publication.id if publication else None
    state.token = uuid4()
    if operation == "publish":
        state.latest_version = publication.version_number
    session.add(state)
    # 指针与审计在同一事务内提交; 回滚/下线不修改快照和草稿。
    session.add(
        AuditEvent(
            resource_type="flow",
            resource_id=flow.id,
            resource_name=flow.name,
            actor_type="user",
            actor_id=user.id,
            user_id=user.id,
            action="workflow:publish",
            operation=operation,
            event_type="action",
            result="succeeded",
            request_id=uuid4(),
            details={
                "schema_version": 1,
                "publication_id": str(publication.id) if publication else None,
                "previous_publication_id": str(previous) if previous else None,
            },
        )
    )
    await session.flush()
    return await state_response(session, flow)
