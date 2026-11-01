"""Platform model metadata and delegated invocation; no provider key crosses this bridge."""

import re
import ssl
from contextlib import suppress
from contextvars import ContextVar
from inspect import getsource
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

from langflow.services.auth import platform_model_component

runtime = ContextVar("platform_model_runtime", default=None)
MAX_TEMPERATURE = 2
MAX_TOKENS = 4096
MAX_TEXT_BYTES = 64 * 1024


class PlatformModelSessionExpired(HTTPException):
    """Keep bridge expiry distinct from model/Secret permission denial."""

    def __init__(self):
        super().__init__(status.HTTP_401_UNAUTHORIZED, "平台关联会话已失效。")


async def session_exception_handler(request, exc):
    return await session_failure(request.state.platform_workflow, exc)


async def session_failure(identity: dict, exc: HTTPException):
    if exc.status_code != status.HTTP_401_UNAUTHORIZED:
        raise exc
    # 供应商桥接恢复到旧会话的 401 也必须使用宿主已约定的协议, 并收紧能力头。
    with suppress(HTTPException):
        await identity["recheck"]()
    return JSONResponse(
        {"detail": "平台关联会话已失效。", "code": "PLATFORM_SSO_REQUIRED", "hostOrigin": identity["host_origin"]},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


def source() -> str:
    return getsource(platform_model_component)


def is_source(code: str) -> bool:
    return isinstance(code, str) and code.strip() == source().strip()


def template() -> dict:
    from lfx.custom.utils import build_custom_component_template

    node, _ = build_custom_component_template(platform_model_component.PlatformModelComponent(_code=source()))
    return node


def configuration(fields: dict, *, required: bool = True) -> dict:
    if not isinstance(fields, dict) or any(
        name in fields and not isinstance(fields[name], dict)
        for name in ("connection_id", "model_name", "temperature", "max_tokens")
    ):
        raise HTTPException(422, "平台模型参数格式不正确。")
    if set(fields) - {
        "input_value",
        "connection_id",
        "model_name",
        "temperature",
        "max_tokens",
        "code",
        "_type",
        "is_refresh",
        "_frontend_node_flow_id",
        "_frontend_node_folder_id",
    }:
        raise HTTPException(403, "平台模型不接受额外凭据、地址或运行配置字段。")
    connection = fields.get("connection_id", {}).get("value", "")
    model = fields.get("model_name", {}).get("value", "")
    temperature = fields.get("temperature", {}).get("value", 0)
    tokens = fields.get("max_tokens", {}).get("value", 1024)
    try:
        if connection or required:
            UUID(connection)
        if not isinstance(model, str) or (
            (model or required) and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}", model)
        ):
            raise ValueError
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not 0 <= temperature <= MAX_TEMPERATURE
        ):
            raise ValueError
        if isinstance(tokens, bool) or not isinstance(tokens, int) or not 1 <= tokens <= MAX_TOKENS:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(422, "平台模型需要有效租户连接 ID、模型名称、Temperature 0-2 和 Token 1-4096。") from exc
    return {"connection_id": connection, "model_name": model, "temperature": temperature, "max_tokens": tokens}


async def call(action: str, body: dict, settings) -> dict:
    try:
        verify = ssl.create_default_context(cafile=str(settings.ca_bundle)) if settings.ca_bundle else True
        async with httpx.AsyncClient(
            timeout=40 if action == "invoke" else 5, follow_redirects=False, trust_env=False, verify=verify
        ) as client:
            response = await client.post(
                f"{settings.platform_url.rstrip('/')}/api/internal/workflow/models/{action}",
                json=body,
                headers={
                    "x-workflow-client": settings.client_id,
                    "x-workflow-secret": settings.client_secret.get_secret_value(),
                },
            )
        if response.status_code in {401, 403, 404, 409, 429}:
            messages = {
                401: "平台关联会话已失效。",
                403: "没有平台模型元数据或凭据使用权限。",
                404: "租户模型连接不存在或不可用。",
                409: "该模型运行已受理。不能自动重试。",
                429: "平台模型调试繁忙。请稍后重试。",
            }
            detail = messages[response.status_code]
            if response.status_code == status.HTTP_409_CONFLICT:
                detail = {"code": "platform_model_run_accepted", "message": detail}
            raise HTTPException(response.status_code, detail)
        if response.status_code != status.HTTP_200_OK:
            raise HTTPException(502, "平台模型服务调用失败或超时。本次请求不会自动重试。")
        envelope = response.json()
        if (
            not isinstance(envelope, dict)
            or envelope.get("statusCode") != status.HTTP_200_OK
            or not isinstance(envelope.get("data"), dict)
        ):
            raise HTTPException(502, "平台模型服务返回无效结果。")
        return envelope["data"]
    except (httpx.HTTPError, ValueError, OSError) as exc:
        raise HTTPException(502, "平台模型服务不可用或结果未知。请核对后再发起新运行。") from exc


async def invoke_current(connection: str, model: str, text: str, temperature: float, max_tokens: int) -> str:
    invocation = runtime.get()
    if invocation is None:
        raise HTTPException(403, "平台模型只能通过受控调试入口执行。")
    result = await invocation(
        connectionId=connection, modelName=model, input=text, temperature=temperature, maxTokens=max_tokens
    )
    output = result.get("text")
    if not isinstance(output, str) or len(output.encode("utf-8")) > MAX_TEXT_BYTES:
        raise HTTPException(502, "平台模型返回非文本或过大结果。")
    return output


async def refresh(request, payload) -> dict:
    from copy import deepcopy

    node = template()
    fields = node["template"]
    if payload.field not in fields or payload.field == "code":
        raise HTTPException(403, "平台模型字段不可修改。")
    for name in fields:
        if name != "code" and name in payload.template and isinstance(payload.template[name], dict):
            fields[name]["value"] = deepcopy(payload.template[name].get("value", fields[name].get("value")))
    fields[payload.field]["value"] = payload.field_value
    configuration(fields, required=False)
    catalog = await request.state.platform_workflow["model_call"]("catalog", {})
    fields["connection_id"]["options"] = [item["id"] for item in catalog["connections"]]
    fields["connection_id"]["options_metadata"] = [{"description": item["name"]} for item in catalog["connections"]]
    selected = next((item for item in catalog["connections"] if item["id"] == fields["connection_id"]["value"]), None)
    fields["model_name"]["options"] = [
        item["modelName"] for item in catalog.get("models", []) if selected and item["provider"] == selected["provider"]
    ]
    return node


async def validate_references(request, data) -> None:
    # 保存可留空配置草稿, 非空连接引用必须属于当前用户有权查看的实际租户目录。
    references = set()
    nodes = data.get("nodes", []) if isinstance(data, dict) else []
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("data"), dict):
            continue
        body = node.get("data", {})
        if body.get("type") == "PlatformModel":
            definition = body.get("node")
            fields = definition.get("template") if isinstance(definition, dict) else None
            code = fields.get("code") if isinstance(fields, dict) else None
            if not isinstance(code, dict) or not is_source(code.get("value")):
                raise HTTPException(403, "平台模型节点必须使用服务器固定组件。")
            config = configuration(fields, required=False)
            if config["connection_id"]:
                references.add(config["connection_id"])
    if not references:
        return
    catalog = await request.state.platform_workflow["model_call"]("catalog", {})
    visible = {item["id"] for item in catalog["connections"]}
    if not references.issubset(visible):
        raise HTTPException(404, "模型连接不存在、已禁用或当前身份不可见。请重新选择连接。")
