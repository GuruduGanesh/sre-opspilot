from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from opspilot.adapters.kubernetes import FakeKubernetesAdapter, KubernetesAdapter
from opspilot.adapters.prometheus import PrometheusAdapter
from opspilot.dashboard import DashboardService
from opspilot.domain.actions import (
    ActionPlanStatus,
    ActionPolicy,
    ActionProposal,
    ActionType,
    action_fingerprint,
)
from opspilot.domain.evidence import EvidenceRecord, EvidenceSourceType
from opspilot.domain.incidents import LifecycleState
from opspilot.domain.tools import MetricQueryResult, WorkloadStatus
from opspilot.llm_provider import create_responses_client
from opspilot.model_selection import result_from_response
from opspilot.recovery import RecoveryVerifier
from opspilot.remediation import KubernetesRemediationAdapter, RemediationCoordinator
from opspilot.settings import Settings


def test_prometheus_adapter_rejects_raw_or_unknown_query() -> None:
    adapter = PrometheusAdapter("http://prometheus.example")
    with pytest.raises(ValueError, match="unsupported metric query"):
        adapter.get_metric("up{job='anything'}", "checkout")


def test_prometheus_adapter_rejects_non_allowlisted_service() -> None:
    adapter = PrometheusAdapter("http://prometheus.example")
    with pytest.raises(ValueError, match="not allowlisted"):
        adapter.get_metric("service_5xx_rate", 'checkout"} or up{job="anything')


