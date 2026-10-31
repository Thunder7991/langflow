"""Personal file policy for platform SSO; never delegates ownership to share plugins."""

from pathlib import PurePosixPath
from uuid import UUID

from fastapi import HTTPException, Request
from sqlmodel import select

from langflow.services.auth.platform_workflows import context, require_action
from langflow.services.database.models.file.model import File as UserFile
from langflow.services.storage.local import LocalStorageService

MAX_FILE_BYTES = 20 * 1024 * 1024
PREVIEW_BYTES = 256 * 1024
CHUNK_BYTES = 64 * 1024
MAX_REFERENCES = 100
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json"}
EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def authorize(request: Request, user_id: UUID, action: str) -> dict:
    identity = context(request)
    if identity is None or identity["user_id"] != str(user_id):
        raise HTTPException(403, "Personal files require platform SSO")
    require_action(request, "file_read")
    require_action(request, action)
    return identity


def is_personal_file(file: UserFile, user_id: UUID) -> bool:
    parts = file.path.split("/")
    return (
        file.user_id == user_id
        and len(parts) == 2  # noqa: PLR2004 - user UUID plus one basename
        and parts[0] == str(user_id)
        and "\\" not in file.path
        and ".." not in parts[1]
        and PurePosixPath(file.path).suffix.lower() in EXTENSIONS
        and not file.name.casefold().startswith("_mcp_servers")
    )


async def local_path(storage, file: UserFile):
    if not isinstance(storage, LocalStorageService):
        raise HTTPException(503, "Platform personal files currently require local storage")
    if not is_personal_file(file, file.user_id):
        raise HTTPException(404, "File not found")
    base = await storage.data_dir.resolve()
    path = storage.data_dir / file.path
    # 同时约束到本人目录, 防止目录符号链接落到另一用户的存储空间。
    if not (await path.resolve()).is_relative_to(base / str(file.user_id)):
        raise HTTPException(404, "File not found")
    return path


async def owned_file(session, file_id: UUID, user_id: UUID, storage) -> UserFile:
    file = (await session.exec(select(UserFile).where(UserFile.id == file_id, UserFile.user_id == user_id))).first()
    if file is None or not is_personal_file(file, user_id):
        raise HTTPException(404, "File not found")
    path = await local_path(storage, file)
    if not await path.is_file():
        raise HTTPException(404, "File not found")
    return file


async def validate_references(request: Request, session, data: dict | None, user_id: UUID) -> set[str]:
    fields = []

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if value.get("file_path") and value.get("type") != "file":
                raise HTTPException(422, "File paths must belong to a file input")
            if value.get("type") == "file" and (value.get("value") or value.get("file_path")):
                fields.append(value)
            for item in value.values():
                visit(item)

    visit(data)
    if not fields:
        return set()
    authorize(request, user_id, "file_read")
    paths = set()
    for field in fields:
        supplied = field.get("file_path")
        if not isinstance(supplied, list) or not supplied or not all(isinstance(path, str) for path in supplied):
            raise HTTPException(422, "File input requires personal file_path references")
        paths.update(supplied)
    if len(paths) > MAX_REFERENCES:
        raise HTTPException(422, "Too many file references")
    rows = (await session.exec(select(UserFile).where(UserFile.user_id == user_id, UserFile.path.in_(paths)))).all()
    from langflow.services.deps import get_storage_service

    known = {}
    for row in rows:
        if is_personal_file(row, user_id) and await (await local_path(get_storage_service(), row)).is_file():
            known[row.path] = row.name
    if paths != known.keys():
        raise HTTPException(404, "Referenced file not found")
    for field in fields:
        if field.get("value") != [known[path] for path in field["file_path"]]:
            raise HTTPException(422, "File names do not match personal references")
    return paths
