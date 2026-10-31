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
