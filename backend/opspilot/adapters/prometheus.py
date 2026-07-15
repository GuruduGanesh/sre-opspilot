from datetime import UTC, datetime

import httpx

from opspilot.domain.tools import MetricQueryResult


class PrometheusAdapter:
    """Prometheus adapter restricted to named server-owned query templates."""

    QUERY_TEMPLATES = {
        "service_5xx_rate": 'sum(rate(http_requests_total{service="{service}",status=~"5.."}[1m]))',
        "service_request_rate": 'sum(rate(http_requests_total{service="{service}"}[1m]))',
    }

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=5.0)

    def get_metric(self, query_name: str, service: str) -> MetricQueryResult:
        try:
            template = self.QUERY_TEMPLATES[query_name]
        except KeyError as error:
            raise ValueError(f"unsupported metric query: {query_name}") from error

        query = template.format(service=service)
        response = self._client.get(f"{self._base_url}/api/v1/query", params={"query": query})
        response.raise_for_status()
        payload = response.json()
        results = payload.get("data", {}).get("result", [])
        value = float(results[0]["value"][1]) if results else 0.0
        return MetricQueryResult(
            query_name=query_name,
            value=value,
            observed_at=datetime.now(UTC),
            source_ref=f"prometheus:{query_name}:{service}",
        )
