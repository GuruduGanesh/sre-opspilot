from pydantic import BaseModel, ConfigDict, Field


class EvidenceBackedHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_cause: str = Field(min_length=1, max_length=600)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(min_length=1)
    contradictory_evidence_ids: list[str] = Field(default_factory=list)
    next_evidence_needed: str | None = Field(default=None, max_length=300)


class InvestigationReport(BaseModel):
    """A structured, evidence-cited result that may be rendered to an engineer."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=800)
    hypotheses: list[EvidenceBackedHypothesis] = Field(min_length=1, max_length=3)
    recommended_next_step: str = Field(min_length=1, max_length=400)
