from opspilot.domain.incidents import LifecycleState
from opspilot.simulation import DemoScenarioService
from opspilot.storage.incidents import IngestResult


class FakeScenarioAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, bool]] = []

    def set_modes(self, namespace: str, *, fail_mode: bool, memory_leak_mode: bool) -> None:
        self.calls.append((namespace, fail_mode, memory_leak_mode))


class FakeScenarioStore:
    def __init__(self, disposition: str = "incident_created") -> None:
        self.transitions: list[LifecycleState] = []
        self.payload = None
        self.disposition = disposition

    def ingest(self, payload):
        self.payload = payload
        return IngestResult(incident_id="demo-incident", disposition=self.disposition)

    def transition(self, _incident_id, target, actor, reason):
        del actor, reason
        self.transitions.append(target)


def test_p1_demo_starts_only_the_controlled_failure_and_reaches_triage() -> None:
    adapter = FakeScenarioAdapter()
    store = FakeScenarioStore()

    result = DemoScenarioService(store, "opspilot-demo", adapter).start("p1")

    assert result.recommended_action == "restore_response_mode"
    assert adapter.calls == [("opspilot-demo", True, False)]
    assert store.payload.common_labels["service"] == "checkout"
    assert store.payload.common_labels["opspilot_run_id"] == "ui-p1"
    assert store.transitions == [
        LifecycleState.CLASSIFIED,
        LifecycleState.ENRICHED,
        LifecycleState.TRIAGING,
    ]


def test_p2_demo_selects_the_memory_restoration_without_enabling_p1() -> None:
    adapter = FakeScenarioAdapter()
    store = FakeScenarioStore()

    result = DemoScenarioService(store, "opspilot-demo", adapter).start("p2")

    assert result.recommended_action == "restore_memory_mode"
    assert adapter.calls == [("opspilot-demo", False, True)]


def test_reset_turns_off_both_controlled_failure_modes() -> None:
    adapter = FakeScenarioAdapter()

    DemoScenarioService(FakeScenarioStore(), "opspilot-demo", adapter).reset()

    assert adapter.calls == [("opspilot-demo", False, False)]


def test_restarting_an_open_demo_scenario_reuses_its_incident() -> None:
    adapter = FakeScenarioAdapter()
    store = FakeScenarioStore(disposition="alert_update_recorded")

    result = DemoScenarioService(store, "opspilot-demo", adapter).start("p1")

    assert result.incident_id == "demo-incident"
    assert store.transitions == []
    assert "Existing active P1" in result.message
