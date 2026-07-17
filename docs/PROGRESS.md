# OpsPilot progress tracker

> Update this document only after verifying the work locally. Use **Blocked** for
> work that cannot proceed without a user decision, credential, or external change.

> **Claim rule:** a feature, outcome, or metric stays *planned* until this tracker
> links it to repeatable evidence. Never promote a planned item to a Devpost claim
> because it appears in a design document, prompt, screenshot mockup, or demo plan.

## Current status

**Phase:** Controlled rehearsal workflow complete; live-provider validation blocked

**Next critical outcome:** Obtain usable GPT-5.6 provider access and record a
live evidence-linked fixture. Direct OpenAI is required for final validation and
recording once its project has usable quota; OpenRouter is optional for local
development experiments only.

## Delivery board

| Area | Status | Definition of done | Evidence / notes |
| --- | --- | --- | --- |
| Git repository | Complete | Local repository initialized with GitHub remote | `origin` points to `GuruduGanesh/sre-opspilot` |
| Private research separation | Complete | Internal plan, notes, Devpost archive, and research are ignored by Git | `.gitignore` verified locally |
| Public README | Complete | Product boundary, intended architecture, and status documented | `README.md` |
| Architecture and delivery plan | Complete | Safety model, tool boundary, milestones, and tests documented | `docs/ARCHITECTURE.md`, `docs/DELIVERY_PLAN.md` |
| Local runtime check | Complete | Docker, Compose, kind, kubectl, Python, Node, and Git available | Verified July 14, 2026 |
| OpenAI developer-docs connector | Pending restart | Connector available in the current Codex session | Installed globally; restart Codex before API implementation verification |
| OpenAI API access / Build Week credits | Blocked | Configure server-side access without exposing a key | Local key now has Responses API permission, but the project returned `429 insufficient_quota`; Codex credits do not establish API quota |
| Backend scaffold | Complete | FastAPI app, dependency management, health endpoint, tests | `uv run pytest` (9 passing tests) |
| Frontend scaffold | Complete | React/TypeScript app with a build and test command | `npm run build` passes locally |
| kind simulation environment | Complete | Services, Prometheus, load generator, reset command | `opspilot-dev` kind cluster, verified July 14 |
| P1 bad-deploy scenario | Complete (controlled verification) | Reproducible 5xx telemetry, deployment revision, recovery baseline | `artifacts/verification-p1-20260714.md`; repeat before video |
| P2 OOMKill scenario | Complete (controlled remediation verification) | Reproducible OOMKill/restarts and evidence-bound restoration | `scripts/test-e2e-p2-remediation.ps1` observed OOMKilled, rejected an unapproved action, then restored the controlled memory mode and verified rollout health |
| Scenario alert ingress | Complete (controlled verification) | Alertmanager v4-compatible payload validates, delivery-deduplicates, and creates `Received` incident | 6 ingress tests plus local API run; not an Alertmanager deployment |
| Evidence collection adapters | Complete (controlled verification) | K8s, logs, metrics, deploy history with typed outputs | Unit contracts plus `scripts/test-e2e-p1.ps1` read live events, a log excerpt, deployment history, workload status, and persisted alert evidence |
| Incident store and lifecycle | Complete (foundation) | SQLite-backed state transitions and audit events | Ingress evidence and valid server-owned transitions persist; no agent may set execution or resolution states |
| GPT-5.6 model selection | Blocked on API quota | Saved fixture result with model, quality, latency, tokens, tool calls, priced estimate, and GPT-5.6-only fallback | `scripts/model-smoke.ps1` confirmed the permission change on Jul 15, then received `429 insufficient_quota`; no fixture artifact exists |
| GPT-5.6 investigation workflow | Complete (provider contract and live-evidence verification); live answer blocked | Bounded function-tool investigation with structured hypotheses | Each request now snapshots and persists live Prometheus/Kubernetes/deployment context before the model gets the read-only evidence/timeline tools. Fake-client contracts pass; direct OpenAI is blocked by project quota and the OpenRouter GPT-5.6 test-provider has not yet made a live request. |
| Action policy and approval UX | Complete (controlled P1/P2 verification) | Allowlist, dry-run preflight, explicit approval, immutable audit, execution, and independent verification | Fresh July 16 P1/P2 runs passed. The preview exposes server-read before/after values, persisted evidence links, the self-declared local requester, server timestamp/expiry, fingerprint/resource-version binding, an audited rejection path, and independent recovery checks. No-op previews are refused. |
| Incident console and chat | Complete (controlled rehearsal; live GPT blocked) | Command-center UI with actual controlled telemetry/context, follow-up input, approval, recovery, and RCA | The console can start fresh local P1/P2 incidents, auto-select from a persisted open-incident queue, support `?incident=<id>` deep links, display the 15-second 5xx trend, and move a recovered incident through audit-derived RCA draft and published states. Chrome verified P2 queue selection and its restoration action. Live GPT remains blocked by provider quota/key. |
| Test suite and evaluation harness | Complete (controlled verification) | Unit, integration, end-to-end, safety tests, and saved evaluation results | `scripts/eval.ps1` v2 passed static checks, 27 unit/contract tests, frontend build, P1/P2 scenario checks, and both approved remediation flows; ignored `artifacts/eval-20260715-104325.json` |
| Judge test path | Ready (local wrapper verification) | Prebuilt no-rebuild local path | The checkout image archive was rebuilt July 16; `run-judge.ps1` imported it, provisioned the dedicated cluster, and passed both P1/P2 safety flows locally. Run the same wrapper from a clean machine before submission. |
| Devpost assets and compliance | Not started | Video, screenshots, final README, feedback ID, form review | — |

