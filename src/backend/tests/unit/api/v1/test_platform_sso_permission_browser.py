"""Opt-in loopback browser fixture; never changes the development IAM or database.

Run WORKFLOW_B2_BROWSER_LAB=1 with pytest -s; an existing Vite at 7861 serves assets.
The platform status boundary is replaced by the existing editor fixture; all
Langflow routes, owner queries, response capabilities and persistence are real.
"""

import asyncio
import os

import pytest
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from tests.unit.api.v1.test_platform_sso_editing import (
    GRAPH,
    builtin,
    create,
    editor,  # noqa: F401 - imported pytest fixture
    isolated_auth_configuration,  # noqa: F401 - imported pytest fixture
)

PUBLIC = "http://127.0.0.1:17861"
HOST = "http://127.0.0.1:17860"


@pytest.fixture(autouse=True)
def browser_catalog(monkeypatch):
    """Use one real builtin template; provider discovery is outside this permission lab."""
    import inspect
    import sys

    from langflow.interface import components as langflow_components
    from lfx.components.processing.parser import ParserComponent
    from lfx.custom.utils import build_custom_component_template
    from lfx.interface import components

    code = inspect.getsource(sys.modules[ParserComponent.__module__])
    template, _ = build_custom_component_template(ParserComponent(_code=code))
    registry = {"processing": {"ParserComponent": template}}
    monkeypatch.setattr(components.component_cache, "all_types_dict", registry)
    monkeypatch.setattr(components.component_cache, "all_types_ready", True)
    for name in (
        "type_to_current_hash",
        "all_known_hashes",
        "code_by_hash",
        "type_to_code",
        "component_identity_index",
    ):
        monkeypatch.setattr(components.component_cache, name, getattr(components.component_cache, name))
    components._build_code_hash_lookups(components.component_cache)

    async def limited_types(*_args, **_kwargs):
        return registry

    monkeypatch.setattr(components, "get_and_cache_all_types_dict", limited_types)
    monkeypatch.setattr(langflow_components, "get_and_cache_all_types_dict", limited_types)


