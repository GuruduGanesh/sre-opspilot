import { FormEvent, StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

const lifecycle = [
  "Received", "Classified", "Enriched", "Triaging", "ActionProposed",
  "Executing", "Monitoring", "Resolved", "RCA", "RCAPublished",
];

type Incident = { id: string; lifecycle_state: string; created_at?: string; updated_at?: string };
type IncidentQueueItem = Incident & { severity: string; service: string; alert_name: string };
type TimelineItem = { occurred_at: string; event_type: "evidence" | "lifecycle"; detail: string };
type Evidence = { id: string; summary: string; source_type: string; source_ref: string; observed_at: string };
type Report = {
  summary: string;
  hypotheses: Array<{ root_cause: string; confidence: number; evidence_ids: string[]; contradictory_evidence_ids: string[]; next_evidence_needed?: string | null }>;
  recommended_next_step: string;
};
type ActionPlan = {
  id: string; status: string; proposal: { action_type: string; evidence_ids: string[]; expires_at: string };
  preview: { target?: string; dry_run?: boolean; verification_plan?: { independent?: boolean; checks?: Array<{ kind: string; condition?: string; query?: string; window?: string; maximum?: number }> } };
  approved_by?: string;
};
type Postmortem = { summary: string; lifecycle_state: string; evidence_count: number; actions: Array<{ action_id: string; action_type: string; status: string; approved_by?: string }>; timeline: TimelineItem[]; sections: Array<{ heading: string; body: string }> };
type DemoScenario = { incident_id: string; scenario: string; recommended_action: string; message: string };
type Dashboard = {
  severity: string; alert_name: string; service: string; observed_at: string; incident_age_seconds: number;
  workload?: { ready_replicas: number; desired_replicas: number; restart_count: number; observed_at: string } | null;
  deployment_history: Array<{ revision: string; images: string[]; observed_at: string }>;
  events: Array<{ reason: string; message: string; event_type: string; involved_object: string; observed_at: string }>;
  telemetry: { status: string; message?: string | null; error_rate?: number | null; request_rate?: number | null; recovery_error_rate?: number | null; error_rate_trend: Array<{ observed_at: string; value: number }> };
  blast_radius: { status: string; workload: string; namespace: string; message: string };
  slo_status: string; slo_message: string; collection_notes: string[];
};
type ConversationTurn = { question: string; report: Report };

async function jsonOrError<T>(response: Promise<Response>): Promise<T> {
  const completed = await response;
  if (!completed.ok) {
    const body = (await completed.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? `Request failed (${completed.status})`);
  }
  return completed.json() as Promise<T>;
}

function formatTime(value?: string): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
}

function metric(value?: number | null, suffix = "/s"): string {
  return value === undefined || value === null ? "—" : `${value.toFixed(3)}${suffix}`;
}

function decimal(value?: number): string {
  return value === undefined ? "—" : value.toFixed(3);
}

function displayState(state: string): string {
  return state.replace(/([a-z])([A-Z])/g, "$1 $2");
}

function elapsed(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return minutes ? `${minutes}m ${remaining}s` : `${remaining}s`;
}

