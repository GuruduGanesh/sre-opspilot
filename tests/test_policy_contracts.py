from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from opspilot.adapters.kubernetes import FakeKubernetesAdapter, KubernetesAdapter
from opspilot.adapters.prometheus import PrometheusAdapter
from opspilot.domain.actions import ActionPolicy, ActionProposal, ActionType
from opspilot.model_selection import result_from_response
from opspilot.settings import Settings


def test_prometheus_adapter_rejects_raw_or_unknown_query() -> None:
    adapter = PrometheusAdapter("http://prometheus.example")
    with pytest.raises(ValueError, match="unsupported metric query"):
        adapter.get_metric("up{job='anything'}", "checkout")


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
        "action_type": ActionType.ROLLBACK,
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
    settings = Settings(OPENAI_MODEL="gpt-5.6-terra")

    result = result_from_response(response, settings, latency_ms=123)

    assert result["pass"] is True
    assert result["estimated_cost_usd"] is None
    assert "not estimated" in str(result["cost_status"])