def test_prometheus_adapter_formats_only_server_owned_promql_template() -> None:
    seen_query = ""

    def responder(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        seen_query = request.url.params["query"]
        return httpx.Response(
            200,
            json={"data": {"result": [{"value": [0, "0.02"]}]}},
        )

    adapter = PrometheusAdapter(
        "http://prometheus.example", client=httpx.Client(transport=httpx.MockTransport(responder))
    )

    result = adapter.get_metric("service_5xx_recovery_rate", "checkout")

    assert result.value == 0.02
    assert seen_query == (
        'sum(rate(http_requests_total{service="checkout",method="GET",'
        'route="/checkout",status=~"5.."}[15s]))'
    )


def test_prometheus_adapter_retries_a_transient_connection_failure() -> None:
    attempts = 0

    def responder(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("port-forward connection reset")
        return httpx.Response(200, json={"data": {"result": [{"value": [0, "0.02"]}]}})

    adapter = PrometheusAdapter(
        "http://prometheus.example", client=httpx.Client(transport=httpx.MockTransport(responder))
    )

    assert adapter.get_metric("service_5xx_rate", "checkout").value == 0.02
    assert attempts == 2


def test_prometheus_adapter_formats_server_owned_2xx_template() -> None:
    seen_query = ""

    def responder(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        seen_query = request.url.params["query"]
        return httpx.Response(200, json={"data": {"result": [{"value": [0, "18.2"]}]}})

    adapter = PrometheusAdapter(
        "http://prometheus.example", client=httpx.Client(transport=httpx.MockTransport(responder))
    )

    result = adapter.get_metric("service_2xx_rate", "checkout")

    assert result.value == 18.2
    assert seen_query == (
        'sum(rate(http_requests_total{service="checkout",method="GET",'
        'route="/checkout",status=~"2.."}[1m]))'
    )


def test_prometheus_adapter_chart_template_emits_zero_when_5xx_series_is_absent() -> None:
    seen_query = ""

    def responder(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        seen_query = request.url.params["query"]
        return httpx.Response(200, json={"data": {"result": []}})

    adapter = PrometheusAdapter(
        "http://prometheus.example", client=httpx.Client(transport=httpx.MockTransport(responder))
    )

    assert adapter.get_metric_series("service_5xx_chart_rate", "checkout") == []
    assert seen_query == (
        '(sum(rate(http_requests_total{service="checkout",method="GET",'
        'route="/checkout",status=~"5.."}[15s])) or vector(0))'
    )


def test_prometheus_adapter_returns_bounded_metric_series() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query_range"
        assert request.url.params["step"] == "15s"
        return httpx.Response(
            200,
            json={"data": {"result": [{"values": [["1721000000", "0.1"], ["1721000015", "0.2"]]}]}},
        )

    adapter = PrometheusAdapter(
        "http://prometheus.example", client=httpx.Client(transport=httpx.MockTransport(responder))
    )

    result = adapter.get_metric_series("service_5xx_rate", "checkout")

    assert [value for _, value in result] == [0.1, 0.2]


def test_dashboard_projection_exposes_only_declared_controlled_traffic_scope() -> None:
    class DashboardPrometheus:
        def get_metric(self, query_name: str, service: str) -> MetricQueryResult:
            values = {
                "service_5xx_rate": 0.0,
                "service_2xx_rate": 18.2,
                "service_request_rate": 18.2,
                "service_5xx_recovery_rate": 0.0,
                "service_2xx_recovery_rate": 18.2,
            }
            return MetricQueryResult(
                query_name=query_name,
                value=values[query_name],
                observed_at=datetime.now(UTC),
                source_ref=f"fake:{service}:{query_name}",
            )

        def get_metric_series(self, query_name: str, _service: str):
            values = [0.0, 0.0] if query_name == "service_5xx_chart_rate" else [18.1, 18.2]
            return [
                (datetime(2026, 7, 15, 12, index, tzinfo=UTC), value)
                for index, value in enumerate(values)
            ]

    store = FakeActionStore()
    store.incident_state = LifecycleState.TRIAGING
    store.evidence = [
        EvidenceRecord(
            id="evidence-alert-1",
            incident_id="incident-1",
            source_type=EvidenceSourceType.ALERT,
            source_ref="alert://checkout",
            observed_at=datetime(2026, 7, 15, tzinfo=UTC),
            summary="Checkout alert fired",
            structured_payload={
                "commonLabels": {
                    "service": "checkout",
                    "severity": "critical",
                    "alertname": "Checkout5xxHigh",
                }
            },
            content_hash="hash",
        )
    ]
    snapshot = DashboardService(
        store, Settings(), kubernetes=FakeKubernetesAdapter(), prometheus=DashboardPrometheus()
    ).snapshot("incident-1")

    assert snapshot.blast_radius.status == "declared_controlled_topology"
    assert snapshot.blast_radius.method == "GET"
    assert snapshot.blast_radius.route == "/checkout"
    assert snapshot.blast_radius.configured_callers == ["load-generator → GET /checkout"]
    assert snapshot.blast_radius.downstream_dependencies == []
    assert snapshot.slo_status == "not_configured"
    assert snapshot.service == "checkout"
    assert snapshot.telemetry.success_rate == 18.2
    assert [point.value for point in snapshot.telemetry.success_rate_trend] == [18.1, 18.2]


def test_dashboard_retries_a_transient_failure_before_marking_telemetry_unavailable() -> None:
    class FlakyDashboardPrometheus:
        def __init__(self) -> None:
            self.calls = 0

        def get_metric(self, query_name: str, service: str) -> MetricQueryResult:
            self.calls += 1
            if self.calls == 1:
                raise httpx.ConnectError("local port-forward reset")
            values = {
                "service_5xx_rate": 0.0,
                "service_2xx_rate": 18.2,
                "service_request_rate": 18.2,
                "service_5xx_recovery_rate": 0.0,
                "service_2xx_recovery_rate": 18.2,
            }
            return MetricQueryResult(
                query_name=query_name,
                value=values[query_name],
                observed_at=datetime.now(UTC),
                source_ref=f"fake:{service}:{query_name}",
            )

        def get_metric_series(self, query_name: str, _service: str):
            value = 0.0 if query_name == "service_5xx_chart_rate" else 18.2
            return [(datetime(2026, 7, 15, 12, 0, tzinfo=UTC), value)]

    store = FakeActionStore()
    store.incident_state = LifecycleState.TRIAGING
    store.evidence = [
        EvidenceRecord(
            id="evidence-alert-1",
            incident_id="incident-1",
            source_type=EvidenceSourceType.ALERT,
            source_ref="alert://checkout",
            observed_at=datetime(2026, 7, 15, tzinfo=UTC),
            summary="Checkout alert fired",
            structured_payload={
                "commonLabels": {"service": "checkout", "severity": "critical"}
            },
            content_hash="hash",
        )
    ]
    prometheus = FlakyDashboardPrometheus()

    snapshot = DashboardService(
        store, Settings(), kubernetes=FakeKubernetesAdapter(), prometheus=prometheus
    ).snapshot("incident-1")

    assert snapshot.telemetry.status == "live"
    assert snapshot.telemetry.success_rate == 18.2
    # A snapshot contains five current-value reads. Concurrent collection lets
    # in-flight reads finish before the bounded whole-snapshot retry, so the
    # first transient error is followed by one complete second collection.
    assert prometheus.calls == 10


def test_fake_kubernetes_adapter_returns_typed_status() -> None:
    result = FakeKubernetesAdapter().get_workload_status("opspilot-demo", "checkout")
    assert result.namespace == "opspilot-demo"
    assert result.observed_at <= datetime.now(UTC)


def test_kubernetes_adapter_reads_deployment_and_restart_status() -> None:
    deployment = SimpleNamespace(
        spec=SimpleNamespace(
            replicas=2,
            selector=SimpleNamespace(match_labels={"app": "checkout"}),
        ),
        status=SimpleNamespace(ready_replicas=1),
    )
    pod = SimpleNamespace(
        status=SimpleNamespace(
            container_statuses=[SimpleNamespace(restart_count=2), SimpleNamespace(restart_count=1)]
        )
    )
    apps_api = SimpleNamespace(read_namespaced_deployment=lambda *_: deployment)
    core_api = SimpleNamespace(
        list_namespaced_pod=lambda *_args, **_kwargs: SimpleNamespace(items=[pod])
    )

    result = KubernetesAdapter(apps_api=apps_api, core_api=core_api).get_workload_status(
        "opspilot-demo", "checkout"
    )

    assert result.ready_replicas == 1
    assert result.desired_replicas == 2
    assert result.restart_count == 3


def test_kubernetes_adapter_rejects_cross_namespace_reads() -> None:
    adapter = KubernetesAdapter(apps_api=SimpleNamespace(), core_api=SimpleNamespace())
    with pytest.raises(ValueError, match="restricted"):
        adapter.get_workload_status("default", "checkout")


def test_kubernetes_adapter_redacts_bounded_log_excerpt() -> None:
    core_api = SimpleNamespace(
        read_namespaced_pod_log=lambda *_args, **_kwargs: "ok\ntoken=very-secret\nstill ok"
    )
    adapter = KubernetesAdapter(apps_api=SimpleNamespace(), core_api=core_api)

    excerpt = adapter.get_log_excerpt("opspilot-demo", "checkout-1", "checkout", tail_lines=3)

    assert excerpt.lines == ["ok", "token=[REDACTED]", "still ok"]
    assert excerpt.redacted_line_count == 1
    with pytest.raises(ValueError, match="log tail"):
        adapter.get_log_excerpt("opspilot-demo", "checkout-1", "checkout", tail_lines=501)


def test_kubernetes_adapter_returns_typed_event_and_deployment_history() -> None:
    observed = datetime(2026, 7, 14, tzinfo=UTC)
    event = SimpleNamespace(
        metadata=SimpleNamespace(name="checkout-event", creation_timestamp=observed),
        involved_object=SimpleNamespace(kind="Pod", name="checkout-abc"),
        type="Warning",
        reason="BackOff",
        message="back-off restarting failed container",
        event_time=None,
        last_timestamp=None,
    )
    replica_set = SimpleNamespace(
        metadata=SimpleNamespace(
            name="checkout-abc",
            creation_timestamp=observed,
            annotations={"deployment.kubernetes.io/revision": "2"},
            owner_references=[SimpleNamespace(kind="Deployment", name="checkout")],
        ),
        spec=SimpleNamespace(
            template=SimpleNamespace(
                spec=SimpleNamespace(containers=[SimpleNamespace(image="checkout:bad")])
            )
        ),
    )
    deployment = SimpleNamespace(
        spec=SimpleNamespace(selector=SimpleNamespace(match_labels={"app": "checkout"}))
    )
    apps_api = SimpleNamespace(
        read_namespaced_deployment=lambda *_args: deployment,
        list_namespaced_replica_set=lambda *_args, **_kwargs: SimpleNamespace(items=[replica_set]),
    )
    core_api = SimpleNamespace(list_namespaced_event=lambda *_args: SimpleNamespace(items=[event]))
    adapter = KubernetesAdapter(apps_api=apps_api, core_api=core_api)

    events = adapter.get_events("opspilot-demo", "checkout")
    history = adapter.get_deployment_history("opspilot-demo", "checkout")

    assert events[0].reason == "BackOff"
    assert history[0].revision == "2"
    assert history[0].images == ["checkout:bad"]


def proposal(**overrides: object) -> ActionProposal:
    values: dict[str, object] = {
        "incident_id": "incident-1",
        "action_type": ActionType.RESTORE_RESPONSE_MODE,
        "namespace": "opspilot-demo",
        "workload": "checkout",
        "evidence_ids": ["evidence-1"],
        "expected_resource_version": "42",
        "expires_at": datetime(2026, 7, 15, tzinfo=UTC),
    }
    values.update(overrides)
    return ActionProposal.model_validate(values)


def test_action_policy_rejects_cross_namespace_stale_and_invalid_parameters() -> None:
    policy = ActionPolicy()
    now = datetime(2026, 7, 14, tzinfo=UTC)
    with pytest.raises(ValueError, match="outside"):
        policy.validate_for_preview(proposal(namespace="default"), now=now)
    with pytest.raises(ValueError, match="stale"):
        policy.validate_for_preview(proposal(expires_at=now), now=now)
    with pytest.raises(ValueError, match="scale requires"):
        policy.validate_for_preview(proposal(action_type=ActionType.SCALE), now=now)


def test_action_policy_requires_human_approval_and_unchanged_target() -> None:
    policy = ActionPolicy()
    candidate = proposal()
    now = datetime(2026, 7, 14, tzinfo=UTC)
    with pytest.raises(ValueError, match="human approval"):
        policy.validate_for_execution(candidate, None, "42", now=now)
    with pytest.raises(ValueError, match="target changed"):
        policy.validate_for_execution(candidate, now, "43", now=now)
    policy.validate_for_execution(candidate, now, "42", now=now)


def test_model_fixture_metadata_requires_expected_tool_call_and_never_invents_cost() -> None:
    response = SimpleNamespace(
        id="resp_fixture",
        usage=SimpleNamespace(input_tokens=12, output_tokens=8),
        output=[
            SimpleNamespace(
                type="function_call",
                name="get_incident_snapshot",
                arguments='{"incident_id":"fixture-incident-001"}',
            )
        ],
    )
    settings = Settings(
        LLM_PROVIDER="openai",
        OPENAI_MODEL="gpt-5.6-terra",
        MODEL_PRICE_INPUT_PER_MILLION="",
        MODEL_PRICE_OUTPUT_PER_MILLION="",
    )

    result = result_from_response(response, settings, latency_ms=123)

    assert result["pass"] is True
    assert result["estimated_cost_usd"] is None
    assert "not estimated" in str(result["cost_status"])


def test_settings_treats_blank_optional_price_as_unconfigured() -> None:
    settings = Settings(
        OPENAI_MODEL="gpt-5.6-terra",
        MODEL_PRICE_INPUT_PER_MILLION="",
        MODEL_PRICE_OUTPUT_PER_MILLION="",
    )

    assert settings.model_price_input_per_million is None
    assert settings.model_price_output_per_million is None


def test_openrouter_provider_uses_its_key_base_url_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        pass

    def fake_openai(**kwargs: object) -> FakeClient:
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr("opspilot.llm_provider.OpenAI", fake_openai)
    settings = Settings(
        LLM_PROVIDER="openrouter",
        OPENROUTER_API_KEY="test-openrouter-key",
        OPENROUTER_MODEL="openai/gpt-5.6-luna",
    )

    client = create_responses_client(settings)

    assert isinstance(client, FakeClient)
    assert settings.active_model == "openai/gpt-5.6-luna"
    assert captured == {
        "api_key": "test-openrouter-key",
        "base_url": "https://openrouter.ai/api/v1",
    }


def test_direct_openai_provider_uses_its_key_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        pass

    def fake_openai(**kwargs: object) -> FakeClient:
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr("opspilot.llm_provider.OpenAI", fake_openai)
    settings = Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test-openai-key",
        OPENAI_MODEL="gpt-5.6-terra",
    )

    client = create_responses_client(settings)

    assert isinstance(client, FakeClient)
    assert settings.active_model == "gpt-5.6-terra"
    assert captured == {"api_key": "test-openai-key"}


class FakeActionStore:
    def __init__(self) -> None:
        self.plans = {}
        self.incident_state = LifecycleState.TRIAGING
        self.evidence = [
            EvidenceRecord(
                id="evidence-1",
                incident_id="incident-1",
                source_type=EvidenceSourceType.ALERT,
                source_ref="alert://checkout",
                observed_at=datetime(2026, 7, 14, tzinfo=UTC),
                summary="checkout errors observed",
                content_hash="hash",
            )
        ]

    def list_evidence(self, _incident_id: str):
        return self.evidence

    def incident(self, _incident_id: str):
        return {"id": "incident-1", "lifecycle_state": self.incident_state.value}

    def transition(self, _incident_id: str, target, actor: str, reason: str):
        del actor, reason
        self.incident_state = target
        return self.incident("incident-1")

    def create_action_plan(self, plan):
        self.plans[plan.id] = plan

    def action_plan(self, action_id: str):
        return self.plans.get(action_id)

    def update_action_plan(self, plan, expected_status=None):
        if expected_status is not None and self.plans[plan.id].status is not expected_status:
            raise ValueError("action plan changed concurrently; reload before continuing")
        self.plans[plan.id] = plan


class FakeRemediationAdapter:
    def __init__(self) -> None:
        self.version = "42"
        self.executed = False

    def resource_version(self, _namespace: str, _workload: str) -> str:
        return self.version

    def preview(self, proposal):
        return {"dry_run": True, "resource_version": proposal.expected_resource_version}

    def execute(self, _proposal):
        self.executed = True
        return {"executed": True}


class FakeAppsApi:
    """Minimal Apps API fixture for concrete preview contract tests."""

    def __init__(self, fail_mode: str) -> None:
        self.deployment = SimpleNamespace(
            metadata=SimpleNamespace(resource_version="42"),
            spec=SimpleNamespace(
                replicas=1,
                template=SimpleNamespace(
                    metadata=SimpleNamespace(annotations={}),
                    spec=SimpleNamespace(
                        containers=[
                            SimpleNamespace(
                                name="checkout",
                                env=[SimpleNamespace(name="FAIL_MODE", value=fail_mode)],
                            )
                        ]
                    ),
                ),
            ),
        )
        self.preview_patch = None

    def read_namespaced_deployment(self, _workload: str, _namespace: str):
        return self.deployment

    def patch_namespaced_deployment(
        self, _workload: str, _namespace: str, patch, dry_run: str | None = None
    ):
        assert dry_run == "All"
        self.preview_patch = patch
        return self.deployment


class FakeWorkloadReader:
    def __init__(self, ready_replicas: int = 1, desired_replicas: int = 1) -> None:
        self.ready_replicas = ready_replicas
        self.desired_replicas = desired_replicas

    def get_workload_status(self, namespace: str, workload: str) -> WorkloadStatus:
        return WorkloadStatus(
            namespace=namespace,
            workload=workload,
            ready_replicas=self.ready_replicas,
            desired_replicas=self.desired_replicas,
            restart_count=0,
            observed_at=datetime.now(UTC),
        )


class FakeIndicatorReader:
    def __init__(self, value: float, success_value: float = 18.0) -> None:
        self.value = value
        self.success_value = success_value

    def get_metric(self, query_name: str, service: str) -> MetricQueryResult:
        return MetricQueryResult(
            query_name=query_name,
            value=self.success_value if query_name == "service_2xx_recovery_rate" else self.value,
            observed_at=datetime.now(UTC),
            source_ref=f"fake:{service}:{query_name}",
        )


class UnavailableIndicatorReader:
    def get_metric(self, _query_name: str, _service: str) -> MetricQueryResult:
        raise RuntimeError("controlled indicator outage")


class TransientPrometheusIndicatorReader:
    def get_metric(self, _query_name: str, _service: str) -> MetricQueryResult:
        raise httpx.ConnectError("controlled local port-forward reset")


def test_kubernetes_preview_shows_exact_change_and_rejects_noop() -> None:
    proposal = ActionProposal(
        incident_id="incident-1",
        action_type=ActionType.RESTORE_RESPONSE_MODE,
        namespace="opspilot-demo",
        workload="checkout",
        evidence_ids=["evidence-1"],
        expected_resource_version="42",
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )
    changed_api = FakeAppsApi(fail_mode="true")
    changed_preview = KubernetesRemediationAdapter(apps_api=changed_api).preview(proposal)

    assert changed_preview["changes"] == [
        {
            "field": "FAIL_MODE",
            "before": "true",
            "after": "false",
            "effect": "A rollout replaces the checkout pod.",
        }
    ]
    assert changed_api.preview_patch is not None

    noop_api = FakeAppsApi(fail_mode="false")
    with pytest.raises(ValueError, match="would make no controlled change"):
        KubernetesRemediationAdapter(apps_api=noop_api).preview(proposal)
    assert noop_api.preview_patch is None


def test_remediation_requires_evidence_approval_and_unchanged_target() -> None:
    store = FakeActionStore()
    adapter = FakeRemediationAdapter()
    coordinator = RemediationCoordinator(store, adapter)
    plan = coordinator.propose("incident-1", ActionType.RESTORE_RESPONSE_MODE, ["evidence-1"])

    with pytest.raises(ValueError, match="human approval"):
        coordinator.execute(plan.id)
    approved = coordinator.approve(plan.id, "oncall@example.test")
    assert approved.approved_by == "oncall@example.test"

    adapter.version = "43"
    with pytest.raises(ValueError, match="target changed"):
        coordinator.execute(plan.id)
    assert adapter.executed is False
    stale = store.action_plan(plan.id)
    assert stale.status is ActionPlanStatus.STALE
    assert stale.invalidation_reason == "action proposal is stale because the target changed"
    assert store.incident_state is LifecycleState.TRIAGING


def test_remediation_rejects_unknown_evidence() -> None:
    with pytest.raises(ValueError, match="must cite evidence"):
        RemediationCoordinator(FakeActionStore(), FakeRemediationAdapter()).propose(
            "incident-1", ActionType.RESTORE_RESPONSE_MODE, ["invented"]
        )


def test_remediation_rejects_tampered_preview_before_approval() -> None:
    store = FakeActionStore()
    coordinator = RemediationCoordinator(store, FakeRemediationAdapter())
    plan = coordinator.propose("incident-1", ActionType.RESTORE_RESPONSE_MODE, ["evidence-1"])
    store.plans[plan.id] = plan.model_copy(update={"preview": {"dry_run": False}})

    with pytest.raises(ValueError, match="preview changed"):
        coordinator.approve(plan.id, "oncall@example.test")


def test_remediation_records_human_rejection_and_reopens_triage() -> None:
    store = FakeActionStore()
    coordinator = RemediationCoordinator(store, FakeRemediationAdapter())
    plan = coordinator.propose("incident-1", ActionType.RESTORE_RESPONSE_MODE, ["evidence-1"])

    rejected = coordinator.reject(plan.id, "oncall@example.test", "Need another signal")

    assert rejected.status is ActionPlanStatus.REJECTED
    assert rejected.rejected_by == "oncall@example.test"
    assert rejected.rejection_reason == "Need another signal"
    assert store.incident_state is LifecycleState.TRIAGING


def test_expired_preview_is_audited_and_returns_incident_to_triage() -> None:
    store = FakeActionStore()
    coordinator = RemediationCoordinator(store, FakeRemediationAdapter())
    plan = coordinator.propose("incident-1", ActionType.RESTORE_RESPONSE_MODE, ["evidence-1"])
    expired_proposal = plan.proposal.model_copy(
        update={"expires_at": datetime(2026, 7, 14, tzinfo=UTC)}
    )
    expired = plan.model_copy(
        update={
            "proposal": expired_proposal,
            "fingerprint": action_fingerprint(expired_proposal, plan.preview),
        }
    )
    store.plans[plan.id] = expired

    with pytest.raises(ValueError, match="only a previewed"):
        coordinator.approve(plan.id, "oncall@example.test")

    assert store.action_plan(plan.id).status is ActionPlanStatus.EXPIRED
    assert store.incident_state is LifecycleState.TRIAGING


def test_remediation_executes_only_after_approval_and_verifies_recovery() -> None:
    store = FakeActionStore()
    adapter = FakeRemediationAdapter()
    coordinator = RemediationCoordinator(store, adapter)
    previewed = coordinator.propose("incident-1", ActionType.RESTORE_RESPONSE_MODE, ["evidence-1"])
    assert previewed.status is ActionPlanStatus.PREVIEWED
    assert store.incident_state is LifecycleState.ACTION_PROPOSED
    verification_plan = previewed.preview["verification_plan"]
    assert verification_plan["independent"] is True
    assert verification_plan["checks"][1] == {
        "kind": "metric_threshold",
        "query": "service_5xx_recovery_rate",
        "window": "15 seconds",
        "maximum": 0.01,
    }
    assert verification_plan["checks"][2] == {
        "kind": "metric_threshold",
        "query": "service_2xx_recovery_rate",
        "window": "15 seconds",
        "minimum": 0.01,
    }

    coordinator.approve(previewed.id, "oncall@example.test")
    executed = coordinator.execute(previewed.id)
    assert executed.status is ActionPlanStatus.EXECUTED
    assert adapter.executed is True
    assert store.incident_state is LifecycleState.MONITORING

    completed, result = coordinator.verify(
        previewed.id,
        RecoveryVerifier(FakeWorkloadReader(), FakeIndicatorReader(value=0.001)),
    )
    assert result.recovered is True
    assert completed.status is ActionPlanStatus.VERIFIED
    assert completed.recovery == result.model_dump(mode="json")
    assert store.incident_state is LifecycleState.RESOLVED


def test_recovery_waits_for_rollout_readiness_without_reopening_triage() -> None:
    store = FakeActionStore()
    coordinator = RemediationCoordinator(store, FakeRemediationAdapter())
    plan = coordinator.propose("incident-1", ActionType.RESTORE_RESPONSE_MODE, ["evidence-1"])
    coordinator.approve(plan.id, "oncall@example.test")
    coordinator.execute(plan.id)

    pending, result = coordinator.verify(
        plan.id,
        RecoveryVerifier(FakeWorkloadReader(ready_replicas=0, desired_replicas=1)),
    )

    assert result.recovered is False
    assert result.pending is True
    assert "waiting for the controlled rollout" in result.reason
    assert pending.status is ActionPlanStatus.EXECUTED
    assert store.incident_state is LifecycleState.MONITORING


def test_failed_recovery_reopens_triage_without_resolving_incident() -> None:
    store = FakeActionStore()
    coordinator = RemediationCoordinator(store, FakeRemediationAdapter())
    plan = coordinator.propose("incident-1", ActionType.RESTORE_RESPONSE_MODE, ["evidence-1"])
    coordinator.approve(plan.id, "oncall@example.test")
    coordinator.execute(plan.id)

    observed_at = datetime(2026, 7, 14, tzinfo=UTC)
    pending, first = coordinator.verify(
        plan.id,
        RecoveryVerifier(FakeWorkloadReader(), FakeIndicatorReader(value=0.5)),
        now=observed_at,
    )
    assert first.pending is True
    assert pending.status is ActionPlanStatus.EXECUTED
    assert store.incident_state is LifecycleState.MONITORING

    completed, result = coordinator.verify(
        plan.id,
        RecoveryVerifier(FakeWorkloadReader(), FakeIndicatorReader(value=0.5)),
        now=observed_at.replace(second=15),
    )
    assert result.recovered is False
    assert result.pending is False
    assert completed.status is ActionPlanStatus.FAILED
    assert store.incident_state is LifecycleState.TRIAGING


def test_response_mode_recovery_requires_observed_2xx_traffic() -> None:
    store = FakeActionStore()
    coordinator = RemediationCoordinator(store, FakeRemediationAdapter())
    plan = coordinator.propose("incident-1", ActionType.RESTORE_RESPONSE_MODE, ["evidence-1"])
    coordinator.approve(plan.id, "oncall@example.test")
    coordinator.execute(plan.id)

    observed_at = datetime(2026, 7, 14, tzinfo=UTC)
    pending, first = coordinator.verify(
        plan.id,
        RecoveryVerifier(FakeWorkloadReader(), FakeIndicatorReader(value=0.0, success_value=0.0)),
        now=observed_at,
    )
    assert first.pending is True
    assert first.service_2xx_rate == 0.0
    assert pending.status is ActionPlanStatus.EXECUTED

    monitoring, result = coordinator.verify(
        plan.id,
        RecoveryVerifier(FakeWorkloadReader(), FakeIndicatorReader(value=0.0, success_value=0.0)),
        now=observed_at.replace(second=15),
    )
    assert result.recovered is False
    assert result.pending is True
    assert "no successful checkout traffic" in result.reason
    assert monitoring.status is ActionPlanStatus.EXECUTED
    assert store.incident_state is LifecycleState.MONITORING


def test_memory_recovery_requires_a_stable_restart_count_over_time() -> None:
    store = FakeActionStore()
    coordinator = RemediationCoordinator(store, FakeRemediationAdapter())
    plan = coordinator.propose("incident-1", ActionType.RESTORE_MEMORY_MODE, ["evidence-1"])
    verification_plan = plan.preview["verification_plan"]
    assert verification_plan["checks"][1] == {
        "kind": "restart_stability",
        "condition": "restart count must not increase for 30 seconds",
        "window": "30 seconds",
    }
    coordinator.approve(plan.id, "oncall@example.test")
    coordinator.execute(plan.id)
    observed_at = datetime(2026, 7, 14, tzinfo=UTC)

    pending, first = coordinator.verify(
        plan.id,
        RecoveryVerifier(FakeWorkloadReader()),
        now=observed_at,
    )
    assert first.pending is True
    assert pending.status is ActionPlanStatus.EXECUTED
    assert store.incident_state is LifecycleState.MONITORING

    completed, result = coordinator.verify(
        plan.id,
        RecoveryVerifier(FakeWorkloadReader()),
        now=observed_at.replace(second=30),
    )
    assert result.recovered is True
    assert completed.status is ActionPlanStatus.VERIFIED
    assert store.incident_state is LifecycleState.RESOLVED


def test_verifier_outage_keeps_executed_action_retryable() -> None:
    store = FakeActionStore()
    coordinator = RemediationCoordinator(store, FakeRemediationAdapter())
    plan = coordinator.propose("incident-1", ActionType.RESTORE_RESPONSE_MODE, ["evidence-1"])
    coordinator.approve(plan.id, "oncall@example.test")
    coordinator.execute(plan.id)

    with pytest.raises(RuntimeError, match="indicator outage"):
        coordinator.verify(
            plan.id,
            RecoveryVerifier(FakeWorkloadReader(), UnavailableIndicatorReader()),
        )
    assert store.action_plan(plan.id).status is ActionPlanStatus.EXECUTED
    assert store.incident_state is LifecycleState.MONITORING


def test_transient_prometheus_disconnect_keeps_response_recovery_in_monitoring() -> None:
    store = FakeActionStore()
    coordinator = RemediationCoordinator(store, FakeRemediationAdapter())
    plan = coordinator.propose("incident-1", ActionType.RESTORE_RESPONSE_MODE, ["evidence-1"])
    coordinator.approve(plan.id, "oncall@example.test")
    coordinator.execute(plan.id)

    pending, result = coordinator.verify(
        plan.id,
        RecoveryVerifier(FakeWorkloadReader(), TransientPrometheusIndicatorReader()),
    )

    assert result.pending is True
    assert result.recovered is False
    assert "temporarily unavailable" in result.reason
    assert pending.status is ActionPlanStatus.EXECUTED
    assert store.incident_state is LifecycleState.MONITORING
