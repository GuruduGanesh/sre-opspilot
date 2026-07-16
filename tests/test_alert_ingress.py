from copy import deepcopy
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from opspilot.domain.actions import ActionPlan, ActionPlanStatus, ActionProposal, ActionType
from opspilot.domain.evidence import EvidenceRecord, EvidenceSourceType
from opspilot.domain.incidents import LifecycleState


def payload(run_id: str = "run-001", status: str = "firing") -> dict[str, object]:
    alert = {
        "status": status,
        "labels": {
            "alertname": "Checkout5xxHigh",
            "service": "checkout",
            "severity": "critical",
            "opspilot_run_id": run_id,
        },
        "annotations": {"summary": "Checkout 5xx rate is above threshold"},
        "startsAt": "2026-07-14T10:00:00Z",
        "endsAt": "2026-07-14T10:05:00Z" if status == "resolved" else "0001-01-01T00:00:00Z",
        "generatorURL": "http://prometheus.demo/graph?g0.expr=checkout",
        "fingerprint": "checkout-5xx-fingerprint",
    }
    return {
        "version": "4",
        "groupKey": f'{{alertname="Checkout5xxHigh",opspilot_run_id="{run_id}"}}',
        "truncatedAlerts": 0,
        "status": status,
        "receiver": "opspilot",
        "groupLabels": {"alertname": "Checkout5xxHigh", "opspilot_run_id": run_id},
        "commonLabels": deepcopy(alert["labels"]),
        "commonAnnotations": deepcopy(alert["annotations"]),
        "externalURL": "http://alertmanager.demo",
        "alerts": [alert],
    }


def post(client: TestClient, body: dict[str, object]):
    return client.post(
        "/api/v1/ingress/alertmanager",
        json=body,
        headers={"X-OpsPilot-Scenario-Secret": "test-secret"},
    )


def test_firing_alert_creates_received_incident(client: TestClient) -> None:
    response = post(client, payload())

    assert response.status_code == 202
    body = response.json()
    assert body["disposition"] == "incident_created"
    assert body["incident_id"]

    incident = client.get(f"/api/v1/incidents/{body['incident_id']}")
    assert incident.status_code == 200
    assert incident.json()["lifecycle_state"] == "Received"

    evidence = client.get(f"/api/v1/incidents/{body['incident_id']}/evidence")
    assert evidence.status_code == 200
    assert evidence.json()[0]["source_type"] == "alert"
    assert evidence.json()[0]["structured_payload"]["version"] == "4"


def test_open_incident_queue_is_prioritized_and_excludes_closed_incidents(
    client: TestClient,
) -> None:
    critical_id = post(client, payload("queue-critical")).json()["incident_id"]
    warning = payload("queue-warning")
    warning["alerts"][0]["labels"]["severity"] = "warning"  # type: ignore[index]
    warning["commonLabels"]["severity"] = "warning"  # type: ignore[index]
    warning_id = post(client, warning).json()["incident_id"]
    assert critical_id and warning_id

    queued = client.get("/api/v1/incidents?state=open")
    assert queued.status_code == 200
    assert [item["id"] for item in queued.json()] == [critical_id, warning_id]
    assert queued.json()[0]["alert_name"] == "Checkout5xxHigh"

    store = client.app.state.store
    for state in (
        LifecycleState.CLASSIFIED,
        LifecycleState.ENRICHED,
        LifecycleState.TRIAGING,
        LifecycleState.ACTION_PROPOSED,
        LifecycleState.EXECUTING,
        LifecycleState.MONITORING,
        LifecycleState.RESOLVED,
    ):
        store.transition(critical_id, state, "test", "close controlled incident")

    assert [item["id"] for item in client.get("/api/v1/incidents?state=open").json()] == [
        warning_id
    ]
    assert {item["id"] for item in client.get("/api/v1/incidents?state=all").json()} == {
        critical_id,
        warning_id,
    }


def test_identical_delivery_is_ignored_as_retry(client: TestClient) -> None:
    first = post(client, payload()).json()
    retry = post(client, payload()).json()

    assert retry == {"incident_id": first["incident_id"], "disposition": "retry_ignored"}


def test_live_collection_deduplicates_an_unchanged_source_payload(client: TestClient) -> None:
    incident_id = post(client, payload()).json()["incident_id"]
    assert incident_id
    record = EvidenceRecord(
        id="telemetry-first",
        incident_id=incident_id,
        source_type=EvidenceSourceType.PROMETHEUS,
        source_ref="prometheus:dashboard:checkout",
        observed_at=datetime.now(UTC),
        summary="Current checkout telemetry: 5xx 0.0/s",
        structured_payload={"error_rate": 0.0},
        content_hash="same-live-observation",
    )

    store = client.app.state.store
    assert store.append_evidence_if_new(record) is True
    duplicate = record.model_copy(update={"id": "telemetry-duplicate"})
    assert store.append_evidence_if_new(duplicate) is False


def test_changed_firing_alert_attaches_to_open_incident(client: TestClient) -> None:
    first = post(client, payload()).json()
    changed = payload()
    changed["alerts"][0]["annotations"]["detail"] = "A second observation arrived"  # type: ignore[index]

    update = post(client, changed).json()
    assert update == {
        "incident_id": first["incident_id"],
        "disposition": "alert_update_recorded",
    }


