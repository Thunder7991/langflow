"""Platform ticket SSO with independent cookies and linked-session checks."""

from __future__ import annotations

import json
import re
import secrets
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path  # noqa: TC003 - Pydantic resolves this settings field at runtime.
from typing import Annotated
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
import jwt
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import HTTPConnection

MIN_SERVICE_SECRET_LENGTH = 32


class PlatformSsoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LANGFLOW_PLATFORM_SSO_", extra="ignore")
    enabled: bool = False
    platform_url: str = ""
    client_id: str = ""
    client_secret: SecretStr = SecretStr("")
    tenant_id: str = ""
    public_url: str = ""
    host_origin: str = ""
    secure: bool = True
    ca_bundle: Path | None = None

    @model_validator(mode="after")
    def validate_enabled(self):
        if not self.enabled:
            return self
        if (
            not self.client_id
            or not self.tenant_id
            or len(self.client_secret.get_secret_value()) < MIN_SERVICE_SECRET_LENGTH
        ):
            msg = "平台 SSO 需要明确的客户端、租户和至少 32 字符服务凭据"
            raise ValueError(msg)
        for value in (self.platform_url, self.public_url, self.host_origin):
            parsed = urlsplit(value)
            local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                msg = "SSO URL 必须是固定可信地址"
                raise ValueError(msg)
            if parsed.scheme != "https" and not (not self.secure and local and parsed.scheme == "http"):
                msg = "SSO 仅允许 HTTPS; 本机开发可显式关闭 secure"
                raise ValueError(msg)
        if urlsplit(self.host_origin).path not in {"", "/"}:
            msg = "宿主必须配置精确 origin"
            raise ValueError(msg)
        return self


def encode_cookie(data: dict, seconds: int) -> str:
    from langflow.services.deps import get_auth_service

    return get_auth_service().create_token(data, timedelta(seconds=seconds))


def decode_cookie(token: str) -> dict:
    from langflow.services.auth.utils import get_jwt_verification_key
    from langflow.services.deps import get_settings_service

    settings = get_settings_service()
    return jwt.decode(
        token,
        get_jwt_verification_key(settings),
        algorithms=[settings.auth_settings.ALGORITHM],
        options={"require": ["exp", "type"]},
    )


def native_access_token(user_id: str) -> str:
    # 原生依赖只接收本次核验后的短期内部 token;浏览器拿不到可绕过 SSO 的原生会话。
    return encode_cookie({"sub": user_id, "type": "access"}, 60)


async def platform_call(action: str, body: dict, settings: PlatformSsoSettings) -> dict:
    try:
        # 私有 HTTPS 部署显式指定信任链;未配置时沿用 httpx 默认,始终校验证书和主机名。
        verify = ssl.create_default_context(cafile=str(settings.ca_bundle)) if settings.ca_bundle else True
        async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False, verify=verify) as client:
            response = await client.post(
                f"{settings.platform_url.rstrip('/')}/api/internal/workflow/sso/{action}",
                json=body,
                headers={
                    "x-workflow-client": settings.client_id,
                    "x-workflow-secret": settings.client_secret.get_secret_value(),
                },
            )
        if response.status_code in {401, 403}:
            raise HTTPException(401, "Platform session or workflow permission is no longer valid")
        if response.status_code != status.HTTP_200_OK:
            raise HTTPException(503, "Platform SSO is unavailable")
        envelope = response.json()
        if envelope.get("statusCode") != status.HTTP_200_OK or not isinstance(envelope.get("data"), dict):
            raise HTTPException(503, "Invalid platform SSO response")
        return envelope["data"]
    except (httpx.HTTPError, ValueError, OSError) as exc:
        # 不记录请求体、票据或服务凭据;错误信息不能进入自动重登循环。
        raise HTTPException(503, "Platform SSO is unavailable") from exc


async def map_user(identity: dict) -> str:
    from langflow.services.auth.external import ExternalIdentity
    from langflow.services.deps import get_auth_service, session_scope

    async with session_scope() as db:
        # 专用 provider 避免与历史邮箱/同名管理员自动关联;实例必须绑定唯一租户。
        external = ExternalIdentity(provider="deep_agents", subject=identity["userId"], username=identity["username"])
        # 复用身份映射的事务/冲突处理, 不重复创建本地账户生命周期。
        user = await get_auth_service()._materialize_external_user(external, db)  # noqa: SLF001
        if user.is_superuser or not user.is_active:
            raise HTTPException(403, "SSO users must be active non-superusers")
        return str(user.id)


def _cookie_name(connection: HTTPConnection, name: str) -> str:
    from langflow.services.auth.cookies import auth_cookie_name

    return auth_cookie_name(name, connection)


def _path(connection: HTTPConnection) -> str:
    return connection.app.root_path.rstrip("/") + "/"


def _set_cookie(response, connection, settings, name, value, seconds):
    response.set_cookie(
        _cookie_name(connection, name),
        value,
        max_age=seconds,
        path=_path(connection),
        httponly=True,
        secure=settings.secure,
        samesite="lax",
    )


