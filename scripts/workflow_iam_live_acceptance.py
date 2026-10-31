"""B2 localhost integration: real IAM management, ticket exchange and live flow APIs.

Requires the explicitly started Nest test/e2e/workflow-iam-live-acceptance.ts.
Never prints credentials. Only fixture actors/flows are changed.
"""

import copy
import json
import os
import re
from contextlib import closing
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = Path(os.environ.get("WORKFLOW_IAM_LIVE_DIRECTORY", ROOT / "deep_agents/.tmp/workflow-iam-live"))
HOST = "http://localhost:5999"
FLOW = "http://localhost:7861"
API = "http://127.0.0.1:3000"
CONTROL = "http://127.0.0.1:17863"
MODES = {
    "read": ["read"],
    "create": ["read", "create"],
    "update": ["read", "update"],
    "editor": ["read", "create", "update"],
}


def main():
    key = json.loads((DIRECTORY / "control.json").read_text())["capability"]
    proof = []

    def check(name, actual, expected):
        if actual != expected:
            message = f"{name}: {actual!r} != {expected!r}"
            raise AssertionError(message)
        proof.append({"name": name, "actual": actual, "expected": expected})

    with httpx.Client(trust_env=False, timeout=60) as control:

        def action(path, body=None):
            r = control.post(CONTROL + path, json=body or {}, headers={"Origin": HOST, "X-Acceptance-Key": key})
            r.raise_for_status()
            return r.json()

        def login(mode):
            pair = action("/select", {"mode": mode})
            client = httpx.Client(base_url=FLOW, headers={"Origin": FLOW}, trust_env=False, timeout=60)
            start = client.get("/api/v1/platform-sso/start", params={"session": pair["sessionId"]})
            start.raise_for_status()
            state = re.search(r'"state": "([A-Za-z0-9_-]{43})"', start.text)[1]
            ticket = control.post(
                API + "/api/workflow/sso/tickets",
                headers={"Authorization": "Bearer " + pair["accessToken"]},
                json={"clientId": "langflow-local", "state": state},
            )
            ticket.raise_for_status()
            issued = ticket.json()["data"]
            callback = client.get(issued["callbackUrl"], params={"code": issued["code"], "state": state})
            check(f"{mode} real ticket callback", callback.status_code, 303)
            ready = client.get(callback.headers["location"])
            check(f"{mode} ready", '"status": "ready"' in ready.text, expected=True)
            return client, pair

        action("/prepare-resources")
        fixtures = {}
        for mode in MODES:
            client, _ = login(mode)
            with closing(client):
                if mode == "read":
                    catalog = client.get("/api/v1/all").json()
                    parser = catalog["processing"]["ParserComponent"]
                node = copy.deepcopy(parser)
                node["template"]["pattern"]["value"] = f"{mode} saved baseline: {{text}}"
                graph = {
                    "nodes": [
                        {
                            "id": "ParserComponent-live",
                            "type": "genericNode",
                            "position": {"x": 450, "y": 170},
                            "data": {
                                "id": "ParserComponent-live",
                                "type": "ParserComponent",
                                "node": node,
                                "showNode": True,
                            },
                        }
                    ],
                    "edges": [],
                    "viewport": {"x": 0, "y": 0, "zoom": 1},
                }
                created = client.post("/api/v1/flows/", json={"name": "B2 real IAM " + mode, "data": graph})
                check(mode + " fixture create", created.status_code, 201)
                fixtures[mode] = created.json()
        action("/begin-matrix")
        identities = {}
        clients = {}
        pairs = {}
        try:
            for mode, actions in MODES.items():
                client, pair = login(mode)
                clients[mode], pairs[mode] = client, pair
                own = fixtures[mode]
                read = client.get(f"/api/v1/flows/{own['id']}")
                check(mode + " own flow read", read.status_code, 200)
                check(mode + " live actions", read.headers["x-workflow-actions"].split(","), actions)
                identities[mode] = own["user_id"]
                check(mode + " mapped owner stable", read.json()["user_id"], own["user_id"])
                original = read.json()
                update = client.patch(
                    f"/api/v1/flows/{own['id']}",
                    headers={"If-Match": original["version_token"]},
                    json={"name": "B2 real IAM " + mode + " checked"},
                )
                check(mode + " own save", update.status_code, 200 if "update" in actions else 403)
                after = client.get(f"/api/v1/flows/{own['id']}").json()
                if "update" not in actions:
                    check(mode + " refused save unchanged", after, original)
                create = client.post(
                    "/api/v1/flows/", json={"name": "B2 IAM matrix " + mode, "data": {"nodes": [], "edges": []}}
                )
                check(mode + " create", create.status_code, 201 if "create" in actions else 403)
                other = fixtures["editor" if mode != "editor" else "read"]
                check(mode + " other user read", client.get(f"/api/v1/flows/{other['id']}").status_code, 404)
                # 更新权限缺失先返回动作拒绝, 有权限也不能越过真实 owner。
                foreign = client.patch(
                    f"/api/v1/flows/{other['id']}",
                    headers={"If-Match": other["version_token"], "X-Workflow-Actions": '["read","create","update"]'},
                    json={"name": "must-not-land"},
                )
                check(mode + " other user save", foreign.status_code, 404 if "update" in actions else 403)
                check(mode + " files still blocked", client.get("/api/v2/files/").status_code, 403)
                check(
                    mode + " runtime still blocked", client.post(f"/api/v1/run/{own['id']}", json={}).status_code, 403
                )
                check(mode + " deletion still blocked", client.delete(f"/api/v1/flows/{own['id']}").status_code, 403)
            check("four separate mapped users", len(set(identities.values())), 4)
            client = clients["update"]
            target = fixtures["update"]["id"]
            before = client.get(f"/api/v1/flows/{target}").json()
            action("/revoke-update")
            denied = client.patch(
                f"/api/v1/flows/{target}",
                headers={"If-Match": before["version_token"], "X-Workflow-Actions": '["read","update"]'},
                json={"name": "revoked-must-not-land"},
            )
            check("real IAM role change invalidates old cookie", denied.status_code, 401)
            check("real IAM old-cookie error code", denied.json()["code"], "PLATFORM_SSO_REQUIRED")
            check(
                "old IAM bearer rejected",
                control.get(
                    API + "/api/iam/current-user/access",
                    headers={"Authorization": "Bearer " + pairs["update"]["accessToken"]},
                ).status_code,
                401,
            )
            check(
                "old IAM refresh rejected",
                control.post(
                    API + "/api/iam/auth/refresh", json={"refreshToken": pairs["update"]["refreshToken"]}
                ).status_code,
                401,
            )
            fresh, _ = login("update")
            with closing(fresh):
                current = fresh.get(f"/api/v1/flows/{target}")
                check("relogin after revoke readonly", current.headers["x-workflow-actions"].split(","), ["read"])
                check("rejected save did not persist", current.json(), before)
                check(
                    "fresh readonly cookie save refused",
                    fresh.patch(
                        f"/api/v1/flows/{target}",
                        headers={"If-Match": before["version_token"]},
                        json={"name": "still-denied"},
                    ).status_code,
                    403,
                )
            action("/restore-update")
            restored, _ = login("update")
            with closing(restored):
                current = restored.get(f"/api/v1/flows/{target}")
                check("restored current actions", current.headers["x-workflow-actions"].split(","), ["read", "update"])
                check(
                    "restored save",
                    restored.patch(
                        f"/api/v1/flows/{target}",
                        headers={"If-Match": current.json()["version_token"]},
                        json={"name": "B2 real IAM update restored"},
                    ).status_code,
                    200,
                )
        finally:
            for client in clients.values():
                client.close()
        report = {
            "checks": proof,
            "fixtures": {mode: {"id": flow["id"], "user_id": flow["user_id"]} for mode, flow in fixtures.items()},
            "identityMapping": identities,
            "platform": action("/evidence"),
        }
        (DIRECTORY / "http-evidence.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Real IAM / SSO / Langflow HTTP acceptance: {len(proof)} checks passed")


if __name__ == "__main__":
    main()
