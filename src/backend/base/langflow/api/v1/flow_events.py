from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlmodel import or_, select

from langflow.api.utils import CurrentActiveUser, DbSession
from langflow.services.database.models.flow.model import Flow
from langflow.services.deps import get_flow_events_service
from langflow.services.flow_events import FLOW_EVENT_TYPES, FlowEventsService

router = APIRouter(prefix="/flows", tags=["Flow Events"], include_in_schema=False)


class FlowEventResponse(BaseModel):
    type: str
    timestamp: float
    summary: str


class FlowEventsResponse(BaseModel):
    events: list[FlowEventResponse]
    settled: bool


class FlowEventCreate(BaseModel):
    type: FLOW_EVENT_TYPES
    summary: str = Field(default="", max_length=500)


async def _verify_flow_owner(session: DbSession, flow_id: UUID, user_id: UUID) -> None:
    result = await session.exec(
        select(Flow).where(
            Flow.id == flow_id,
            or_(Flow.user_id == user_id, Flow.user_id == None),  # noqa: E711
        )
    )
    if not result.first():
        raise HTTPException(status_code=404, detail="Flow not found")


@router.get("/{flow_id}/events", response_model=FlowEventsResponse)
async def get_flow_events(
    request: Request,
    flow_id: UUID,
    current_user: CurrentActiveUser,
    session: DbSession,
    since: Annotated[float, Query(description="UTC timestamp to get events after")] = 0.0,
    *,
    service: Annotated[FlowEventsService, Depends(get_flow_events_service)],
) -> FlowEventsResponse:
    from langflow.services.auth.platform_workflows import context

    if context(request) is not None:
        owned = await session.get(Flow, flow_id)
        if owned is None or owned.user_id != current_user.id or owned.workspace_id is not None:
            raise HTTPException(404, "Flow not found")
    await _verify_flow_owner(session, flow_id, current_user.id)
    events, settled = service.get_since(str(flow_id), since)
    return FlowEventsResponse(
        events=[FlowEventResponse(type=e.type, timestamp=e.timestamp, summary=e.summary) for e in events],
        settled=settled,
    )


@router.post("/{flow_id}/events", response_model=FlowEventResponse, status_code=201)
async def create_flow_event(
    flow_id: UUID,
    event: FlowEventCreate,
    current_user: CurrentActiveUser,
    session: DbSession,
    *,
    service: Annotated[FlowEventsService, Depends(get_flow_events_service)],
) -> FlowEventResponse:
    """Append an event to a flow's event queue."""
    await _verify_flow_owner(session, flow_id, current_user.id)
    e = service.append(str(flow_id), event.type, event.summary)
    return FlowEventResponse(type=e.type, timestamp=e.timestamp, summary=e.summary)
