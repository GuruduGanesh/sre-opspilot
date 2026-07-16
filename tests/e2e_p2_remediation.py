"""Run the controlled P2 approval and recovery flow against kind."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from opspilot.api.main import create_app
from opspilot.settings import Settings


def scenario_payload(run_id: str) -> dict[str, object]:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    labels = {
        "alertname": "CheckoutOOMKilled",
        "service": "checkout",
        "severity": "critical",
        "opspilot_run_id": run_id,
    }
    return {
        "version": "4",
        "groupKey": f'{{alertname="CheckoutOOMKilled",opspilot_run_id="{run_id}"}}',
        "truncatedAlerts": 0,
        "status": "firing",
        "receiver": "opspilot",
        "groupLabels": {"alertname": "CheckoutOOMKilled", "opspilot_run_id": run_id},
        "commonLabels": labels,
        "commonAnnotations": {"summary": "E2E controlled checkout memory scenario"},
        "externalURL": "http://alertmanager.local",
        "alerts": [
            {
                "status": "firing",
                "labels": labels,
                "annotations": {"summary": "E2E controlled checkout memory scenario"},
                "startsAt": now,
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://kubernetes.local/events?reason=OOMKilled",
                "fingerprint": f"checkout-oom-{run_id}",
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(OPS_PILOT_DB_PATH=args.db_path)
    with TestClient(create_app(settings)) as http:
        created = http.post("/api/v1/ingress/alertmanager", json=scenario_payload(args.run_id))
        created.raise_for_status()
        incident_id = created.json()["incident_id"]
        if not incident_id:
            raise AssertionError("controlled P2 alert did not create an incident")
        for target in ("Classified", "Enriched", "Triaging"):
            response = http.post(
                f"/api/v1/incidents/{incident_id}/lifecycle",
                json={
                    "target": target,
                    "actor": "e2e-test",
                    "reason": "prepare controlled P2 remediation",
                },
            )
            response.raise_for_status()
        evidence = http.get(f"/api/v1/incidents/{incident_id}/evidence")
        evidence.raise_for_status()
        evidence_ids = [item["id"] for item in evidence.json()]
        preview = http.post(
            f"/api/v1/incidents/{incident_id}/actions/preview",
            json={"action_type": "restore_memory_mode", "evidence_ids": evidence_ids},
        )
        preview.raise_for_status()
        plan = preview.json()
        if not plan["preview"]["dry_run"]:
            raise AssertionError("P2 action preview was not a Kubernetes dry-run")
        if http.post(f"/api/v1/actions/{plan['id']}/execute").status_code != 409:
            raise AssertionError("unapproved P2 action was not rejected")
        approved = http.post(
            f"/api/v1/actions/{plan['id']}/approve",
            json={"approved_by": "e2e-oncall@example.test"},
        )
        approved.raise_for_status()
        executed = http.post(f"/api/v1/actions/{plan['id']}/execute")
        executed.raise_for_status()
        print(
            json.dumps(
                {
                    "run_id": args.run_id,
                    "incident_id": incident_id,
                    "action_id": plan["id"],
                    "lifecycle_after_execution": executed.json()["status"],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