## Verified progress log

| Date | Completed work | Verification |
| --- | --- | --- |
| 2026-07-14 | Initialized the local Git repository and configured the GitHub remote. | `git remote -v` |
| 2026-07-14 | Archived the supplied Devpost pages, launch update, and current public forum pages for private reference. | 27 local archive files; ignored by Git |
| 2026-07-14 | Added ignore rules for private planning, local research, secrets, dependencies, build output, and local editor settings. | `git check-ignore -v` |
| 2026-07-14 | Verified Docker Desktop, Docker Compose, kind, kubectl, Python, Node, npm, and Git. | Version commands completed successfully |
| 2026-07-14 | Created the public README, architecture, delivery plan, and this tracker. | Files present in repository |
| 2026-07-14 | Built backend and frontend scaffolds with lint, type-check, test, and build commands. | Ruff and Ty pass; 9 pytest tests pass; `npm run build` passes |
| 2026-07-14 | Created and verified `opspilot-dev`, a dedicated kind environment containing checkout, Prometheus, and load generator. | All three deployments available; `scripts/scenario.ps1 status` |
| 2026-07-14 | Verified P1 controlled failure and recovery, private ingress, Prometheus 5xx telemetry, and a live Kubernetes workload-status read. | `artifacts/verification-p1-20260714.md` |
| 2026-07-15 | Added typed Kubernetes event, bounded redacted log, and deployment-history adapters; persisted normalized evidence and server-owned lifecycle transitions; added action-policy safety contracts and a timeline console. | Ruff and Ty pass; 15 unit/contract tests pass; frontend build passes |
| 2026-07-15 | Ran the repeatable P1 kind E2E path from a clean baseline. It injected P1, read live workload/log/history/event evidence, created and transitioned an incident, then reset checkout. | `scripts/test-e2e-p1.ps1`; ignored `artifacts/e2e-p1-20260715000217.json` |
| 2026-07-15 | Implemented the bounded, read-only GPT-5.6 investigation workflow. It accepts only incident snapshot/evidence tools and rejects reports with unknown evidence IDs. | 20 unit/contract tests pass; live provider run awaits local key |
| 2026-07-15 | Added and verified P2 controlled memory leak scenario. | `scripts/test-e2e-p2.ps1` observed `OOMKilled`, exit code 137, then reset the workload |
| 2026-07-15 | Added a single-command controlled evaluation harness covering checks, P1, and P2. | `scripts/eval.ps1`; ignored `artifacts/eval-20260715-090854.json` |
| 2026-07-15 | Added server-owned remediation preview, approval, execution, recovery verification, streamed incident updates, audit postmortem draft, and the three-surface console. | 27 unit/contract tests plus production frontend build pass; `scripts/test-e2e-p1-remediation.ps1` completed an approved P1 recovery with verified 5xx rate 0.0. |
| 2026-07-15 | Added a P2-specific allowlisted restoration action after confirming restart/scale would not clear the controlled memory-leak injection. | `scripts/test-e2e-p2-remediation.ps1` observed OOMKilled, blocked unapproved execution, then verified the restored workload is ready with zero restarts. |
| 2026-07-15 | Expanded the controlled evaluation harness to include P1 and P2 remediation and corrected its PowerShell warning/JSON capture behavior. | `scripts/eval.ps1`; ignored `artifacts/eval-20260715-104325.json` records the complete v2 pass. |
| 2026-07-15 | Hardened action-plan integrity and auditability after a final safety review. | 29 unit/contract tests, frontend build, and fresh live P1/P2 remediation runs passed; each persisted a fingerprint-bound, append-only action history. |
| 2026-07-15 | Reconciled implementation claims and surfaced the deterministic recovery contract in the server-side approval preview. | 30 unit/contract tests, Ruff, Ty, and the production frontend build pass; the P1 preview names workload readiness plus the 15-second 5xx threshold before approval. |
| 2026-07-15 | Completed a safe Chrome console walkthrough and captured the approval-preview screen. | Loaded a controlled incident; verified lifecycle and evidence display, a dry-run response-restoration preview with readiness/15-second 5xx checks, and the audit draft. No action was approved or executed. `artifacts/screenshots/console-approval-preview-20260715.png` |
| 2026-07-15 | Re-tested the API after the Responses API permission change. | `scripts/model-smoke.ps1` advanced past the prior missing-scope failure and received `429 insufficient_quota`; no model-selection artifact was created. |
| 2026-07-15 | Added an explicit OpenRouter test-provider configuration while retaining direct OpenAI for final validation. | PowerShell syntax validation, Ruff, 32 pytest tests, Ty, and the frontend production build passed. Fake-client tests verify the selected credential, base URL, and model are provider-owned; no live OpenRouter request has run. |
| 2026-07-15 | Re-ran the controlled P1 remediation scenario and started its verified incident in the local console. | `scripts/test-e2e-p1-remediation.ps1` injected the controlled 5xx failure, completed the approval/recovery path, and independently verified 0.0 5xx rate with ready workload. API `/healthz`, console HTTP 200, and incident state `Resolved` were then checked locally. |
| 2026-07-15 | Replaced the foundation console with an evidence-backed command center. | Ruff, Ty, 34 pytest tests, and the production frontend build passed. Chrome loaded incident `2245fbc5-8693-40e9-b2c1-2bda060fbc1d` and displayed live 5xx/request rates, readiness/restarts, current K8s events, deployment revisions, lifecycle, and persisted alert evidence. Scope/SLO/model confidence remained explicitly unknown where the controlled environment has no source. |
| 2026-07-15 | Added live per-question evidence capture, a server-owned incident timeline tool, dashboard auto-refresh, chat-turn history, and source-payload deduplication. | Ruff, Ty, 36 pytest tests, and the production frontend build passed. A fresh P1 remediation run (`e2e-remediation-20260715181402`) completed with independent 0.0 5xx recovery. Against its live API, the first blocked-provider question persisted 15 current records; a second added only two refreshed telemetry/workload records while unchanged events and deployment history were deduplicated. |
| 2026-07-15 | Added local console rehearsal controls and the post-recovery RCA lifecycle. | Ruff, Ty, 39 pytest tests, and the production frontend build passed. The browser started fresh P1 incident `7634d1e7-6a4e-4656-b5ea-eb29cb83adf1`; Prometheus reported 18.49 5xx/s and the chart was populated. It remains in `Triaging` for human approval testing. |
| 2026-07-15 | Expanded the audit-derived RCA draft into explicit factual review sections. | Ruff, Ty, 39 pytest tests, and the production frontend build passed. A persisted recovered incident returned incident context, conclusion boundary, recovery/verification, scope/impact, and follow-up sections; unsupported cause and impact claims remain explicit unknowns. |
| 2026-07-15 | Added a persisted on-call queue, incident deep links, and measured incident age to the console. | Ruff, Ty, 41 pytest tests, and the production frontend build passed. The live API returned seven open incidents; Chrome auto-selected the newest critical incident, selected P2's `restore_memory_mode` action from the queue, and opened the same P2 record through `?incident=<id>`. |
| 2026-07-15 | Published a controlled on-call rehearsal guide and standardized displayed telemetry precision. | Ruff, Ty, 42 pytest tests, and the production frontend build passed. The evidence formatter is covered with a representative live-rate value and produces `18.492/s`, `18.492/s`, and `18.504/s`; dashboard/UI presentation uses three decimal places. |
| 2026-07-16 | Made each console telemetry signal route-aware and clarified the controlled traffic scope. | Ruff, Ty, 42 pytest tests, and the production frontend build passed. A rebuilt local checkout deployment exposed `GET /checkout` labels to Prometheus; the console then displayed a live 13.767/s 5xx rate with its service, method, route, status set, and rate window. The blast-radius panel names the configured load-generator call path and explicitly reports that no downstream dependency is instrumented. |
| 2026-07-16 | Added deterministic incident situation, next-step, service-context, and signal-quality views. | Ruff, Ty, 43 pytest tests, and the production frontend build passed. The console presents current route error ratio, recovery gate state, the server-owned next safe step, current revision/configuration, sorted revision and event context, and distinct queue row IDs/ages. Repeated starts of an open P1/P2 reuse that scenario's existing record. Rollout-to-alert timing and customer-impact totals remain absent because the current evidence sources do not measure them reliably. |
| 2026-07-16 | Turned the approval card into an informed controlled-change review rather than a generic confirmation prompt. | Ruff, Ty, 46 pytest tests, and the production frontend build passed. Fresh controlled P1/P2 remediation wrappers independently verified recovery. Browser review verified preview, audited rejection without a cluster change, and plan rehydration after refresh. |
| 2026-07-16 | Rebuilt and locally ran the credential-free prebuilt reviewer package. | `scripts/package-judge.ps1` rebuilt the ignored checkout image archive; `scripts/run-judge.ps1` imported it and passed its P1/P2 controlled safety flows. This is not yet a clean-machine result. |
| 2026-07-16 | Hardened the local console telemetry startup and rehearsal readiness flow. | `run-console.ps1` passed PowerShell parsing; Ruff, Ty, 46 pytest tests, and the production frontend build passed. Browser P1 injection waited for real telemetry, then displayed 17.857 5xx/s, 100% failing, a populated trend, and a failing recovery gate; reset completed afterward. |
| 2026-07-16 | Clarified incident selection and the controlled data boundary in the console. | Ruff, Ty, 46 pytest tests, and the production frontend build passed. The neutral page now requires an explicit incident selection or P1/P2 start, identifies local Kubernetes, Prometheus, and incident-store sources, and states that GitHub is not connected. |
| 2026-07-16 | Reconciled action expiry, recovery proof, and controlled-action naming. | Ruff, Ty, 48 pytest tests, and the production frontend build passed. Expired previews are recorded as `Expired` and return the incident to triage; P2 recovery requires a 30-second stable restart count; P1 is accurately named `restore_response_mode`, reflecting the controlled response-mode patch it performs. |
| 2026-07-16 | Re-ran both controlled remediation flows after the action-contract changes. | `scripts/test-e2e-p1-remediation.ps1` independently verified ready checkout and `0.0` 5xx/s after `restore_response_mode`; `scripts/test-e2e-p2-remediation.ps1` recorded a pending 30-second restart-stability observation, then independently verified recovery with no restart-count increase. |
| 2026-07-16 | Hardened judge-facing console startup and documentation. | PowerShell parsing, Ruff, Ty, 48 pytest tests, and the production frontend build passed. A fresh local browser session opened a queued incident with its explicit accessible button name and reported no duplicate-React-root warning. README now states the neutral-selection behavior, supported platforms, Codex/GPT-5.6 validation boundary, and clean-rehearsal procedure. |
| 2026-07-17 | Added a clearly labelled controlled-investigation rehearsal mode. | Ruff, Ty, 49 pytest tests, and the production frontend build passed. A fresh kind P1 flow collected local evidence, returned a deterministic report labelled `controlled_simulation` / `not GPT-5.6`, completed explicit approval, independently verified `0.0` 5xx/s recovery, and reset the controlled environment. This mode is rehearsal-only, not live-model validation. |
| 2026-07-17 | Clarified rehearsal lifecycle behavior and runtime mode diagnosis. | Ruff, Ty, 50 pytest tests, the PowerShell parser, and the production frontend build passed. The console now explains that investigation is read-only and a dry-run preview moves Triaging to Action Proposed; `/healthz` reports `live_model` or `controlled_simulation` so an old server process is detectable. |
| 2026-07-17 | Performed a fresh controlled-simulation P1 verification and documented the clean local rehearsal sequence. | After clearing ignored local incident history, `scripts/test-e2e-p1-remediation.ps1 -SimulationInvestigation` passed: it injected P1, created an explicitly labelled deterministic report, performed the dry-run/approval/execution path, independently verified ready checkout with 0.0 5xx/s, and reset the scenario. API `/healthz` reported `ok` and `controlled_simulation`; the console returned HTTP 200 with an empty open-incident queue after cleanup. |
| 2026-07-17 | Final local console and safety-flow audit. | Ruff, Ty, 50 pytest tests, production frontend build, and `git diff --check` passed. The console rendered a live P1 as `checkout GET /checkout` with 18.510 5xx/s, declared scope, evidence, and a Triaging approval gate. The P2 wrapper independently verified readiness plus 30-second restart stability after approved remediation. Inject now opens the new incident immediately while telemetry warms in the background. |
| 2026-07-17 | Cleared stale deep-link behavior after local incident-history cleanup. | Ruff, Ty, 50 pytest tests, the production frontend build, and `git diff --check` passed. A missing `?incident=` record now clears the URL and returns the console to explicit scenario selection instead of repeatedly polling a deleted record. |

