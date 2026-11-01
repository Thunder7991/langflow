"""Execute a bounded text pair using server-created components, never posted source."""

import json
from importlib import import_module
from inspect import getsource

from fastapi import HTTPException

from langflow.services.auth.platform_workflows import validate_graph

MAX_TEXT_BYTES = 64 * 1024
MAX_NODE_ID_LENGTH = 100
TIMEOUT_SECONDS = 10
ACTIVE_USERS: set[str] = set()


def text_pair(data: dict) -> tuple[str, str, str]:
    if not isinstance(data, dict) or len(json.dumps(data).encode()) > 256 * 1024:
        raise HTTPException(422, "Dry run graph is missing or too large")
    # 首轮只允许一条文本连线; 不展开分组、代理、循环、模型、文件或自定义代码。
    nodes, edges = data.get("nodes"), data.get("edges")
    if not isinstance(nodes, list) or len(nodes) != 2 or not isinstance(edges, list) or len(edges) != 1:  # noqa: PLR2004 - one input and one output
        raise HTTPException(422, "Dry run requires exactly TextInput -> TextOutput")
    validate_graph(data)
    reviewed = {"TextInput": "input_output.text", "TextOutput": "input_output.text_output"}
    found = {}
    for node in nodes:
        body = node.get("data", {})
        kind = body.get("type")
        definition = body.get("node", {})
        template = definition.get("template", {})
        if kind not in reviewed or kind in found or node.get("type") != "genericNode" or definition.get("tool_mode"):
            raise HTTPException(403, "Only reviewed plain text nodes can run")
        source = template.get("code", {}).get("value")
        if (
            not isinstance(source, str)
            or source.strip() != getsource(import_module(f"lfx.components.{reviewed[kind]}")).strip()
        ):
            raise HTTPException(403, "Dry run component source does not match the server builtin")
        if template.get("use_global_variable", {}).get("value"):
            raise HTTPException(403, "Global variables are not enabled for text dry runs")
        value = template.get("input_value", {}).get("value", "")
        if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_TEXT_BYTES:
            raise HTTPException(422, "Text input must be a string of at most 64 KiB")
        node_id = node.get("id")
        if (
            not isinstance(node_id, str)
            or not node_id
            or len(node_id) > MAX_NODE_ID_LENGTH
            or body.get("id") != node_id
        ):
            raise HTTPException(422, "Invalid text node identity")
        found[kind] = (node_id, value)
    source_id, value = found["TextInput"]
    target_id, _ = found["TextOutput"]
    edge = edges[0]
    if source_id == target_id or edge.get("source") != source_id or edge.get("target") != target_id:
        raise HTTPException(422, "Invalid text graph connection")
    try:
        source_handle = json.loads(edge["sourceHandle"])
        target_handle = json.loads(edge["targetHandle"])
        if (
            source_handle["id"] != source_id
            or source_handle["name"] != "text"
            or target_handle["id"] != target_id
            or target_handle["fieldName"] != "input_value"
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, "Invalid text graph handles") from exc
    return source_id, target_id, value


async def execute_text_pair(
    source_id: str, target_id: str, text: str, *, flow_id: str, user_id: str, run_id: str
) -> str:
    from lfx.components.input_output.text import TextInputComponent
    from lfx.components.input_output.text_output import TextOutputComponent
    from lfx.graph import Graph

    start = TextInputComponent(_id=source_id)
    start.set(input_value=text, use_global_variable=False)
    end = TextOutputComponent(_id=target_id)
    end.set(input_value=start.text_response)
    # 图由固定服务器类创建; 保存图中的源码、输出方法、冻结缓存均不会传入执行器。
    graph = Graph(start, end, flow_id=flow_id, user_id=user_id)
    graph.set_run_id(run_id)
    output = None
    async for result in graph.async_start(max_iterations=2, fallback_to_env_vars=False):
        if hasattr(result, "valid") and not result.valid:
            raise RuntimeError
        if hasattr(result, "vertex") and result.vertex.id == target_id:
            output = result.result_dict.results["text"].text
    if not isinstance(output, str) or len(output.encode("utf-8")) > MAX_TEXT_BYTES:
        raise RuntimeError
    return output


