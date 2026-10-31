"""Verify production assets, SSO and conflicting saves over local HTTPS."""

# 验收期望值保留字面量, 便于独立核对每个协议结果。
# ruff: noqa: FBT003, PLR2004

import copy
import json
import re
import socket
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx


def main():
    directory = Path(sys.argv[1]).resolve()
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    if config["database"] != "workflow_sso_a2_e2e" or config["postgresPort"] != 55433:
        message = "Dedicated lab required"
        raise RuntimeError(message)
    host = "https://ai.workflow.localhost:8443"
    origins = {
        "a1": "https://flow-a.workflow.localhost:8443",
        "a2": "https://flow-a.workflow.localhost:8443",
        "b1": "https://flow-b.workflow.localhost:8443",
    }
    tls = ssl.create_default_context(cafile=str(directory / "ca.pem"))
    original_resolve = socket.getaddrinfo

    def resolve(name, *args, **kwargs):
        if name == "localhost" or name.endswith(".workflow.localhost"):
            name = "127.0.0.1"
        return original_resolve(name, *args, **kwargs)

    socket.getaddrinfo = resolve
    checks, clients, pairs, fixtures = [], {}, {}, {}

    def check(name, actual, expected):
        passed = actual == expected
        checks.append({"name": name, "passed": passed})
        if not passed:
            message = f"{name}: unexpected result {actual!r}, expected {expected!r}"
            raise AssertionError(message)

    control = httpx.Client(base_url=host, verify=tls, trust_env=False, timeout=90)

    def action(path, actor="a1", **body):
        response = control.post(
            "/fixture/" + path,
            headers={"Origin": host, "X-Acceptance-Key": config["capability"]},
            json={"actor": actor, **body},
        )
        if response.status_code != 200:
            message = f"fixture {path} returned {response.status_code}"
            raise AssertionError(message)
        return response.json()

    def login(actor, pair=None):
        pair = pair or action("pair", actor)
        client = httpx.Client(
            base_url=origins[actor], verify=tls, trust_env=False, headers={"Origin": origins[actor]}, timeout=90
        )
        clients.setdefault(actor, []).append(client)
        start = client.get("/api/v1/platform-sso/start", params={"session": pair["sessionId"]})
        check(actor + " start HTTPS", start.status_code, 200)
        state = re.search(r'"state": "([A-Za-z0-9_-]{43})"', start.text)[1]
        issued = control.post(
            "/backend/api/workflow/sso/tickets",
            headers={"Authorization": "Bearer " + pair["accessToken"]},
            json={"clientId": "a2-tenant-b" if actor == "b1" else "a2-tenant-a", "state": state},
        )
        check(actor + " issue real ticket", issued.status_code, 200)
        ticket = issued.json()["data"]
        callback = client.get(ticket["callbackUrl"], params={"code": ticket["code"], "state": state})
        check(actor + " HTTPS callback", callback.status_code, 303)
        cookie = callback.headers.get("set-cookie", "").lower()
        for attr in ["secure", "httponly", "samesite=lax", "path=/"]:
            check(actor + " cookie " + attr, attr in cookie, True)
        check(actor + " cookie host-only", "domain=" in cookie, False)
        check(actor + " callback clears ticket URL", "code=" in callback.headers["location"], False)
        ready = client.get(callback.headers["location"])
        check(actor + " ready", '"status": "ready"' in ready.text, True)
        session = client.get("/api/v1/session")
        check(actor + " session", session.status_code, 200)
        check(actor + " session no-store", "no-store" in session.headers.get("cache-control", ""), True)
        pairs[actor] = pair
        return client

    error = None
    try:
        for path in ["/", "/tenant-b/", "/.codex/b2-static.html", "/tenant-b/.codex/b2-static.html"]:
            response = control.get(path)
            check("Vue static " + path, response.status_code, 200)
            for asset in re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', response.text):
                check("Vue asset " + asset.split("/")[-1], control.get(asset).status_code, 200)
        for actor in origins:
            action("permissions", actor, mode="editor")
            client = login(actor)
            page = client.get("/")
            check(actor + " Langflow static entry", page.status_code, 200)
            check(actor + " static frame ancestors", host in page.headers.get("content-security-policy", ""), True)
            assets = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', page.text)
            for asset in assets:
                check(actor + " Langflow asset " + asset.split("/")[-1], client.get(asset).status_code, 200)
            if actor == "a1":
                parser = client.get("/api/v1/all").json()["processing"]["ParserComponent"]
            node = copy.deepcopy(parser)
            node["template"]["pattern"]["value"] = f"B2 HTTPS {actor} baseline: {{text}}"
            graph = {
                "nodes": [
                    {
                        "id": "ParserComponent-static",
                        "type": "genericNode",
                        "position": {"x": 450, "y": 170},
                        "data": {
                            "id": "ParserComponent-static",
                            "type": "ParserComponent",
                            "node": node,
                            "showNode": True,
                        },
                    }
                ],
                "edges": [],
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            }
            created = client.post("/api/v1/flows/", json={"name": "B2 HTTPS " + actor, "data": graph})
            check(actor + " create fixture", created.status_code, 201)
            fixtures[actor] = created.json()
            reused = client.get("/api/v1/platform-sso/start", params={"session": pairs[actor]["sessionId"]})
            check(
                actor + " refresh reuses cookie without ticket",
                '"status": "ready"' in reused.text and "needs-ticket" not in reused.text,
                True,
            )
            check(actor + " mapped ordinary user", client.get("/api/v1/users/whoami").json()["is_superuser"], False)
        check("distinct mapped owners", len({f["user_id"] for f in fixtures.values()}), 3)
        for actor in origins:
            client = clients[actor][-1]
            for other in origins:
                expected = 200 if actor == other else 404
                check(
                    actor + " reads " + other,
                    client.get("/api/v1/flows/" + fixtures[other]["id"]).status_code,
                    expected,
                )
            for path in ["/api/v2/files/", "/api/v1/mcp/servers"]:
                check(actor + " unopened " + path, client.get(path).status_code, 403)
            check(
                actor + " execution closed",
                client.post("/api/v1/run/" + fixtures[actor]["id"], json={}).status_code,
                403,
            )
        a = clients["a1"][-1]
        flow = fixtures["a1"]
        graph_a = copy.deepcopy(flow["data"])
        graph_a["nodes"][0]["data"]["node"]["template"]["pattern"]["value"] = "B2 HTTPS tab A saved: {text}"
        saved = a.patch(
            "/api/v1/flows/" + flow["id"], headers={"If-Match": flow["version_token"]}, json={"data": graph_a}
        )
        check("first tab saves graph", saved.status_code, 200)
        tab_b = httpx.Client(
            base_url=origins["a1"],
            verify=tls,
            trust_env=False,
            cookies=a.cookies,
            headers={"Origin": origins["a1"]},
            timeout=90,
        )
        clients["a1"].append(tab_b)
        graph_b = copy.deepcopy(flow["data"])
        graph_b["nodes"][0]["data"]["node"]["template"]["pattern"]["value"] = "B2 HTTPS tab B retained draft: {text}"
        conflict = tab_b.patch(
            "/api/v1/flows/" + flow["id"], headers={"If-Match": flow["version_token"]}, json={"data": graph_b}
        )
        check("stale second tab rejected", conflict.status_code, 409)
        check("structured conflict code", conflict.json()["detail"]["code"], "flow_version_conflict")
        check(
            "conflict leaves database graph and version unchanged",
            a.get("/api/v1/flows/" + flow["id"]).json(),
            saved.json(),
        )
        for suffix, payload in [("/fork", {}), ("/overwrite", {"data": graph_b})]:
            check(
                "conflict escape blocked " + suffix,
                a.post("/api/v1/flows/" + flow["id"] + suffix, json=payload).status_code,
                403,
            )
        (directory / "browser-draft-reference.json").write_text(
            json.dumps(
                {
                    "flowId": flow["id"],
                    "tabASaved": graph_a["nodes"][0]["data"]["node"]["template"]["pattern"]["value"],
                    "tabBDraft": graph_b["nodes"][0]["data"]["node"]["template"]["pattern"]["value"],
                }
            ),
            encoding="utf-8",
        )
        action("permissions", "a2", mode="read")
        check(
            "read-only role change invalidates prior cookie", clients["a2"][-1].get("/api/v1/session").status_code, 401
        )
        readonly = login("a2")
        read = readonly.get("/api/v1/flows/" + fixtures["a2"]["id"])
        check("fresh read-only action set", read.headers["x-workflow-actions"], "read")
        check(
            "read-only update denied",
            readonly.patch("/api/v1/flows/" + fixtures["a2"]["id"], json={"name": "must not save"}).status_code,
            403,
        )
        check(
            "read-only create denied",
            readonly.post("/api/v1/flows/", json={"name": "must not create"}).status_code,
            403,
        )
        check(
            "read-only refusal leaves saved graph unchanged",
            readonly.get("/api/v1/flows/" + fixtures["a2"]["id"]).json(),
            fixtures["a2"],
        )
        logout = control.post(
            "/backend/api/iam/auth/logout", headers={"Authorization": "Bearer " + pairs["a1"]["accessToken"]}
        )
        check("platform logout", logout.status_code, 201)
        check("logout rejects old flow cookie", a.get("/api/v1/session").status_code, 401)
        check("logout rejects same-session second tab", tab_b.get("/api/v1/session").status_code, 401)
        check("logout does not revoke another user", readonly.get("/api/v1/session").status_code, 200)
        fresh = login("a1")
        restored = fresh.get("/api/v1/flows/" + flow["id"]).json()
        check("relogin retains stable owner", restored["user_id"], flow["user_id"])
        check("relogin restores saved graph", restored["data"], graph_a)
        b = clients["b1"][-1]
        alien = httpx.Client(base_url=origins["b1"], verify=tls, trust_env=False, timeout=90)
        try:
            for cookie in fresh.cookies.jar:
                alien.cookies.set(cookie.name, cookie.value, domain="flow-b.workflow.localhost", path=cookie.path)
            check("tenant B rejects copied tenant A cookie", alien.get("/api/v1/session").status_code, 401)
        finally:
            alien.close()
        old_a_session = fresh.get("/api/v1/platform-sso/start", params={"session": pairs["b1"]["sessionId"]})
        check(
            "switch tenant starts new handshake instead of reusing prior user",
            '"status": "needs-ticket"' in old_a_session.text,
            True,
        )
        check("switch tenant clears previous instance cookie", fresh.get("/api/v1/session").status_code, 401)
        wrong_ticket = control.post(
            "/backend/api/workflow/sso/tickets",
            headers={"Authorization": "Bearer " + pairs["b1"]["accessToken"]},
            json={"clientId": "a2-tenant-a", "state": "s" * 43},
        )
        check("wrong tenant fixed instance fails closed", wrong_ticket.status_code, 403)
        check("proper tenant B session stays valid", b.get("/api/v1/session").status_code, 200)
        csrf = b.post("/api/v1/flows/", headers={"Origin": "https://evil.example"}, json={"name": "csrf"})
        check("HTTPS foreign Origin write denied", csrf.status_code, 403)
        check(
            "control wrong origin denied",
            control.post(
                "/fixture/pair",
                headers={"Origin": "https://evil.example", "X-Acceptance-Key": config["capability"]},
                json={"actor": "a1"},
            ).status_code,
            403,
        )
        check(
            "control missing capability denied",
            control.post("/fixture/pair", headers={"Origin": host}, json={"actor": "a1"}).status_code,
            403,
        )
    except (AssertionError, httpx.HTTPError, KeyError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        for group in clients.values():
            for client in group:
                client.close()
        control.close()
        socket.getaddrinfo = original_resolve
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
            "passed": sum(c["passed"] for c in checks),
            "total": len(checks),
            "error": error,
            "fixtures": {name: {"id": f["id"], "user_id": f["user_id"]} for name, f in fixtures.items()},
            "browser": "Separate manual browser validation required",
        }
        (directory / "static-https-http-evidence.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if error:
        raise SystemExit(error)
    print(f"Static HTTPS / SSO / conflicts: {len(checks)} checks passed")


if __name__ == "__main__":
    main()
