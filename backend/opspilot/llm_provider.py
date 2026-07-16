"""Explicit provider selection for local GPT-5.6 testing and final validation."""

from openai import OpenAI

from opspilot.settings import Settings


def create_responses_client(settings: Settings) -> OpenAI:
    """Build an OpenAI-compatible Responses client without reading global state."""

    api_key = settings.active_api_key
    if not api_key:
        raise RuntimeError(
            f"{settings.llm_provider} API key is not configured in the ignored local .env file"
        )
    if settings.llm_provider == "openrouter":
        return OpenAI(api_key=api_key, base_url=settings.openrouter_base_url)
    return OpenAI(api_key=api_key)