def _identity_matches(identity: dict, settings: PlatformSsoSettings, claims: dict | None = None):
    if (
        identity.get("tenantId") != settings.tenant_id
        or identity.get("clientId") != settings.client_id
        or identity.get("accessLevel") != "viewer"
    ):
        raise HTTPException(401, "SSO instance binding mismatch")
    if claims and any(identity.get(key) != claims.get(key) for key in ("userId", "sessionId", "tenantId", "clientId")):
        raise HTTPException(401, "SSO identity changed")


async def _session(connection: HTTPConnection, settings: PlatformSsoSettings):
    token = connection.cookies.get(_cookie_name(connection, "platform_sso"), "")
    try:
        claims = decode_cookie(token)
        if claims.get("type") != "platform_sso":
            msg = "wrong cookie type"
            raise ValueError(msg)
        UUID(claims["sub"])
        identity = await platform_call("status", {"grant": claims["grant"]}, settings)
        _identity_matches(identity, settings, claims)
    except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
        raise HTTPException(401, "SSO login required") from exc
    else:
        return claims, identity


def _bridge(settings: PlatformSsoSettings, payload: dict, next_path: str | None = None):
    nonce = secrets.token_urlsafe(24)
    # JSON 转义 '<',防止配置/身份字符串提前闭合 script;只向精确宿主发消息。
    message = json.dumps({"channel": "workflow-sso", **payload}).replace("<", "\\u003c")
    origin = json.dumps(settings.host_origin.rstrip("/"))
    destination = json.dumps(next_path).replace("<", "\\u003c")
    navigate = f"location.replace({destination});" if next_path else ""
    html = (
        '<!doctype html><meta charset="utf-8"><p>正在连接工作流登录…</p>'
        f'<script nonce="{nonce}">window.parent.postMessage({message},{origin});{navigate}</script>'
    )
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; frame-ancestors {settings.host_origin.rstrip('/')}"
            ),
        },
    )


def install_platform_sso(app: FastAPI, settings: PlatformSsoSettings | None = None):
    settings = settings or PlatformSsoSettings()
    if not settings.enabled:
        return
    if urlsplit(settings.public_url).path.rstrip("/") != app.root_path.rstrip("/"):
        msg = "Platform SSO public URL path must match the configured Langflow root_path"
        raise ValueError(msg)
    # 独立于已有企业 SSO 插件开关;不能同时开启自动登录或外部头认证形成旁路。
    app.state.platform_sso = settings
    router = APIRouter(prefix="/api/v1/platform-sso")

    @router.get("/start")
    async def start(request: Request, session: str = "", return_url: Annotated[str, Query(alias="returnUrl")] = "/"):
        if not re.fullmatch(r"/[A-Za-z0-9/_-]*", return_url) or return_url.startswith("//"):
            raise HTTPException(422, "Invalid return URL")
        next_path = _path(request).rstrip("/") + return_url
        try:
            claims, identity = await _session(request, settings)
            if identity["sessionId"] == session:
                return _bridge(
                    settings,
                    {"status": "ready", "sessionId": identity["sessionId"], "tenantId": identity["tenantId"]},
                    next_path,
                )
            await platform_call("revoke", {"grant": claims["grant"]}, settings)
        except HTTPException as exc:
            if exc.status_code != status.HTTP_401_UNAUTHORIZED:
                raise
        state = secrets.token_urlsafe(32)
        response = _bridge(settings, {"status": "needs-ticket", "state": state, "clientId": settings.client_id})
        transaction = encode_cookie(
            {"type": "platform_sso_tx", "state": state, "sessionId": session, "next": next_path}, 120
        )
        _set_cookie(response, request, settings, "platform_sso_tx", transaction, 120)
        response.delete_cookie(_cookie_name(request, "platform_sso"), path=_path(request))
        return response

    @router.get("/callback")
    async def callback(request: Request, code: str, state: str):
        try:
            transaction = decode_cookie(request.cookies.get(_cookie_name(request, "platform_sso_tx"), ""))
            if (
                transaction.get("type") != "platform_sso_tx"
                or not secrets.compare_digest(transaction["state"], state)
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", code)
            ):
                raise ValueError
        except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
            raise HTTPException(401, "Invalid SSO browser transaction") from exc
        identity = await platform_call("exchange", {"code": code, "state": state}, settings)
        _identity_matches(identity, settings)
        if identity["sessionId"] != transaction["sessionId"]:
            raise HTTPException(401, "Platform session changed during login")
        user_id = await map_user(identity)
        remaining = int(
            (
                datetime.fromisoformat(identity["expiresAt"].replace("Z", "+00:00")) - datetime.now(timezone.utc)
            ).total_seconds()
        )
        seconds = min(3600, remaining)
        if seconds <= 0:
            raise HTTPException(401, "Platform session expired")
        claims = {key: identity[key] for key in ("userId", "sessionId", "tenantId", "clientId", "grant")}
        token = encode_cookie({**claims, "sub": user_id, "type": "platform_sso"}, seconds)
        # 回调先清除含票据 URL,start 再校验现有 Cookie 并通知宿主 ready;不会再申请票据。
        target = (
            _path(request).rstrip("/")
            + "/api/v1/platform-sso/start?"
            + urlencode(
                {"session": identity["sessionId"], "returnUrl": transaction["next"][len(_path(request).rstrip("/")) :]}
            )
        )
        response = RedirectResponse(
            target, status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
        )
        _set_cookie(response, request, settings, "platform_sso", token, seconds)
        response.delete_cookie(_cookie_name(request, "platform_sso_tx"), path=_path(request))
        return response

    app.include_router(router)
    app.add_middleware(PlatformSsoMiddleware, settings=settings)


