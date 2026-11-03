"""B4 uses real SSO middleware, SQLite and an isolated local storage directory."""

from urllib.parse import quote
from uuid import uuid4

import anyio
import pytest
from langflow.services.database.models.file.model import File as UserFile
from langflow.services.deps import get_storage_service, session_scope

from tests.unit.api.v1.test_platform_sso_editing import (
    editor,  # noqa: F401
    isolated_auth_configuration,  # noqa: F401
)

BASE = "/api/v2/platform-files"


@pytest.fixture
async def files_editor(editor, monkeypatch, tmp_path):  # noqa: F811
    client, actor, live = editor
    live["actions"] += ["file_read", "file_upload", "file_download"]
    monkeypatch.setattr(get_storage_service(), "data_dir", anyio.Path(tmp_path))
    return client, actor, live, tmp_path


async def upload(client, name="资料.txt", content=b"hello workflow"):
    return await client.post(
        BASE,
        content=content,
        headers={"Content-Type": "application/octet-stream", "X-Workflow-Filename": quote(name, safe="")},
    )


async def test_personal_file_rename_delete_require_explicit_actions(files_editor):
    client, _, live, root = files_editor
    file = (await upload(client)).json()
    path = f"{BASE}/{file['id']}"
    assert (await client.patch(path, json={"name": "renamed.txt"})).status_code == 403
    live["actions"] += ["file_update", "file_delete"]
    response = await client.patch(path, json={"name": "renamed.txt"})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "renamed.txt"
    assert response.json()["path"] == file["path"]
    assert (root / file["path"]).read_bytes() == b"hello workflow"
    assert (await client.patch(path, json={"name": "bad.py"})).status_code == 422
    assert (await client.delete(path)).status_code == 204
    assert not (root / file["path"]).exists()
    assert (await client.get(path)).status_code == 404


async def test_rename_conflict_returns_409_and_preserves_both_files(files_editor):
    client, _, live, root = files_editor
    first = (await upload(client, "first.txt", b"first")).json()
    second = (await upload(client, "second.txt", b"second")).json()
    live["actions"].append("file_update")
    response = await client.patch(f"{BASE}/{first['id']}", json={"name": second["name"]})
    assert response.status_code == 409, response.text
    assert {row["name"] for row in (await client.get(BASE)).json()} == {"first.txt", "second.txt"}
    assert (root / first["path"]).read_bytes() == b"first"
    assert (root / second["path"]).read_bytes() == b"second"
    assert (await client.patch(f"{BASE}/{first['id']}", json={"name": "available.txt"})).status_code == 200


