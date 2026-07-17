# OpsPilot delivery plan

## Delivery strategy

The winning unit is not a collection of integrations. It is one reliable,
visible, end-to-end incident lifecycle that judges can understand in seconds and
reproduce without rebuilding the project. Build the P1 scenario first, prove the
safety gates, then add P2 using the same architecture.

No stretch work starts until the preceding quality gate passes. In particular,
do not add MCP, external incident-management integrations, a knowledge base, or
additional failure scenarios before the two core scenarios are reliable.

## Milestones

| Priority | Milestone | Deliverable | Quality gate |
| --- | --- | --- | --- |
| P0 | Repository and developer experience | Project scaffold, linting, tests, local configuration template, architecture docs | A new contributor can run checks without secrets |
| P1 | Live test environment | kind cluster, sample services, Prometheus, load generator, deterministic reset | P1 can be injected and observed manually three times in a row |
| P2 | Read-only investigation | K8s and Prometheus adapters, evidence records, incident lifecycle, audit store | API returns source-backed evidence for P1 |
| P3 | Agentic diagnosis | GPT-5.6 function-tool workflow, structured hypothesis ledger, streamed UI state | Agent identifies P1 and cites the deployment, metrics, and logs |
| P4 | Safe remediation | proposal, policy checks, approval UX, action executor, independent verifier | A controlled response restoration cannot run without approval and recovery is verified |
| P5 | Conversational UX and P2 | Incident console, chat, blast-radius view, OOMKill scenario | P1 and P2 share tools and lifecycle state |
| P6 | Submission hardening | Reproducible judge path, README, screenshots, video, final compliance pass | Fresh-machine or hosted test succeeds; claims match evidence |

## Calendar checkpoints

This is a short runway. A quality gate is not optional because a date has arrived;
the date is an early warning to reduce scope before the submission becomes risky.

| Date | Required checkpoint | If it is not green |
| --- | --- | --- |
| Tue Jul 14 | P0 started; model reachability test prepared; kind proof-of-life started | Do not begin UI work |
| Wed Jul 15 | P0 complete by midday; P1 produces its telemetry manually by end of day | Remove all non-P1 work until P1 is repeatable |
| Thu Jul 16 | P2 evidence and incident core returns a source-linked P1 snapshot without a model call | Do not add chat or remediation UX |
| Fri Jul 17 | P3 identifies P1 with evidence in repeated controlled runs | Invoke the cut list: preserve P1 safety/recovery, defer P2 polish and all stretch work |
| Sat Jul 18 | P4 approval and verified P1 recovery; hosting decision made | Use the documented prebuilt local judge path if hosted access is not ready |
| Sun Jul 19 | P5 complete: P2 uses the shared flow and screenshots are captured | Keep P2 functional; cut only presentation polish |
| Mon Jul 20 | Internal content freeze: P6 dry run, video, submission materials, and judge path complete | Use only defect fixes and claim corrections |
| Tue Jul 21 | Final link/access verification and submission before 5:00 PM PT | No feature work; do not rely on being able to edit a submitted entry |

## Execution sequence

### Phase 0 — foundation and contracts

1. Create the monorepo layout: `backend/`, `frontend/`, `infra/`, `tests/`, and
   `docs/`.
2. Add `.env.example`; validate required environment variables at startup.
3. Use `uv`, Ruff, and one Python type checker; use React, TypeScript, and Vite
   for the console. Add formatting, linting, type checks, unit-test commands, and
   a root `Makefile` or task runner. Add further UI libraries only when they serve
   a shipped screen.
4. Define Pydantic contracts for evidence, hypothesis, action proposal, approval,
   verification, and postmortem before implementing UI or agent prompts.
5. Add a `make verify` command that runs the fastest meaningful checks.
6. Add GitHub Actions to run the same install, lint, type-check, and test commands
   on pushes and pull requests once the scaffold exists.
7. With a server-side key in the ignored environment file, run a minimal model
   reachability smoke test. For each candidate, save the fixture version, model ID,
   reasoning effort, pass/fail, wall-clock latency, returned token usage, tool-call
   count, and dated estimated cost with its pricing snapshot. This is configuration
   validation, not evidence that the agent workflow works.

**Exit condition:** a clean clone can install dependencies and run static checks.

### Phase 1 — deterministic Kubernetes simulation

1. Create a dedicated kind cluster and namespace.
2. Deploy the sample service topology, Prometheus, and a load generator.
3. Publish controlled scenario commands: `reset`, `inject-p1`, `inject-p2`, and
   `status`.
4. Define metric baselines and recovery windows in versioned configuration.
5. Capture a fixture of expected observability signals for each incident.
6. Have each scenario injection send a version-4 Alertmanager generic-webhook
   compatible payload to the private incident-ingress endpoint. Include a new
   `opspilot_run_id` in the standard labels for each reset. Do not add Alertmanager
   or Prometheus alert-rule integration in this phase.

**Exit condition:** both scenarios produce the expected events and metrics on
three consecutive runs. Do not call this a live investigation until this passes.

### Phase 2 — evidence and incident core

1. Implement K8s and Prometheus adapters behind interfaces.
2. Implement time-bounded, redacted log collection and parameterized metric
   templates.
3. Store evidence records and lifecycle transitions in SQLite.
4. Build a basic incident API that validates, delivery-deduplicates, and records
   the Alertmanager-compatible payload before creating an incident. Test retry,
   alert-update, resolved-signal, and reset/new-run behavior explicitly.