@pytest.mark.skipif(os.getenv("WORKFLOW_B2_BROWSER_LAB") != "1", reason="Manual loopback browser lab only")
@pytest.mark.timeout(1300)
@pytest.mark.parametrize("editor", [{"public_url": PUBLIC, "host_origin": HOST}], indirect=True)
async def test_permission_browser_lab(editor):  # noqa: F811 - pytest fixture injection
    import httpx
    import uvicorn

    client, _, live = editor
    flow = await create(client)
    node = await builtin(client, "ParserComponent")
    node["template"]["pattern"]["value"] = "persisted-before-revocation: {text}"
    graph = {
        **GRAPH,
        "nodes": [
            {
                "id": "ParserComponent-permission",
                "type": "genericNode",
                "position": {"x": 450, "y": 170},
                "data": {"id": "ParserComponent-permission", "type": "ParserComponent", "node": node, "showNode": True},
            }
        ],
    }
    seeded = await client.patch(
        f"/api/v1/flows/{flow['id']}",
        json={"name": "B2 permission fixture", "data": graph},
        headers={"If-Match": flow["version_token"]},
    )
    assert seeded.status_code == 200, seeded.text[:200]
    cookie = client.cookies.get("platform_sso")
    live["actions"] = ["read"]
    modes = {
        "read": ["read"],
        "create": ["read", "create"],
        "update": ["read", "update"],
        "editor": ["read", "create", "update"],
    }
    state = {"mode": "read", "armed": False, "audit": []}
    finished = asyncio.Event()

    async def control(request: Request):
        if request.method == "POST":
            if request.headers.get("origin") != HOST:
                return JSONResponse({"error": "origin"}, 403)
            action = request.path_params["action"]
            if action in modes:
                live["actions"], live["active"] = modes[action], True
                state.update(mode=action, armed=False)
            elif action == "arm":
                state["armed"] = True
            elif action == "finish":
                finished.set()
            else:
                return JSONResponse({"error": "action"}, 400)
            response = JSONResponse({"mode": state["mode"], "armed": state["armed"]})
            response.set_cookie("platform_sso", cookie, httponly=True, samesite="lax", path="/")
            return response
        if request.url.path == "/lab/result":
            persisted = (await client.get(f"/api/v1/flows/{flow['id']}")).json()
            return JSONResponse({**state, "flow_id": flow["id"], "saved_graph": persisted["data"]})
        return HTMLResponse(f"""<!doctype html><meta charset="utf-8"><title>B2 权限隔离验收</title>
<h2>B2 权限隔离验收</h2><p>临时 SQLite / 替换平台状态边界 / 真实 Langflow 页面和 API</p>
<button data-mode="read">只读</button><button data-mode="create">仅创建</button>
<button data-mode="update">仅更新</button><button data-mode="editor">可创建和更新</button>
<button id="arm">下次保存时撤销更新权限</button><p id="state">请选择能力</p>
<iframe id="flow" title="B2 权限测试编辑器" style="width:100%;height:780px;border:1px solid #bbb"></iframe>
<script>
async function change(mode, reload) {{
 const result = await fetch('/lab/control/'+mode, {{method:'POST'}});
 if (!result.ok) throw new Error('control refused');
 document.getElementById('state').textContent=JSON.stringify(await result.json());
 if (reload) document.getElementById('flow').src='{PUBLIC}/flows';
}}
document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>change(button.dataset.mode,true));
document.getElementById('arm').onclick=()=>change('arm',false);
</script>""")

    app = client._transport.app
    # Route only within this ephemeral fixture, before the static SPA catch-all.
    import inspect

    middleware = inspect.getclosurevars(app).nonlocals["middleware"]
    real_app = middleware.app
    real_app.router.routes[:0] = [
        Route("/lab", control),
        Route("/lab/result", control),
        Route("/lab/control/{action}", control, methods=["POST"]),
    ]

    async def observed(scope, receive, send):
        path = scope.get("path", "")
        if state["armed"] and scope.get("method") == "PATCH" and path.startswith("/api/v1/flows/"):
            state["armed"] = False
            live["actions"] = ["read"]

        async def record(message):
            if message["type"] == "http.response.start" and path.startswith("/api/"):
                state["audit"].append({"method": scope["method"], "path": path, "status": message["status"]})
            await send(message)

        await app(scope, receive, record)

    server = uvicorn.Server(
        uvicorn.Config(observed, host="127.0.0.1", port=17860, lifespan="off", access_log=False, log_level="warning")
    )
    # 仅复用现有 Vite 的源码资源, API 始终指向临时 SQLite。避免再启一套前端耗尽内存。
    proxy_client = httpx.AsyncClient(trust_env=False, timeout=30)

    async def proxy(scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1000})
            return
        incoming = Request(scope, receive)
        target = HOST if scope["path"].startswith("/api/") else "http://localhost:7861"
        forwarded = await proxy_client.request(
            incoming.method,
            f"{target}{incoming.url.path}" + (f"?{incoming.url.query}" if incoming.url.query else ""),
            content=await incoming.body(),
            headers={
                **{key: value for key, value in incoming.headers.items() if key != "host"},
                "accept-encoding": "identity",
            },
        )
        headers = [
            (key, value)
            for key, value in forwarded.headers.raw
            if key.lower() not in {b"content-length", b"content-encoding", b"transfer-encoding", b"connection"}
        ]
        await send({"type": "http.response.start", "status": forwarded.status_code, "headers": headers})
        await send({"type": "http.response.body", "body": forwarded.content})

    frontend = uvicorn.Server(
        uvicorn.Config(proxy, host="127.0.0.1", port=17861, lifespan="off", access_log=False, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    frontend_serving = asyncio.create_task(frontend.serve())
    try:
        for _ in range(100):
            if server.started and frontend.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        assert frontend.started
        print(f"B2 permission lab ready: {HOST}/lab", flush=True)  # noqa: T201 - manual lab readiness
        await asyncio.wait_for(finished.wait(), timeout=1200)
    finally:
        server.should_exit = True
        frontend.should_exit = True
        await serving
        await frontend_serving
        await proxy_client.aclose()