def test_resolved_signal_does_not_resolve_incident(client: TestClient) -> None:
    first = post(client, payload()).json()
    resolved = post(client, payload(status="resolved")).json()

    assert resolved == {
        "incident_id": first["incident_id"],
        "disposition": "resolution_signal_recorded",
    }
    incident = client.get(f"/api/v1/incidents/{first['incident_id']}")
    assert incident.json()["lifecycle_state"] == "Received"


def test_resolved_incident_can_draft_and_publish_an_audit_rca(client: TestClient) -> None:
    incident_id = post(client, payload()).json()["incident_id"]
    assert incident_id
    store = client.app.state.store
    for state in (
        LifecycleState.CLASSIFIED,
        LifecycleState.ENRICHED,
        LifecycleState.TRIAGING,
        LifecycleState.ACTION_PROPOSED,
        LifecycleState.EXECUTING,
        LifecycleState.MONITORING,
        LifecycleState.RESOLVED,
    ):
        store.transition(incident_id, state, "test", "advance controlled incident")

    draft = client.post(f"/api/v1/incidents/{incident_id}/postmortem/draft")
    assert draft.status_code == 200
    assert client.get(f"/api/v1/incidents/{incident_id}").json()["lifecycle_state"] == "RCA"

    published = client.post(f"/api/v1/incidents/{incident_id}/postmortem/publish")
    assert published.status_code == 200
    assert published.json()["lifecycle_state"] == "RCAPublished"


def test_new_run_creates_new_incident(client: TestClient) -> None:
    first = post(client, payload("run-001")).json()
    second = post(client, payload("run-002")).json()

    assert first["incident_id"] != second["incident_id"]
    assert second["disposition"] == "incident_created"


def test_postmortem_draft_is_assembled_from_persisted_incident_records(client: TestClient) -> None:
    created = post(client, payload()).json()
    incident_id = created["incident_id"]
    assert incident_id

    response = client.get(f"/api/v1/incidents/{incident_id}/postmortem")

    assert response.status_code == 200
    body = response.json()
    assert body["incident_id"] == incident_id
    assert body["evidence_count"] == 1
    assert body["actions"] == []
    assert body["timeline"][0]["event_type"] == "evidence"
    assert [section["heading"] for section in body["sections"]] == [
        "Incident context",
        "Investigation conclusion",
        "Recovery and verification",
        "Affected scope and impact",
        "Follow-up",
    ]
    assert "does not infer a cause" in body["sections"][1]["body"]


def test_action_plan_status_history_is_append_only(client: TestClient) -> None:
    incident_id = post(client, payload()).json()["incident_id"]
    assert incident_id
    plan = ActionPlan(
        id="action-history-test",
        proposal=ActionProposal(
            incident_id=incident_id,
            action_type=ActionType.ROLLBACK,
            namespace="opspilot-demo",
            workload="checkout",
            evidence_ids=[client.get(f"/api/v1/incidents/{incident_id}/evidence").json()[0]["id"]],
            expected_resource_version="1",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ),
        preview={"dry_run": True},
        fingerprint="test-fingerprint",
        status=ActionPlanStatus.PREVIEWED,
    )
    store = client.app.state.store
    store.create_action_plan(plan)
    store.update_action_plan(
        plan.model_copy(update={"status": ActionPlanStatus.APPROVED}),
        expected_status=ActionPlanStatus.PREVIEWED,
    )

    events = store.action_audit(plan.id)
    assert [(event["event_type"], event["action_status"]) for event in events] == [
        ("preview_created", "Previewed"),
        ("status_changed", "Approved"),
    ]


def test_rejects_payload_without_alertmanager_v4_contract(client: TestClient) -> None:
    invalid = payload()
    invalid["version"] = "3"

    response = post(client, invalid)
    assert response.status_code == 422


def test_server_validates_lifecycle_transitions_and_audits_them(client: TestClient) -> None:
    incident_id = post(client, payload()).json()["incident_id"]
    assert incident_id

    invalid = client.post(
        f"/api/v1/incidents/{incident_id}/lifecycle",
        json={"target": "Executing", "actor": "agent", "reason": "skip approval"},
    )
    assert invalid.status_code == 403

    classified = client.post(
        f"/api/v1/incidents/{incident_id}/lifecycle",
        json={"target": "Classified", "actor": "system", "reason": "validated alert"},
    )
    assert classified.status_code == 200
    assert classified.json()["lifecycle_state"] == "Classified"

    timeline = client.get(f"/api/v1/incidents/{incident_id}/timeline")
    assert timeline.status_code == 200
    assert [item["event_type"] for item in timeline.json()] == ["evidence", "lifecycle"]


def test_public_lifecycle_endpoint_rejects_protected_action_state(client: TestClient) -> None:
    incident_id = post(client, payload()).json()["incident_id"]
    assert incident_id
    for target in ("Classified", "Enriched", "Triaging"):
        response = client.post(
            f"/api/v1/incidents/{incident_id}/lifecycle",
            json={"target": target, "actor": "system", "reason": "test progression"},
        )
        assert response.status_code == 200

    protected = client.post(
        f"/api/v1/incidents/{incident_id}/lifecycle",
        json={"target": "ActionProposed", "actor": "agent", "reason": "bypass approval flow"},
    )
    assert protected.status_code == 403
