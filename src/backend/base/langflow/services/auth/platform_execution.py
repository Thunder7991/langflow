"""Saved graph runner for reviewed platform nodes; never executes caller source or cached outputs."""

import asyncio
import json
import time
from contextvars import ContextVar
from copy import deepcopy
from importlib import import_module
from inspect import getsource

from fastapi import HTTPException

from langflow.services.auth.platform_dry_run import MAX_TEXT_BYTES, parse_handle

# 原生供应商、MCP、任意代码与文件解析仍需各自的凭据/资源适配,不能因 run 权限绕过。
REVIEWED = {
    "TextInput": ("input_output.text", "TextInputComponent"),
    "TextOutput": ("input_output.text_output", "TextOutputComponent"),
    "ChatInput": ("input_output.chat", "ChatInput"),
    "ChatOutput": ("input_output.chat_output", "ChatOutput"),
    "Prompt Template": ("models_and_agents.prompt", "PromptComponent"),
    "ParserComponent": ("processing.parser", "ParserComponent"),
}
MAX_NODES = 64
MAX_EDGES = 128
MAX_NODE_ID = 100

ACTIVE: dict[tuple[str, str, str], asyncio.Task] = {}
ACTIVE_MODELS: set[tuple[str, str, str]] = set()
# 仅平台 Playground 订阅安全事件; 普通运行接口保持原有 JSON 响应。
EVENT_SINK: ContextVar = ContextVar("platform_execution_events", default=None)
DRAFT_RUNS: dict[tuple[str, str, str], float] = {}
MAX_DRAFT_RUN_RECORDS = 4096


def emit(event_type, **data):
    sink = EVENT_SINK.get()
    if sink is not None:
        sink({"type": event_type, **data})


def claim_draft_run(key):
    now = time.monotonic()
    for previous, expires in list(DRAFT_RUNS.items()):
        if expires <= now:
            del DRAFT_RUNS[previous]
    if key in DRAFT_RUNS:
        raise HTTPException(409, "该草稿运行 ID 已受理, 请勿重复执行。")
    # 草稿防重有界且不驱逐未过期记录; 发布运行仍使用数据库跨 worker 防重。
    if len(DRAFT_RUNS) >= MAX_DRAFT_RUN_RECORDS:
        raise HTTPException(429, "草稿调试受理记录已满, 请稍后再试。")
    DRAFT_RUNS[key] = now + 600


