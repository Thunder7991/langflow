"""Behavior tests for SSO transactions, session binding and restricted access."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langflow.services.auth import platform_sso


@pytest.fixture
def sso_app(monkeypatch):
    settings = platform_sso.PlatformSsoSettings(
        enabled=True,
        platform_url="https://api.example.com",
        client_id="flow-a",
        client_secret="test-service-secret-with-enough-characters",  # noqa: S106
        tenant_id="tenant-a",
        public_url="https://flow.example.com",
        host_origin="https://ai.example.com",
        secure=True,  # HTTPS 用例不继承开发机 .env 中的本地 HTTP 例外。
    )
    live = {"active": True, "calls": 0}

    async def platform_call(action, body, _settings):
        assert action in {"exchange", "status", "revoke"}
        assert set(body) == ({"code", "state"} if action == "exchange" else {"grant"})
        live["calls"] += 1
        if not live["active"]:
            from fastapi import HTTPException

            raise HTTPException(401, "Platform session revoked")
        return {
            "userId": "user-a",
            "username": "alice",
            "tenantId": "tenant-a",
            "clientId": "flow-a",
            "sessionId": "session-a",
            "expiresAt": "2099-01-01T00:00:00Z",
            "accessLevel": "viewer",
            "grant": "g" * 43,
        }

    monkeypatch.setattr(platform_sso, "platform_call", platform_call)
    import jwt

    key = "test-local-cookie-key-with-enough-characters"
    monkeypatch.setattr(
        platform_sso,
        "encode_cookie",
        lambda data, seconds: jwt.encode({**data, "exp": __import__("time").time() + seconds}, key, algorithm="HS256"),
    )
    monkeypatch.setattr(platform_sso, "decode_cookie", lambda token: jwt.decode(token, key, algorithms=["HS256"]))

    async def map_user(identity):
        assert identity["userId"] == "user-a"
        return "11111111-1111-4111-8111-111111111111"

    monkeypatch.setattr(platform_sso, "map_user", map_user)
    monkeypatch.setattr(platform_sso, "native_access_token", lambda user_id: f"native-{user_id}")
    app = FastAPI()
    platform_sso.install_platform_sso(app, settings)

    @app.get("/api/v1/flows/")
    def flows():
        return {"flows": []}

    return TestClient(app, base_url="https://flow.example.com"), live


def test_native_credentials_cannot_bypass_missing_sso_cookie(sso_app):
    client, _ = sso_app
    response = client.get("/api/v1/flows/", headers={"authorization": "Bearer native-token", "x-api-key": "key"})
    assert response.status_code == 401


def test_tampered_cookie_is_rejected_and_websocket_is_closed(sso_app):
    client, _ = sso_app
    client.cookies.set("platform_sso", "tampered")
    assert client.get("/api/v1/flows/").status_code == 401
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/v1/voice/ws"):
        pass


def test_disabled_sso_does_not_change_existing_application():
    app = FastAPI()
    platform_sso.install_platform_sso(app, platform_sso.PlatformSsoSettings(enabled=False))

    @app.get("/api/v1/flows/")
    def flows():
        return {"existing": True}

    assert TestClient(app).get("/api/v1/flows/").json() == {"existing": True}


def login(client):
    response = client.get("/api/v1/platform-sso/start?session=session-a")
    assert response.status_code == 200
    transaction = client.cookies.get("platform_sso_tx")
    state = platform_sso.decode_cookie(transaction)["state"]
    response = client.get(f"/api/v1/platform-sso/callback?code={'c' * 43}&state={state}", follow_redirects=False)
    assert response.status_code == 303
    assert "code=" not in response.headers["location"]
    return response


def test_callback_rejects_missing_browser_transaction(sso_app):
    client, _ = sso_app
    assert client.get(f"/api/v1/platform-sso/callback?code={'c' * 43}&state={'s' * 43}").status_code == 401


def test_cookie_login_refresh_reuse_and_platform_revocation(sso_app):
    client, live = sso_app
    assert client.get("/api/v1/flows/").status_code == 401
    response = login(client)
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "Domain=" not in response.headers["set-cookie"]
    assert client.get("/api/v1/flows/").status_code == 200
    response = client.get("/api/v1/platform-sso/start?session=session-a")
    assert '"ready"' in response.text
    assert "needs-ticket" not in response.text
    assert client.post("/api/v1/refresh", headers={"origin": "https://flow.example.com"}).status_code == 200
    live["active"] = False
    assert client.get("/api/v1/flows/").status_code == 401
    assert client.post("/api/v1/refresh", headers={"origin": "https://flow.example.com"}).status_code == 401


def test_read_only_mode_blocks_writes_and_native_login_bypasses(sso_app):
    client, _ = sso_app
    login(client)
    assert client.post("/api/v1/flows/", headers={"origin": "https://flow.example.com"}).status_code == 403
    assert client.post("/api/v1/login", headers={"origin": "https://flow.example.com"}).status_code == 403
    assert client.get("/api/v1/api_key/").status_code == 403
    assert client.get("/api/v1/auto_login").status_code == 403
    assert client.post("/api/v1/refresh", headers={"origin": "https://evil.example.com"}).status_code == 403


def test_wrong_platform_context_does_not_reuse_old_cookie(sso_app):
    client, _ = sso_app
    login(client)
    response = client.get("/api/v1/platform-sso/start?session=session-b")
    assert "needs-ticket" in response.text


def test_return_url_cannot_redirect_to_other_site(sso_app):
    client, _ = sso_app
    response = client.get("/api/v1/platform-sso/start?returnUrl=https://evil.example.com")
    assert response.status_code == 422


def test_return_url_cannot_inject_inline_script(sso_app):
    client, _ = sso_app
    response = client.get("/api/v1/platform-sso/start", params={"returnUrl": "/</script><script>alert(1)</script>"})
    assert response.status_code == 422
