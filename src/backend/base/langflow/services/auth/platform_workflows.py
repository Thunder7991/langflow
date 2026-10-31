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


def validate_graph(data: dict | None, *, verified_files: set[str] | None = None) -> None:
    """Save inert builtin graphs; file paths must first pass the owner policy."""
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
            if (value.get("password") is True or value.get("load_from_db") is True) and value.get("value"):
                raise HTTPException(403, "Credentials are not enabled for platform editing")
            if value.get("type") == "file" and (value.get("value") or value.get("file_path")):
                paths = value.get("file_path")
                if (
                    not isinstance(paths, list)
                    or not paths
                    or not verified_files
                    or not all(path in verified_files for path in paths)
                ):
                    raise HTTPException(403, "File inputs require verified personal references")
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
    from langflow.services.auth.platform_files import validate_references

    verified = await validate_references(request, session, payload.data, user_id)
    validate_graph(payload.data, verified_files=verified)


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
    from langflow.services.auth.platform_files import validate_references

    data = payload.data if payload.data is not None else flow.data
    verified = await validate_references(request, session, data, user_id)
    validate_graph(data, verified_files=verified)
    if payload.data is not None and not request.headers.get("if-match"):
        raise HTTPException(428, "Graph updates require If-Match")


async def prepare_node_refresh(request: Request, payload, flow_id: UUID | None, user) -> bool:
    """Authorize design-time refresh before any posted source or hook is executed."""
    if not require_action(request, "update"):
        return False
    if flow_id is None or "project_id" in request.query_params:
        raise HTTPException(422, "Node initialization requires an owned flow_id")
    from langflow.services.database.models.flow.model import Flow
    from langflow.services.deps import session_scope

    async with session_scope() as session:
        flow = await session.get(Flow, flow_id)
        if flow is None or flow.user_id != user.id or flow.workspace_id is not None:
            raise HTTPException(404, "Flow not found")
        validate_fields(flow, existing=True)
        await require_personal_destination(session, flow.folder_id, user.id)
        from langflow.services.authorization.guards import FlowAction, ensure_flow_permission

        await ensure_flow_permission(
            user,
            FlowAction.WRITE,
            flow_id=flow.id,
            flow_user_id=flow.user_id,
            workspace_id=flow.workspace_id,
            folder_id=flow.folder_id,
        )

    if payload.tool_mode or payload.field in {"code", "tool_mode", "tools_metadata"} or payload.field.startswith("_"):
        raise HTTPException(403, "B2 暂不支持自定义代码、工具模式或内部字段初始化")

    from importlib import import_module
    from inspect import getsource
    from types import SimpleNamespace

    from langflow.api.v1.custom_component_policy import resolve_component_code_for_action
    from langflow.services.deps import get_catalog_policy_service

    trusted = resolve_component_code_for_action(
        payload.code,
        user=SimpleNamespace(is_superuser=False),
        settings=SimpleNamespace(allow_custom_components=False),
        snapshot=get_catalog_policy_service().snapshot,
        disabled_detail="B2 仅允许服务器可信内置节点初始化, 不能执行自定义代码",
        admin_only_detail="Custom component editing is disabled",
    )
    # “内置”不等于无副作用; 这里只开放已审查的纯表单更新源码。按完整服务器源码
    # 比较, 避免同名插件/多类选择绕过, 也不依赖客户端的类型、hash 或实例配置。
    reviewed = {
        "input_output.chat": "ChatInput",
        "input_output.chat_output": "ChatOutput",
        "input_output.text": "TextInputComponent",
        "input_output.text_output": "TextOutputComponent",
        "models_and_agents.prompt": "PromptComponent",
        "processing.parser": "ParserComponent",
    }
    class_name = next(
        (
            name
            for module, name in reviewed.items()
            if getsource(import_module(f"lfx.components.{module}")).strip() == trusted.strip()
        ),
        None,
    )
    if class_name is None:
        raise HTTPException(403, "当前仅支持已审查的六类内置节点; 通用文件解析、模型和 MCP 节点尚未开放")
    if payload.get_class_name() not in {None, class_name}:
        raise HTTPException(403, "Component class does not match trusted builtin")

    for key, expected in {
        "_frontend_node_flow_id": str(flow.id),
        "_frontend_node_folder_id": str(flow.folder_id),
    }.items():
        supplied = payload.template.get(key, {})
        if supplied and (not isinstance(supplied, dict) or supplied.get("value") != expected):
            raise HTTPException(403, "Node scope does not match owned flow")
    for name, field in payload.template.items():
        if name.startswith("_") and name not in {"_type", "_frontend_node_flow_id", "_frontend_node_folder_id"}:
            raise HTTPException(403, "Internal template fields are not enabled")
        if isinstance(field, dict) and field.get("load_from_db"):
            raise HTTPException(403, "B2 暂不支持数据库变量或凭据初始化")
    # field_value 可以比模板快一拍; 两份都审查, 避免空模板携带秘密值绕过检查。
    field_template = payload.template.get(payload.field)
    if not isinstance(field_template, dict):
        raise HTTPException(422, "Unknown node input field")
    from langflow.services.auth.platform_files import validate_references

    # 初始化也会接收现有文件字段; 两份输入均验证归属, 不读取或执行文件内容。
    data = {
        "template": payload.template,
        "field_value": payload.field_value,
        "updated": {payload.field: {**field_template, "value": payload.field_value}},
    }
    async with session_scope() as session:
        verified = await validate_references(request, session, data, user.id)
    validate_graph(data, verified_files=verified)
    if class_name == "TextInputComponent" and (
        payload.template.get("use_global_variable", {}).get("value")
        or (payload.field == "use_global_variable" and payload.field_value)
    ):
        raise HTTPException(403, "B2 暂不支持全局变量输入")
    payload.code = trusted
    # 原生更新钩子有的读取 self.<field>, 有的读取 field_value, 两者必须对应同次编辑。
    payload.template[payload.field]["value"] = payload.field_value
    payload.frontend_node = {"metadata": {"component_class_name": class_name}}
    return True


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