5. Write unit tests with fake adapters plus one integration test against kind.

**Exit condition:** the API produces a complete, evidence-linked P1 snapshot
without any model call.

### Phase 3 — agent workflow

1. Configure GPT-5.6 only through the server-side environment. Test the available
   GPT-5.6 family IDs against a representative structured tool-call fixture, then
   record the selected ID, reasoning effort, latency, and fallback order in ignored
   local configuration. Start with `gpt-5.6-terra` at `medium` reasoning effort;
   use another available GPT-5.6 family model only if the recorded test supports the
   change. Ollama is never a submission fallback.
2. Treat OpenAI as the primary runtime for every submitted investigation flow;
   use Ollama only for optional offline development tests, never as the demo
   substitute for GPT-5.6.
3. Expose read-only adapters as typed function tools with strict schemas and
   timeouts.
4. Require structured hypothesis output: candidate cause, evidence IDs,
   contradictory evidence, confidence, and next investigation step.
5. Add an evidence validator that rejects a conclusion with missing or invalid
   source IDs.
6. Stream tool progress and lifecycle events to the frontend.

**Exit condition:** the agent produces a defensible P1 diagnosis in repeated runs
and visibly reports uncertainty when a tool fails or evidence conflicts.

### Phase 4 — approval and recovery

1. Implement the action catalog and policy engine.
2. Add preflight checks for namespace, target, expected revision, and action bounds.
   Run the matching Kubernetes dry-run and bind the result to a versioned action plan.
3. Build the approval card showing the preview, proposed change, rationale, impact,
   and verification plan. Reject execution when the plan or target version is stale.
4. Execute only after explicit approval; persist an immutable audit event.
5. Implement independent recovery checks and reopen investigation on failure.
6. Capture the P1 screenshot set immediately after this gate passes and store the
   source files with the submission assets.

**Exit condition:** an attempted action without approval is denied in tests and in
the UI; an approved P1 controlled response restoration returns the service to baseline.

### Phase 5 — product experience and P2

1. Keep the console to three surfaces: an incident view (status ribbon, evidence
   timeline, hypothesis ledger, and chat input), an approval card, and a postmortem
   view. Use SSE for server-to-browser investigation progress.
2. Add conversational questions that route to the same evidence tools and current
   incident state.
3. Implement P2 using the existing action and verification interfaces.
4. Add empty-state, loading, failure, and ambiguous-evidence states.
5. Capture the P2 screenshot set immediately after this gate passes; do not wait
   for video day.

**Exit condition:** a judge can understand the full safety story without reading
source code and can complete both demos from the UI.

### Phase 6 — proof and submission

1. Produce a judge path that does not require rebuilding from scratch: hosted
   sandbox or prebuilt local test image with clear, credential-free access.
2. Document supported platforms, prerequisites, setup, scenario reset, and tests.
3. Run a recorded dry run from a fresh environment.
4. Record the public YouTube demo under three minutes. Show the product working and
   explain exactly how Codex and GPT-5.6 were used.
5. Update the Devpost description to reflect only verified features; retrieve the
   `/feedback` session ID from the project thread where core work was completed.
6. Make the hosting decision by July 18. If a hosted path is used, add endpoint
   rate limits, per-incident token budgets, image/resource limits, a reset process,
   and a non-root container configuration before sharing access. Do not spend on
   hosting without the project owner's approval.
7. If a hosted judge path is selected, keep the test account and reset process
   working through the published judging period (currently August 5, 2026, 5:00 PM
   PT); check it daily and record outages or limitations honestly.

**Exit condition:** an independent person can follow the README, run or access the
demo, reproduce P1, and understand the safety controls without verbal help.

## Test strategy

| Level | Focus | Examples |
| --- | --- | --- |
| Unit | Deterministic domain behavior | policy denies non-allowlisted action; metric template validation; evidence source validation |
| Contract | Tool input/output schemas | malformed workload IDs, out-of-range scale request, missing evidence ID |
| Integration | Real adapters against kind/Prometheus | deploy history, OOMKill event retrieval, P1 response restoration, recovery query |
| End-to-end | User-visible lifecycle | alert to postmortem, approval required, failed recovery reopens triage |
| Evaluation | Agent quality and safety | `make eval` records expected-cause evidence, unsupported-claim rejection, and blocked unsafe/unapproved actions |

## Demo narrative

1. Start with the checkout alert and show the status ribbon moving into triage.
2. Show parallel evidence arriving: deployment changed, 5xx rate increased, logs
   show the fault.
3. Show the evidence-linked response-restoration recommendation and approval gate.
4. Approve the response restoration; show rollout and error-rate recovery verification.
5. Show the generated postmortem and ask one conversational follow-up question.
6. Briefly show the OOMKill scenario as proof that the design generalizes.
7. Explain how Codex accelerated implementation and how GPT-5.6 drives the bounded
   investigation workflow.

## Scope control

| Must ship | May ship only after P0–P5 are green | Do not build for this submission |
| --- | --- | --- |
| Two incident scenarios, evidence, chat, approval, verification, postmortem | historical postmortem search, SLO burn-rate calculation, third scenario | MCP server, PagerDuty/Datadog integration, multi-cluster control, unrestricted cluster access, production claims |

If time is constrained, retain both scenario contracts and the approval/recovery
path. Reduce only visual polish: present blast radius as an evidence-linked list,
generate one postmortem draft path, and keep P2 on the same console without a
separate polished view.