async def test_rename_database_failure_is_not_reported_as_duplicate_name(files_editor, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from sqlmodel.ext.asyncio.session import AsyncSession

    client, _, live, root = files_editor
    file = (await upload(client, "original.txt")).json()
    live["actions"].append("file_update")
    original = AsyncSession.flush

    async def fail_file_update(session, *args, **kwargs):
        if any(isinstance(row, UserFile) for row in session.dirty):
            message = "internal SQL and parameters"
            raise IntegrityError(message, {}, RuntimeError())
        return await original(session, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", fail_file_update)
    result = await client.patch(f"{BASE}/{file['id']}", json={"name": "changed.txt"})
    assert result.status_code == 503, result.text
    assert "internal SQL" not in result.text
    assert (await client.get(BASE)).json()[0]["name"] == "original.txt"
    assert (root / file["path"]).read_bytes() == b"hello workflow"


@pytest.mark.parametrize("operation", ["create", "duplicate", "move", "rename", "delete"])
async def test_file_transactions_support_durable_authorization_audit(files_editor, monkeypatch, operation):
    import asyncio

    from langflow.services.deps import get_settings_service

    from tests.unit.api.v1.test_platform_sso_editing import GRAPH, create

    client, _, live, _ = files_editor
    file = (await upload(client)).json()
    graph = {**GRAPH, "attachment": {"type": "file", "value": [file["name"]], "file_path": [file["path"]]}}
    live["actions"] += ["duplicate", "move", "project_create", "file_update", "file_delete"]
    flow = await create(client)
    if operation in {"duplicate", "move"}:
        response = await client.patch(
            f"/api/v1/flows/{flow['id']}", json={"data": graph}, headers={"If-Match": flow["version_token"]}
        )
        assert response.status_code == 200, response.text
        flow = response.json()
    project = (await client.post("/api/v1/projects/", json={"name": uuid4().hex})).json()
    settings = get_settings_service().auth_settings
    monkeypatch.setattr(settings, "AUTHZ_AUDIT_ENABLED", True)
    monkeypatch.setattr(settings, "AUTHZ_AUDIT_DURABLE", True)
    if operation == "create":
        pending = client.post("/api/v1/flows/", json={"name": "audited file", "data": graph})
    elif operation == "duplicate":
        pending = client.post(f"/api/v2/platform-workflow/flows/{flow['id']}/duplicate", json={})
    elif operation == "move":
        pending = client.patch(f"/api/v1/flows/{flow['id']}", json={"folder_id": project["id"]})
    elif operation == "rename":
        pending = client.patch(f"{BASE}/{file['id']}", json={"name": "renamed.txt"})
    else:
        pending = client.delete(f"{BASE}/{file['id']}")
    result = await asyncio.wait_for(pending, 5)
    assert result.status_code in {200, 201, 204}, result.text
    from langflow.services.database.models.auth.authz import AuthzAuditLog
    from sqlmodel import select

    expected = {
        "create": "flow:create",
        "duplicate": "flow:create",
        "move": "flow:write",
        "rename": "file:write",
        "delete": "file:delete",
    }[operation]
    async with session_scope() as session:
        decisions = (await session.exec(select(AuthzAuditLog).where(AuthzAuditLog.action == expected))).all()
        assert any(row.result in {"allow", "owner_override"} for row in decisions)


@pytest.mark.parametrize("operation", ["create", "import", "duplicate"])
async def test_owned_file_references_survive_all_creation_paths(files_editor, operation):
    from tests.unit.api.v1.test_platform_sso_editing import GRAPH, create

    client, actor, live, _ = files_editor
    file = (await upload(client)).json()
    graph = {**GRAPH, "attachment": {"type": "file", "value": [file["name"]], "file_path": [file["path"]]}}
    live["actions"] += ["import", "duplicate"]
    if operation == "create":
        response = await client.post("/api/v1/flows/", json={"name": "with-file", "data": graph})
    elif operation == "import":
        response = await client.post(
            "/api/v2/platform-workflow/import", json={"flows": [{"name": "with-file", "data": graph}]}
        )
    else:
        flow = await create(client)
        saved = await client.patch(
            f"/api/v1/flows/{flow['id']}", json={"data": graph}, headers={"If-Match": flow["version_token"]}
        )
        assert saved.status_code == 200, saved.text
        response = await client.post(f"/api/v2/platform-workflow/flows/{flow['id']}/duplicate", json={})
    assert response.status_code == 201, response.text
    result = response.json()[0] if operation == "import" else response.json()
    assert result["user_id"] == str(actor.id)
    assert result["data"]["attachment"] == graph["attachment"]


async def test_file_mutations_preserve_current_and_historical_references(files_editor):
    from uuid import UUID

    from langflow.services.database.models.flow.model import Flow
    from langflow.services.database.models.flow_version.model import FlowVersion

    client, actor, live, root = files_editor
    file = (await upload(client)).json()
    live["actions"] += ["file_update", "file_delete"]
    async with session_scope() as session:
        flow = Flow(name=uuid4().hex, user_id=actor.id, data={"file_path": file["path"]})
        session.add(flow)
        await session.flush()
        flow_id = flow.id
    path = f"{BASE}/{file['id']}"
    for history_only in [False, True]:
        if history_only:
            async with session_scope() as session:
                flow = await session.get(Flow, flow_id)
                session.add(FlowVersion(flow_id=flow_id, user_id=actor.id, version_number=1, data=flow.data))
                flow.data = {"nodes": [], "edges": []}
                session.add(flow)
        assert (await client.patch(path, json={"name": "changed.txt"})).status_code == 409
        assert (await client.delete(path)).status_code == 409
    assert (root / file["path"]).exists()
    async with session_scope() as session:
        assert (await session.get(UserFile, UUID(file["id"]))).name == file["name"]


async def test_delete_database_failure_restores_file(files_editor, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from sqlmodel.ext.asyncio.session import AsyncSession

    client, _, live, root = files_editor
    file = (await upload(client)).json()
    live["actions"].append("file_delete")
    original = AsyncSession.commit

    async def fail_delete(session):
        if any(isinstance(row, UserFile) for row in session.deleted):
            message = "test-only deletion failure"
            raise IntegrityError(message, {}, RuntimeError())
        return await original(session)

    monkeypatch.setattr(AsyncSession, "commit", fail_delete)
    response = await client.delete(f"{BASE}/{file['id']}")
    assert response.status_code == 503
    assert (root / file["path"]).read_bytes() == b"hello workflow"
    assert (await client.get(f"{BASE}/{file['id']}")).status_code == 200


async def test_upload_list_content_download_and_range(files_editor):
    client, actor, _, root = files_editor
    result = await upload(client)
    assert result.status_code == 201, result.text
    file = result.json()
    assert file["user_id"] == str(actor.id)
    assert (root / file["path"]).read_bytes() == b"hello workflow"
    assert (await client.get(BASE)).json() == [file]
    read = await client.get(f"{BASE}/{file['id']}/content")
    assert read.status_code == 200
    assert read.content == b"hello workflow"
    download = await client.get(f"{BASE}/{file['id']}")
    assert download.status_code == 200
    assert download.content == b"hello workflow"
    assert "filename*=" in download.headers["content-disposition"]
    assert download.headers["x-content-type-options"] == "nosniff"
    partial = await client.get(f"{BASE}/{file['id']}", headers={"Range": "bytes=2-5"})
    assert partial.status_code == 206
    assert partial.content == b"llo "
    assert partial.headers["content-range"] == "bytes 2-5/14"
    assert (await client.head(f"{BASE}/{file['id']}")).content == b""
    assert (await client.get(f"{BASE}/{file['id']}", headers={"Range": "bytes=100-"})).status_code == 416


async def test_file_actions_are_independent_and_revocation_preserves_content(files_editor):
    client, _, live, root = files_editor
    file = (await upload(client)).json()
    live["actions"] = ["read", "create", "update"]
    assert (await client.get(BASE)).status_code == 403
    assert (await upload(client, "denied.txt")).status_code == 403
    live["actions"] = ["read", "file_read"]
    assert (await client.get(BASE)).status_code == 200
    assert (await client.get(f"{BASE}/{file['id']}/content")).status_code == 200
    assert (await client.get(f"{BASE}/{file['id']}")).status_code == 403
    assert (await client.delete(f"{BASE}/{file['id']}")).status_code == 403
    live["active"] = False
    assert (await client.get(f"{BASE}/{file['id']}/content")).status_code == 401
    assert (root / file["path"]).read_bytes() == b"hello workflow"


@pytest.mark.parametrize(
    "name",
    [
        "../escape.txt",
        "folder\\escape.txt",
        "_mcp_servers.json",
        "_MCP_SERVERS_fake.json",
        "bad.html",
        "run.py",
        "archive.zip",
        "CON.txt",
    ],
)
async def test_unsafe_and_reserved_uploads_have_no_side_effects(files_editor, name):
    client, _, _, root = files_editor
    assert (await upload(client, name)).status_code in {400, 415}
    assert not list(root.rglob("*.*"))
    assert (await client.get(BASE)).json() == []


async def test_duplicate_name_never_overwrites_and_invalid_content_is_cleaned(files_editor):
    client, _, _, root = files_editor
    first = (await upload(client, "one.txt", b"original")).json()
    assert (await upload(client, "one.txt", b"overwrite")).status_code == 409
    assert (await upload(client, "fake.pdf", b"not pdf")).status_code == 415
    assert (await upload(client, "binary.txt", b"\x00\xff")).status_code == 415
    assert (root / first["path"]).read_bytes() == b"original"
    assert len(list(root.rglob("*.*"))) == 1


async def test_existing_foreign_file_and_tampered_namespace_are_hidden(files_editor):
    client, actor, _, _ = files_editor
    from langflow.services.database.models.user.model import User

    async with session_scope() as session:
        other = User(username=f"b4-other-{uuid4().hex}", password="unused", is_active=True)  # noqa: S106
        session.add(other)
        await session.flush()
        foreign = UserFile(user_id=other.id, name="foreign.txt", path=f"{other.id}/foreign.txt", size=7)
        tampered = UserFile(user_id=actor.id, name="tampered.txt", path=f"{other.id}/foreign.txt", size=7)
        session.add(foreign)
        session.add(tampered)
        await session.flush()
        ids = [foreign.id, tampered.id]
    for file_id in ids:
        assert (await client.get(f"{BASE}/{file_id}")).status_code == 404
        assert (await client.get(f"{BASE}/{file_id}/content")).status_code == 404
    assert (await client.get(BASE)).json() == []


async def test_chunked_overflow_disconnect_and_revoked_upload_are_cleaned(files_editor, monkeypatch):
    import asyncio

    from langflow.services.auth import platform_files

    client, _, live, root = files_editor
    monkeypatch.setattr(platform_files, "MAX_FILE_BYTES", 5)

    async def overflow():
        yield b"123"
        yield b"456"

    response = await upload(client, "overflow.txt", overflow())
    assert response.status_code == 413

    async def cancel():
        yield b"abc"
        raise asyncio.CancelledError

    # BaseHTTPMiddleware 在 ASGI 测试传输中将取消包装为 No response returned。
    with pytest.raises((RuntimeError, asyncio.CancelledError)) as cancelled:
        await upload(client, "cancelled.txt", cancel())
    if isinstance(cancelled.value, RuntimeError):
        assert str(cancelled.value) == "No response returned."

    async def revoke():
        yield b"abc"
        live["actions"] = ["read", "file_read"]

    response = await upload(client, "revoked.txt", revoke())
    assert response.status_code == 403
    assert (await client.get(BASE)).json() == []
    assert not [path for path in root.rglob("*") if path.is_file()]


async def test_csrf_old_paths_and_upload_options_stay_closed(files_editor):
    client, _, _, _ = files_editor
    denied = await client.post(
        BASE,
        content=b"x",
        headers={
            "Origin": "https://evil.example",
            "Content-Type": "application/octet-stream",
            "X-Workflow-Filename": "x.txt",
        },
    )
    assert denied.status_code == 403
    assert (await client.post(BASE + "?append=true", content=b"x")).status_code == 422
    assert (await client.post("/api/v2/files", files={"file": ("x.txt", b"x")})).status_code == 403
    assert (await client.post(BASE + "/batch/", json=[])).status_code == 403


async def test_session_revoked_during_upload_reports_expiry_and_cleans_files(files_editor):
    client, _, live, root = files_editor

    async def revoke_session():
        yield b"abc"
        live["active"] = False

    response = await upload(client, "expired.txt", revoke_session())
    assert response.status_code == 401
    assert response.json()["code"] == "PLATFORM_SSO_REQUIRED"
    assert response.headers["x-workflow-actions"] == ""
    assert not [path for path in root.rglob("*") if path.is_file()]


async def test_saved_graph_only_accepts_real_personal_file_references(files_editor):
    from tests.unit.api.v1.test_platform_sso_editing import builtin, create

    client, _, live, _ = files_editor
    file = (await upload(client)).json()
    flow = await create(client)
    node = await builtin(client, "ChatInput")
    node["template"]["files"]["value"] = [file["name"]]
    node["template"]["files"]["file_path"] = [file["path"]]
    graph = {"nodes": [{"id": "ChatInput-b4", "data": {"type": "ChatInput", "node": node}}], "edges": []}
    url = f"/api/v1/flows/{flow['id']}"
    response = await client.patch(url, json={"data": graph}, headers={"If-Match": flow["version_token"]})
    assert response.status_code == 200, response.text
    token = response.json()["version_token"]
    node["template"]["files"]["file_path"] = [f"{uuid4()}/foreign.txt"]
    rejected = await client.patch(url, json={"data": graph}, headers={"If-Match": token})
    assert rejected.status_code == 404
    assert (await client.get(url)).json()["data"]["nodes"][0]["data"]["node"]["template"]["files"]["file_path"] == [
        file["path"]
    ]
    node["template"]["files"]["file_path"] = [file["path"]]
    live["actions"] = ["read", "update"]
    assert (await client.patch(url, json={"data": graph}, headers={"If-Match": token})).status_code == 403


async def test_node_refresh_keeps_only_owned_file_pairs(files_editor):
    from tests.unit.api.v1.test_platform_sso_editing import builtin, create, refresh_payload

    client, _, _, _ = files_editor
    file = (await upload(client)).json()
    flow = await create(client)
    node = await builtin(client, "ChatInput")
    field = node["template"]["files"]
    field.update(value=[file["name"]], file_path=[file["path"]])
    url = f"/api/v1/custom_component/update?flow_id={flow['id']}"
    response = await client.post(url, json=refresh_payload(node, "input_value", "with files"))
    assert response.status_code == 200, response.text
    assert response.json()["template"]["files"]["file_path"] == [file["path"]]
    field["value"] = ["forged-name.txt"]
    assert (await client.post(url, json=refresh_payload(node, "input_value", "bad alias"))).status_code == 422
    field.update(type="str", value=[file["name"]])
    assert (await client.post(url, json=refresh_payload(node, "input_value", "disguised"))).status_code == 422


async def test_cross_tenant_cookie_cannot_list_read_or_upload(files_editor, monkeypatch):
    from langflow.services.auth import platform_sso

    client, _, _, root = files_editor
    file = (await upload(client)).json()
    original = platform_sso.platform_call

    async def wrong_tenant(*args):
        identity = await original(*args)
        return {**identity, "tenantId": "other-tenant"}

    monkeypatch.setattr(platform_sso, "platform_call", wrong_tenant)
    for path in [BASE, f"{BASE}/{file['id']}", f"{BASE}/{file['id']}/content"]:
        assert (await client.get(path)).status_code == 401
    assert (await upload(client, "wrong-tenant.txt")).status_code == 401
    assert (root / file["path"]).read_bytes() == b"hello workflow"
    assert len([path for path in root.rglob("*") if path.is_file()]) == 1


async def test_database_failure_removes_new_file(files_editor, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from sqlmodel.ext.asyncio.session import AsyncSession

    client, _, _, root = files_editor
    original = AsyncSession.commit

    async def fail_file_commit(session):
        if any(isinstance(row, UserFile) for row in session.new):
            message = "test collision"
            raise IntegrityError(message, {}, RuntimeError())
        return await original(session)

    monkeypatch.setattr(AsyncSession, "commit", fail_file_commit)
    assert (await upload(client, "failed.txt")).status_code == 409
    assert (await client.get(BASE)).json() == []
    assert not [path for path in root.rglob("*") if path.is_file()]


async def test_storage_failure_removes_partial_file(files_editor, monkeypatch):
    client, _, _, root = files_editor

    async def fail_write(*_args):
        message = "test storage unavailable"
        raise OSError(message)

    monkeypatch.setattr(anyio.AsyncFile, "write", fail_write)
    with pytest.raises(OSError, match="test storage unavailable"):
        await upload(client, "partial.txt")
    assert (await client.get(BASE)).json() == []
    assert not [path for path in root.rglob("*") if path.is_file()]


async def test_empty_text_binary_preview_and_metadata_mismatch(files_editor, monkeypatch):
    from langflow.services.auth import platform_files

    client, _, _, root = files_editor
    empty = (await upload(client, "empty.txt", b"")).json()
    assert empty["size"] == 0
    assert (await client.get(f"{BASE}/{empty['id']}/content")).content == b""
    image = (await upload(client, "photo.png", b"\x89PNG\r\n\x1a\n")).json()
    assert (await client.get(f"{BASE}/{image['id']}/content")).status_code == 415
    assert (await client.get(f"{BASE}/{image['id']}")).content == b"\x89PNG\r\n\x1a\n"
    text = (await upload(client, "large.txt", b"123")).json()
    monkeypatch.setattr(platform_files, "PREVIEW_BYTES", 2)
    assert (await client.get(f"{BASE}/{text['id']}/content")).status_code == 415
    (root / text["path"]).write_bytes(b"changed")
    assert (await client.get(f"{BASE}/{text['id']}")).status_code == 409
