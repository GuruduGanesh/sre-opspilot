"""Key-gated GPT-5.6 availability and structured-tool fixture measurement."""

from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast

from openai.types.responses.function_tool_param import FunctionToolParam
from openai.types.shared_params.reasoning import Reasoning

from opspilot.llm_provider import create_responses_client
from opspilot.settings import Settings

FIXTURE_VERSION = "model-selection-v1"
INCIDENT_SNAPSHOT_TOOL: FunctionToolParam = {
    "type": "function",
    "name": "get_incident_snapshot",
    "description": "Read the current server-owned incident state by ID.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {"incident_id": {"type": "string"}},
        "required": ["incident_id"],
        "additionalProperties": False,
    },
}


def run_model_selection_fixture(settings: Settings) -> dict[str, object]:
    """Run one narrow Responses API tool-call fixture; do not save model content."""

    if not settings.active_api_key:
        raise RuntimeError(
            f"{settings.llm_provider} API key is required in an ignored local .env file"
        )

    started = perf_counter()
    response = create_responses_client(settings).responses.create(
        model=settings.active_model,
        reasoning=cast(Reasoning, {"effort": settings.openai_reasoning_effort}),
        input=(
            "For this fixture, call get_incident_snapshot exactly once with incident_id "
            "'fixture-incident-001'. Do not make up incident data."
        ),
        tools=[INCIDENT_SNAPSHOT_TOOL],
        tool_choice="required",
    )
    latency_ms = round((perf_counter() - started) * 1000)
    result = result_from_response(response, settings, latency_ms)
    result["recorded_at"] = datetime.now(UTC).isoformat()
    return result


def result_from_response(response: Any, settings: Settings, latency_ms: int) -> dict[str, object]:
    """Extract stable metadata only; raw prompt, output, and secrets never enter artifacts."""

    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    output = getattr(response, "output", []) or []
    tool_calls = [item for item in output if getattr(item, "type", None) == "function_call"]
    expected_call = any(
        getattr(call, "name", None) == "get_incident_snapshot"
        and '"fixture-incident-001"' in str(getattr(call, "arguments", ""))
        for call in tool_calls
    )
    estimated_cost = _estimate_cost(
        input_tokens,
        output_tokens,
        settings.model_price_input_per_million,
        settings.model_price_output_per_million,
    )
    return {
        "fixture_version": FIXTURE_VERSION,
        "provider": settings.llm_provider,
        "model": settings.active_model,
        "reasoning_effort": settings.openai_reasoning_effort,
        "response_id": getattr(response, "id", None),
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tool_call_count": len(tool_calls),
        "expected_tool_call_observed": expected_call,
        "pass": expected_call,
        "estimated_cost_usd": estimated_cost,
        "cost_status": "estimated from local pricing snapshot"
        if estimated_cost is not None
        else "not estimated; configure a dated local pricing snapshot",
    }


def _estimate_cost(
    input_tokens: int | None,
    output_tokens: int | None,
    input_per_million: float | None,
    output_per_million: float | None,
) -> float | None:
    if (
        input_tokens is None
        or output_tokens is None
        or input_per_million is None
        or output_per_million is None
    ):
        return None
    return round(
        (input_tokens * input_per_million + output_tokens * output_per_million) / 1_000_000,
        8,
    )
