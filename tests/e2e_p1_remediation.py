"""Run the complete controlled P1 approval and recovery flow against kind."""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from e2e_p1_kind import scenario_payload
from fastapi.testclient import TestClient
from opspilot.adapters.prometheus import PrometheusAdapter
from opspilot.api.main import create_app
from opspilot.settings import Settings


def _transition(http: TestClient, incident_id: str, target: str) -> None:
    response = http.post(
        f"/api/v1/incidents/{incident_id}/lifecycle",
        json={"target": target, "actor": "e2e-test", "reason": "prepare controlled remediation"},
    )
    response.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--prometheus-url", required=True)
    parser.add_argument("--simulate-investigation", action="store_true")
    args = parser.parse_args()
    settings = Settings(
        OPS_PILOT_DB_PATH=args.db_path,
        OPS_PILOT_PROMETHEUS_URL=args.prometheus_url,
        OPS_PILOT_SIMULATION_INVESTIGATION_ENABLED=args.simulate_investigation,
    )
    prometheus = PrometheusAdapter(args.prometheus_url)
    try:
        deadline = time.monotonic() + 90
        while True:
            pre_action_5xx = prometheus.get_metric("service_5xx_rate", "checkout").value
            if pre_action_5xx >= 0.01:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("P1 5xx telemetry did not warm before remediation")
            time.sleep(2)
    finally:
        prometheus.close()
    with TestClient(create_app(settings)) as http:
        created = http.post("/api/v1/ingress/alertmanager", json=scenario_payload(args.run_id))
        created.raise_for_status()
        incident_id = created.json()["incident_id"]
        if not incident_id:
            raise AssertionError("controlled alert did not create an incident")
        for target in ("Classified", "Enriched", "Triaging"):
            _transition(http, incident_id, target)
        evidence = http.get(f"/api/v1/incidents/{incident_id}/evidence")
        evidence.raise_for_status()
        evidence_ids = [item["id"] for item in evidence.json()]
        investigation_mode = None
        if args.simulate_investigation:
            investigation = http.post(
                f"/api/v1/incidents/{incident_id}/investigate",
                json={"question": "What evidence supports the controlled failure?"},
            )
            investigation.raise_for_status()
            report = investigation.json()
            if report["mode"] != "controlled_simulation":
                raise AssertionError("simulation investigation was not explicitly labelled")
            if "not GPT-5.6" not in report["summary"]:
                raise AssertionError("simulation investigation did not state its model boundary")
            investigation_mode = report["mode"]

        preview = http.post(
            f"/api/v1/incidents/{incident_id}/actions/preview",
            json={"action_type": "restore_response_mode", "evidence_ids": evidence_ids},
        )
        preview.raise_for_status()
        plan = preview.json()
        if not plan["preview"]["dry_run"]:
            raise AssertionError("action preview was not a Kubernetes dry-run")

        blocked = http.post(f"/api/v1/actions/{plan['id']}/execute")
        if blocked.status_code != 409:
            raise AssertionError("unapproved action was not rejected")
        approved = http.post(
            f"/api/v1/actions/{plan['id']}/approve",
            json={"approved_by": "e2e-oncall@example.test"},
        )
        approved.raise_for_status()
        executed = http.post(f"/api/v1/actions/{plan['id']}/execute")
        executed.raise_for_status()

        # The PowerShell wrapper waits for the rollout before this call, so this
        # response verifies the independently read workload and 5xx indicator.
        print(
            json.dumps(
                {
                    "run_id": args.run_id,
                    "incident_id": incident_id,
                    "action_id": plan["id"],
                    "lifecycle_after_execution": executed.json()["status"],
                    "investigation_mode": investigation_mode,
                    "pre_action_5xx_rate": pre_action_5xx,
                    "verified_at": datetime.now(UTC).isoformat(),
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