## Immediate backlog

1. Add an ignored `OPENROUTER_API_KEY`, set `LLM_PROVIDER=openrouter`, then run the GPT-5.6 model-selection and live investigation fixture with a dated local pricing snapshot. This is the only blocker to actual conversational model answers.
2. Restore `LLM_PROVIDER=openai` and use the direct OpenAI project only for the final validation and recording after it has usable quota.
3. Extend the controlled evaluation output with model diagnosis results only after live GPT-5.6 runs exist.
4. Run the prebuilt reviewer wrapper on a clean machine, then attach the image archive to the review release and capture final submission assets.

## Development routine

At the end of each implementation session:

1. run the relevant checks and update this tracker only with verified results;
2. append factual Codex contribution notes to `internal/CODEX_WORK_LOG.md`—task,
   prompt/decision, changed files, verification, and limitation; and
3. capture a screenshot as soon as a scenario gate first passes.

Do not reconstruct the Codex log later or turn a planning discussion into a claim
that a feature was built.

## Submission gates

- [ ] Every Devpost claim has a matching, tested feature.
- [ ] P1 and P2 run from a clean environment without manual repair.
- [ ] No remediation occurs without explicit human approval.
- [ ] Every hypothesis and blast-radius answer cites evidence.
- [ ] Judge path works without asking judges to rebuild the project or use their own API key.
- [ ] README lists installation, supported platforms, testing path, and exact use of Codex and GPT-5.6.
- [ ] Public YouTube video is under three minutes and includes audio covering the product, Codex, and GPT-5.6.
- [ ] `/feedback` session ID is taken from the Codex project thread where most core functionality was built.
- [ ] Final Devpost form, licensing, and private-repository sharing requirements are checked against the official rules.
