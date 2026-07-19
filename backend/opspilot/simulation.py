"""Explicit local-only controls for the reproducible Kubernetes demo scenarios."""

from datetime import UTC, datetime

from kubernetes import client, config
from pydantic import BaseModel, ConfigDict

from opspilot.domain.alerts import AlertmanagerAlert, AlertmanagerWebhookV4
from opspilot.domain.incidents import LifecycleState
from opspilot.storage.incidents import SQLiteIncidentStore


class DemoScenarioStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    scenario: str
    recommended_action: str
    message: str


class ControlledScenarioAdapter:
    """Can change only the two documented fault toggles on the demo checkout app."""

    def __init__(self, apps_api: client.AppsV1Api | None = None) -> None:
        if apps_api is None:
            config.load_kube_config()
            apps_api = client.AppsV1Api()
        self._apps_api = apps_api

    def set_modes(self, namespace: str, *, fail_mode: bool, memory_leak_mode: bool) -> None:
        deployment = self._apps_api.read_namespaced_deployment("checkout", namespace)
        self._apps_api.patch_namespaced_deployment(
            "checkout",
            namespace,
            {
                "metadata": {"resourceVersion": deployment.metadata.resource_version},
                "spec": {
                    "strategy": {
                        "type": "RollingUpdate",
                        "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1},
                    },
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "checkout",
                                    "env": [
                                        {"name": "FAIL_MODE", "value": str(fail_mode).lower()},
                                        {
                                            "name": "MEMORY_LEAK_MODE",
                                            "value": str(memory_leak_mode).lower(),
                                        },
                                    ],
                                }
                            ]
                        }
                    }
                },
            },
        )


class DemoScenarioService:
    def __init__(
        self,
        store: SQLiteIncidentStore,
        namespace: str,
        adapter: ControlledScenarioAdapter | None = None,
    ) -> None:
        self._store = store
        self._namespace = namespace
        self._adapter = adapter or ControlledScenarioAdapter()

    def start(self, scenario: str) -> DemoScenarioStart:
        if scenario not in {"p1", "p2"}:
            raise ValueError("only the documented p1 and p2 demo scenarios are available")
        is_p1 = scenario == "p1"
        self._adapter.set_modes(
            self._namespace,
            fail_mode=is_p1,
            memory_leak_mode=not is_p1,
        )
        # One active incident per controlled scenario prevents repeat clicks from
        # filling the on-call queue with indistinguishable demo records. A new
        # record is created only after the prior scenario incident reaches a
        # closed lifecycle state.
        run_id = f"ui-{scenario}"
        result = self._store.ingest(self._payload(scenario, run_id))
        if result.incident_id is None:
            raise RuntimeError("controlled scenario did not create an incident")
        incident_id = result.incident_id
        if result.disposition == "incident_created":
            for target in (
                LifecycleState.CLASSIFIED,
                LifecycleState.ENRICHED,
                LifecycleState.TRIAGING,
            ):
                self._store.transition(
                    incident_id,
                    target,
                    actor="opspilot-demo-controller",
                    reason=(
                        f"controlled {scenario.upper()} simulation started; "
                        "awaiting evidence and engineer review"
                    ),
                )
        return DemoScenarioStart(
            incident_id=incident_id,
            scenario=scenario.upper(),
            recommended_action="restore_response_mode" if is_p1 else "restore_memory_mode",
            message=(
                "P1 is rolling out controlled checkout 500 responses under load."
                if is_p1
                else "P2 is rolling out the controlled checkout memory-leak mode under load."
            )
            if result.disposition == "incident_created"
            else (
                f"Existing active {scenario.upper()} controlled incident reopened; "
                "complete verified recovery before starting a new run."
            ),
        )

    def reset(self) -> None:
        self._adapter.set_modes(self._namespace, fail_mode=False, memory_leak_mode=False)

    @staticmethod
    def _payload(scenario: str, run_id: str) -> AlertmanagerWebhookV4:
        now = datetime.now(UTC)
        alert_name = "Checkout5xxHigh" if scenario == "p1" else "CheckoutMemoryPressure"
        summary = (
            "Controlled checkout 5xx scenario started"
            if scenario == "p1"
            else "Controlled checkout memory scenario started"
        )
        labels = {
            "alertname": alert_name,
            "service": "checkout",
            "severity": "critical",
            "opspilot_run_id": run_id,
        }
        return AlertmanagerWebhookV4(
            version="4",
            groupKey=f'{{alertname="{alert_name}",opspilot_run_id="{run_id}"}}',
            truncatedAlerts=0,
            status="firing",
            receiver="opspilot-demo",
            groupLabels={"alertname": alert_name, "opspilot_run_id": run_id},
            commonLabels=labels,
            commonAnnotations={"summary": summary},
            externalURL="http://alertmanager.local/",
            alerts=[
                AlertmanagerAlert(
                    status="firing",
                    labels=labels,
                    annotations={"summary": summary},
                    startsAt=now,
                    generatorURL="http://prometheus.local/graph?g0.expr=checkout",
                    fingerprint=f"{scenario}-checkout-{run_id}",
                )
            ],
        )
