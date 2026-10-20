import uuid

import httpx

from app.core.config import get_settings
from app.domain.enums import UserRole


class TestQueueAPIIntegration:
    """Verifies complete HTTP endpoint behavior, validation, status codes, and error semantics."""

    async def test_complete_queue_lifecycle(self, client: httpx.AsyncClient):
        """Tests complete citizen journey: location -> service -> counter -> queue lifecycle."""
        # 1. Create Location
        loc_resp = await client.post(
            "/api/v1/locations",
            json={
                "name": "District Hospital Civil Lines",
                "category": "hospital",
                "address": "Civil Lines Ward 4",
                "city": "Bhopal",
                "state": "Madhya Pradesh",
                "timezone": "Asia/Kolkata",
                "latitude": 23.2599,
                "longitude": 77.4126,
            },
        )
        assert loc_resp.status_code == 201
        loc_data = loc_resp.json()
        loc_id = loc_data["id"]
        assert loc_data["name"] == "District Hospital Civil Lines"

        # 2. List Locations
        list_loc_resp = await client.get("/api/v1/locations")
        assert list_loc_resp.status_code == 200
        assert len(list_loc_resp.json()) == 1

        # 3. Get Location by ID
        get_loc_resp = await client.get(f"/api/v1/locations/{loc_id}")
        assert get_loc_resp.status_code == 200
        assert get_loc_resp.json()["id"] == loc_id

        # 4. Create Service under Location
        svc_resp = await client.post(
            "/api/v1/services",
            json={
                "location_id": loc_id,
                "name": "OPD Registration",
                "description": "General Patient Intake Counter",
            },
        )
        assert svc_resp.status_code == 201
        svc_data = svc_resp.json()
        svc_id = svc_data["id"]

        # 5. List Services
        list_svc_resp = await client.get(f"/api/v1/services?location_id={loc_id}")
        assert list_svc_resp.status_code == 200
        assert len(list_svc_resp.json()) == 1

        # 6. Create Counter
        counter_resp = await client.post(
            "/api/v1/counters",
            json={
                "service_id": svc_id,
                "name": "Counter 1 - General",
            },
        )
        assert counter_resp.status_code == 201
        counter_id = counter_resp.json()["id"]

        # 7. List Counters
        list_cnt_resp = await client.get(f"/api/v1/counters?service_id={svc_id}")
        assert list_cnt_resp.status_code == 200
        assert len(list_cnt_resp.json()) == 1

        # 8. Check In to Queue
        checkin_resp = await client.post(
            "/api/v1/queue/check-in",
            json={
                "service_id": svc_id,
                "user_id": "citizen_42",
                "idempotency_key": "idem_checkin_001",
            },
        )
        assert checkin_resp.status_code == 201
        session_data = checkin_resp.json()
        session_id = session_data["id"]
        assert session_data["status"] == "WAITING"
        assert session_data["user_id"] == "citizen_42"

        # 9. Get Queue Session
        get_sess_resp = await client.get(f"/api/v1/queue/{session_id}")
        assert get_sess_resp.status_code == 200
        assert get_sess_resp.json()["id"] == session_id

        # 10. Check Queue Status (Estimator Unavailable initially because no completed sessions)
        status_resp = await client.get(f"/api/v1/services/{svc_id}/queue-status")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["people_ahead"] == 1
        assert status_data["active_counters"] == 1
        # Explicitly unavailable, NOT fake 0 minutes!
        assert status_data["estimated_wait"]["is_available"] is False
        assert "Insufficient sample data" in status_data["estimated_wait"]["reason"]

        # 11. Start Service
        start_resp = await client.post(
            f"/api/v1/queue/{session_id}/start",
            json={
                "counter_id": counter_id,
                "idempotency_key": "idem_start_001",
            },
        )
        assert start_resp.status_code == 200
        assert start_resp.json()["status"] == "IN_SERVICE"
        assert start_resp.json()["service_started_at"] is not None

        # 12. Complete Service
        complete_resp = await client.post(
            f"/api/v1/queue/{session_id}/complete",
            json={
                "idempotency_key": "idem_comp_001",
            },
        )
        assert complete_resp.status_code == 200
        assert complete_resp.json()["status"] == "COMPLETED"
        assert complete_resp.json()["completed_at"] is not None

        # 13. Now check in citizen 2 and check queue status with now 1 completed sample!
        checkin_2_resp = await client.post(
            "/api/v1/queue/check-in",
            json={
                "service_id": svc_id,
                "user_id": "citizen_43",
            },
        )
        assert checkin_2_resp.status_code == 201

        status_2_resp = await client.get(f"/api/v1/services/{svc_id}/queue-status")
        assert status_2_resp.status_code == 200
        status_2_data = status_2_resp.json()
        assert status_2_data["people_ahead"] == 1
        assert status_2_data["active_counters"] == 1
        # Now estimate IS available because we have completed service session throughput!
        assert status_2_data["estimated_wait"]["is_available"] is True
        assert status_2_data["estimated_wait"]["sample_size"] == 1
        assert status_2_data["estimated_wait"]["lower_minutes"] is not None
        assert status_2_data["estimated_wait"]["upper_minutes"] is not None
        assert (
            status_2_data["estimated_wait"]["lower_minutes"]
            <= status_2_data["estimated_wait"]["upper_minutes"]
        )

    async def test_queue_cancellation_flow(self, client: httpx.AsyncClient):
        """Verifies cancellation of a waiting queue session."""
        # Create service
        loc = (
            await client.post(
                "/api/v1/locations",
                json={
                    "name": "RTO West Zone",
                    "category": "rto",
                    "address": "Sector 5",
                    "city": "Mumbai",
                    "state": "Maharashtra",
                },
            )
        ).json()
        svc = (
            await client.post(
                "/api/v1/services",
                json={"location_id": loc["id"], "name": "Driving Licence"},
            )
        ).json()

        session = (
            await client.post(
                "/api/v1/queue/check-in",
                json={"service_id": svc["id"]},
            )
        ).json()

        cancel_resp = await client.post(
            f"/api/v1/queue/{session['id']}/cancel",
            json={"reason": "Emergency call", "idempotency_key": "cancel_key_1"},
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "CANCELLED"
        assert cancel_resp.json()["cancelled_at"] is not None

    async def test_invalid_state_transitions(self, client: httpx.AsyncClient):
        """Verifies 400 Bad Request on invalid queue transitions."""
        loc = (
            await client.post(
                "/api/v1/locations",
                json={
                    "name": "SDM Office",
                    "category": "government_office",
                    "address": "Court Complex",
                    "city": "Jaipur",
                    "state": "Rajasthan",
                },
            )
        ).json()
        svc = (
            await client.post(
                "/api/v1/services",
                json={"location_id": loc["id"], "name": "Caste Certificate"},
            )
        ).json()

        session = (
            await client.post(
                "/api/v1/queue/check-in",
                json={"service_id": svc["id"]},
            )
        ).json()

        # Cannot complete directly from WAITING -> 400 Bad Request
        resp_bad_complete = await client.post(f"/api/v1/queue/{session['id']}/complete")
        assert resp_bad_complete.status_code == 400
        assert "Invalid queue session transition" in resp_bad_complete.json()["detail"]

        # Cancel session
        await client.post(f"/api/v1/queue/{session['id']}/cancel")

        # CANCELLED cannot start -> 400 Bad Request
        resp_cancelled_start = await client.post(f"/api/v1/queue/{session['id']}/start")
        assert resp_cancelled_start.status_code == 400

    async def test_duplicate_requests_idempotency(self, client: httpx.AsyncClient):
        """Verifies repeated check-in with same idempotency key returns same session."""
        loc = (
            await client.post(
                "/api/v1/locations",
                json={
                    "name": "District Hospital",
                    "category": "hospital",
                    "address": "Ring Road",
                    "city": "Pune",
                    "state": "Maharashtra",
                },
            )
        ).json()
        svc = (
            await client.post(
                "/api/v1/services",
                json={"location_id": loc["id"], "name": "Radiology"},
            )
        ).json()

        key = "unique-key-xyz-999"
        res1 = await client.post(
            "/api/v1/queue/check-in",
            json={"service_id": svc["id"], "idempotency_key": key},
        )
        assert res1.status_code == 201

        res2 = await client.post(
            "/api/v1/queue/check-in",
            json={"service_id": svc["id"], "idempotency_key": key},
        )
        # Should return existing session
        assert res2.status_code == 201
        assert res1.json()["id"] == res2.json()["id"]

    async def test_wait_observation_submission_and_retrieval(self, client: httpx.AsyncClient):
        """Verifies submitting crowd observation reflects in queue status."""
        loc = (
            await client.post(
                "/api/v1/locations",
                json={
                    "name": "Civil Hospital",
                    "category": "hospital",
                    "address": "Station Road",
                    "city": "Surat",
                    "state": "Gujarat",
                },
            )
        ).json()
        svc = (
            await client.post(
                "/api/v1/services",
                json={"location_id": loc["id"], "name": "Laboratory"},
            )
        ).json()

        # Submit observation
        obs_resp = await client.post(
            f"/api/v1/services/{svc['id']}/observations",
            json={
                "people_ahead": 14,
                "reported_wait_minutes": 25.0,
                "active_counters": 3,
                "source": "USER_REPORTED",
            },
        )
        assert obs_resp.status_code == 201
        obs_data = obs_resp.json()
        assert obs_data["people_ahead"] == 14
        assert obs_data["active_counters"] == 3

        # Invalid observation with negative values -> 422
        bad_obs = await client.post(
            f"/api/v1/services/{svc['id']}/observations",
            json={
                "people_ahead": -1,
                "active_counters": 1,
            },
        )
        assert bad_obs.status_code == 422

        # Boundary violation: people_ahead exceeds 5000 -> 422
        bad_people = await client.post(
            f"/api/v1/services/{svc['id']}/observations",
            json={"people_ahead": 5001, "active_counters": 1},
        )
        assert bad_people.status_code == 422

        # Boundary violation: active_counters exceeds 100 -> 422
        bad_counters = await client.post(
            f"/api/v1/services/{svc['id']}/observations",
            json={"people_ahead": 10, "active_counters": 101},
        )
        assert bad_counters.status_code == 422

        # Boundary violation: reported_wait_minutes exceeds 1440.0 -> 422
        bad_wait = await client.post(
            f"/api/v1/services/{svc['id']}/observations",
            json={"people_ahead": 10, "active_counters": 2, "reported_wait_minutes": 1441.0},
        )
        assert bad_wait.status_code == 422

        # Valid observation at boundary values -> 201
        valid_boundary = await client.post(
            f"/api/v1/services/{svc['id']}/observations",
            json={"people_ahead": 5000, "active_counters": 100, "reported_wait_minutes": 1440.0},
        )
        assert valid_boundary.status_code == 201

    async def test_observation_submission_rate_limiting(
        self, client: httpx.AsyncClient, monkeypatch
    ):
        """Verifies observation submissions are subject to rate limiting (HTTP 429)."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        settings = get_settings()
        monkeypatch.setattr(settings, "RATE_LIMIT_OBSERVATION_MAX", 2)
        monkeypatch.setattr(settings, "RATE_LIMIT_OBSERVATION_WINDOW", 60)

        loc = (
            await client.post(
                "/api/v1/locations",
                json={
                    "name": "Rate Limit Observation Facility",
                    "category": "hospital",
                    "address": "123 Limit St",
                    "city": "Pune",
                    "state": "Maharashtra",
                },
            )
        ).json()
        svc = (
            await client.post(
                "/api/v1/services",
                json={"location_id": loc["id"], "name": "Limit OPD"},
            )
        ).json()

        payload = {"people_ahead": 5, "active_counters": 2}
        r1 = await client.post(f"/api/v1/services/{svc['id']}/observations", json=payload)
        r2 = await client.post(f"/api/v1/services/{svc['id']}/observations", json=payload)
        assert r1.status_code == 201
        assert r2.status_code == 201

        r3 = await client.post(f"/api/v1/services/{svc['id']}/observations", json=payload)
        assert r3.status_code == 429
        assert "Retry-After" in r3.headers

    async def test_not_found_handling(self, client: httpx.AsyncClient):
        """Verifies 404 for non-existent entities."""
        random_id = uuid.uuid4()
        assert (await client.get(f"/api/v1/locations/{random_id}")).status_code == 404
        assert (await client.get(f"/api/v1/services/{random_id}")).status_code == 404
        assert (await client.get(f"/api/v1/queue/{random_id}")).status_code == 404
        assert (await client.get(f"/api/v1/services/{random_id}/queue-status")).status_code == 404

    async def test_operator_desk_waiting_tokens_and_visibility(
        self,
        client: httpx.AsyncClient,
        make_user,
        unauth_client: httpx.AsyncClient,
    ):
        """Verifies operator desk fetches waiting tokens and active seated participant
        for assigned service.
        """
        loc = (
            await client.post(
                "/api/v1/locations",
                json={
                    "name": "Fergusson College Campus Office",
                    "category": "college",
                    "address": "FC Road, Shivajinagar",
                    "city": "Pune",
                    "state": "Maharashtra",
                },
            )
        ).json()
        svc = (
            await client.post(
                "/api/v1/services",
                json={
                    "location_id": loc["id"],
                    "name": "Admissions & Merit List Verification",
                },
            )
        ).json()

        # Create operator assigned to this service
        _, _, op_headers = await make_user(
            "operator_pune@queless.gov.in",
            role=UserRole.OPERATOR,
            full_name="Pune Desk Operator",
            assigned_service_id=uuid.UUID(svc["id"]),
        )

        # Check-in 2 citizens
        cit1 = (
            await client.post(
                "/api/v1/queue/check-in",
                json={"service_id": svc["id"], "user_id": "Citizen Alpha"},
            )
        ).json()
        cit2 = (
            await client.post(
                "/api/v1/queue/check-in",
                json={"service_id": svc["id"], "user_id": "Citizen Beta"},
            )
        ).json()

        # Operator fetches waiting sessions
        wait_resp = await unauth_client.get(
            f"/api/v1/queue/services/{svc['id']}/waiting",
            headers=op_headers,
        )
        assert wait_resp.status_code == 200
        waiting_list = wait_resp.json()
        assert len(waiting_list) == 2
        assert waiting_list[0]["id"] == cit1["id"]
        assert waiting_list[0]["position"] == 1
        assert waiting_list[1]["id"] == cit2["id"]
        assert waiting_list[1]["position"] == 2

        # Operator fetches desk state
        desk_resp = await unauth_client.get(
            f"/api/v1/queue/services/{svc['id']}/desk",
            headers=op_headers,
        )
        assert desk_resp.status_code == 200
        desk_data = desk_resp.json()
        assert desk_data["service"]["id"] == svc["id"]
        assert desk_data["active_session"] is None
        assert desk_data["waiting_count"] == 2
        assert len(desk_data["waiting_sessions"]) == 2

        # Operator starts service on first citizen
        start_resp = await unauth_client.post(
            f"/api/v1/queue/{cit1['id']}/start",
            headers=op_headers,
        )
        assert start_resp.status_code == 200

        # Verify desk reflects active seated participant and remaining waiting
        desk_resp_2 = await unauth_client.get(
            "/api/v1/queue/operator/desk",
            headers=op_headers,
        )
        assert desk_resp_2.status_code == 200
        desk_data_2 = desk_resp_2.json()
        assert desk_data_2["active_session"] is not None
        assert desk_data_2["active_session"]["id"] == cit1["id"]
        assert desk_data_2["active_session"]["status"] == "IN_SERVICE"
        assert desk_data_2["waiting_count"] == 1
        assert desk_data_2["waiting_sessions"][0]["id"] == cit2["id"]

    async def test_operator_service_assignment_consistency_and_bola(
        self,
        client: httpx.AsyncClient,
        make_user,
        unauth_client: httpx.AsyncClient,
    ):
        """Verifies operator assigned to Service A cannot query or operate Service B
        (BOLA protection).
        """
        loc = (
            await client.post(
                "/api/v1/locations",
                json={
                    "name": "District Complex",
                    "category": "government_office",
                    "address": "Collectorate",
                    "city": "Mumbai",
                    "state": "Maharashtra",
                },
            )
        ).json()
        svcA = (
            await client.post(
                "/api/v1/services",
                json={"location_id": loc["id"], "name": "Income Certificate"},
            )
        ).json()
        svcB = (
            await client.post(
                "/api/v1/services",
                json={"location_id": loc["id"], "name": "Domicile Certificate"},
            )
        ).json()

        # Operator assigned strictly to svcA
        _, _, op_headers = await make_user(
            "op_svca@queless.gov.in",
            role=UserRole.OPERATOR,
            assigned_service_id=uuid.UUID(svcA["id"]),
        )

        # Check in citizen to svcB
        citB = (
            await client.post(
                "/api/v1/queue/check-in",
                json={"service_id": svcB["id"], "user_id": "Citizen B"},
            )
        ).json()

        # Operator svcA attempts to view waiting tokens for svcB -> 403 Forbidden
        resp_b_wait = await unauth_client.get(
            f"/api/v1/queue/services/{svcB['id']}/waiting",
            headers=op_headers,
        )
        assert resp_b_wait.status_code == 403
        assert "not assigned to this service" in resp_b_wait.json()["detail"]

        # Operator svcA attempts to view desk for svcB -> 403 Forbidden
        resp_b_desk = await unauth_client.get(
            f"/api/v1/queue/services/{svcB['id']}/desk",
            headers=op_headers,
        )
        assert resp_b_desk.status_code == 403

        # Operator svcA attempts to start token on svcB -> 403 Forbidden
        resp_b_start = await unauth_client.post(
            f"/api/v1/queue/{citB['id']}/start",
            headers=op_headers,
        )
        assert resp_b_start.status_code == 403
        assert "not assigned to this service" in resp_b_start.json()["detail"]

    async def test_citizen_role_forbidden_on_operator_desk_endpoints(
        self,
        client: httpx.AsyncClient,
        citizen_auth,
        unauth_client: httpx.AsyncClient,
    ):
        """Verifies citizens are rejected with 403 Forbidden on operator queue endpoints."""
        _, _, cit_headers = citizen_auth
        loc = (
            await client.post(
                "/api/v1/locations",
                json={
                    "name": "RTO East",
                    "category": "rto",
                    "address": "East Highway",
                    "city": "Bengaluru",
                    "state": "Karnataka",
                },
            )
        ).json()
        svc = (
            await client.post(
                "/api/v1/services",
                json={"location_id": loc["id"], "name": "LL Test"},
            )
        ).json()

        resp_wait = await unauth_client.get(
            f"/api/v1/queue/services/{svc['id']}/waiting",
            headers=cit_headers,
        )
        assert resp_wait.status_code == 403
        assert "lacks permission for this action" in resp_wait.json()["detail"]

        resp_desk = await unauth_client.get(
            f"/api/v1/queue/services/{svc['id']}/desk",
            headers=cit_headers,
        )
        assert resp_desk.status_code == 403
        assert "lacks permission for this action" in resp_desk.json()["detail"]
