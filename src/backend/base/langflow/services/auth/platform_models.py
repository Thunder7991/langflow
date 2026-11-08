"""Platform model metadata and delegated invocation; no provider key crosses this bridge."""

import asyncio
import hashlib
import json
import re
import ssl
from contextlib import suppress
from contextvars import ContextVar
from inspect import getsource
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

from langflow.services.auth import platform_model_component, platform_model_legacy_component

runtime = ContextVar("platform_model_runtime", default=None)
MAX_TEMPERATURE = 2
MAX_TOKENS = 4096
MAX_TEXT_BYTES = 64 * 1024
MAX_DEPENDENCY_BATCH = 256


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
    return isinstance(code, str) and code.strip() in {
        source().strip(),
        getsource(platform_model_legacy_component).strip(),
    }


def template(*, legacy=False) -> dict:
    from lfx.custom.utils import build_custom_component_template

    module = platform_model_legacy_component if legacy else platform_model_component
    node, _ = build_custom_component_template(module.PlatformModelComponent(_code=getsource(module)))
    return node


def configuration(fields: dict, *, required: bool = True) -> dict:
    if not isinstance(fields, dict) or any(
        name in fields and not isinstance(fields[name], dict)
        for name in (
            "selection_mode",
            "connection_model_id",
            "connection_id",
            "model_name",
            "temperature",
            "max_tokens",
        )
    ):
        raise HTTPException(422, "平台模型参数格式不正确。")
    if set(fields) - {
        "input_value",
        "connection_id",
        "model_name",
        "selection_mode",
        "connection_model_id",
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
    mode = fields.get("selection_mode", {}).get("value")
    record = fields.get("connection_model_id", {}).get("value", "")
    temperature = fields.get("temperature", {}).get("value", 0)
    tokens = fields.get("max_tokens", {}).get("value", 1024)
    code = fields.get("code", {}).get("value", "")
    if is_source(code) and (code.strip() == source().strip()) != (mode is not None):
        raise HTTPException(403, "固定模型组件源码与选择契约不一致,请重新添加节点或保留原历史配置。")
    try:
        if mode is not None:
            if mode not in {"tenant_default", "explicit"} or connection or model:
                raise ValueError
            if mode == "tenant_default" and record:
                raise ValueError
            if mode == "explicit" and (record or required):
                UUID(record)
        else:
            if record:
                raise ValueError
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
        raise HTTPException(
            422, "请选择企业默认或有效企业模型记录;Temperature 0-2,Token 1-4096。新旧选择不可混用。"
        ) from exc
    if mode is not None:
        return {"selection_mode": mode, "connection_model_id": record, "temperature": temperature, "max_tokens": tokens}
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
        if response.status_code in {400, 401, 403, 404, 409, 429, 503}:
            messages = {
                401: "平台关联会话已失效。",
                403: "没有平台模型元数据或凭据使用权限。",
                404: "租户模型连接不存在或不可用。",
                409: "该模型运行已受理。不能自动重试。",
                429: "平台模型调试繁忙。请稍后重试。",
                400: "企业模型选择无效,请重新选择。",
                503: "模型依赖台账尚未就绪,请检查平台迁移及同步状态。",
            }
            detail = messages[response.status_code]
            if response.status_code == status.HTTP_409_CONFLICT:
                envelope = response.json()
                error = envelope.get("errorCode") if isinstance(envelope, dict) else None
                # 只有真正的重复受理才提示结果未知,目录状态冲突不冒充已调用供应商。
                if error in {"TENANT_DEFAULT_UNSET", "MODEL_UNAVAILABLE"}:
                    detail = {
                        "code": error,
                        "message": "企业默认模型未设置。"
                        if error == "TENANT_DEFAULT_UNSET"
                        else "企业模型或所属渠道不可用。",
                    }
                else:
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


async def invoke_current(
    connection: str,
    model: str,
    text: str,
    temperature: float,
    max_tokens: int,
    *,
    selection_mode=None,
    connection_model_id="",
) -> str:
    invocation = runtime.get()
    if invocation is None:
        raise HTTPException(403, "平台模型只能通过受控调试入口执行。")
    selection = (
        {"connectionId": connection, "modelName": model}
        if selection_mode is None
        else {
            "selectionMode": selection_mode,
            **({"connectionModelId": connection_model_id} if selection_mode == "explicit" else {}),
        }
    )
    result = await invocation(**selection, input=text, temperature=temperature, maxTokens=max_tokens)
    output = result.get("text")
    if not isinstance(output, str) or len(output.encode("utf-8")) > MAX_TEXT_BYTES:
        raise HTTPException(502, "平台模型返回非文本或过大结果。")
    return output


async def refresh(request, payload) -> dict:
    from copy import deepcopy

    await ensure_inventory(request)
    if payload.field == "selection_mode" and payload.field_value == "tenant_default":
        payload.template["connection_model_id"]["value"] = ""
    configuration(payload.template, required=False)
    legacy = payload.code.strip() != source().strip()
    node = template(legacy=legacy)
    fields = node["template"]
    if payload.field not in fields or payload.field == "code":
        raise HTTPException(403, "平台模型字段不可修改。")
    for name in fields:
        if name != "code" and name in payload.template and isinstance(payload.template[name], dict):
            fields[name]["value"] = deepcopy(payload.template[name].get("value", fields[name].get("value")))
    fields[payload.field]["value"] = payload.field_value
    if not legacy and payload.field == "selection_mode" and payload.field_value == "tenant_default":
        fields["connection_model_id"]["value"] = ""
    configuration(fields, required=False)
    catalog = await request.state.platform_workflow["model_call"]("catalog", {} if legacy else {"catalogVersion": 2})
    if not legacy:
        fields["connection_model_id"]["show"] = fields["selection_mode"]["value"] == "explicit"
        fields["connection_model_id"]["options"] = [item["id"] for item in catalog["items"]]
        fields["connection_model_id"]["options_metadata"] = [
            {"description": f"{item['connectionName']} · {item['displayName']} ({item['modelName']})"}
            for item in catalog["items"]
        ]
        state = catalog["defaultState"]
        fields["selection_mode"]["info"] = {
            "unset": "企业默认模型未设置,请在企业模型管理中设置。",
            "unavailable": "企业默认模型不可用或当前身份不可见,请检查状态与权限。",
            "configured": "企业默认模型已配置;执行期间固定本次选择,下一次执行再解析最新默认。",
        }[state]
        if not catalog["items"]:
            fields["connection_model_id"]["info"] = "企业模型目录为空,请检查已启用模型和目录权限。"
        elif (
            fields["connection_model_id"]["value"]
            and fields["connection_model_id"]["value"] not in fields["connection_model_id"]["options"]
        ):
            fields["connection_model_id"]["info"] = "原选择已不可用或当前身份不可见;保留原 ID,请重新选择。"
        return node
    fields["connection_id"]["options"] = [item["id"] for item in catalog["connections"]]
    fields["connection_id"]["options_metadata"] = [{"description": item["name"]} for item in catalog["connections"]]
    selected = next((item for item in catalog["connections"] if item["id"] == fields["connection_id"]["value"]), None)
    fields["model_name"]["options"] = [
        item["modelName"] for item in catalog.get("models", []) if selected and item["provider"] == selected["provider"]
    ]
    return node


def model_selections(value):
    if isinstance(value, list):
        for child in value:
            yield from model_selections(child)
    elif isinstance(value, dict):
        if value.get("type") == "PlatformModel" and "node" in value:
            fields = value["node"].get("template")
            if not isinstance(fields, dict) or not is_source(fields.get("code", {}).get("value")):
                raise HTTPException(403, "平台模型节点必须使用服务器固定组件。")
            config = configuration(fields, required=False)
            if config.get("selection_mode"):
                if config["selection_mode"] == "tenant_default" or config["connection_model_id"]:
                    yield {
                        "selectionMode": config["selection_mode"],
                        **(
                            {"connectionModelId": config["connection_model_id"]}
                            if config["selection_mode"] == "explicit"
                            else {}
                        ),
                    }
            elif config["connection_id"]:
                yield {"connectionId": config["connection_id"], "modelName": config["model_name"]}
        for child in value.values():
            yield from model_selections(child)


async def validate_references(request, data, *, require_use=False) -> None:
    # 保存可留空配置草稿, 非空连接引用必须属于当前用户有权查看的实际租户目录。
    selections = list(model_selections(data))
    references = {item["connectionId"] for item in selections if item.get("connectionId")}
    if selections:
        await ensure_inventory(request)
        digest = hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        result = await request.state.platform_workflow["model_call"](
            "references",
            {"references": [{**item, "graphDigest": digest} for item in selections], "requireUse": require_use},
        )
        if result.get("registered") is not True:
            raise HTTPException(503, "模型依赖登记未确认,不能保存或发布。")
    if not references:
        return
    catalog = await request.state.platform_workflow["model_call"]("catalog", {})
    visible = {item["id"] for item in catalog["connections"]}
    if not references.issubset(visible):
        raise HTTPException(404, "模型连接不存在、已禁用或当前身份不可见。请重新选择连接。")


async def ensure_inventory(request):
    """每个实例首次消费模型目录前清点旧图; 未确认完整台账时删除侧保持拒绝."""
    settings = request.state.platform_workflow["model_settings"]
    key = (settings.client_id, settings.tenant_id)
    state = getattr(request.app.state, "platform_model_inventory", None)
    if state is None:
        state = request.app.state.platform_model_inventory = {"lock": asyncio.Lock(), "ready": set()}
    async with state["lock"]:
        if key not in state["ready"]:
            await synchronize_references(settings)
            state["ready"].add(key)


async def synchronize_references(settings):
    from sqlmodel import select

    from langflow.services.database.models.flow.model import Flow
    from langflow.services.database.models.flow_version.model import FlowVersion
    from langflow.services.database.models.workflow_publication import WorkflowPublication
    from langflow.services.deps import session_scope

    async def register(references, **phase):
        result = await call("sync-references", {"references": references, **phase}, settings)
        if result.get("registered") is not True:
            raise HTTPException(503, "工作流历史模型依赖未确认,不能完成清点。")

    await register([], begin=True)
    pending = []
    # 三类保留数据都属于当前独立租户实例。分页只传引用,不将历史图送入平台库。
    async with session_scope() as session:
        for model in (Flow, FlowVersion, WorkflowPublication):
            cursor = None
            while True:
                query = select(model.id, model.data).order_by(model.id).limit(100)
                if cursor is not None:
                    query = query.where(model.id > cursor)
                rows = (await session.exec(query)).all()
                if not rows:
                    break
                for identifier, data in rows:
                    cursor = identifier
                    if data:
                        digest = hashlib.sha256(
                            json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
                        ).hexdigest()
                        for item in model_selections(data):
                            pending.append({"graphDigest": digest, **item})
                            if len(pending) == MAX_DEPENDENCY_BATCH:
                                await register(pending)
                                pending = []
        if pending:
            await register(pending)
    await register([], complete=True)
