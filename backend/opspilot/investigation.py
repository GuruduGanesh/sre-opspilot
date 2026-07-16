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
        self._client = client or create_responses_client(settings)
        self._collector = collector or LiveEvidenceCollector(store, settings)

    def investigate(self, incident_id: str, question: str) -> InvestigationReport:
        if not question.strip():
            raise ValueError("investigation question must not be blank")
        incident = self._store.incident(incident_id)
        if incident is None:
            raise KeyError(f"incident not found: {incident_id}")
        self._collector.collect(incident_id)

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
            assert incident is not None
            return json.dumps(incident, sort_keys=True)
        if name == "get_incident_evidence":
            evidence = [
                item.model_dump(mode="json") for item in self._store.list_evidence(incident_id)
            ]
            return json.dumps(evidence, sort_keys=True)
        if name == "get_incident_timeline":
            return json.dumps(self._store.timeline(incident_id), sort_keys=True)
        raise ValueError(f"unsupported investigation tool: {name}")

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
