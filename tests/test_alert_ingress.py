from copy import deepcopy

from fastapi.testclient import TestClient


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


def test_identical_delivery_is_ignored_as_retry(client: TestClient) -> None:
    first = post(client, payload()).json()
    retry = post(client, payload()).json()

    assert retry == {"incident_id": first["incident_id"], "disposition": "retry_ignored"}


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


def test_new_run_creates_new_incident(client: TestClient) -> None:
    first = post(client, payload("run-001")).json()
    second = post(client, payload("run-002")).json()

    assert first["incident_id"] != second["incident_id"]
    assert second["disposition"] == "incident_created"


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
    assert invalid.status_code == 409

    classified = client.post(
        f"/api/v1/incidents/{incident_id}/lifecycle",
        json={"target": "Classified", "actor": "system", "reason": "validated alert"},
    )
    assert classified.status_code == 200
    assert classified.json()["lifecycle_state"] == "Classified"

    timeline = client.get(f"/api/v1/incidents/{incident_id}/timeline")
    assert timeline.status_code == 200
    assert [item["event_type"] for item in timeline.json()] == ["evidence", "lifecycle"]
