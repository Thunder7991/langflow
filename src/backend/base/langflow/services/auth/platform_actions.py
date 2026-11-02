"""Explicit platform operation contract; unknown endpoints stay closed."""

import re
from uuid import UUID

from fastapi import HTTPException

ACTIONS = {
    "read",
    "create",
    "update",
    "delete",
    "duplicate",
    "import",
    "export",
    "move",
    "project_create",
    "project_update",
    "project_delete",
    "project_import",
    "project_export",
    "file_read",
    "file_upload",
    "file_download",
    "file_update",
    "file_delete",
    "version_read",
    "version_create",
    "version_restore",
    "version_export",
    "version_delete",
    "dry_run",
    "run",
    "stop",
    "publish",
    "share",
    "api_access",
}

UUID_PATTERN = r"[0-9a-fA-F-]{36}"


def route_action(method: str, path: str) -> str | None:
    routes = [
        ("DELETE", rf"/api/v1/flows/{UUID_PATTERN}", "delete"),
        ("DELETE", r"/api/v1/flows/", "delete"),
        ("POST", r"/api/v1/flows/download/", "export"),
        ("POST", r"/api/v1/flows/upload/", "import"),
        ("POST", r"/api/v1/projects/upload/", "project_import"),
        ("PATCH", rf"/api/v1/projects/{UUID_PATTERN}", "project_update"),
        ("DELETE", rf"/api/v1/projects/{UUID_PATTERN}", "project_delete"),
        ("GET", rf"/api/v1/projects/download/{UUID_PATTERN}", "project_export"),
        ("POST", rf"/api/v1/flows/{UUID_PATTERN}/versions/?", "version_create"),
        ("POST", rf"/api/v1/flows/{UUID_PATTERN}/versions/{UUID_PATTERN}/activate", "version_restore"),
        ("DELETE", rf"/api/v1/flows/{UUID_PATTERN}/versions/{UUID_PATTERN}", "version_delete"),
        ("PATCH", rf"/api/v2/platform-files/{UUID_PATTERN}", "file_update"),
        ("DELETE", rf"/api/v2/platform-files/{UUID_PATTERN}", "file_delete"),
        ("POST", r"/api/v2/platform-workflow/import", "import"),
        ("POST", r"/api/v2/platform-workflow/projects/import", "project_import"),
        ("POST", rf"/api/v2/platform-workflow/flows/{UUID_PATTERN}/duplicate", "duplicate"),
        ("GET", rf"/api/v2/platform-workflow/flows/{UUID_PATTERN}/export", "export"),
        ("GET", rf"/api/v2/platform-workflow/flows/{UUID_PATTERN}/versions/{UUID_PATTERN}/export", "version_export"),
        ("POST", rf"/api/v2/platform-workflow/flows/{UUID_PATTERN}/publish", "publish"),
        ("GET", rf"/api/v2/platform-workflow/flows/{UUID_PATTERN}/share", "share"),
        ("GET", rf"/api/v2/platform-workflow/flows/{UUID_PATTERN}/api", "api_access"),
        ("POST", rf"/api/v2/platform-workflow/flows/{UUID_PATTERN}/run", "run"),
        ("POST", rf"/api/v2/platform-workflow/flows/{UUID_PATTERN}/runs/{UUID_PATTERN}/stop", "stop"),
    ]
    return next((action for verb, pattern, action in routes if method == verb and re.fullmatch(pattern, path)), None)


async def check_native_owner(path: str, user_id: str, actions: list[str], method: str):
    """Keep platform resources personal even when a native authz plugin widens fetches."""
    from langflow.services.auth.platform_workflows import require_personal_destination
    from langflow.services.database.models.flow.model import Flow
    from langflow.services.database.models.folder.model import Folder
    from langflow.services.deps import session_scope

    match = re.fullmatch(rf"/api/v1/(flows|projects)/(?:download/)?({UUID_PATTERN})(?:/.*)?", path)
    if not match:
        return
    model = Flow if match[1] == "flows" else Folder
    async with session_scope() as session:
        row = await session.get(model, UUID(match[2]))
        if row is None or str(row.user_id) != user_id or row.workspace_id is not None:
            raise HTTPException(404, "Resource not found")
        if model is Flow:
            await require_personal_destination(session, row.folder_id, UUID(user_id))
        elif method == "DELETE" or "/download/" in path:
            from sqlmodel import select

            flows = (await session.exec(select(Flow).where(Flow.folder_id == row.id))).all()
            if method == "DELETE" and flows and "delete" not in actions:
                raise HTTPException(403, "Deleting a nonempty project also requires workflow delete permission")
            if row.auth_settings or any(
                flow.user_id != UUID(user_id) or flow.workspace_id is not None for flow in flows
            ):
                raise HTTPException(403, "Only personal unmanaged projects can be deleted")
        elif method == "PATCH" and row.auth_settings:
            raise HTTPException(403, "Only personal unmanaged projects can be updated")


async def validate_graph_references(request, session, data, user_id):
    from langflow.services.auth.platform_files import validate_references
    from langflow.services.auth.platform_models import validate_references as validate_models
    from langflow.services.auth.platform_workflows import context, validate_graph

    if context(request) is None:
        return
    verified = await validate_references(request, session, data, user_id)
    validate_graph(data, verified_files=verified)
    await validate_models(request, data)
