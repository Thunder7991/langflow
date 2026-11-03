"""Real database races: reference writes and file mutations cannot both succeed."""

# ruff: noqa: F811 - imported database fixture is injected by pytest.

import asyncio
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from langflow.api.v2.platform_files import RenameFileBody, delete_personal_file, rename_personal_file
from langflow.services.auth import platform_files
from langflow.services.database.models.file.model import File as UserFile
from langflow.services.database.models.flow.model import Flow
from langflow.services.database.models.flow_version.model import FlowVersion
from langflow.services.database.models.user.model import User
from langflow.services.storage.local import LocalStorageService
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.requests import Request

from tests.unit.services.database.models.flow_version.test_delete_attachment_concurrency import db_engine  # noqa: F401


@pytest.fixture
def event_loop_policy():
    # psycopg 异步驱动在 Windows 上要求 Selector, 只改变本模块的验收循环。
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.mark.parametrize("db_engine", ["sqlite", "postgres", "postgres-repeatable-read"], indirect=True)
@pytest.mark.parametrize("mutation", ["rename", "delete"])
@pytest.mark.parametrize("history", [False, True])
async def test_reference_transaction_blocks_file_mutation_until_commit(
    db_engine, tmp_path, monkeypatch, mutation, history, request
):
    if request.node.callspec.params["db_engine"] == "postgres-repeatable-read":
        db_engine = db_engine.execution_options(isolation_level="REPEATABLE READ")
    monkeypatch.setenv("LANGFLOW_AUTHZ_ENABLED", "false")
    storage = LocalStorageService(None, SimpleNamespace(settings=SimpleNamespace(config_dir=tmp_path)))
    monkeypatch.setattr("langflow.services.deps.get_storage_service", lambda: storage)
    actor = User(id=uuid4(), username=uuid4().hex, password="test-only", is_active=True)  # noqa: S106
    row = UserFile(user_id=actor.id, name="race.txt", path=f"{actor.id}/race.txt", size=4)
    flow = Flow(name="file race", user_id=actor.id, data={"nodes": [], "edges": []})
    async with AsyncSession(db_engine) as session, session.begin():
        session.add(actor)
        await session.flush()
        session.add(row)
        session.add(flow)
        await session.flush()
        actor_id, file_id, flow_id, path = actor.id, row.id, flow.id, row.path
    actor = User(id=actor_id, username="race", password="test-only", is_active=True)  # noqa: S106
    physical = tmp_path / path
    physical.parent.mkdir(exist_ok=True)
    physical.write_bytes(b"data")
    request = Request(
        {
            "type": "http",
            "headers": [],
            "state": {
                "platform_workflow": {
                    "user_id": str(actor_id),
                    "actions": ["file_read", "file_update", "file_delete"],
                }
            },
        }
    )
    graph = {"attachment": {"type": "file", "value": ["race.txt"], "file_path": [path]}}

    async def mutate(session):
        if mutation == "rename":
            return await rename_personal_file(
                file_id, RenameFileBody(name="renamed.txt"), request, actor, session, storage
            )
        return await delete_personal_file(file_id, request, actor, session, storage)

    async with AsyncSession(db_engine) as writer:
        await platform_files.validate_references(request, writer, graph, actor_id)
        # 引用已检查但尚未持久化, 模拟真实保存请求在校验与提交之间被另一 worker 抢占。
        async with AsyncSession(db_engine) as remover:
            with pytest.raises(HTTPException) as busy:
                await mutate(remover)
            assert busy.value.status_code == 503
            await remover.rollback()
        if history:
            writer.add(FlowVersion(flow_id=flow_id, user_id=actor_id, version_number=1, data=graph))
        else:
            saved = await writer.get(Flow, flow_id)
            saved.data = graph
            writer.add(saved)
        await writer.commit()
    async with AsyncSession(db_engine) as remover:
        with pytest.raises(HTTPException) as referenced:
            await mutate(remover)
        assert referenced.value.status_code == 409
        await remover.rollback()
    assert physical.read_bytes() == b"data"


@pytest.mark.parametrize("db_engine", ["postgres"], indirect=True)
async def test_repeatable_read_rejects_stale_snapshot_after_reference_commit(db_engine):
    from sqlmodel import select

    db_engine = db_engine.execution_options(isolation_level="REPEATABLE READ")
    actor = User(username=uuid4().hex, password="test-only", is_active=True)  # noqa: S106
    async with AsyncSession(db_engine) as seed:
        seed.add(actor)
        await seed.flush()
        user_id = actor.id
        await seed.commit()
    async with AsyncSession(db_engine) as stale:
        # 先建立旧快照, 再让另一事务完成引用写入; 排他锁本身不能刷新 RR 快照。
        await stale.exec(select(User.id).where(User.id == user_id))
        async with AsyncSession(db_engine) as writer:
            await platform_files.lock_personal_files(writer, user_id)
            writer.add(Flow(name="reference committed", user_id=user_id, data={"file_path": "owned/file.txt"}))
            await writer.commit()
        with pytest.raises(HTTPException) as busy:
            await platform_files.lock_personal_files(stale, user_id)
        assert busy.value.status_code == 503
        await stale.rollback()
