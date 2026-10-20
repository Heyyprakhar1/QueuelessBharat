"""Tests verifying authoritative counter precedence and freshness constraints
in EstimationService.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.enums import CounterStatus
from app.domain.models import Counter, Location, Service, WaitObservation
from app.services.estimation_service import EstimationService


@pytest.fixture
async def sample_service(db_session):
    """Creates a sample location and service."""
    location = Location(
        name="Precedence Test Hospital",
        category="hospital",
        address="1 Precedence Way",
        city="Pune",
        state="Maharashtra",
    )
    db_session.add(location)
    await db_session.flush()

    service = Service(
        location_id=location.id,
        name="Precedence OPD",
        status="active",
    )
    db_session.add(service)
    await db_session.commit()
    await db_session.refresh(service)
    return service


class TestCounterObservationPrecedence:
    """Verifies that DB counters strictly take precedence over observations."""

    async def test_registered_active_counter_takes_precedence_over_observation(
        self, db_session, sample_service
    ):
        """When 1 counter is ACTIVE in DB, it overrides an observation claiming 4 counters."""
        service = sample_service
        now = datetime.now(UTC)

        # 1 active counter in DB
        counter = Counter(
            service_id=service.id,
            name="Counter 1",
            status=CounterStatus.ACTIVE,
        )
        db_session.add(counter)

        # Observation claiming 4 active counters
        obs = WaitObservation(
            service_id=service.id,
            people_ahead=5,
            source="USER_REPORTED",
            active_counters=4,
            reported_wait_minutes=15.0,
            observed_at=now,
        )
        db_session.add(obs)
        await db_session.commit()

        estimation_svc = EstimationService(db_session)
        eff_counters, _ = await estimation_svc._resolve_effective_counters(service.id, now)
        assert eff_counters == 1  # Authoritative DB counter, NOT the 4 from observation

    async def test_zero_active_registered_counters_not_resurrected_by_observation(
        self, db_session, sample_service
    ):
        """When registered counters are INACTIVE (0 active), observations
        CANNOT resurrect counters.
        """
        service = sample_service
        now = datetime.now(UTC)

        # Registered counter is INACTIVE
        counter = Counter(
            service_id=service.id,
            name="Counter 1",
            status=CounterStatus.INACTIVE,
        )
        db_session.add(counter)

        # Observation claiming 3 active counters
        obs = WaitObservation(
            service_id=service.id,
            people_ahead=5,
            source="USER_REPORTED",
            active_counters=3,
            reported_wait_minutes=10.0,
            observed_at=now,
        )
        db_session.add(obs)
        await db_session.commit()

        estimation_svc = EstimationService(db_session)
        eff_counters, _ = await estimation_svc._resolve_effective_counters(service.id, now)
        assert eff_counters == 0  # DB says 0 active; queue is halted!

        # Estimation status must be unavailable
        status_resp = await estimation_svc.get_service_queue_status(
            service.id, persist_estimate=False
        )
        assert status_resp.active_counters == 0
        assert status_resp.estimated_wait.is_available is False

    async def test_unmanaged_service_uses_fresh_observation_counters(
        self, db_session, sample_service
    ):
        """When 0 counters are registered in DB, fresh observation
        (<= 1800 seconds / 30 minutes) provides active count.
        """
        service = sample_service
        now = datetime.now(UTC)

        # Fresh observation from 5 minutes ago (300s <= 1800s)
        obs = WaitObservation(
            service_id=service.id,
            people_ahead=5,
            source="USER_REPORTED",
            active_counters=2,
            reported_wait_minutes=12.0,
            observed_at=now - timedelta(minutes=5),
        )
        db_session.add(obs)
        await db_session.commit()

        estimation_svc = EstimationService(db_session)
        eff_counters, _ = await estimation_svc._resolve_effective_counters(service.id, now)
        assert eff_counters == 2

    async def test_unmanaged_service_rejects_stale_observation_counters(
        self, db_session, sample_service
    ):
        """When 0 counters are registered in DB, stale observation
        (> 1800 seconds / 30 minutes) is discarded.
        """
        service = sample_service
        now = datetime.now(UTC)

        # Stale observation from 45 minutes ago (> 1800s)
        obs = WaitObservation(
            service_id=service.id,
            people_ahead=5,
            source="USER_REPORTED",
            active_counters=2,
            reported_wait_minutes=12.0,
            observed_at=now - timedelta(minutes=45),
        )
        db_session.add(obs)
        await db_session.commit()

        estimation_svc = EstimationService(db_session)
        eff_counters, _ = await estimation_svc._resolve_effective_counters(service.id, now)
        assert eff_counters == 0  # Stale observation discarded

    async def test_unmanaged_service_without_observations_yields_zero_counters(
        self, db_session, sample_service
    ):
        """When 0 counters registered and no observations exist, returns 0."""
        service = sample_service
        now = datetime.now(UTC)

        estimation_svc = EstimationService(db_session)
        eff_counters, _ = await estimation_svc._resolve_effective_counters(service.id, now)
        assert eff_counters == 0
