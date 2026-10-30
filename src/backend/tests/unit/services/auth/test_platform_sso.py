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


async def test_platform_exchange_verifies_https_with_explicit_private_ca(tmp_path):
    """Verify private HTTPS certificates without disabling TLS verification."""
    import json
    import ssl
    import threading
    from datetime import datetime, timedelta, timezone
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "ca.pem", tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            valid = (
                self.path == "/api/internal/workflow/sso/status"
                and body == {"grant": "g" * 43}
                and self.headers.get("x-workflow-client") == "flow-a"
            )
            self.send_response(200 if valid else 403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"statusCode": 200, "data": {"userId": "verified"}}).encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = platform_sso.PlatformSsoSettings(
        enabled=True,
        platform_url=f"https://localhost:{server.server_port}",
        ca_bundle=cert_file,
        client_id="flow-a",
        client_secret="private-ca-test-service-secret-length-32",  # noqa: S106
        tenant_id="tenant-a",
        public_url="https://flow.example.com",
        host_origin="https://ai.example.com",
        secure=True,
    )
    try:
        assert await platform_sso.platform_call("status", {"grant": "g" * 43}, settings) == {"userId": "verified"}
        from fastapi import HTTPException

        settings.ca_bundle = None
        with pytest.raises(HTTPException) as error:
            await platform_sso.platform_call("status", {"grant": "g" * 43}, settings)
        assert error.value.status_code == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
