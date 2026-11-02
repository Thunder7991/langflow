"""Bounded raw uploads and owner-only file reads for the platform iframe."""

import codecs
from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import unquote_to_bytes
from uuid import UUID, uuid4

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from starlette.requests import ClientDisconnect

from langflow.api.utils import CurrentActiveUser, DbSession, build_content_disposition, release_db_transaction
from langflow.api.v2.files import _validate_file_name
from langflow.services.auth import platform_files as policy
from langflow.services.authorization import FileAction, ensure_file_permission
from langflow.services.database.models.file.model import File as UserFile
from langflow.services.deps import get_settings_service, get_storage_service
from langflow.services.storage.local import LocalStorageService
from langflow.services.storage.service import StorageService

router = APIRouter(prefix="/platform-files", tags=["Platform personal files"])
Storage = Annotated[StorageService, Depends(get_storage_service)]


def max_bytes():
    return min(policy.MAX_FILE_BYTES, int(get_settings_service().settings.max_file_size_upload * 1024 * 1024))


@router.get("/policy")
async def file_policy(request: Request, current_user: CurrentActiveUser):
    policy.authorize(request, current_user.id, "file_read")
    if not isinstance(get_storage_service(), LocalStorageService):
        raise HTTPException(503, "Platform personal files currently require local storage")
    return {"max_bytes": max_bytes(), "extensions": sorted(policy.EXTENSIONS), "preview_bytes": policy.PREVIEW_BYTES}


@router.get("")
async def list_personal_files(request: Request, current_user: CurrentActiveUser, session: DbSession, storage: Storage):
    policy.authorize(request, current_user.id, "file_read")
    if not isinstance(storage, LocalStorageService):
        raise HTTPException(503, "Platform personal files currently require local storage")
    await ensure_file_permission(current_user, FileAction.READ, file_user_id=current_user.id)
    rows = (
        await session.exec(
            select(UserFile).where(UserFile.user_id == current_user.id).order_by(UserFile.created_at.desc())
        )
    ).all()
    return [row for row in rows if policy.is_personal_file(row, current_user.id)]


