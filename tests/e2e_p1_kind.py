"""Executable P1 integration assertion for a running dedicated kind cluster and local API."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from kubernetes import client
from opspilot.adapters.kubernetes import KubernetesAdapter
from opspilot.api.main import create_app
from opspilot.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    args = parser.parse_args()
    namespace = "opspilot-demo"
    adapter = KubernetesAdapter()
    status = adapter.get_workload_status(namespace, "checkout")
    if status.ready_replicas != status.desired_replicas or status.ready_replicas < 1:
        raise AssertionError("checkout is not ready after controlled P1 rollout")

    core_api = client.CoreV1Api()
    pods = core_api.list_namespaced_pod(
        namespace, label_selector="app.kubernetes.io/name=checkout"
    ).items
    pod = next((item for item in pods if item.status.phase == "Running"), None)
    if pod is None:
        raise AssertionError("no running checkout pod found")
    logs = adapter.get_log_excerpt(namespace, pod.metadata.name, "checkout", tail_lines=100)
    history = adapter.get_deployment_history(namespace, "checkout")
    events = adapter.get_events(namespace, "checkout")
    if not history:
        raise AssertionError("deployment history is empty")
    if not logs.lines:
        raise AssertionError("checkout log excerpt is empty")

    payload = scenario_payload(args.run_id)
    settings = Settings(OPS_PILOT_DB_PATH=args.db_path)
    with TestClient(create_app(settings)) as http:
        created = http.post("/api/v1/ingress/alertmanager", json=payload)
        created.raise_for_status()
        response = created.json()
        incident_id = response["incident_id"]
        if response["disposition"] != "incident_created" or not incident_id:
            raise AssertionError(f"scenario alert did not create an incident: {response}")
        evidence = http.get(f"/api/v1/incidents/{incident_id}/evidence")
        evidence.raise_for_status()
        transition = http.post(
            f"/api/v1/incidents/{incident_id}/lifecycle",
            json={"target": "Classified", "actor": "e2e-test", "reason": "validated P1 ingress"},
        )
        transition.raise_for_status()
        timeline = http.get(f"/api/v1/incidents/{incident_id}/timeline")
        timeline.raise_for_status()

    result = {
        "run_id": args.run_id,
        "incident_id": incident_id,
        "workload": status.model_dump(mode="json"),
        "log_line_count": len(logs.lines),
        "deployment_revision_count": len(history),
        "event_count": len(events),
        "evidence_count": len(evidence.json()),
        "timeline_count": len(timeline.json()),
        "verified_at": datetime.now(UTC).isoformat(),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


def scenario_payload(run_id: str) -> dict[str, object]:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    labels = {
        "alertname": "Checkout5xxHigh",
        "service": "checkout",
        "severity": "critical",
        "opspilot_run_id": run_id,
    }
    return {
        "version": "4",
        "groupKey": f'{{alertname="Checkout5xxHigh",opspilot_run_id="{run_id}"}}',
        "truncatedAlerts": 0,
        "status": "firing",
        "receiver": "opspilot",
        "groupLabels": {"alertname": "Checkout5xxHigh", "opspilot_run_id": run_id},
        "commonLabels": labels,
        "commonAnnotations": {"summary": "E2E controlled checkout 5xx scenario"},
        "externalURL": "http://alertmanager.local",
        "alerts": [
            {
                "status": "firing",
                "labels": labels,
                "annotations": {"summary": "E2E controlled checkout 5xx scenario"},
                "startsAt": now,
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus.local/graph?g0.expr=checkout",
                "fingerprint": f"checkout-5xx-{run_id}",
            }
        ],
    }


if __name__ == "__main__":
    sys.exit(main())
