"""本机 A 阶段验收工具。凭据/数据库/证书仅写入指定的 Git 忽略目录。"""

# 验收断言保留字面量期望值,中文说明无需转换为 ASCII 标点。
# ruff: noqa: FBT003, PLR2004, RUF002, RUF003, D415

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def prepare(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / "config.json"
    if config_path.exists():
        message = "Lab config exists; refusing to replace credentials or data"
        raise SystemExit(message)
    certs = directory / "certs"
    certs.mkdir()
    # Docker Desktop 的 DNS 地址因环境而异,只读查询现有镜像,不用固定的 Linux DNS 常量。
    docker = shutil.which("docker")
    if docker is None:
        message = "Docker CLI is required for the local acceptance environment"
        raise SystemExit(message)
    # 可执行文件由本机 PATH 解析为绝对路径,参数固定,不接收外部命令输入。
    docker_dns = subprocess.check_output(  # noqa: S603
        [docker, "run", "--rm", "--entrypoint", "cat", "nginx:stable-alpine3.23-perl", "/etc/resolv.conf"],
        text=True,
    )
    resolver = re.search(r"^nameserver ([0-9.]+)$", docker_dns, re.MULTILINE)[1]
    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Workflow A2 local acceptance CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .add_extension(
            x509.NameConstraints(
                permitted_subtrees=[
                    x509.DNSName("workflow.localhost"),
                    x509.DNSName(".workflow.localhost"),
                    x509.DNSName("localhost"),
                ],
                excluded_subtrees=None,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    hosts = ["ai.workflow.localhost", "flow-a.workflow.localhost", "flow-b.workflow.localhost", "localhost"]
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])]))
        .issuer_name(name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host) for host in hosts]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    # 根私钥只在生成时存在于内存，不写盘；此 CA 无法再签发其他证书。
    (directory / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (directory / "ca.cer").write_bytes(ca.public_bytes(serialization.Encoding.DER))
    (certs / "leaf.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (certs / "leaf-key.pem").write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    config = {
        "database": "workflow_sso_a2_e2e",
        "postgresPort": 55433,
        "postgresPassword": secrets.token_urlsafe(32),
        "serviceSecrets": {tenant: secrets.token_urlsafe(32) for tenant in ("a", "b")},
        # Windows 证书存储用 SHA-1 指纹定位;证书签名本身使用 SHA-256。
        "certificateThumbprint": ca.fingerprint(hashes.SHA1()).hex().upper(),  # noqa: S303
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    proxy = """events {}
http {
  log_format safe '$time_iso8601 $server_name $request_method $uri $status';
  access_log /var/log/nginx/access.log safe;
  # 本地夹具不记录可能含 callback query 的错误上下文;生产需独立实现完整错误日志脱敏。
  error_log /var/log/nginx/error.log crit;
  include /etc/nginx/mime.types;
  resolver DOCKER_RESOLVER ipv6=off;
  map '' $lab_host { default host.docker.internal; }
  ssl_certificate /etc/nginx/lab/leaf.pem;
  ssl_certificate_key /etc/nginx/lab/leaf-key.pem;
  ssl_protocols TLSv1.2 TLSv1.3;
  proxy_set_header X-Forwarded-Proto https;
  proxy_set_header X-Forwarded-For $remote_addr;
  proxy_set_header X-Forwarded-Host $host;
  proxy_set_header X-Forwarded-Prefix '';
  proxy_set_header Forwarded '';
  proxy_set_header X-Api-Key '';
  proxy_set_header X-Workflow-User '';
  proxy_set_header X-Workflow-Tenant '';
  proxy_redirect off;
  server { listen 443 ssl default_server; server_name _; return 421; }
  server {
    listen 443 ssl;
    server_name ai.workflow.localhost localhost;
    location /api/ { proxy_pass http://$lab_host:18300; }
    location / { proxy_pass http://$lab_host:18301; }
  }
"""
    for tenant, port in (("a", 18860), ("b", 18861)):
        proxy += f"""  server {{
    listen 443 ssl; server_name flow-{tenant}.workflow.localhost;
    location /api/ {{ proxy_pass http://$lab_host:{port}; }}
    location /health {{ proxy_pass http://$lab_host:{port}; }}
    location ~ ^/(mcp|v1|v2|logs)(/|$) {{ return 403; }}
    location / {{
      root /usr/share/nginx/html;
      try_files $uri $uri/ /index.html;
      add_header Content-Security-Policy "frame-ancestors https://ai.workflow.localhost:8443" always;
      add_header Referrer-Policy no-referrer always;
    }}
  }}
"""
    (directory / "nginx.conf").write_text((proxy + "}\n").replace("DOCKER_RESOLVER", resolver), encoding="utf-8")
    print(
        json.dumps(
            {
                "directory": str(directory),
                "caThumbprint": config["certificateThumbprint"],
                "hosts": hosts,
                "validDays": 2,
                "rootPrivateKeySaved": False,
            }
        )
    )


def configure_instances(directory: Path):
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    identities = json.loads((directory / "identities.json").read_text(encoding="utf-8"))
    for tenant in ("a", "b"):
        client = next(item for item in identities["clients"] if item["clientId"] == f"a2-tenant-{tenant}")
        instance = directory / f"tenant-{tenant}"
        instance.mkdir(exist_ok=True)
        values = {
            "LANGFLOW_CONFIG_DIR": instance.as_posix(),
            "LANGFLOW_DATABASE_URL": f"sqlite+aiosqlite:///{instance.as_posix()}/langflow.db",
            "LANGFLOW_SECRET_KEY": secrets.token_urlsafe(48),
            "LANGFLOW_AUTO_LOGIN": "false",
            "LANGFLOW_SKIP_AUTH_AUTO_LOGIN": "false",
            "LANGFLOW_EXTERNAL_AUTH_ENABLED": "false",
            "LANGFLOW_SSO_ENABLED": "false",
            "LANGFLOW_PLATFORM_SSO_ENABLED": "true",
            "LANGFLOW_PLATFORM_SSO_SECURE": "true",
            "LANGFLOW_PLATFORM_SSO_PLATFORM_URL": "https://localhost:8443",
            "LANGFLOW_PLATFORM_SSO_CA_BUNDLE": (directory / "ca.pem").as_posix(),
            "LANGFLOW_PLATFORM_SSO_CLIENT_ID": client["clientId"],
            "LANGFLOW_PLATFORM_SSO_CLIENT_SECRET": config["serviceSecrets"][tenant],
            "LANGFLOW_PLATFORM_SSO_TENANT_ID": client["tenantId"],
            "LANGFLOW_PLATFORM_SSO_PUBLIC_URL": f"https://flow-{tenant}.workflow.localhost:8443",
            "LANGFLOW_PLATFORM_SSO_HOST_ORIGIN": "https://ai.workflow.localhost:8443",
            "LANGFLOW_ALLOW_CUSTOM_COMPONENTS": "false",
            "LANGFLOW_MCP_COMPOSER_ENABLED": "false",
            "LANGFLOW_DO_NOT_TRACK": "true",
            "DO_NOT_TRACK": "true",
        }
        target = directory / f"tenant-{tenant}.env"
        if target.exists():
            message = "Instance environment exists; refusing to rotate keys silently"
            raise SystemExit(message)
        target.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    print("Configured two independent instances; no application database was copied")


def verify(directory: Path):
    """真实 TLS/HTTP/Redis/IAM/SQLite 验收；报告仅保存状态与不敏感资源标识。"""
    from concurrent.futures import ThreadPoolExecutor
    from http.cookies import SimpleCookie

    from langflow.services.database.models import File, Flow, Folder
    from sqlmodel import Session, create_engine

    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    fixture = json.loads((directory / "identities.json").read_text(encoding="utf-8"))
    context = ssl.create_default_context(cafile=str(directory / "ca.pem"))
    actors = fixture["actors"]
    hosts = {f"flow-{key}.workflow.localhost": key for key in ("a", "b")}
    original_resolve = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        # 等价于专用 hosts 映射，只影响本验收进程；TLS 仍按原 URL 主机名验证。
        return original_resolve("127.0.0.1" if host in hosts else host, port, *args, **kwargs)

    socket.getaddrinfo = resolve
    results = []
    browsers = {}
    base = "https://localhost:8443"
    api = httpx.Client(base_url=base, verify=context, trust_env=False, timeout=15)

    def check(name, actual, expected):
        passed = actual == expected
        results.append({"case": name, "passed": passed, "actual": actual, "expected": expected})
        if not passed:
            message = f"{name}: actual={actual}, expected={expected}"
            raise AssertionError(message)

    def bearer(actor):
        return {"Authorization": f"Bearer {actors[actor]['accessToken']}"}

    def service(key):
        return {"x-workflow-client": f"a2-tenant-{key}", "x-workflow-secret": config["serviceSecrets"][key]}

    def ticket(actor, state):
        response = api.post(
            "/api/workflow/sso/tickets",
            headers=bearer(actor),
            json={"clientId": "a2-tenant-b" if actor == "b1" else "a2-tenant-a", "state": state},
        )
        check(f"{actor}:ticket", response.status_code, 200)
        return response.json()["data"]["code"]

    def login(actor):
        key = "b" if actor == "b1" else "a"
        origin = f"https://flow-{key}.workflow.localhost:8443"
        browser = httpx.Client(base_url=origin, verify=context, trust_env=False, timeout=15)
        browsers[actor] = browser
        response = browser.get("/api/v1/platform-sso/start", params={"session": actors[actor]["sessionId"]})
        check(f"{actor}:start", response.status_code, 200)
        state = re.search(r'"state": "([A-Za-z0-9_-]{43})"', response.text)[1]
        code = ticket(actor, state)
        response = browser.get("/api/v1/platform-sso/callback", params={"code": code, "state": state})
        check(f"{actor}:callback", response.status_code, 303)
        check(f"{actor}:callback-no-ticket-location", "code=" in response.headers["location"], False)
        for header in response.headers.get_list("set-cookie"):
            cookie = SimpleCookie()
            cookie.load(header)
            if "platform_sso" in cookie:
                value = cookie["platform_sso"]
                for attribute, expected in (
                    ("secure", True),
                    ("httponly", True),
                    ("samesite", "lax"),
                    ("domain", ""),
                    ("path", "/"),
                ):
                    check(f"{actor}:cookie-{attribute}", value[attribute], expected)
        ready = browser.get(response.headers["location"])
        check(f"{actor}:ready", '"status": "ready"' in ready.text, True)
        response = browser.get("/api/v1/session")
        check(f"{actor}:session", response.status_code, 200)
        check(
            f"{actor}:frame-ancestors",
            response.headers.get("content-security-policy"),
            "frame-ancestors https://ai.workflow.localhost:8443",
        )
        check(f"{actor}:no-store", "no-store" in response.headers.get("cache-control", ""), True)
        response = browser.get("/api/v1/platform-sso/start", params={"session": actors[actor]["sessionId"]})
        check(
            f"{actor}:refresh-reuses-cookie",
            '"status": "ready"' in response.text and "needs-ticket" not in response.text,
            True,
        )
        check(
            f"{actor}:refresh-own-origin", browser.post("/api/v1/refresh", headers={"Origin": origin}).status_code, 200
        )
        check(
            f"{actor}:csrf-origin",
            browser.post("/api/v1/refresh", headers={"Origin": "https://evil.workflow.localhost:8443"}).status_code,
            403,
        )
        return browser

    try:
        for key in ("a", "b"):
            origin = f"https://flow-{key}.workflow.localhost:8443"
            with httpx.Client(base_url=origin, verify=context, trust_env=False, timeout=15) as guest:
                check(f"tenant-{key}:anonymous", guest.get("/api/v1/session").status_code, 401)
                check(
                    f"tenant-{key}:native-key-bypass",
                    guest.get(
                        "/api/v1/session", headers={"Authorization": "Bearer forged", "x-api-key": "forged"}
                    ).status_code,
                    401,
                )
                check(
                    f"tenant-{key}:missing-browser-transaction",
                    guest.get(
                        "/api/v1/platform-sso/callback", params={"code": "c" * 43, "state": "s" * 43}
                    ).status_code,
                    401,
                )
        for actor, target in (("a1", "b"), ("b1", "a")):
            check(
                f"{actor}:cross-tenant-ticket",
                api.post(
                    "/api/workflow/sso/tickets",
                    headers=bearer(actor),
                    json={"clientId": f"a2-tenant-{target}", "state": "s" * 43},
                ).status_code,
                403,
            )
        check(
            "identity-field-injection",
            api.post(
                "/api/workflow/sso/tickets",
                headers=bearer("a1"),
                json={"clientId": "a2-tenant-a", "state": "s" * 43, "userId": actors["a2"]["userId"]},
            ).status_code,
            400,
        )
        check(
            "forged-tenant-header",
            api.post(
                "/api/workflow/sso/tickets",
                headers={**bearer("a1"), "x-tenant-id": actors["b1"]["tenantId"]},
                json={"clientId": "a2-tenant-a", "state": "s" * 43},
            ).status_code,
            403,
        )
        check(
            "forged-service-credential",
            api.post(
                "/api/internal/workflow/sso/status",
                headers={**service("a"), "x-workflow-secret": "wrong"},
                json={"grant": "g" * 43},
            ).status_code,
            401,
        )
        code = ticket("a1", "s" * 43)

        def exchange(_index):
            return api.post(
                "/api/internal/workflow/sso/exchange", headers=service("a"), json={"code": code, "state": "s" * 43}
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(exchange, range(8)))
        check("redis-concurrent-single-consume", sorted(item.status_code for item in responses), [200] + [401] * 7)
        grant = next(item.json()["data"]["grant"] for item in responses if item.status_code == 200)
        check(
            "grant-wrong-client",
            api.post("/api/internal/workflow/sso/status", headers=service("b"), json={"grant": grant}).status_code,
            401,
        )
        for actor in actors:
            login(actor)

        resource_file = directory / "resources.json"
        existing_resources = json.loads(resource_file.read_text(encoding="utf-8")) if resource_file.exists() else {}
        resources = {}
        for actor in actors:
            key = "b" if actor == "b1" else "a"
            instance = directory / f"tenant-{key}"
            database = instance / "langflow.db"
            connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
            rows = connection.execute(
                "select p.user_id,u.is_superuser,u.is_active from sso_user_profile p "
                "join user u on u.id=p.user_id where p.sso_user_id=?",
                (actors[actor]["userId"],),
            ).fetchall()
            connection.close()
            check(f"{actor}:unique-user-mapping", len(rows), 1)
            local_id = UUID(rows[0][0])
            check(f"{actor}:non-superuser", rows[0][1:], (0, 1))
            if actor in existing_resources:
                resources[actor] = existing_resources[actor]
                check(f"{actor}:mapping-stable", str(local_id), resources[actor]["localUser"])
                continue
            engine = create_engine(f"sqlite:///{database.as_posix()}")
            with Session(engine) as session:
                folder = Folder(name=f"a2-fixture-{actor}", user_id=local_id)
                session.add(folder)
                session.flush()
                flow = Flow(
                    name=f"a2-fixture-{actor}", user_id=local_id, folder_id=folder.id, data={"nodes": [], "edges": []}
                )
                file_id = uuid4()
                filename = f"{file_id}.txt"
                marker = f"A2_PRIVATE_FILE_{actor}_{file_id}"
                storage = instance / str(local_id)
                storage.mkdir(exist_ok=True)
                (storage / filename).write_text(marker, encoding="utf-8")
                file = File(
                    id=file_id,
                    user_id=local_id,
                    name=f"a2-fixture-{actor}",
                    path=f"{local_id}/{filename}",
                    size=len(marker),
                )
                session.add(flow)
                session.add(file)
                session.commit()
                resources[actor] = {
                    "flow": str(flow.id),
                    "folder": str(folder.id),
                    "file": str(file.id),
                    "marker": marker,
                    "localUser": str(local_id),
                }
            engine.dispose()
        check("same-tenant-distinct-users", resources["a1"]["localUser"] != resources["a2"]["localUser"], True)
        for actor, browser in browsers.items():
            own = resources[actor]
            response = browser.get(f"/api/v1/flows/{own['flow']}")
            check(f"{actor}:own-flow-read", response.status_code, 200)
            check(f"{actor}:own-flow-exists", response.json().get("id"), own["flow"])
            listing = browser.get("/api/v1/flows/", params={"get_all": "true"})
            check(f"{actor}:flow-list", listing.status_code, 200)
            check(f"{actor}:own-flow-listed", own["flow"] in listing.text, True)
            for other, resource in resources.items():
                if actor == other:
                    continue
                response = browser.get(f"/api/v1/flows/{resource['flow']}")
                check(f"{actor}:cannot-read-{other}-flow", response.status_code, 404)
                check(f"{actor}:cannot-list-{other}-flow", resource["flow"] in listing.text, False)
                response = browser.get(f"/api/v1/projects/{resource['folder']}")
                check(f"{actor}:cannot-read-{other}-project", response.status_code, 404)
            # A1 未开放文件 API：验证真实存在的文件也不能通过 v1/v2/批量或静态路径泄漏。
            for other, resource in resources.items():
                response = browser.get(f"/api/v2/files/{resource['file']}")
                check(f"{actor}:file-api-denied-{other}", response.status_code, 403)
                check(f"{actor}:file-content-not-leaked-{other}", resource["marker"] in response.text, False)
            for path in ("/api/v2/files/", "/api/v1/api_key/", f"/api/v1/flows/public_flow/{own['flow']}"):
                check(f"{actor}:restricted:{path.split('/')[3]}", browser.get(path).status_code, 403)
            check(
                f"{actor}:write-denied",
                browser.post(
                    "/api/v1/flows/", headers={"Origin": str(browser.base_url).rstrip("/")}, json={"name": "blocked"}
                ).status_code,
                403,
            )
        # 即使主动复制另一实例 Cookie，独立签名密钥/租户/客户端绑定也必须拒绝。
        check(
            "cross-instance-cookie-rejected",
            browsers["b1"]
            .get("/api/v1/session", headers={"Cookie": f"platform_sso={browsers['a1'].cookies.get('platform_sso')}"})
            .status_code,
            401,
        )
        check("platform-logout", api.post("/api/iam/auth/logout", headers=bearer("a2")).status_code, 201)
        check("revoked-cookie-denied", browsers["a2"].get("/api/v1/session").status_code, 401)
        check("other-user-unaffected", browsers["a1"].get("/api/v1/session").status_code, 200)
        (directory / "resources.json").write_text(json.dumps(resources), encoding="utf-8")
    finally:
        for browser in browsers.values():
            browser.close()
        api.close()
        socket.getaddrinfo = original_resolve
        (directory / "http-report.json").write_text(
            json.dumps(
                {"date": datetime.now(timezone.utc).isoformat(), "cases": results}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "passed": sum(item["passed"] for item in results),
                    "total": len(results),
                    "report": str(directory / "http-report.json"),
                }
            )
        )


def verify_native_file_owner(directory: Path, tenant: str):
    """补充验证原生文件 owner；SSO 对外文件接口仍全部阻断，不借此扩大白名单。"""
    import os

    from dotenv import load_dotenv
    from fastapi.testclient import TestClient

    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    if config["database"] != "workflow_sso_a2_e2e":
        message = "Not an acceptance directory"
        raise SystemExit(message)
    load_dotenv(directory / f"tenant-{tenant}.env", override=True)
    # 仅在无监听端口的进程内 TestClient 使用原生认证；不改实例配置/运行服务。
    os.environ["LANGFLOW_PLATFORM_SSO_ENABLED"] = "false"
    # 原生模式启动要求引导账号;仅在独立测试库准备,随机密码不保存/输出,不参与任何读取断言。
    os.environ["LANGFLOW_SUPERUSER"] = "a2-offline-fixture-admin"
    os.environ["LANGFLOW_SUPERUSER_PASSWORD"] = secrets.token_urlsafe(48)
    __import__("langflow.__main__")  # Windows 信号处理器必须先在主线程注册。
    from langflow.main import create_app
    from langflow.services.auth.platform_sso import native_access_token

    resources = json.loads((directory / "resources.json").read_text(encoding="utf-8"))
    names = ("a1", "a2") if tenant == "a" else ("b1",)
    results = []

    def check(name, actual, expected):
        passed = actual == expected
        results.append({"case": name, "passed": passed, "actual": actual, "expected": expected})
        if not passed:
            message = f"{name}: actual={actual}, expected={expected}"
            raise AssertionError(message)

    try:
        with TestClient(create_app()) as client:
            for name in names:
                headers = {"Authorization": f"Bearer {native_access_token(resources[name]['localUser'])}"}
                response = client.get("/api/v2/files/", headers=headers)
                check(f"{name}:native-file-list", response.status_code, 200)
                check(f"{name}:own-file-listed", resources[name]["file"] in response.text, True)
                for other, resource in resources.items():
                    response = client.get(f"/api/v2/files/{resource['file']}", headers=headers)
                    check(f"{name}:native-file-{other}", response.status_code, 200 if name == other else 404)
                    check(f"{name}:native-file-content-{other}", resource["marker"] in response.text, name == other)
    finally:
        (directory / f"native-file-report-{tenant}.json").write_text(
            json.dumps({"cases": results}, indent=2), encoding="utf-8"
        )
        print(json.dumps({"tenant": tenant, "passed": sum(item["passed"] for item in results), "total": len(results)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "configure", "verify", "native-files-a", "native-files-b"])
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    if arguments.action.startswith("native-files-"):
        verify_native_file_owner(arguments.directory.resolve(), arguments.action[-1])
    else:
        {"prepare": prepare, "configure": configure_instances, "verify": verify}[arguments.action](
            arguments.directory.resolve()
        )