@router.post("", status_code=201)
async def upload_personal_file(request: Request, current_user: CurrentActiveUser, session: DbSession, storage: Storage):
    identity = policy.authorize(request, current_user.id, "file_upload")
    await ensure_file_permission(current_user, FileAction.CREATE, file_user_id=current_user.id)
    if request.query_params:
        raise HTTPException(422, "Append, ephemeral and custom upload options are not enabled")
    if request.headers.get("content-type", "").split(";")[0] != "application/octet-stream":
        raise HTTPException(415, "Use a raw single-file upload")
    try:
        name = unquote_to_bytes(request.headers.get("x-workflow-filename", "")).decode("utf-8")
    except UnicodeError as exc:
        raise HTTPException(400, "Invalid filename encoding") from exc
    name = _validate_file_name(name)
    extension = PurePosixPath(name).suffix.lower()
    if name.casefold().startswith("_mcp_servers") or extension not in policy.EXTENSIONS:
        raise HTTPException(415, "This file type or reserved filename is not enabled")
    limit = max_bytes()
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > limit):
        raise HTTPException(413, "File exceeds upload limit")
    if (
        await session.exec(select(UserFile.id).where(UserFile.user_id == current_user.id, UserFile.name == name))
    ).first():
        raise HTTPException(409, "A file with this name already exists; rename it before uploading")
    # 网络上传期间不占用数据库连接; 同名并发最终由既有唯一索引裁决。
    await release_db_transaction(session)
    file_id = uuid4()
    row = UserFile(
        id=file_id, user_id=current_user.id, name=name, path=f"{current_user.id}/{file_id}{extension}", size=0
    )
    path = await policy.local_path(storage, row)
    await path.parent.mkdir(parents=True, exist_ok=True)
    committed, created = False, False
    output = None
    try:
        output = await path.open("xb")
        created = True
        decoder = codecs.getincrementaldecoder("utf-8")() if extension in policy.TEXT_EXTENSIONS else None
        prefix = b""
        async for chunk in request.stream():
            row.size += len(chunk)
            if row.size > limit:
                raise HTTPException(413, "File exceeds upload limit")
            prefix = (prefix + chunk[:16])[:16]
            if decoder:
                try:
                    decoder.decode(chunk)
                except UnicodeError as exc:
                    raise HTTPException(415, "Text files must use UTF-8") from exc
                if b"\x00" in chunk:
                    raise HTTPException(415, "Binary content is not a text file")
            # ASGI chunks can be large in tests/proxies; disk writes stay bounded as well.
            for start in range(0, len(chunk), policy.CHUNK_BYTES):
                await output.write(chunk[start : start + policy.CHUNK_BYTES])
        if decoder:
            try:
                decoder.decode(b"", final=True)
            except UnicodeError as exc:
                raise HTTPException(415, "Text files must use UTF-8") from exc
        signatures = {".pdf": b"%PDF-", ".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff"}
        if extension in signatures and not prefix.startswith(signatures[extension]):
            raise HTTPException(415, "File signature does not match its extension")
        if extension == ".webp" and not (prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP"):
            raise HTTPException(415, "Invalid WebP file")
        # 长上传结束后再次确认平台关联和能力, 撤权不能利用旧请求完成落库。
        try:
            actions = await identity["recheck"]()
        except HTTPException as exc:
            # FastAPI 会提前处理 HTTPException; 在这里保持与 SSO 中间件相同的宿主失效协议。
            return JSONResponse(
                {
                    "detail": exc.detail,
                    "code": "PLATFORM_SSO_REQUIRED"
                    if exc.status_code == status.HTTP_401_UNAUTHORIZED
                    else "PLATFORM_SSO_ERROR",
                    "hostOrigin": identity["host_origin"],
                },
                status_code=exc.status_code,
                headers={"Cache-Control": "no-store"},
            )
        if not {"file_read", "file_upload"}.issubset(actions):
            raise HTTPException(403, "File upload permission was revoked")
        await output.aclose()
        output = None
        session.add(row)
        # 数据库提交与成功标记为一个取消屏障, 避免正常提交后又误删文件。
        with anyio.CancelScope(shield=True):
            await session.commit()
            committed = True
    except IntegrityError as exc:
        raise HTTPException(409, "A file with this name already exists") from exc
    except ClientDisconnect as exc:
        raise HTTPException(499, "Upload cancelled") from exc
    finally:
        # 取消、超限、内容不符和数据库失败均关闭句柄并清理本次独占文件。
        with anyio.CancelScope(shield=True):
            if output is not None:
                await output.aclose()
            if not committed:
                try:
                    await session.rollback()
                finally:
                    if created:
                        await path.unlink(missing_ok=True)
    return row


@router.api_route("/{file_id}", methods=["GET", "HEAD"])
@router.api_route("/{file_id}/content", methods=["GET", "HEAD"])
async def read_personal_file(
    file_id: UUID, request: Request, current_user: CurrentActiveUser, session: DbSession, storage: Storage
):
    preview = request.url.path.endswith("/content")
    policy.authorize(request, current_user.id, "file_read" if preview else "file_download")
    row = await policy.owned_file(session, file_id, current_user.id, storage)
    await ensure_file_permission(current_user, FileAction.READ, file_id=row.id, file_user_id=row.user_id)
    if preview and (
        PurePosixPath(row.path).suffix.lower() not in policy.TEXT_EXTENSIONS or row.size > policy.PREVIEW_BYTES
    ):
        raise HTTPException(415, "Only small UTF-8 text files support preview; use download")
    path = await policy.local_path(storage, row)
    if (await path.stat()).st_size != row.size:
        raise HTTPException(409, "Stored file size does not match metadata")
    return FileResponse(
        str(path),
        media_type="text/plain; charset=utf-8" if preview else "application/octet-stream",
        headers={
            "Content-Disposition": build_content_disposition(row.name),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


class RenameFileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)


async def require_unreferenced(session, row, user_id):
    import json

    from langflow.services.database.models.flow.model import Flow
    from langflow.services.database.models.flow_version.model import FlowVersion

    # 修改显示名会改变图中的文件名校验;历史版本同样要能恢复,不能静默留下失效引用。
    for model in (Flow, FlowVersion):
        graphs = (await session.exec(select(model.data).where(model.user_id == user_id))).all()
        if any(row.path in json.dumps(graph, ensure_ascii=False) for graph in graphs):
            raise HTTPException(409, "文件被流程或历史版本引用,请先移除引用后再修改或删除。")


@router.patch("/{file_id}")
async def rename_personal_file(
    file_id: UUID,
    body: RenameFileBody,
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage: Storage,
):
    policy.authorize(request, current_user.id, "file_update")
    row = await policy.owned_file(session, file_id, current_user.id, storage)
    await ensure_file_permission(current_user, FileAction.WRITE, file_id=row.id, file_user_id=row.user_id)
    name = _validate_file_name(body.name)
    if PurePosixPath(name).suffix.lower() != PurePosixPath(row.path).suffix.lower():
        raise HTTPException(422, "重命名不能改变文件类型。")
    if name != row.name:
        await require_unreferenced(session, row, current_user.id)
    row.name = name
    session.add(row)
    await session.flush()
    return {"id": str(row.id), "name": row.name, "path": row.path, "size": row.size}


@router.delete("/{file_id}", status_code=204)
async def delete_personal_file(
    file_id: UUID,
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage: Storage,
):
    policy.authorize(request, current_user.id, "file_delete")
    row = await policy.owned_file(session, file_id, current_user.id, storage)
    await ensure_file_permission(current_user, FileAction.DELETE, file_id=row.id, file_user_id=row.user_id)
    await require_unreferenced(session, row, current_user.id)
    path = await policy.local_path(storage, row)
    pending = path.with_name(f".{path.name}.{uuid4().hex}.delete")
    # 同目录暂存保证数据库失败时可恢复原文件;取消不能打断补偿过程。
    with anyio.CancelScope(shield=True):
        try:
            await path.rename(pending)
        except OSError as exc:
            raise HTTPException(503, "文件存储暂时不可写,请稍后重试。") from exc
        try:
            await session.delete(row)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await pending.rename(path)
            raise HTTPException(503, "文件删除未完成,原文件已保留,请稍后重试。") from exc
        try:
            await pending.unlink()
        except OSError:
            from lfx.log.logger import logger

            # 数据库已提交,残留暂存文件没有 API 入口;记录清理需求,不能误报删除未发生。
            logger.warning("Platform file removed; quarantined storage requires cleanup")
