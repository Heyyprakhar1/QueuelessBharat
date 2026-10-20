"""Queue lifecycle, observation, and status estimation schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import EstimateConfidence, ObservationSource, QueueSessionStatus
from app.schemas.service import ServiceResponse


class CheckInRequest(BaseModel):
    """Payload to check into a service queue."""

    model_config = ConfigDict(str_strip_whitespace=True)

    service_id: uuid.UUID
    user_id: str | None = Field(
        default=None,
        max_length=100,
        description="Optional client-provided user identifier (no auth subsystem required)",
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description="Client-generated key to prevent duplicate check-ins",
    )


class StartServiceRequest(BaseModel):
    """Payload to initiate active service for a waiting queue participant."""

    model_config = ConfigDict(str_strip_whitespace=True)

    counter_id: uuid.UUID | None = Field(
        default=None,
        description="Optional counter window serving the participant",
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description="Client-generated key to prevent duplicate service starts",
    )


class CompleteServiceRequest(BaseModel):
    """Payload to complete an active service session."""

    model_config = ConfigDict(str_strip_whitespace=True)

    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description="Client-generated key to prevent duplicate service completions",
    )


class CancelQueueRequest(BaseModel):
    """Payload to cancel a waiting queue session."""

    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str | None = Field(default=None, max_length=255)
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description="Client-generated key to prevent duplicate cancellation commands",
    )


class QueueSessionResponse(BaseModel):
    """Public representation of an individual participant's queue session."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service_id: uuid.UUID
    user_id: str | None
    user_uuid: uuid.UUID | None = None
    status: QueueSessionStatus
    position: int | None = Field(
        default=None,
        description="1-based position in waiting line if waiting",
    )
    checked_in_at: datetime
    service_started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WaitObservationCreate(BaseModel):
    """Payload to submit a point-in-time queue condition observation."""

    people_ahead: int = Field(
        ...,
        ge=0,
        le=5000,
        description="Observed people waiting in line (up to 5,000)",
    )
    reported_wait_minutes: float | None = Field(
        default=None,
        ge=0.0,
        le=1440.0,
        description="Self-reported wait time in minutes (up to 24 hours / 1,440 minutes)",
    )
    active_counters: int = Field(
        ...,
        ge=0,
        le=100,
        description="Observed active service windows (up to 100)",
    )
    source: ObservationSource = Field(
        default=ObservationSource.USER_REPORTED,
        description="Source channel (SYSTEM or USER_REPORTED)",
    )


class WaitObservationResponse(BaseModel):
    """Public representation of an observed queue state."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service_id: uuid.UUID
    observed_at: datetime
    people_ahead: int
    reported_wait_minutes: float | None
    active_counters: int
    source: str
    created_at: datetime


class WaitEstimatePayload(BaseModel):
    """Structured representation of a derived deterministic wait estimate."""

    is_available: bool
    lower_minutes: float | None = None
    upper_minutes: float | None = None
    expected_minutes: float | None = None
    confidence: EstimateConfidence | None = None
    sample_size: int = 0
    freshness_seconds: int | None = None
    model_version: str = "v1-deterministic"
    reason: str | None = None
    supporting_signals: dict[str, Any] = Field(default_factory=dict)


class QueueStateSummary(BaseModel):
    """Operational queue state metrics."""

    waiting_count: int
    in_service_count: int
    active_counters: int


class QueueStatusResponse(BaseModel):
    """Comprehensive queue state and deterministic wait projection response."""

    service: ServiceResponse
    queue_state: QueueStateSummary
    people_ahead: int
    active_counters: int
    estimated_wait: WaitEstimatePayload
    is_simulated: bool = Field(
        default=True,
        description=(
            "Indicates whether queue metrics are demo/simulated rather than live facility telemetry"
        ),
    )


class UserQueueStatusResponse(BaseModel):
    """User-centric queue status including personal position and wait estimate."""

    session: QueueSessionResponse
    service: ServiceResponse
    position: int | None = Field(
        default=None,
        description="1-based position in waiting line; None if in service, completed, or cancelled",
    )
    people_ahead: int = Field(
        default=0,
        description="Number of participants waiting ahead of this specific user",
    )
    queue_state: QueueStateSummary
    estimated_wait: WaitEstimatePayload
    is_simulated: bool = Field(
        default=True,
        description=(
            "Indicates whether queue metrics are demo/simulated rather than live facility telemetry"
        ),
    )


class OperatorDeskResponse(BaseModel):
    """Operational desk status including waiting queue and seated participant."""

    service: ServiceResponse
    active_session: QueueSessionResponse | None = None
    waiting_sessions: list[QueueSessionResponse] = Field(default_factory=list)
    waiting_count: int = 0