class PlatformSsoMiddleware:
    """Check SSO before native dependencies to enforce platform revocation."""

    def __init__(self, app, settings: PlatformSsoSettings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        connection = HTTPConnection(scope)
        path = scope["path"]
        root = connection.app.root_path.rstrip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        original_send = send

        async def send_with_security(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append(
                    (b"content-security-policy", f"frame-ancestors {self.settings.host_origin.rstrip('/')}".encode())
                )
                headers.append((b"referrer-policy", b"no-referrer"))
                if path.startswith("/api/"):
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
                if path == "/api/v1/platform-sso/callback":
                    # Uvicorn 在发送响应时读取 scope 生成访问日志,避免记录一次性票据。
                    # 边缘代理也必须使用不含 query 的日志格式。
                    scope["query_string"] = b""
            await original_send(message)

        send = send_with_security
        if scope["type"] == "websocket":
            # A 首个里程碑无调试/长连接开放;后续必须加入连接级撤销后再放行。
            await send({"type": "websocket.close", "code": 1008})
            return None
        if path.startswith("/api/v1/platform-sso/"):
            return await self.app(scope, receive, send)
        if not path.startswith("/api/"):
            if path.startswith(("/mcp", "/v1/", "/v2/", "/logs")):
                return await JSONResponse({"detail": "Endpoint disabled in SSO milestone"}, 403)(scope, receive, send)
            return await self.app(scope, receive, send)
        if path.rstrip("/") == "/api/v1/auto_login":
            return await JSONResponse({"detail": {"auto_login": False}}, 403)(scope, receive, send)
        try:
            claims, identity = await _session(connection, self.settings)
            method = scope["method"]
            if (
                method not in {"GET", "HEAD"}
                and connection.headers.get("origin")
                != f"{urlsplit(self.settings.public_url).scheme}://{urlsplit(self.settings.public_url).netloc}"
            ):
                raise HTTPException(403, "Invalid request origin")
            if path.rstrip("/") == "/api/v1/refresh" and method == "POST":
                remaining = int(
                    (
                        datetime.fromisoformat(identity["expiresAt"].replace("Z", "+00:00"))
                        - datetime.now(timezone.utc)
                    ).total_seconds()
                )
                seconds = min(3600, remaining)
                if seconds <= 0:
                    raise HTTPException(401, "Platform session expired")
                response = JSONResponse({"access_token": "", "refresh_token": "", "token_type": "cookie"})
                _set_cookie(
                    response,
                    connection,
                    self.settings,
                    "platform_sso",
                    encode_cookie({k: v for k, v in claims.items() if k != "exp"}, seconds),
                    seconds,
                )
                return await response(scope, receive, send)
            if path.rstrip("/") == "/api/v1/logout" and method == "POST":
                await platform_call("revoke", {"grant": claims["grant"]}, self.settings)
                response = JSONResponse({"message": "Logout successful"})
                response.delete_cookie(_cookie_name(connection, "platform_sso"), path=_path(connection))
                return await response(scope, receive, send)
            # 明确白名单,未知 GET 也不放行,避免公开执行或 GET 管理入口产生副作用。
            allowed = re.fullmatch(
                r"/api/v1/(session|version|config|all|sidebar_categories|users/whoami|flows(?:/.*)?|projects(?:/.*)?|folders(?:/.*)?|variables(?:/.*)?|starter-projects(?:/.*)?)/?",
                path,
            )
            if (
                method not in {"GET", "HEAD"}
                or not allowed
                or "/public_flow" in path
                or "x-api-key" in connection.query_params
            ):
                raise HTTPException(403, "Only personal read access is enabled in SSO milestone")
            # 身份只由已验证的 Cookie/平台关联决定,丢弃浏览器的原生认证头和 Cookie。
            scope["headers"] = [
                (k, v) for k, v in scope["headers"] if k.lower() not in {b"authorization", b"x-api-key", b"cookie"}
            ]
            scope["headers"].append((b"authorization", f"Bearer {native_access_token(claims['sub'])}".encode()))
            return await self.app(scope, receive, send)
        except HTTPException as exc:
            response = JSONResponse(
                {
                    "detail": exc.detail,
                    "code": "PLATFORM_SSO_REQUIRED"
                    if exc.status_code == status.HTTP_401_UNAUTHORIZED
                    else "PLATFORM_SSO_ERROR",
                    "hostOrigin": self.settings.host_origin.rstrip("/"),
                },
                exc.status_code,
                headers={"Cache-Control": "no-store"},
            )
            return await response(scope, receive, send)