def prepared_graph(data, *, input_text=None):
    from lfx.custom.utils import build_custom_component_template

    from langflow.services.auth import platform_models
    from langflow.services.auth.platform_workflows import validate_graph

    if not isinstance(data, dict) or len(json.dumps(data).encode()) > 256 * 1024:
        raise HTTPException(422, "Saved graph is missing or exceeds 256 KiB")
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    if (
        not isinstance(nodes, list)
        or not isinstance(edges, list)
        or not 1 <= len(nodes) <= MAX_NODES
        or len(edges) > MAX_EDGES
    ):
        raise HTTPException(422, "A run requires 1-64 nodes and at most 128 edges")
    validate_graph(data)
    fresh = []
    outputs, inputs, kinds = {}, {}, {}
    for raw in nodes:
        if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict):
            raise HTTPException(422, "Invalid graph node")
        body = raw.get("data", {})
        kind = body.get("type")
        saved = body.get("node", {})
        if not isinstance(saved, dict) or not isinstance(saved.get("template"), dict):
            raise HTTPException(422, "Invalid node template")
        fields = saved.get("template", {})
        if raw.get("type") != "genericNode" or saved.get("tool_mode"):
            raise HTTPException(403, "Grouped and tool-mode nodes require separate runtime support")
        if kind == "PlatformModel":
            canonical = platform_models.template(legacy="selection_mode" not in fields)
            if not platform_models.is_source(fields.get("code", {}).get("value")):
                raise HTTPException(403, "Invalid platform model source")
            platform_models.configuration(fields)
        elif kind in REVIEWED:
            module, name = REVIEWED[kind]
            imported = import_module(f"lfx.components.{module}")
            code = getsource(imported)
            if fields.get("code", {}).get("value", "").strip() != code.strip():
                raise HTTPException(403, "Node source differs from the server component")
            canonical, _ = build_custom_component_template(getattr(imported, name)(_code=code))
        else:
            raise HTTPException(403, "This node does not yet have a platform runtime adapter")
        if kind == "Prompt Template":
            from lfx.base.prompts.api_utils import process_prompt_template

            try:
                process_prompt_template(
                    fields.get("template", {}).get("value", ""),
                    "template",
                    canonical.setdefault("custom_fields", {}),
                    canonical["template"],
                    is_mustache=fields.get("use_double_brackets", {}).get("value") is True,
                )
            except (ValueError, TypeError, AttributeError) as exc:
                raise HTTPException(422, "Invalid prompt template") from exc
        node_id = raw.get("id")
        if not isinstance(node_id, str) or not node_id or len(node_id) > MAX_NODE_ID or node_id in outputs:
            raise HTTPException(422, "Invalid or duplicate node ID")
        # 输入只携带表单值;源码、输出方法、冻结缓存及类元数据一律由服务器生成。
        for field, spec in fields.items():
            if field == "code" or not isinstance(spec, dict):
                continue
            if field == "use_global_variable" and spec.get("value"):
                raise HTTPException(403, "Global variables and persisted chat history are not enabled")
            if field in canonical["template"] and isinstance(canonical["template"][field], dict):
                if canonical["template"][field].get("type") == "file" and (spec.get("value") or spec.get("file_path")):
                    raise HTTPException(403, "File parsing requires a separate runtime adapter")
                canonical["template"][field]["value"] = deepcopy(spec.get("value"))
        if "should_store_message" in canonical["template"]:
            canonical["template"]["should_store_message"]["value"] = False
        if kind == "PlatformModel" and "selection_mode" in canonical["template"]:
            # 原生参数处理会忽略隐藏字段;根据已验证的选择模式重新生成可见性,不能信任画布 show。
            canonical["template"]["connection_model_id"]["show"] = (
                canonical["template"]["selection_mode"]["value"] == "explicit"
            )
        if kind in {"TextInput", "ChatInput"}:
            text = canonical["template"]["input_value"].get("value", "")
            if not isinstance(text, str) or len(text.encode()) > MAX_TEXT_BYTES:
                raise HTTPException(422, "Text input must be a string of at most 64 KiB")
        if input_text is not None and kind in {"TextInput", "ChatInput"}:
            canonical["template"]["input_value"]["value"] = input_text
        canonical["tool_mode"] = False
        kinds[node_id] = kind
        outputs[node_id] = {output["name"]: output for output in canonical.get("outputs", [])}
        inputs[node_id] = {
            name: spec for name, spec in canonical["template"].items() if isinstance(spec, dict) and name != "code"
        }
        fresh.append(
            {
                "id": node_id,
                "type": "genericNode",
                "position": {"x": 0, "y": 0},
                "data": {"id": node_id, "type": kind, "node": canonical},
            }
        )
    indegree = dict.fromkeys(outputs, 0)
    children = {node_id: [] for node_id in outputs}
    clean_edges = []
    for edge in edges:
        try:
            source, target = edge["source"], edge["target"]
            source_handle, target_handle = parse_handle(edge["sourceHandle"]), parse_handle(edge["targetHandle"])
            if source_handle["id"] != source or target_handle["id"] != target:
                raise ValueError
            if source_handle["name"] not in outputs[source] or target_handle["fieldName"] not in inputs[target]:
                raise ValueError
            # 只接受连接的端口名;运行类型来自服务器模板,兼容旧图缺失类型元数据的情况。
            output = outputs[source][source_handle["name"]]
            field = inputs[target][target_handle["fieldName"]]
            source_handle = {
                "id": source,
                "name": output["name"],
                "dataType": kinds[source],
                "output_types": output["types"],
            }
            target_handle = {
                "id": target,
                "fieldName": target_handle["fieldName"],
                "type": field.get("type"),
                "inputTypes": field.get("input_types"),
            }
            children[source].append(target)
            indegree[target] += 1
            # LFX 使用 data 中的结构化 handles; 丢弃客户端其余边元数据。
            clean_edges.append(
                {
                    "id": f"edge-{len(clean_edges)}",
                    "source": source,
                    "target": target,
                    "sourceHandle": json.dumps(source_handle),
                    "targetHandle": json.dumps(target_handle),
                    "data": {"sourceHandle": source_handle, "targetHandle": target_handle},
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, "节点连线无效，请重新连接后保存并重试。") from exc  # noqa: RUF001
    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        source = queue.pop()
        visited += 1
        for target in children[source]:
            indegree[target] -= 1
            if not indegree[target]:
                queue.append(target)
    if visited != len(fresh):
        raise HTTPException(422, "Cyclic graphs require a separate iteration policy")
    return {"nodes": fresh, "edges": clean_edges}


async def execute(data, *, flow_id, user_id, run_id, invoke):
    from lfx.events.event_manager import EventManager
    from lfx.graph import Graph
    from lfx.schema.message import Message

    from langflow.services.auth.platform_models import runtime

    token = runtime.set(invoke)
    failure = None

    async def delegated(**parameters):
        nonlocal failure
        try:
            return await invoke(**parameters)
        except HTTPException as exc:
            failure = exc
            raise

    runtime.set(delegated)
    try:
        graph = Graph.from_payload(data, flow_id=flow_id, user_id=user_id, emit_extension_events=False)
        graph.set_run_id(run_id)
        results = []
        starts = {}

        def node_started(manager, event_type, data):  # noqa: ARG001 - EventManager requires these parameter names.
            node_id = data["id"]
            starts[node_id] = time.monotonic()
            emit("node_started", node_id=node_id)

        manager = EventManager(None)
        manager.register_event("on_build_start", "node_started", node_started)
        async for item in graph.async_start(
            max_iterations=len(data["nodes"]) + 1, fallback_to_env_vars=False, event_manager=manager
        ):
            if failure:
                raise failure
            if hasattr(item, "valid") and not item.valid:
                raise HTTPException(422, "Node execution failed; check node inputs and connections")
            if hasattr(item, "vertex"):
                # 进度仅包含节点标识与耗时, 中间文本及组件内部参数不提前交付。
                emit(
                    "node_finished",
                    node_id=item.vertex.id,
                    duration_ms=round((time.monotonic() - starts.get(item.vertex.id, time.monotonic())) * 1000),
                )
                for value in item.result_dict.results.values():
                    if isinstance(value, Message):
                        results.append({"node_id": item.vertex.id, "text": value.text or ""})  # noqa: PERF401 - filter typed node results
        if len(json.dumps(results).encode()) > 256 * 1024:
            raise HTTPException(422, "Run output exceeds 256 KiB")
    except Exception:
        # LFX 可能把组件异常包装成图执行错误; 保留平台会话、权限及防重错误的真实状态。
        if failure:
            raise failure from None
        raise
    else:
        return results
    finally:
        runtime.reset(token)
