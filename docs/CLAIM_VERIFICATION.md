# Claim verification ledger

This ledger prevents a common hackathon failure: describing a planned or
partially working feature as if it were a demonstrated result. A public claim is
allowed only when its evidence is linked here and the corresponding item is marked
verified in [PROGRESS.md](PROGRESS.md).

## Rules

1. **No invented metrics.** Do not state accuracy, MTTR reduction, latency, token
   cost, availability, or any percentage without a saved measurement method, raw
   result, date, and scenario.
2. **No invented features.** Do not say a feature exists because it is in an
   architecture diagram, backlog, prompt, mockup, or screen recording plan.
3. **No simulated results presented as production results.** The project may use
   injected incidents in a local Kubernetes test environment. Describe those
   incidents as controlled demonstrations and identify what was actually measured.
4. **No implied autonomy.** Remediation remains human-approved. Do not describe it
   as autonomous remediation or self-healing.
5. **No unsupported comparison.** Do not claim to outperform tools, save a stated
   amount of time, or be production ready unless an evaluation supports it.
6. **Use uncertainty honestly.** If the evidence is incomplete or a tool fails,
   the product and video must show that limitation rather than present certainty.

## Current public-claim status

| Proposed claim | Status | Required evidence before use in Devpost/video |
| --- | --- | --- |
| The repository contains the project architecture and delivery plan. | Verified | Files and Git history |
| Local prerequisites are available: Docker, kind, kubectl, Python, Node, and Git. | Verified | Recorded version checks in `PROGRESS.md` |
| OpsPilot collects live controlled Kubernetes/Prometheus telemetry. | Verified (controlled) | P1/P2 end-to-end scripts and saved evaluation artifact |
| OpsPilot identifies the P1 cause with GPT-5.6. | Not claimable yet | Successful live GPT-5.6 investigation with evidence-linked hypothesis |
| OpsPilot proposes a controlled recovery and blocks execution before human approval. | Verified (controlled) | P1/P2 remediation flows and action-policy tests |
| OpsPilot verifies recovery after an approved action. | Verified (controlled) | P1/P2 remediation flows with independent verifier output |
| OpsPilot supports the OOMKill/P2 scenario. | Verified (controlled) | Repeatable P2 remediation run with OOMKill and recovery evidence |
| OpsPilot creates an audit-derived postmortem draft. | Complete (contract verification) | Postmortem endpoint and persisted incident audit record; capture a live UI result before video |
| OpsPilot reduces MTTR, improves accuracy, or lowers cost. | Not claimable yet | Defined baseline, method, raw measurements, and repeatable result |
| OpsPilot is production ready or works on any cluster. | Not claimable | Out of scope for this Build Week submission |

## Evidence package for every completed feature

When a feature is ready, add a dated entry to `PROGRESS.md` with:

- the exact command or test used;
- the scenario and expected behavior;
- the actual result, including failures or limitations;
- a stable artifact path, test name, screenshot, or short recording; and
- the Devpost/README sentence that the evidence now permits.

## Final submission review

Before recording the video or submitting, review every sentence and frame against
this checklist:

- [ ] Is it implemented in the submitted commit?
- [ ] Did it work in the recorded or tested scenario?
- [ ] Can the repository, test output, or UI evidence support the statement?
- [ ] Is the scope clear: local controlled demonstration, not production system?
- [ ] Does any number have a method and raw result behind it?
- [ ] Does the narration clearly distinguish Codex-assisted development from
      GPT-5.6 runtime behavior?
