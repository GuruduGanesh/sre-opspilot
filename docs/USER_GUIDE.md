# OpsPilot on-call rehearsal guide

OpsPilot is a controlled local Kubernetes demonstration. Use this guide to run a
P1 or P2 rehearsal from alert to RCA. It is not a production runbook and does
not connect to a production cluster or pager.

## 1. Start the local environment

From PowerShell in the repository root, create the dedicated kind cluster and
start the local API:

```powershell
.\scripts\scenario.ps1 create

# In another terminal. Keep this terminal running. It starts the local
# Prometheus port-forward when needed and starts the API with that endpoint.
# It enables deterministic evidence reports for local UI rehearsal; they are
# explicitly labelled and are not GPT-5.6 outputs.
.\scripts\run-console.ps1 -ControlledSimulation
```

Start the console in a third terminal:

```powershell
cd frontend
npm run dev
```

Open `http://127.0.0.1:5173`. The local cluster must be running before using
the simulation controls. The console shows an explicit telemetry-unavailable
state when Prometheus is not connected; it never draws synthetic data.

For the four captured states of the controlled P1 walkthrough, see the
[recorded walkthrough in the README](../README.md#recorded-controlled-p1-walkthrough).

Confirm the API was restarted in the intended mode:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
# Expected: status = ok; investigation_mode = controlled_simulation
```

For a read-only local infrastructure check before opening the console, run:

```powershell
.\scripts\demo-proof.ps1
```

It displays the kind cluster, demo workloads, Prometheus readiness, API mode,
and console address. Add `-RequireLiveModel` only for a final live-model demo.

For a fresh start, run `.\scripts\prepare-demo.ps1 -ClearIncidentHistory`.
It stops only the local OpsPilot API if it is running, resets the dedicated demo
workload, removes ignored local incident history, and starts the API again in
the same investigation mode. A browser tab pointing to a deleted incident will
show no record; return to `http://127.0.0.1:5173` and start a new scenario.

## 2. Start a controlled incident

Use the scenario selector beside **Open**, then click **Inject P1 incident** or
**Inject P2 incident**:

- **Start P1 · 5xx rollout** enables controlled checkout failures under the
  local load generator. This produces real HTTP 500 responses and Prometheus
  samples.
- **Start P2 · memory pressure** enables the controlled memory-leak behavior
  for checkout. Kubernetes should show restart or OOMKill evidence as the
  scenario progresses.

The first start creates a persisted incident and moves it to `Triaging`. A
repeated click while the matching scenario incident is still open is deduplicated:
it adds the current scenario alert evidence to that same record rather than
creating confusing duplicate incidents. The left-rail queue selects the
highest-priority open incident automatically. Select another bullet to review it,
or use `?incident=<id>` to open a specific record.

Do not use **Reset environment** while an action is being approved or verified.
Reset turns off only the two controlled failure modes; it does not approve,
execute, or erase an incident.

## 3. Investigate before proposing a fix

The on-call engineer should establish what is known before accepting a remedy.
Use the command center in this order:

1. Confirm the alert, severity, current 5xx rate, request rate, workload
   readiness, and restart count.
2. Review the 15-minute 5xx trend. A P1 should show a rise after the controlled
   failure starts; P2 may have a normal 5xx rate but unhealthy pods or restarts.
3. Read the persisted alert evidence, current Kubernetes events, and deployment
   history. These are the sources that support a conclusion.
4. Confirm the displayed scope. In this demo it is only the checkout workload;
   dependency topology, customer impact, and SLO are deliberately shown as
   unavailable when no source exists.
5. In controlled simulation mode, ask a focused question such as **What
   changed?**, **Show evidence**, **What is affected?**, or **Current health**.
   The report is a deterministic summary of fresh bounded local evidence and is
   visibly marked **not GPT-5.6**. It is appropriate for rehearsing the console,
   not for a final live-model claim.
6. If a live GPT-5.6 provider is configured, ask a focused follow-up question:
   **What changed before the alert?**, **Show evidence**, **What is affected?**,
   or **Current health**. OpsPilot captures a fresh bounded Kubernetes and
   Prometheus snapshot before asking the model. A report must cite persisted
   evidence and can show contradictory evidence.

If the console says the investigation model is unavailable, the controlled
evidence and remediation workflow still works. Do not treat an unavailable model
as a root-cause conclusion; configure the ignored provider key and run the model
fixture before relying on conversational answers.

## 4. Choose the recommended controlled remediation

The recommendation must match the evidence and scenario. The two demonstrated
recoveries are:

| Scenario | Evidence to confirm | Recommended action | Why |
| --- | --- | --- | --- |
| P1 checkout 5xx rollout | Sustained 5xx rise, recent controlled checkout revision, ready workload | **Restore checkout response mode** | Reverses only the controlled failure mode; recovery requires readiness and 5xx at or below `0.010/s` over 15 seconds. |
| P2 memory pressure | Restart/OOMKill evidence and unhealthy checkout workload | **Restore controlled memory mode** | Removes the injected memory-leak behavior; recovery requires readiness and a restart count that does not increase for 30 seconds. |

**Restart checkout workload** and **Scale checkout workload** are allowlisted
diagnostic options, but they are not the demonstrated cure for P2. Do not choose
them merely because they are available. If the evidence is ambiguous, remain in
triage and collect more evidence rather than approving a speculative change.

## 5. Preview, approve, execute, and verify

1. Select the recommended action and click **Create dry-run preview**.
2. Read the target, evidence binding, server-side Kubernetes dry-run, and
   independent verification checks. A preview changes nothing.
3. Click **Review approval**. Enter the engineer identifier that is appropriate
   for this local rehearsal, then click **Approve this exact plan**.
4. Click **Execute approved action**. The server rejects an expired, stale, or
   altered plan rather than executing it.
5. Click **Verify recovery**. The independent verifier—not a model response—must
   confirm recovery. P1 requires checkout readiness, the bounded 5xx metric, and
   observed checkout 2xx traffic. If traffic has not resumed yet, P1 remains in
   `Monitoring` and **Check stability again** stays available; P2 requires
   readiness and a stable restart count for 30 seconds.

Only after verification does the lifecycle move to `Resolved`. If verification
fails, return to triage and reassess the evidence. Never represent an action as
successful because a model suggested it or a preview completed.

## 6. Draft and review the RCA

After the incident is `Resolved`:

1. Click **Draft RCA**.
2. Review the factual incident context, investigation conclusion boundary,
   recovery and verification record, affected scope, follow-up guidance,
   timestamped timeline, and recorded actions.
3. If the review is complete, click **Mark RCA published**. In this controlled
   build that records the lifecycle state only; it does not publish to an
   external system.

The RCA does not invent a root cause, customer impact, dependency topology, or
prevention action when there is no supporting record. One direct GPT-5.6
conclusion is recorded for the controlled P1 walkthrough; it is included only
for that incident and retains its evidence identifiers and stated limitations.

## 7. Repeat safely

Use **Reset environment** after a completed rehearsal, then start a new P1 or
P2 scenario. Each run creates a separate incident trail. Review prior runs from
the queue only as records; do not reuse an old action approval for a new run.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Queue is empty | Start P1 or P2, then use **Refresh** in the left rail. |
| Telemetry is unavailable | Confirm the kind scenario is running, then restart the API with `./scripts/run-console.ps1`. It validates or creates the local Prometheus port-forward. |
| Trend has no line immediately after P1 | Leave the controlled failure active long enough for Prometheus to scrape and the five-second console refresh to receive samples. |
| Model investigation is unavailable | For a UI rehearsal, restart the API with `./scripts/run-console.ps1 -ControlledSimulation`; reports remain visibly marked as not GPT-5.6. For live model answers, configure the ignored provider key and confirm usable GPT-5.6 provider quota. |
| A remediation action is blocked | Read the message. The usual causes are missing evidence, expired preview, changed Kubernetes target, or absent explicit approval. |
| RCA button is disabled | Recovery has not been independently verified. Complete or troubleshoot verification first. |
