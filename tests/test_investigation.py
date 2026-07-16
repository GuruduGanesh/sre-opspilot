import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from opspilot.dashboard import BlastRadiusSnapshot, DashboardSnapshot, TelemetrySnapshot
from opspilot.domain.evidence import EvidenceRecord, EvidenceSourceType
from opspilot.evidence_collection import LiveEvidenceCollector
from opspilot.investigation import InvestigationWorkflow
from opspilot.settings import Settings


class FakeStore:
    def __init__(self) -> None:
        self.evidence = [
            EvidenceRecord(
                id="evidence-alert-1",
                incident_id="incident-1",
                source_type=EvidenceSourceType.ALERT,
                source_ref="alert://checkout",
                observed_at="2026-07-15T00:00:00Z",
                summary="Checkout 5xx alert fired after deployment change",
                content_hash="hash",
            )
        ]
        self.recorded = None

    def incident(self, incident_id: str):
        if incident_id == "incident-1":
            return {"id": incident_id, "lifecycle_state": "Received"}
        return None

    def list_evidence(self, incident_id: str):
        return self.evidence if incident_id == "incident-1" else []

    def record_investigation(self, incident_id: str, model_id: str, report):
        self.recorded = (incident_id, model_id, report)

    def timeline(self, incident_id: str):
        assert incident_id == "incident-1"
        return [
            {
                "occurred_at": "2026-07-15T00:00:00Z",
                "event_type": "evidence",
                "detail": "alert",
            }
        ]


class FakeCollector:
    def __init__(self) -> None:
        self.incident_ids: list[str] = []

    def collect(self, incident_id: str):
        self.incident_ids.append(incident_id)
        return []


class FakeResponses:
    def __init__(self, responses):
        self._responses = iter(responses)

    def create(self, **_kwargs):
        return next(self._responses)


def fake_client(*responses):
    return SimpleNamespace(responses=FakeResponses(responses))


def test_investigation_uses_read_only_evidence_tool_and_persists_valid_report() -> None:
    tool_call = SimpleNamespace(
        type="function_call",
        name="get_incident_evidence",
        call_id="call-1",
        arguments=json.dumps({"incident_id": "incident-1"}),
    )
    report = {
        "summary": "Checkout errors are correlated with the alert evidence.",
        "hypotheses": [
            {
                "root_cause": "The checkout deployment change is the leading hypothesis.",
                "confidence": 0.7,
                "evidence_ids": ["evidence-alert-1"],
                "contradictory_evidence_ids": [],
                "next_evidence_needed": "Inspect the deployment revision and checkout logs.",
            }
        ],
        "recommended_next_step": "Collect deployment history before proposing a change.",
    }
    client = fake_client(
        SimpleNamespace(output=[tool_call], output_text=""),
        SimpleNamespace(output=[], output_text=json.dumps(report)),
    )
    store = FakeStore()
    collector = FakeCollector()

    workflow = InvestigationWorkflow(store, Settings(), client=client, collector=collector)
    result = workflow.investigate("incident-1", "What changed before checkout errors?")

    assert result.hypotheses[0].evidence_ids == ["evidence-alert-1"]
    assert store.recorded[0] == "incident-1"
    assert collector.incident_ids == ["incident-1"]


def test_investigation_rejects_fabricated_evidence_reference() -> None:
    report = {
        "summary": "Unsupported claim.",
        "hypotheses": [
            {
                "root_cause": "Unknown.",
                "confidence": 0.5,
                "evidence_ids": ["invented-evidence"],
                "contradictory_evidence_ids": [],
                "next_evidence_needed": None,
            }
        ],
        "recommended_next_step": "Collect more evidence.",
    }
    store = FakeStore()
    client = fake_client(SimpleNamespace(output=[], output_text=json.dumps(report)))

    with pytest.raises(ValueError, match="not present"):
        workflow = InvestigationWorkflow(
            store, Settings(), client=client, collector=FakeCollector()
        )
        workflow.investigate("incident-1", "What happened?")


def test_investigation_allows_a_server_owned_timeline_tool() -> None:
    tool_call = SimpleNamespace(
        type="function_call",
        name="get_incident_timeline",
        call_id="call-2",
        arguments=json.dumps({"incident_id": "incident-1"}),
    )
    report = {
        "summary": "The alert is the recorded starting point.",
        "hypotheses": [{
            "root_cause": "More evidence is required.",
            "confidence": 0.2,
            "evidence_ids": ["evidence-alert-1"],
            "contradictory_evidence_ids": [],
            "next_evidence_needed": "Inspect a current telemetry snapshot.",
        }],
        "recommended_next_step": "Continue read-only investigation.",
    }
    client = fake_client(
        SimpleNamespace(output=[tool_call], output_text=""),
        SimpleNamespace(output=[], output_text=json.dumps(report)),
    )

    result = InvestigationWorkflow(
        FakeStore(), Settings(), client=client, collector=FakeCollector()
    ).investigate("incident-1", "What happened first?")

    assert result.summary == "The alert is the recorded starting point."


def test_live_telemetry_evidence_uses_exactly_three_decimal_places() -> None:
    snapshot = DashboardSnapshot(
        severity="critical",
        alert_name="Checkout5xxHigh",
        service="checkout",
        observed_at=datetime.now(UTC),
        incident_age_seconds=0,
        telemetry=TelemetrySnapshot(
            service="checkout",
            error_rate=18.492253982107794,
            request_rate=18.49225398210779,
            recovery_error_rate=18.50370074014803,
            status="live",
        ),
        blast_radius=BlastRadiusSnapshot(
            status="not_inferred",
            workload="checkout",
            namespace="opspilot-demo",
            message="Controlled workload only.",
        ),
        slo_status="not_configured",
        slo_message="No SLO configured.",
    )

    records = LiveEvidenceCollector(FakeStore(), Settings())._records(  # noqa: SLF001
        "incident-1", snapshot, datetime.now(UTC)
    )

    assert records[0].summary == (
        "Current checkout telemetry: 5xx 18.492/s, requests 18.492/s, "
        "recovery 5xx 18.504/s"
    )
