"""Structured, audit-derived postmortem drafts for controlled incidents."""

from collections import Counter
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from opspilot.domain.actions import ActionPlan
from opspilot.domain.evidence import EvidenceRecord
from opspilot.domain.investigation import InvestigationReport


class PostmortemAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    action_type: str
    status: str
    approved_by: str | None


class PostmortemSection(BaseModel):
    """A factual section shown in the controlled RCA review."""

    model_config = ConfigDict(extra="forbid")

    heading: str
    body: str


class PostmortemDraft(BaseModel):
    """Factual draft assembled only from persisted incident records."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    lifecycle_state: str
    generated_at: datetime
    summary: str
    evidence_count: int = Field(ge=0)
    actions: list[PostmortemAction]
    timeline: list[dict[str, str]]
    sections: list[PostmortemSection]


class PostmortemService:
    def create(
        self,
        incident: dict[str, str],
        evidence: list[EvidenceRecord],
        actions: list[ActionPlan],
        timeline: list[dict[str, str]],
        investigation: InvestigationReport | None = None,
    ) -> PostmortemDraft:
        action_items = [
            PostmortemAction(
                action_id=plan.id,
                action_type=plan.proposal.action_type.value,
                status=plan.status.value,
                approved_by=plan.approved_by,
            )
            for plan in actions
        ]
        sources = Counter(record.source_type.value for record in evidence)
        source_summary = ", ".join(
            f"{count} {source_type}" for source_type, count in sorted(sources.items())
        ) or "no persisted evidence"
        verified_actions = [item for item in action_items if item.status == "Verified"]
        recovery_summary = (
            "; ".join(f"{item.action_type} ({item.status})" for item in verified_actions)
            if verified_actions
            else "No independently verified remediation is recorded."
        )
        investigation_section = self._investigation_section(investigation)
        sections = [
            PostmortemSection(
                heading="Incident context",
                body=(
                    f"Incident {incident['id']} is recorded in lifecycle state "
                    f"{incident['lifecycle_state']}. The draft is based on {source_summary}."
                ),
            ),
            investigation_section,
            PostmortemSection(
                heading="Recovery and verification",
                body=(
                    f"Recorded remediation outcome: {recovery_summary} "
                    "Recovery status is determined by the independent verifier, not a "
                    "model response."
                ),
            ),
            PostmortemSection(
                heading="Affected scope and impact",
                body=(
                    "The controlled environment can confirm the affected Kubernetes workload only. "
                    "Dependency topology, customer impact, and an SLO are not configured "
                    "and are not estimated."
                ),
            ),
            PostmortemSection(
                heading="Follow-up",
                body=(
                    "Review the persisted evidence and approved action before adding a "
                    "prevention task. OpsPilot does not fabricate a prevention recommendation "
                    "when no supported conclusion exists."
                ),
            ),
        ]
        return PostmortemDraft(
            incident_id=incident["id"],
            lifecycle_state=incident["lifecycle_state"],
            generated_at=datetime.now(UTC),
            summary=(
                "This draft is assembled from the persisted incident timeline, "
                f"{len(evidence)} evidence record(s), and {len(action_items)} action plan(s)."
            ),
            evidence_count=len(evidence),
            actions=action_items,
            timeline=timeline,
            sections=sections,
        )

    @staticmethod
    def _investigation_section(
        investigation: InvestigationReport | None,
    ) -> PostmortemSection:
        if investigation is None:
            return PostmortemSection(
                heading="Investigation conclusion",
                body=(
                    "No model-backed root-cause conclusion is recorded for this incident. "
                    "This controlled RCA preserves the observed evidence and does not "
                    "infer a cause."
                ),
            )
        if investigation.mode != "live_model":
            return PostmortemSection(
                heading="Investigation conclusion",
                body=(
                    "A controlled-simulation investigation is recorded for this incident. "
                    "It is not a model-backed root-cause conclusion, so this RCA preserves "
                    "the observed evidence and does not infer a cause."
                ),
            )

        hypothesis = investigation.hypotheses[0]
        evidence_refs = ", ".join(f"E-{evidence_id[:6]}" for evidence_id in hypothesis.evidence_ids)
        return PostmortemSection(
            heading="Investigation conclusion",
            body=(
                "The latest live-model investigation reported: "
                f"{hypothesis.root_cause} (confidence {hypothesis.confidence:.2f}; "
                f"evidence {evidence_refs}). Its evidence-bounded summary was: "
                f"{investigation.summary}"
            ),
        )
