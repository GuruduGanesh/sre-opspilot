from datetime import UTC, datetime, timedelta

import httpx

from opspilot.domain.tools import MetricQueryResult


class PrometheusAdapter:
    """Prometheus adapter restricted to named server-owned query templates."""

    SERVICE_SELECTORS = {
        "checkout": {"method": "GET", "route": "/checkout"},
    }
    QUERY_TEMPLATES = {
        "service_5xx_rate": (
            'sum(rate(http_requests_total{{service="{service}",method="{method}",'
            'route="{route}",status=~"5.."}}[1m]))'
        ),
        "service_5xx_recovery_rate": (
            'sum(rate(http_requests_total{{service="{service}",method="{method}",'
            'route="{route}",status=~"5.."}}[15s]))'
        ),
        "service_5xx_chart_rate": (
            'sum(rate(http_requests_total{{service="{service}",method="{method}",'
            'route="{route}",status=~"5.."}}[15s]))'
        ),
        "service_request_rate": (
            'sum(rate(http_requests_total{{service="{service}",method="{method}",'
            'route="{route}"}}[1m]))'
        ),
    }

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=5.0)

    def get_metric(self, query_name: str, service: str) -> MetricQueryResult:
        selector = self._selector_for(service)
        if selector is None:
            raise ValueError(f"service is not allowlisted: {service}")
        try:
            template = self.QUERY_TEMPLATES[query_name]
        except KeyError as error:
            raise ValueError(f"unsupported metric query: {query_name}") from error

        query = template.format(service=service, **selector)
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

    def get_metric_series(
        self,
        query_name: str,
        service: str,
        *,
        window: timedelta = timedelta(minutes=15),
        step_seconds: int = 15,
    ) -> list[tuple[datetime, float]]:
        """Read a bounded chart series from a named, server-owned query."""

        selector = self._selector_for(service)
        if selector is None:
            raise ValueError(f"service is not allowlisted: {service}")
        if not 5 <= step_seconds <= 60:
            raise ValueError("chart step must be between 5 and 60 seconds")
        if not timedelta(minutes=1) <= window <= timedelta(minutes=30):
            raise ValueError("chart window must be between 1 and 30 minutes")
        try:
            template = self.QUERY_TEMPLATES[query_name]
        except KeyError as error:
            raise ValueError(f"unsupported metric query: {query_name}") from error

        now = datetime.now(UTC)
        response = self._client.get(
            f"{self._base_url}/api/v1/query_range",
            params={
                "query": template.format(service=service, **selector),
                "start": (now - window).isoformat(),
                "end": now.isoformat(),
                "step": f"{step_seconds}s",
            },
        )
        response.raise_for_status()
        results = response.json().get("data", {}).get("result", [])
        if not results:
            return []
        return [
            (datetime.fromtimestamp(float(timestamp), UTC), float(value))
            for timestamp, value in results[0].get("values", [])
        ]

    @classmethod
    def _selector_for(cls, service: str) -> dict[str, str] | None:
        """Return a fixed route selector; never accept a caller-supplied PromQL label."""

        return cls.SERVICE_SELECTORS.get(service)