def model_chain(data: dict) -> tuple[str, str, str, str, dict]:
    from langflow.services.auth import platform_models

    if len(json.dumps(data).encode()) > 256 * 1024 or len(data.get("edges", [])) != 2:  # noqa: PLR2004
        raise HTTPException(422, "Model dry run requires TextInput -> PlatformModel -> TextOutput")
    validate_graph(data)
    model_nodes = [node for node in data["nodes"] if node.get("data", {}).get("type") == "PlatformModel"]
    if len(model_nodes) != 1:
        raise HTTPException(403, "Only one platform model node is allowed")
    model = model_nodes[0]
    model_id = model.get("id")
    body = model["data"]
    definition = body["node"]
    fields = definition["template"]
    if (
        not isinstance(model_id, str)
        or not model_id
        or len(model_id) > MAX_NODE_ID_LENGTH
        or body.get("id") != model_id
        or model.get("type") != "genericNode"
        or definition.get("tool_mode")
        or not platform_models.is_source(fields.get("code", {}).get("value"))
    ):
        raise HTTPException(403, "Invalid platform model identity or source")
    config = platform_models.configuration(fields)
    inbound = [edge for edge in data["edges"] if edge.get("target") == model_id]
    outbound = [edge for edge in data["edges"] if edge.get("source") == model_id]
    if len(inbound) != 1 or len(outbound) != 1 or inbound[0] is outbound[0]:
        raise HTTPException(422, "Invalid platform model connection")
    try:
        target_handle = json.loads(inbound[0]["targetHandle"])
        source_handle = json.loads(outbound[0]["sourceHandle"])
        if (
            target_handle["id"] != model_id
            or target_handle["fieldName"] != "input_value"
            or source_handle["id"] != model_id
            or source_handle["name"] != "text"
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, "Invalid platform model handles") from exc
    # 复用已验证的文本首尾检查; 执行图仍由服务器类重建, 不使用保存的输出方法。
    source, target, text = text_pair(
        {
            "nodes": [node for node in data["nodes"] if node is not model],
            "edges": [
                {
                    "source": inbound[0]["source"],
                    "target": outbound[0]["target"],
                    "sourceHandle": inbound[0]["sourceHandle"],
                    "targetHandle": outbound[0]["targetHandle"],
                }
            ],
        }
    )
    if model_id in {source, target}:
        raise HTTPException(422, "Duplicate node identity")
    return source, target, text, model_id, config


async def execute_model_chain(source_id, target_id, text, model_id, configuration, *, invoke, flow_id, user_id, run_id):
    from lfx.components.input_output.text import TextInputComponent
    from lfx.components.input_output.text_output import TextOutputComponent
    from lfx.graph import Graph

    from langflow.services.auth.platform_model_component import PlatformModelComponent
    from langflow.services.auth.platform_models import runtime

    platform_failure = None

    async def delegated(**parameters):
        nonlocal platform_failure
        try:
            return await invoke(**parameters)
        except HTTPException as exc:
            platform_failure = exc
            raise

    start = TextInputComponent(_id=source_id)
    start.set(input_value=text, use_global_variable=False)
    model = PlatformModelComponent(_id=model_id)
    model.set(input_value=start.text_response, **configuration)
    end = TextOutputComponent(_id=target_id)
    end.set(input_value=model.text_response)
    graph = Graph(start, end, flow_id=flow_id, user_id=user_id)
    graph.set_run_id(run_id)
    token = runtime.set(delegated)
    output = None
    try:
        async for result in graph.async_start(max_iterations=3, fallback_to_env_vars=False):
            if platform_failure:
                raise platform_failure
            if hasattr(result, "valid") and not result.valid:
                raise RuntimeError
            if hasattr(result, "vertex") and result.vertex.id == target_id:
                output = result.result_dict.results["text"].text
        if not isinstance(output, str) or len(output.encode("utf-8")) > MAX_TEXT_BYTES:
            raise RuntimeError
    except Exception:
        if platform_failure:
            raise platform_failure from None
        raise
    else:
        return output
    finally:
        runtime.reset(token)
