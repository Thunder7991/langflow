"""Personal workflow editing constraints, applied only to platform SSO requests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    from uuid import UUID

    from fastapi import Request
    from sqlmodel.ext.asyncio.session import AsyncSession


def context(request: Request) -> dict | None:
    return getattr(request.state, "platform_workflow", None)


def require_action(request: Request, action: str) -> bool:
    identity = context(request)
    if identity is None:
        return False
    if action not in identity["actions"]:
        raise HTTPException(403, "Platform workflow action is not allowed")
    return True


def validate_graph(data: dict | None) -> None:
    """Save inert builtin graphs; credentials, MCP, file inputs and custom code stay closed."""
    if not data:
        return
    from langflow.api.v1.custom_component_policy import resolve_component_code_for_action
    from langflow.services.deps import get_catalog_policy_service

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            # 在原生秘密提取之前检查模板(包含分组中的子节点), 避免拒绝保存却留下凭据副作用。
            if "mcp" in str(value.get("type", "")).lower():
                raise HTTPException(403, "MCP components are not enabled for platform editing")
            if (
                value.get("password") is True or value.get("load_from_db") is True or value.get("type") == "file"
            ) and value.get("value"):
                raise HTTPException(403, "Credentials and file inputs are not enabled for platform editing")
            code = value.get("code")
            if isinstance(code, str) and code:
                raise HTTPException(403, "Custom code fields are not enabled")
            if isinstance(code, dict) and code.get("value"):
                from types import SimpleNamespace

                code["value"] = resolve_component_code_for_action(
                    code["value"],
                    user=SimpleNamespace(is_superuser=False),
                    settings=SimpleNamespace(allow_custom_components=False),
                    snapshot=get_catalog_policy_service().snapshot,
                    disabled_detail="Only trusted builtin component code can be saved",
                    admin_only_detail="Custom component editing is disabled",
                )
            for name, item in value.items():
                if name.lower() in {
                    "api_key",
                    "token",
                    "password",
                    "secret",
                    "mcp_server",
                    "mcp_servers",
                    "mcpservers",
                    "server_config",
                }:
                    supplied = item.get("value") if isinstance(item, dict) else item
                    if supplied and not (name.lower() == "password" and isinstance(supplied, bool)):
                        raise HTTPException(403, "Secret and MCP configuration fields are not enabled")
                visit(item)

    visit(data)


def validate_fields(payload, *, existing: bool = False) -> None:
    # Neutral defaults emitted by the existing React client remain compatible.
    forbidden = {
        "user_id": None,
        "workspace_id": None,
        "fs_path": None,
        "mcp_enabled": False,
        "webhook": False,
        "is_component": False,
        "endpoint_name": None,
        "access_type": "PRIVATE",
        "flow_type": "workflow",
        "a2a_enabled": False,
        "a2a_card_overrides": None,
    }
    for field, default in forbidden.items():
        if existing and field == "user_id":
            continue
        value = getattr(payload, field, None)
        if value is not None and value != default and not (field == "endpoint_name" and value == ""):
            raise HTTPException(403, f"Field is not enabled for platform editing: {field}")
    if not existing and getattr(payload, "id", None) is not None:
        raise HTTPException(403, "Flow IDs are assigned by the server")


async def require_personal_destination(session: AsyncSession, folder_id: UUID | None, user_id: UUID) -> None:
    if folder_id is None:
        return
    from langflow.services.database.models.folder.model import Folder

    folder = await session.get(Folder, folder_id)
    if folder is None or folder.user_id != user_id or folder.workspace_id is not None:
        raise HTTPException(404, "Project not found")


async def validate_create(request: Request, session: AsyncSession, payload, user_id: UUID) -> None:
    if not require_action(request, "create"):
        return
    validate_fields(payload)
    await require_personal_destination(session, payload.folder_id, user_id)
    validate_graph(payload.data)


async def validate_update(request: Request, session: AsyncSession, payload, flow, user_id: UUID) -> None:
    if not require_action(request, "update"):
        return
    if flow.user_id != user_id:
        raise HTTPException(404, "Flow not found")
    # 不能借由普通保存把已暴露/受外部管理的流程转换成可编辑个人流程。
    validate_fields(flow, existing=True)
    validate_fields(payload)
    await require_personal_destination(session, flow.folder_id, user_id)
    await require_personal_destination(session, getattr(payload, "folder_id", None), user_id)
    validate_graph(payload.data if payload.data is not None else flow.data)
    if payload.data is not None and not request.headers.get("if-match"):
        raise HTTPException(428, "Graph updates require If-Match")


async def effective_permissions(request: Request, session: AsyncSession, body, user_id: UUID) -> dict | None:
    identity = context(request)
    if identity is None:
        return None
    from sqlmodel import col, select

    from langflow.services.database.models.flow.model import Flow
    from langflow.services.database.models.folder.model import Folder

    requested = body.actions or ["read", "write", "execute", "delete", "create"]
    model = {"flow": Flow, "project": Folder}.get(body.resource_type)
    owned = set()
    editable = set()
    if model is not None:
        rows = (
            await session.exec(
                select(model).where(
                    col(model.id).in_(body.resource_ids), model.user_id == user_id, model.workspace_id.is_(None)
                )
            )
        ).all()
        owned = {row.id for row in rows}
        if model is Flow:
            for row in rows:
                try:
                    validate_fields(row, existing=True)
                except HTTPException:
                    continue
                editable.add(row.id)
    allowed = {
        "read": "read" in identity["actions"],
        "create": "create" in identity["actions"],
        "write": body.resource_type == "flow" and "update" in identity["actions"],
    }
    return {
        "resource_type": body.resource_type,
        "permissions": {
            resource_id: [a for a in requested if allowed.get(a, False) and (a != "write" or resource_id in editable)]
            if resource_id in owned
            else []
            for resource_id in body.resource_ids
        },
    }
