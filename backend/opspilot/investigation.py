"""Bounded, evidence-only investigation orchestration for the Responses API."""

import json
from typing import Any, cast

from openai.types.responses.function_tool_param import FunctionToolParam
from openai.types.shared_params.reasoning import Reasoning

from opspilot.domain.investigation import InvestigationReport
from opspilot.evidence_collection import LiveEvidenceCollector
from opspilot.llm_provider import create_responses_client
from opspilot.settings import Settings
from opspilot.storage.incidents import SQLiteIncidentStore

MAX_TOOL_TURNS = 4
READ_ONLY_TOOLS: list[FunctionToolParam] = [
    {
        "type": "function",
        "name": "get_incident_snapshot",
        "description": "Return the current server-owned incident state by ID.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"incident_id": {"type": "string"}},
            "required": ["incident_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_incident_evidence",
        "description": "Return normalized, source-linked evidence collected for an incident.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"incident_id": {"type": "string"}},
            "required": ["incident_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_incident_timeline",
        "description": "Return the server-owned chronological incident timeline by ID.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"incident_id": {"type": "string"}},
            "required": ["incident_id"],
            "additionalProperties": False,
        },
    },
]

_INSTRUCTIONS = """You are OpsPilot's incident investigator. Use only the supplied
read-only tools. Do not infer facts that are absent from their output. Return one
JSON object matching this exact schema:
{
  "summary": "plain-language evidence-based summary",
  "hypotheses": [{
    "root_cause": "specific, bounded hypothesis",
    "confidence": 0.0,
    "evidence_ids": ["persisted evidence IDs only"],
    "contradictory_evidence_ids": ["persisted evidence IDs only"],
    "next_evidence_needed": "optional narrow next collection step"
  }],
  "recommended_next_step": "read-only next step or a request for engineer input"
}
Every hypothesis must cite at least one returned evidence ID. Never recommend or
claim that an action was executed. Before answering, call get_incident_evidence.
Call get_incident_timeline when the engineer asks about sequence or what changed.
Fresh Prometheus, Kubernetes workload, event, and deployment data is persisted as
evidence immediately before this investigation. State data gaps plainly."""


class InvestigationWorkflow:
    def __init__(
        self,
        store: SQLiteIncidentStore,
        settings: Settings,
        client: Any | None = None,
        collector: LiveEvidenceCollector | None = None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._simulation_enabled = settings.simulation_investigation_enabled
        self._client = (
            client
            if client is not None
            else None
            if self._simulation_enabled
            else create_responses_client(settings)
        )
        self._collector = collector or LiveEvidenceCollector(store, settings)

    def investigate(self, incident_id: str, question: str) -> InvestigationReport:
        if not question.strip():
            raise ValueError("investigation question must not be blank")
        incident = self._store.incident(incident_id)
        if incident is None:
            raise KeyError(f"incident not found: {incident_id}")
        self._collector.collect(incident_id)
        if self._simulation_enabled:
            report = self._controlled_simulation_report(incident_id)
            self._store.record_investigation(
                incident_id, "controlled-simulation", report
            )
            return report

        input_items: list[Any] = [
            {
                "role": "user",
                "content": (
                    f"Incident ID: {incident_id}\n"
                    f"Engineer question: {question.strip()}\n"
                    "Investigate with the read-only tools before answering."
                ),
            }
        ]
        response = self._create_response(input_items)
        for _ in range(MAX_TOOL_TURNS):
            calls = [
                item for item in (getattr(response, "output", None) or [])
                if getattr(item, "type", None) == "function_call"
            ]
            if not calls:
                break
            input_items.extend(getattr(response, "output", []))
            for call in calls:
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": self._execute_read_only_tool(
                            call.name, str(getattr(call, "arguments", "{}")), incident_id
                        ),
                    }
                )
            response = self._create_response(input_items)
        else:
            raise ValueError("investigation exceeded the maximum read-only tool turns")

        report = InvestigationReport.model_validate_json(getattr(response, "output_text", ""))
        self._validate_evidence_references(incident_id, report)
        self._store.record_investigation(incident_id, self._settings.active_model, report)
        return report

    def _create_response(self, input_items: list[Any]) -> Any:
        if self._client is None:
            raise RuntimeError("live model client is unavailable in controlled simulation mode")
        return self._client.responses.create(
            model=self._settings.active_model,
            reasoning=cast(Reasoning, {"effort": self._settings.openai_reasoning_effort}),
            instructions=_INSTRUCTIONS,
            input=input_items,
            tools=READ_ONLY_TOOLS,
            parallel_tool_calls=False,
            max_tool_calls=MAX_TOOL_TURNS,
        )

    def _execute_read_only_tool(self, name: str, raw_arguments: str, incident_id: str) -> str:
        arguments = json.loads(raw_arguments)
        if arguments.get("incident_id") != incident_id:
            raise ValueError("tool call attempted to access a different incident")
        if name == "get_incident_snapshot":
            incident = self._store.incident(incident_id)
            if incident is None:
                raise KeyError(f"incident not found: {incident_id}")
            return json.dumps(incident, sort_keys=True)
        if name == "get_incident_evidence":
            evidence = [
                item.model_dump(mode="json") for item in self._store.list_evidence(incident_id)
            ]
            return json.dumps(evidence, sort_keys=True)
        if name == "get_incident_timeline":
            return json.dumps(self._store.timeline(incident_id), sort_keys=True)
        raise ValueError(f"unsupported investigation tool: {name}")

    def _controlled_simulation_report(self, incident_id: str) -> InvestigationReport:
        """Produce a visibly non-model rehearsal report from persisted evidence only."""

        evidence = self._store.list_evidence(incident_id)
        if not evidence:
            raise ValueError("controlled simulation requires persisted incident evidence")
        alert = next((item for item in evidence if item.source_type.value == "alert"), evidence[0])
        supporting_ids = [item.id for item in evidence[-3:]]
        if alert.id not in supporting_ids:
            supporting_ids.insert(0, alert.id)
        supporting_ids = supporting_ids[:3]
        p2 = "Memory" in alert.summary or "OOM" in alert.summary
        root_cause = (
            "Controlled memory-pressure mode is the rehearsal hypothesis; verify "
            "the recorded restart/OOMKill evidence before restoring it."
            if p2
            else "Controlled checkout response mode is the rehearsal hypothesis; verify "
            "the recorded 5xx telemetry and deployment configuration before restoring it."
        )
        next_step = (
            "Review the persisted evidence, then create a dry-run only for the "
            "allowlisted controlled memory restoration."
            if p2
            else "Review the persisted evidence, then create a dry-run only for the "
            "allowlisted controlled response restoration."
        )
        return InvestigationReport(
            summary=(
                "Controlled simulation report derived deterministically from this incident's "
                "persisted evidence. It is not GPT-5.6 output."
            ),
            hypotheses=[
                {
                    "root_cause": root_cause,
                    "confidence": 0.5,
                    "evidence_ids": supporting_ids,
                    "contradictory_evidence_ids": [],
                    "next_evidence_needed": (
                        "Run the live GPT-5.6 investigation after API access is available."
                    ),
                }
            ],
            recommended_next_step=next_step,
            mode="controlled_simulation",
            provenance="deterministic local rehearsal report — not GPT-5.6",
        )

    def _validate_evidence_references(
        self, incident_id: str, report: InvestigationReport
    ) -> None:
        known_ids = {item.id for item in self._store.list_evidence(incident_id)}
        for hypothesis in report.hypotheses:
            cited_ids = set(hypothesis.evidence_ids) | set(hypothesis.contradictory_evidence_ids)
            unknown_ids = cited_ids - known_ids
            if unknown_ids:
                raise ValueError(
                    "investigation cited evidence not present in this incident: "
                    f"{sorted(unknown_ids)}"
                )
