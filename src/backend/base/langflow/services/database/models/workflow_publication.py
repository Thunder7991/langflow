"""Independent immutable workflow releases and durable execution references."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql.naming import conv
from sqlmodel import JSON, Field, SQLModel


class WorkflowPublication(SQLModel, table=True):
    __tablename__ = "workflow_publication"
    __table_args__ = (
        UniqueConstraint("flow_id", "version_number", name="uq_workflow_publication_version"),
        CheckConstraint("version_number > 0", name=conv("ck_workflow_publication_version_positive")),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    flow_id: UUID = Field(sa_column=Column(ForeignKey("flow.id", ondelete="RESTRICT"), nullable=False, index=True))
    version_number: int
    source_version_token: UUID
    data: dict = Field(sa_column=Column(JSON, nullable=False))
    digest: str = Field(max_length=64)
    input_schema: dict = Field(sa_column=Column(JSON, nullable=False))
    output_schema: dict = Field(sa_column=Column(JSON, nullable=False))
    dependencies: dict = Field(sa_column=Column(JSON, nullable=False))
    created_by: UUID
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class WorkflowPublicationState(SQLModel, table=True):
    __tablename__ = "workflow_publication_state"

    flow_id: UUID = Field(sa_column=Column(ForeignKey("flow.id", ondelete="RESTRICT"), primary_key=True))
    publication_id: UUID | None = Field(
        default=None, sa_column=Column(ForeignKey("workflow_publication.id", ondelete="RESTRICT"), nullable=True)
    )
    token: UUID = Field(default_factory=uuid4)
    latest_version: int = Field(default=0)


class WorkflowPublicationRun(SQLModel, table=True):
    __tablename__ = "workflow_publication_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name=conv("ck_workflow_publication_run_status"),
        ),
    )

    id: UUID = Field(primary_key=True)
    publication_id: UUID = Field(
        sa_column=Column(ForeignKey("workflow_publication.id", ondelete="RESTRICT"), nullable=False, index=True)
    )
    created_by: UUID = Field(index=True)
    status: str = Field(default="running", max_length=16)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