function RateTrend({ points }: { points: Dashboard["telemetry"]["error_rate_trend"] }) {
  const path = useMemo(() => {
    if (points.length < 2) return "";
    const values = points.map((point) => point.value);
    const max = Math.max(...values, 0.001);
    return points.map((point, index) => {
      const x = (index / (points.length - 1)) * 100;
      const y = 92 - (point.value / max) * 72;
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(" ");
  }, [points]);
  if (!path) return <div className="chart-empty">Trend appears when the controlled Prometheus endpoint is connected.</div>;
  return <svg className="trend-chart" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Checkout 5xx requests per second over the last 15 minutes"><defs><linearGradient id="rate-fill" x1="0" x2="0" y1="0" y2="1"><stop stopColor="#fb7185" stopOpacity=".45"/><stop offset="1" stopColor="#fb7185" stopOpacity="0"/></linearGradient></defs><path d={`${path} L100,100 L0,100 Z`} fill="url(#rate-fill)"/><path d={path} fill="none" stroke="#fb7185" strokeWidth="2.4" vectorEffect="non-scaling-stroke"/></svg>;
}

function App() {
  const [incidentId, setIncidentId] = useState("");
  const [queue, setQueue] = useState<IncidentQueueItem[]>([]);
  const [queueLoaded, setQueueLoaded] = useState(false);
  const [incident, setIncident] = useState<Incident | null>(null);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [conversation, setConversation] = useState<ConversationTurn[]>([]);
  const [plan, setPlan] = useState<ActionPlan | null>(null);
  const [postmortem, setPostmortem] = useState<Postmortem | null>(null);
  const [question, setQuestion] = useState("What evidence supports the current root-cause hypothesis?");
  const [actionType, setActionType] = useState("rollback");
  const [approvedBy, setApprovedBy] = useState("local-oncall");
  const [message, setMessage] = useState("Open a controlled scenario incident to begin triage.");
  const [loading, setLoading] = useState(false);
  const [demoBusy, setDemoBusy] = useState(false);
  const [demoScenario, setDemoScenario] = useState<"p1" | "p2">("p1");
  const [approvalOpen, setApprovalOpen] = useState(false);

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("incident");
    void refreshQueue(requested ?? undefined, true);
    const timer = window.setInterval(() => void refreshQueue(), 5_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!incident?.id) return;
    const source = new EventSource(`${apiBaseUrl}/api/v1/incidents/${encodeURIComponent(incident.id)}/events`);
    source.addEventListener("incident", (event) => {
      const update = JSON.parse(event.data) as { incident: Incident; timeline: TimelineItem[] };
      setIncident(update.incident);
      setTimeline(update.timeline);
    });
    source.onerror = () => setMessage("Live update stream disconnected. Reload the incident to reconnect.");
    return () => source.close();
  }, [incident?.id]);

  useEffect(() => {
    if (!incident?.id) return;
    const selectedIncidentId = incident.id;
    let mounted = true;
    const refreshLiveSignals = async () => {
      try {
        const next = await jsonOrError<Dashboard>(fetch(`${apiBaseUrl}/api/v1/incidents/${encodeURIComponent(selectedIncidentId)}/dashboard`));
        if (mounted) setDashboard(next);
      } catch {
        // Keep the last good snapshot visible; the manual refresh reports failures.
      }
    };
    const timer = window.setInterval(() => void refreshLiveSignals(), 5_000);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [incident?.id]);

  async function openIncident(id: string) {
    if (!id.trim()) return;
    setLoading(true);
    try {
      const [loadedIncident, loadedTimeline, loadedEvidence, loadedDashboard] = await Promise.all([
        jsonOrError<Incident>(fetch(`${apiBaseUrl}/api/v1/incidents/${encodeURIComponent(id)}`)),
        jsonOrError<TimelineItem[]>(fetch(`${apiBaseUrl}/api/v1/incidents/${encodeURIComponent(id)}/timeline`)),
        jsonOrError<Evidence[]>(fetch(`${apiBaseUrl}/api/v1/incidents/${encodeURIComponent(id)}/evidence`)),
        jsonOrError<Dashboard>(fetch(`${apiBaseUrl}/api/v1/incidents/${encodeURIComponent(id)}/dashboard`)),
      ]);
      setIncident(loadedIncident); setTimeline(loadedTimeline); setEvidence(loadedEvidence); setDashboard(loadedDashboard);
      setActionType(
        loadedDashboard.alert_name === "CheckoutMemoryPressure" ? "restore_memory_mode" : "rollback"
      );
      setReport(null); setConversation([]); setPlan(null); setPostmortem(null); setMessage("");
      window.history.replaceState({}, "", `${window.location.pathname}?incident=${encodeURIComponent(id)}`);
    } catch (error) {
      setIncident(null); setTimeline([]); setEvidence([]); setDashboard(null);
      setMessage(error instanceof Error ? error.message : "Unable to load the incident.");
    } finally { setLoading(false); }
  }

  async function refreshQueue(preferredId?: string, autoOpen = false) {
    try {
      const items = await jsonOrError<IncidentQueueItem[]>(fetch(`${apiBaseUrl}/api/v1/incidents?state=open`));
      setQueue(items);
      if (autoOpen) {
        const selected = items.find((item) => item.id === preferredId) ?? items[0];
        if (selected) await openIncident(selected.id);
      }
    } catch {
      // The active incident remains usable even if the optional queue refresh fails.
    } finally {
      setQueueLoaded(true);
    }
  }

  async function loadIncident(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await openIncident(incidentId.trim());
  }

  async function startDemoScenario(scenario: "p1" | "p2") {
    setDemoBusy(true);
    try {
      const result = await jsonOrError<DemoScenario>(fetch(`${apiBaseUrl}/api/v1/demo/scenarios/${scenario}`, { method: "POST" }));
      setIncidentId(result.incident_id); setActionType(result.recommended_action); setMessage(result.message);
      await openIncident(result.incident_id);
      await refreshQueue();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to start the controlled simulation.");
    } finally { setDemoBusy(false); }
  }

  async function resetDemoScenario() {
    setDemoBusy(true);
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/demo/reset`, { method: "POST" });
      if (!response.ok) throw new Error("Unable to reset the controlled simulation.");
      setMessage("Controlled checkout modes were reset. Existing incident records remain available for review.");
      await refreshDashboard();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to reset the controlled simulation.");
    } finally { setDemoBusy(false); }
  }

  async function refreshDashboard() {
    if (!incident) return;
    try { setDashboard(await jsonOrError<Dashboard>(fetch(`${apiBaseUrl}/api/v1/incidents/${incident.id}/dashboard`))); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Unable to refresh telemetry."); }
  }

  async function investigate() {
    if (!incident || !question.trim()) return;
    setLoading(true);
    try {
      const result = await jsonOrError<Report>(fetch(`${apiBaseUrl}/api/v1/incidents/${incident.id}/investigate`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }),
      }));
      setReport(result); setConversation((history) => [...history, { question: question.trim(), report: result }]); setMessage("");
    } catch (error) {
      const detail = error instanceof Error ? error.message : "Investigation failed.";
      setMessage(detail.includes("investigation model is unavailable") ? "GPT investigation is unavailable. Configure the ignored test-provider key to enable evidence-backed answers; the controlled simulation and safety workflow remain available." : detail);
    }
    finally { setLoading(false); }
  }

  async function previewAction() {
    if (!incident || evidence.length === 0) return;
    setLoading(true);
    try {
      const result = await jsonOrError<ActionPlan>(fetch(`${apiBaseUrl}/api/v1/incidents/${incident.id}/actions/preview`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action_type: actionType, evidence_ids: evidence.map((item) => item.id) }),
      }));
      setPlan(result); setMessage("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Preview failed."); }
    finally { setLoading(false); }
  }

  async function approveAction() {
    if (!plan) return;
    setLoading(true);
    try {
      const result = await jsonOrError<ActionPlan>(fetch(`${apiBaseUrl}/api/v1/actions/${plan.id}/approve`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved_by: approvedBy }),
      }));
      setPlan(result); setApprovalOpen(false); setMessage("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Approval failed."); }
    finally { setLoading(false); }
  }

  async function updateAction(path: "execute" | "verify") {
    if (!plan) return;
    setLoading(true);
    try {
      const result = await jsonOrError<ActionPlan | { plan: ActionPlan }>(fetch(`${apiBaseUrl}/api/v1/actions/${plan.id}/${path}`, { method: "POST" }));
      setPlan("plan" in result ? result.plan : result); setMessage(""); await refreshDashboard();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Action update failed."); }
    finally { setLoading(false); }
  }

  async function loadPostmortem() {
    if (!incident) return;
    try {
      setPostmortem(await jsonOrError<Postmortem>(fetch(`${apiBaseUrl}/api/v1/incidents/${incident.id}/postmortem/draft`, { method: "POST" })));
      setIncident(await jsonOrError<Incident>(fetch(`${apiBaseUrl}/api/v1/incidents/${incident.id}`))); setMessage("");
    }
    catch (error) { setMessage(error instanceof Error ? error.message : "Postmortem unavailable."); }
  }

  async function publishPostmortem() {
    if (!incident) return;
    try {
      setIncident(await jsonOrError<Incident>(fetch(`${apiBaseUrl}/api/v1/incidents/${incident.id}/postmortem/publish`, { method: "POST" })));
      setMessage("RCA marked as published in this controlled local demo.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Unable to publish the RCA state."); }
  }

  const currentStage = incident ? lifecycle.indexOf(incident.lifecycle_state) : -1;
  const telemetry = dashboard?.telemetry;
  const recentTimeline = timeline.slice(-20);
  const recentEvidence = evidence.slice(-12);

  return <main className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-mark">OP</span><div><strong>OpsPilot</strong><span>Evidence-first incident response</span></div></div>
      <div className="environment"><span className="live-dot"/> Controlled Kubernetes simulation</div>
      <form className="incident-picker" onSubmit={loadIncident}><label htmlFor="incident-id">Incident record</label><div><input id="incident-id" value={incidentId} onChange={(event) => setIncidentId(event.target.value)} placeholder="Paste incident ID"/><button disabled={loading} type="submit">{loading ? "Loading…" : "Open"}</button><select aria-label="Controlled rehearsal scenario" value={demoScenario} onChange={(event) => setDemoScenario(event.target.value as "p1" | "p2")}><option value="p1">P1 · 5xx</option><option value="p2">P2 · memory</option></select><button disabled={demoBusy} type="button" onClick={() => startDemoScenario(demoScenario)}>{demoBusy ? "Starting…" : "Start rehearsal"}</button><button disabled={demoBusy} className="ghost-button toolbar-reset" type="button" onClick={resetDemoScenario}>Reset</button></div></form>
    </header>

    {message && <div className="notice" role="status">{message}</div>}

    {!incident && <section className="empty-state"><p className="eyebrow">On-call command center</p><h1>Choose an incident from the queue</h1><p>OpsPilot keeps alert evidence, telemetry, deployment context, human approval, and recovery verification in one controlled incident trail.</p></section>}

    {incident && dashboard && <>
      <section className="incident-banner"><div><p className="eyebrow">Active incident</p><h1>{dashboard.service} <span>/ {dashboard.alert_name}</span></h1><p className="incident-id">{incident.id}</p></div><div className="incident-age"><span>Time since received</span><strong>{elapsed(dashboard.incident_age_seconds)}</strong></div><div className={`severity severity-${dashboard.severity.toLowerCase()}`}>{dashboard.severity}</div><div className="state-pill">{displayState(incident.lifecycle_state)}</div></section>

      <section className="metric-grid" aria-label="Current controlled signals">
        <Metric label="5xx requests / second" value={metric(telemetry?.error_rate)} detail={telemetry?.status === "live" ? "Live Prometheus read" : "Telemetry not connected"} tone="danger"/>
        <Metric label="Request rate" value={metric(telemetry?.request_rate)} detail="Current checkout workload" tone="neutral"/>
        <Metric label="Workload health" value={dashboard.workload ? `${dashboard.workload.ready_replicas}/${dashboard.workload.desired_replicas}` : "—"} detail={dashboard.workload ? `${dashboard.workload.restart_count} pod restarts observed` : "Kubernetes read unavailable"} tone={dashboard.workload?.ready_replicas === dashboard.workload?.desired_replicas ? "success" : "warning"}/>
        <Metric label="Recovery gate" value={metric(telemetry?.recovery_error_rate)} detail="Target ≤ 0.010/s over 15 seconds" tone="success"/>
        <Metric label="Model confidence" value={report?.hypotheses[0] ? `${Math.round(report.hypotheses[0].confidence * 100)}%` : "—"} detail={report ? "Evidence-cited hypothesis" : "Awaiting live investigation"} tone="accent"/>
      </section>

      <section className="command-layout">
        <aside className="lifecycle-rail"><section className="queue-rail" aria-label="Open incident queue"><div className="queue-rail-heading"><p className="rail-title">On-call queue</p><button className="ghost-button" type="button" onClick={() => void refreshQueue()}>Refresh</button></div>{queue.length ? <ul className="queue-bullets">{queue.slice(0, 5).map((item) => <li key={item.id}><button className={item.id === incident?.id ? "selected" : ""} type="button" onClick={() => void openIncident(item.id)}><span className={`queue-severity severity-${item.severity.toLowerCase()}`}/><span><strong>{item.service}</strong><small>{item.alert_name} · {displayState(item.lifecycle_state)}</small></span></button></li>)}</ul> : <p className="queue-empty">{queueLoaded ? "No open incidents" : "Loading queue…"}</p>}{queue.length > 5 && <p className="queue-more">Priority set · refresh for newer alerts</p>}</section><p className="rail-title">Incident lifecycle</p>{lifecycle.map((stage, index) => <div className={`lifecycle-step ${index < currentStage ? "complete" : ""} ${index === currentStage ? "current" : ""}`} key={stage}><span>{index < currentStage ? "✓" : index + 1}</span><strong>{displayState(stage)}</strong></div>)}<div className="rail-note">Server-owned transitions prevent a model or browser from marking remediation complete.</div></aside>

        <section className="main-pane">
          <section className="panel telemetry-panel"><div className="panel-heading"><div><p className="eyebrow">Live telemetry</p><h2>Checkout 5xx trend</h2></div><div className="telemetry-actions"><span>Auto-refresh · 5s · {formatTime(dashboard.observed_at)}</span><button className="ghost-button" type="button" onClick={refreshDashboard}>Refresh</button></div></div><RateTrend points={telemetry?.error_rate_trend ?? []}/><div className="chart-footer"><span>15-minute bounded window</span><strong>{telemetry?.status === "live" ? metric(telemetry.error_rate) : telemetry?.message ?? "Not configured"}</strong></div></section>

          <section className="two-column">
            <section className="panel"><p className="eyebrow">Scope / blast radius</p><h2>Confirmed scope</h2><div className="scope-card"><strong>{dashboard.blast_radius.namespace}/{dashboard.blast_radius.workload}</strong><span>Directly affected controlled workload</span></div><p className="muted">{dashboard.blast_radius.message}</p></section>
            <section className="panel"><p className="eyebrow">SLI and recovery policy</p><h2>No SLO invented</h2><p className="muted">{dashboard.slo_message}</p><div className="policy-line"><span>Recovery verification</span><strong>Readiness + 15s 5xx threshold</strong></div></section>
          </section>

          <section className="panel investigation-panel"><p className="eyebrow">Conversational investigation</p><h2>Ask a follow-up question</h2><p className="muted">Each question takes a fresh Prometheus and Kubernetes evidence snapshot. The model can cite it, but can never execute a remediation.</p><div className="question-chips"><button type="button" onClick={() => setQuestion("What changed before the alert?")}>What changed?</button><button type="button" onClick={() => setQuestion("What evidence supports the current root-cause hypothesis?")}>Show evidence</button><button type="button" onClick={() => setQuestion("What is the confirmed affected scope?")}>What is affected?</button><button type="button" onClick={() => setQuestion("What are the current 5xx rate, request rate, readiness, and restarts?")}>Current health</button></div><div className="chat-compose"><input value={question} onChange={(event) => setQuestion(event.target.value)} aria-label="Ask about the incident"/><button disabled={loading} type="button" onClick={investigate}>{loading ? "Collecting evidence…" : "Investigate"}</button></div>
            {conversation.length ? conversation.map((turn, turnIndex) => <div className="conversation-turn" key={`${turn.question}-${turnIndex}`}><div className="chat-message engineer"><span>ON</span><div><small>On-call engineer</small><p>{turn.question}</p></div></div><ReportMessage report={turn.report}/></div>) : <div className="empty-investigation">No model report recorded. Configure the test provider to run a live GPT-5.6 investigation; no hypothesis or confidence is fabricated here.</div>}
          </section>

          <section className="panel timeline-panel"><p className="eyebrow">Incident trail</p><h2>Evidence and state changes</h2><p className="muted">Showing the latest {recentTimeline.length} events from the persisted audit trail.</p><ol className="timeline">{recentTimeline.map((item, index) => <li key={`${item.occurred_at}-${index}`}><time>{formatTime(item.occurred_at)}</time><span className={`timeline-kind ${item.event_type}`}>{item.event_type}</span><p>{item.detail}</p></li>)}</ol></section>
        </section>

        <aside className="evidence-rail"><section className="panel compact"><p className="eyebrow">Evidence stream</p><h2>{evidence.length} persisted records</h2><p className="muted">Showing latest {recentEvidence.length}</p><div className="evidence-list">{recentEvidence.map((item) => <article key={item.id}><div><span className={`source-badge ${item.source_type}`}>{item.source_type}</span><time>{formatTime(item.observed_at)}</time></div><p>{item.summary}</p><code>{item.source_ref}</code></article>)}</div></section>
          <section className="panel compact"><p className="eyebrow">Cluster events</p><h2>Current Kubernetes context</h2>{dashboard.events.length ? <div className="event-list">{dashboard.events.map((event, index) => <article key={`${event.observed_at}-${index}`}><span>{event.event_type}</span><strong>{event.reason}</strong><p>{event.message}</p><time>{formatTime(event.observed_at)} · {event.involved_object}</time></article>)}</div> : <p className="muted">No matching Kubernetes events were returned for this workload.</p>}</section>
          <section className="panel compact"><p className="eyebrow">Deployment history</p><h2>Recent revisions</h2>{dashboard.deployment_history.length ? <div className="deployment-list">{dashboard.deployment_history.map((revision) => <div key={revision.revision}><strong>Revision {revision.revision}</strong><span>{revision.images.join(", ")}</span><time>{formatTime(revision.observed_at)}</time></div>)}</div> : <p className="muted">Deployment history is unavailable.</p>}</section>
          <section className="panel approval-panel"><p className="eyebrow">Human approval gate</p><h2>Propose a controlled restoration</h2><p className="muted">Preview is a server-side Kubernetes dry-run. It changes nothing.</p><select value={actionType} onChange={(event) => setActionType(event.target.value)} aria-label="Allowlisted action"><option value="rollback">Restore checkout response mode</option><option value="restore_memory_mode">Restore controlled memory mode</option><option value="restart">Restart checkout workload</option><option value="scale">Scale checkout workload</option></select><button disabled={loading || incident.lifecycle_state === "Resolved"} type="button" onClick={previewAction}>Create dry-run preview</button>{incident.lifecycle_state === "Resolved" && <p className="muted">This incident is resolved. New remediation proposals are correctly blocked.</p>}
            {plan && <article className="action-plan"><span className="status-chip">{plan.status}</span><strong>{plan.proposal.action_type}</strong><p>{plan.preview.target ?? "Controlled workload target"}</p><p>{plan.preview.dry_run ? "Dry-run completed — no cluster change" : "Preview data unavailable"}</p>{plan.preview.verification_plan?.checks?.map((check) => <div className="verification-check" key={check.kind}><strong>{check.kind.replaceAll("_", " ")}</strong><span>{check.condition ?? `${check.query} ≤ ${decimal(check.maximum)} / ${check.window ?? "—"}`}</span></div>)}{plan.status === "Previewed" && <button type="button" onClick={() => setApprovalOpen(true)}>Review approval</button>}{plan.status === "Approved" && <button disabled={loading} type="button" onClick={() => updateAction("execute")}>Execute approved action</button>}{plan.status === "Executed" && <button disabled={loading} type="button" onClick={() => updateAction("verify")}>Verify recovery</button>}</article>}
          </section>
          <section className="panel compact postmortem-card"><p className="eyebrow">Audit-derived postmortem</p><p>Creates a factual RCA draft from the persisted incident record after recovery is verified.</p><button className="ghost-button" disabled={incident.lifecycle_state !== "Resolved" && incident.lifecycle_state !== "RCA"} type="button" onClick={loadPostmortem}>Draft RCA</button></section>
          {dashboard.collection_notes.length > 0 && <section className="collection-notes">{dashboard.collection_notes.map((note) => <p key={note}>{note}</p>)}</section>}
        </aside>
      </section>
    </>}

    {approvalOpen && plan && <div className="modal-backdrop" role="presentation"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="approval-title"><p className="eyebrow">Explicit human approval required</p><h2 id="approval-title">Review controlled action</h2><p>This approves the exact fingerprint-bound plan that was dry-run against the current target. It does not authorize any other command.</p><dl><div><dt>Action</dt><dd>{plan.proposal.action_type}</dd></div><div><dt>Target</dt><dd>{plan.preview.target ?? "Controlled workload"}</dd></div><div><dt>Evidence binding</dt><dd>{plan.proposal.evidence_ids.length} persisted records</dd></div><div><dt>Verification</dt><dd>Independent readiness and bounded metric checks</dd></div></dl><label htmlFor="approver">Engineer identity</label><input id="approver" value={approvedBy} onChange={(event) => setApprovedBy(event.target.value)}/><div className="modal-actions"><button className="ghost-button" type="button" onClick={() => setApprovalOpen(false)}>Cancel</button><button disabled={loading} type="button" onClick={approveAction}>Approve this exact plan</button></div></section></div>}

    {postmortem && <div className="modal-backdrop" role="presentation"><section className="modal postmortem-modal" role="dialog" aria-modal="true" aria-labelledby="postmortem-title"><p className="eyebrow">Structured incident record</p><h2 id="postmortem-title">RCA draft</h2><p>{postmortem.summary}</p><div className="rca-sections">{postmortem.sections.map((section) => <section key={section.heading}><h3>{section.heading}</h3><p>{section.body}</p></section>)}</div><h3>Timeline</h3><ol>{postmortem.timeline.map((item, index) => <li key={`${item.occurred_at}-${index}`}>{formatTime(item.occurred_at)} — {item.detail}</li>)}</ol><h3>Recorded actions</h3><ul>{postmortem.actions.map((action) => <li key={action.action_id}>{action.action_type} · {action.status}{action.approved_by ? ` · approved by ${action.approved_by}` : ""}</li>)}</ul><div className="modal-actions"><button className="ghost-button" type="button" onClick={() => setPostmortem(null)}>Close draft</button>{incident?.lifecycle_state === "RCA" && <button type="button" onClick={publishPostmortem}>Mark RCA published</button>}</div></section></div>}
  </main>;
}

function Metric({ label, value, detail, tone }: { label: string; value: string; detail: string; tone: string }) {
  return <article className={`metric-card ${tone}`}><p>{label}</p><strong>{value}</strong><span>{detail}</span></article>;
}

function ReportMessage({ report }: { report: Report }) {
  return <div className="chat-message opspilot"><span>OP</span><div><small>OpsPilot · evidence-backed report</small><p>{report.summary}</p>{report.hypotheses.map((item, index) => <article className="hypothesis" key={`${item.root_cause}-${index}`}><div><strong>{item.root_cause}</strong><b>{Math.round(item.confidence * 100)}% confidence</b></div><div className="confidence-track"><span style={{ width: `${Math.round(item.confidence * 100)}%` }}/></div><p>Evidence: {item.evidence_ids.map((id) => <code key={id}>{id.slice(0, 8)}</code>)}</p>{item.contradictory_evidence_ids.length > 0 && <p>Contradictory evidence: {item.contradictory_evidence_ids.map((id) => <code key={id}>{id.slice(0, 8)}</code>)}</p>}{item.next_evidence_needed && <p className="muted">Next evidence: {item.next_evidence_needed}</p>}</article>)}<p className="next-step">Next safe step: {report.recommended_next_step}</p></div></div>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
