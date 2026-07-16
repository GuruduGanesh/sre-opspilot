from enum import StrEnum


class LifecycleState(StrEnum):
    RECEIVED = "Received"
    CLASSIFIED = "Classified"
    ENRICHED = "Enriched"
    TRIAGING = "Triaging"
    ACTION_PROPOSED = "ActionProposed"
    EXECUTING = "Executing"
    MONITORING = "Monitoring"
    RESOLVED = "Resolved"
    RCA = "RCA"
    RCA_PUBLISHED = "RCAPublished"


_ALLOWED_TRANSITIONS: dict[LifecycleState, set[LifecycleState]] = {
    LifecycleState.RECEIVED: {LifecycleState.CLASSIFIED},
    LifecycleState.CLASSIFIED: {LifecycleState.ENRICHED},
    LifecycleState.ENRICHED: {LifecycleState.TRIAGING},
    LifecycleState.TRIAGING: {LifecycleState.ACTION_PROPOSED},
    LifecycleState.ACTION_PROPOSED: {LifecycleState.TRIAGING, LifecycleState.EXECUTING},
    LifecycleState.EXECUTING: {LifecycleState.MONITORING, LifecycleState.TRIAGING},
    LifecycleState.MONITORING: {LifecycleState.TRIAGING, LifecycleState.RESOLVED},
    LifecycleState.RESOLVED: {LifecycleState.RCA},
    LifecycleState.RCA: {LifecycleState.RCA_PUBLISHED},
    LifecycleState.RCA_PUBLISHED: set(),
}


def validate_transition(current: LifecycleState, target: LifecycleState) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"invalid lifecycle transition: {current} -> {target}")
