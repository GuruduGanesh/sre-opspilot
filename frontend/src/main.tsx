import { FormEvent, StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
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
  mode: "live_model" | "controlled_simulation";
  provenance: string;
};
type ActionPlan = {
  id: string; status: string; fingerprint: string;
  proposal: { action_type: string; evidence_ids: string[]; expires_at: string; expected_resource_version: string; requested_by?: string; proposed_at?: string | null };
  preview: { target?: string; dry_run?: boolean; changes?: Array<{ field: string; before: string; after: string; effect?: string }>; verification_plan?: { independent?: boolean; checks?: Array<{ kind: string; condition?: string; query?: string; window?: string; maximum?: number }> } };
  approved_by?: string; approved_at?: string | null; executed_at?: string | null; rejected_by?: string | null; rejected_at?: string | null; rejection_reason?: string | null;
  recovery?: RecoveryResult | null;
};
type RecoveryResult = { recovered: boolean; pending?: boolean; reason: string; workload: { ready_replicas: number; desired_replicas: number }; service_5xx_rate?: number | null; stability_window_remaining_seconds?: number | null };
type Postmortem = { summary: string; lifecycle_state: string; evidence_count: number; actions: Array<{ action_id: string; action_type: string; status: string; approved_by?: string }>; timeline: TimelineItem[]; sections: Array<{ heading: string; body: string }> };
type DemoScenario = { incident_id: string; scenario: string; recommended_action: string; message: string };
type Dashboard = {
  severity: string; alert_name: string; service: string; observed_at: string; incident_age_seconds: number;
  workload?: { ready_replicas: number; desired_replicas: number; restart_count: number; observed_at: string } | null;
  deployment_history: Array<{ revision: string; images: string[]; controlled_config: Record<string, string>; observed_at: string }>;
  events: Array<{ reason: string; message: string; event_type: string; involved_object: string; observed_at: string }>;
  telemetry: { status: string; message?: string | null; method: string; route: string; rate_window: string; recovery_window: string; error_rate?: number | null; request_rate?: number | null; recovery_error_rate?: number | null; error_ratio?: number | null; recovery_state: string; error_rate_trend: Array<{ observed_at: string; value: number }> };
  blast_radius: { status: string; workload: string; namespace: string; method: string; route: string; configured_callers: string[]; downstream_dependencies: string[]; message: string };
  service_context: { namespace: string; workload: string; image?: string | null; revision?: string | null; revision_observed_at?: string | null; controlled_config: Record<string, string>; ready_replicas?: number | null; desired_replicas?: number | null };
  situation_summary: string;
  next_step: string;
  slo_status: string; slo_message: string; collection_notes: string[];
  investigation_mode: "live_model" | "controlled_simulation";
};
type ConversationTurn = { question: string; report: Report };

declare global {
  interface Window {
    __opspilotReactRoot?: Root;
  }
}

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

function percentage(value?: number | null): string {
  return value === undefined || value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function incidentAge(createdAt?: string): string {
  if (!createdAt) return "—";
  return elapsed(Math.max(0, Math.floor((Date.now() - new Date(createdAt).getTime()) / 1_000)));
}

function displayState(state: string): string {
  return state.replace(/([a-z])([A-Z])/g, "$1 $2");
}

function elapsed(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return minutes ? `${minutes}m ${remaining}s` : `${remaining}s`;
}

function actionLabel(actionType: string): string {
  return {
    restore_response_mode: "Restore checkout response mode",
    restore_memory_mode: "Restore controlled memory mode",
    restart: "Restart checkout workload",
    scale: "Scale checkout workload",
  }[actionType] ?? actionType;
}

function shortId(value: string): string {
  return value.slice(0, 6);
}

function expiresIn(value: string, now: number): string {
  const seconds = Math.ceil((new Date(value).getTime() - now) / 1_000);
  return seconds > 0 ? `expires in ${elapsed(seconds)}` : "expired — create a new preview";
}

function RateTrend({ points, service, method, route }: { points: Dashboard["telemetry"]["error_rate_trend"]; service: string; method: string; route: string }) {
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
  if (!path) return <div className="chart-empty">Trend appears when the controlled Prometheus endpoint is connected for {service} {method} {route}.</div>;
  return <svg className="trend-chart" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label={`${service} ${method} ${route} HTTP 5xx requests per second over the last 15 minutes`}><defs><linearGradient id="rate-fill" x1="0" x2="0" y1="0" y2="1"><stop stopColor="#fb7185" stopOpacity=".45"/><stop offset="1" stopColor="#fb7185" stopOpacity="0"/></linearGradient></defs><path d={`${path} L100,100 L0,100 Z`} fill="url(#rate-fill)"/><path d={path} fill="none" stroke="#fb7185" strokeWidth="2.4" vectorEffect="non-scaling-stroke"/></svg>;
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
  const [recovery, setRecovery] = useState<RecoveryResult | null>(null);
  const [postmortem, setPostmortem] = useState<Postmortem | null>(null);
  const [question, setQuestion] = useState("What evidence supports the current root-cause hypothesis?");
  const [actionType, setActionType] = useState("restore_response_mode");
  const [operatorName, setOperatorName] = useState("local-oncall");
  const [rejectionReason, setRejectionReason] = useState("");
  const [message, setMessage] = useState("Open a controlled scenario incident to begin triage.");
  const [loading, setLoading] = useState(false);
  const [demoBusy, setDemoBusy] = useState(false);
  const [demoScenario, setDemoScenario] = useState<"p1" | "p2">("p1");
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("incident");
    void refreshQueue(requested ?? undefined, Boolean(requested));
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
      const [loadedIncident, loadedTimeline, loadedEvidence, loadedDashboard, loadedPlans, loadedReport] = await Promise.all([
        jsonOrError<Incident>(fetch(`${apiBaseUrl}/api/v1/incidents/${encodeURIComponent(id)}`)),
        jsonOrError<TimelineItem[]>(fetch(`${apiBaseUrl}/api/v1/incidents/${encodeURIComponent(id)}/timeline`)),
        jsonOrError<Evidence[]>(fetch(`${apiBaseUrl}/api/v1/incidents/${encodeURIComponent(id)}/evidence`)),
        jsonOrError<Dashboard>(fetch(`${apiBaseUrl}/api/v1/incidents/${encodeURIComponent(id)}/dashboard`)),
        jsonOrError<ActionPlan[]>(fetch(`${apiBaseUrl}/api/v1/incidents/${encodeURIComponent(id)}/actions`)),
        jsonOrError<Report | null>(fetch(`${apiBaseUrl}/api/v1/incidents/${encodeURIComponent(id)}/investigation`)),
      ]);
      setIncident(loadedIncident); setTimeline(loadedTimeline); setEvidence(loadedEvidence); setDashboard(loadedDashboard);
      setActionType(
        loadedDashboard.alert_name === "CheckoutMemoryPressure" ? "restore_memory_mode" : "restore_response_mode"
      );
      const latestPlan = loadedPlans.at(-1) ?? null;
      setReport(loadedReport); setConversation([]); setPlan(latestPlan); setRecovery(latestPlan?.recovery ?? null); setPostmortem(null); setMessage("");
      window.history.replaceState({}, "", `${window.location.pathname}?incident=${encodeURIComponent(id)}`);
    } catch (error) {
      setIncident(null); setTimeline([]); setEvidence([]); setDashboard(null);
      const detail = error instanceof Error ? error.message : "Unable to load the incident.";
      if (detail.toLowerCase().includes("incident not found")) {
        setIncidentId("");
        window.history.replaceState({}, "", window.location.pathname);
        setMessage("That local incident record no longer exists. Select an open incident or inject a new controlled scenario.");
      } else {
        setMessage(detail);
      }
    } finally { setLoading(false); }
  }

  async function refreshQueue(preferredId?: string, autoOpen = false) {
    try {
      const items = await jsonOrError<IncidentQueueItem[]>(fetch(`${apiBaseUrl}/api/v1/incidents?state=open`));
      setQueue(items);
      if (autoOpen) {
        // The queue deliberately excludes resolved/RCA records, but a direct incident
        // link remains a valid audit-review path for any persisted local incident.
        if (preferredId) await openIncident(preferredId);
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

  async function waitForScenarioTelemetry(incident: string, scenario: "p1" | "p2"): Promise<Dashboard | null> {
    const deadline = Date.now() + 60_000;
    let latest: Dashboard | null = null;
    while (Date.now() < deadline) {
      latest = await jsonOrError<Dashboard>(fetch(`${apiBaseUrl}/api/v1/incidents/${encodeURIComponent(incident)}/dashboard`));
      const hasTraffic = latest.telemetry.status === "live" && (latest.telemetry.request_rate ?? 0) > 0;
      const expectedSignalPresent = scenario === "p1" ? (latest.telemetry.error_rate ?? 0) > 0 : hasTraffic;
      if (hasTraffic && expectedSignalPresent) return latest;
      await new Promise((resolve) => window.setTimeout(resolve, 3_000));
    }
    return latest;
  }

  async function startDemoScenario(scenario: "p1" | "p2") {
    setDemoBusy(true);
    try {
      const result = await jsonOrError<DemoScenario>(fetch(`${apiBaseUrl}/api/v1/demo/scenarios/${scenario}`, { method: "POST" }));
      setIncidentId(result.incident_id); setActionType(result.recommended_action);
      await openIncident(result.incident_id);
      await refreshQueue();
      setMessage(`${result.message} The incident is ready for triage; live route telemetry is warming in the background.`);
      void waitForScenarioTelemetry(result.incident_id, scenario).then((warmedDashboard) => {
        if (warmedDashboard?.telemetry.status === "live" && (warmedDashboard.telemetry.request_rate ?? 0) > 0) {
          setMessage("Live route telemetry is ready.");
        } else {
          setMessage("Telemetry did not warm within 60 seconds. Check the controlled Prometheus connection; no values were invented.");
        }
      }).catch(() => setMessage("Telemetry warm-up could not be checked. Use Refresh; no values were invented."));
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

  function closeIncident() {
    setIncident(null); setTimeline([]); setEvidence([]); setDashboard(null);
    setReport(null); setConversation([]); setPlan(null); setRecovery(null); setPostmortem(null);
    setIncidentId(""); setMessage("Select a controlled incident or start a P1/P2 rehearsal to begin triage.");
    window.history.replaceState({}, "", window.location.pathname);
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
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action_type: actionType, evidence_ids: evidence.map((item) => item.id), requested_by: operatorName }),
      }));
      setPlan(result); setRecovery(null); setMessage("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Preview failed."); }
    finally { setLoading(false); }
  }

  async function approveAction() {
    if (!plan) return;
    setLoading(true);
    try {
      const result = await jsonOrError<ActionPlan>(fetch(`${apiBaseUrl}/api/v1/actions/${plan.id}/approve`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved_by: operatorName }),
      }));
      setPlan(result); setApprovalOpen(false); setMessage("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Approval failed."); }
    finally { setLoading(false); }
  }

  async function rejectAction() {
    if (!plan) return;
    setLoading(true);
    try {
      const result = await jsonOrError<ActionPlan>(fetch(`${apiBaseUrl}/api/v1/actions/${plan.id}/reject`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rejected_by: operatorName, reason: rejectionReason || undefined }),
      }));
      setPlan(result); setApprovalOpen(false); setMessage("Action plan rejected and the incident returned to triage. No cluster change was applied.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Rejection failed."); }
    finally { setLoading(false); }
  }

  async function updateAction(path: "execute" | "verify") {
    if (!plan) return;
    setLoading(true);
    try {
      const result = await jsonOrError<ActionPlan | { plan: ActionPlan; recovery: RecoveryResult }>(fetch(`${apiBaseUrl}/api/v1/actions/${plan.id}/${path}`, { method: "POST" }));
      if ("plan" in result) { setPlan(result.plan); setRecovery(result.recovery); } else { setPlan(result); }
      setMessage(""); await refreshDashboard();
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
  const queueServices = [...new Set(queue.map((item) => item.service))];
  const queueHeading = queueServices.length === 1 ? `${queueServices[0]} incidents` : "On-call queue";
  const evidenceById = new Map(evidence.map((item) => [item.id, item]));

  return <main className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-mark">OP</span><div><strong>OpsPilot</strong><span>Evidence-first incident response</span></div></div>
      <div className="environment"><span className="live-dot"/> Local Kubernetes + Prometheus only <small>GitHub not connected</small>{dashboard?.investigation_mode === "controlled_simulation" && <strong className="simulation-badge">Simulation report mode · not GPT-5.6</strong>}</div>
      <form className="incident-picker" onSubmit={loadIncident}><label htmlFor="incident-id">Incident record</label><div><input id="incident-id" value={incidentId} onChange={(event) => setIncidentId(event.target.value)} placeholder="Paste incident ID"/><button disabled={loading} type="submit">{loading ? "Loading…" : "Open"}</button>{incident && <button className="ghost-button" type="button" onClick={closeIncident}>Close</button>}<select aria-label="Controlled rehearsal scenario" value={demoScenario} onChange={(event) => setDemoScenario(event.target.value as "p1" | "p2")}><option value="p1">P1 · 5xx</option><option value="p2">P2 · memory</option></select><button disabled={demoBusy} type="button" onClick={() => startDemoScenario(demoScenario)}>{demoBusy ? "Starting…" : `Inject ${demoScenario.toUpperCase()} incident`}</button><button disabled={demoBusy} className="ghost-button toolbar-reset" type="button" onClick={resetDemoScenario}>Reset</button></div></form>
    </header>

    {message && <div className="notice" role="status">{message}</div>}

    {!incident && <section className="empty-state command-center-empty"><div className="empty-hero"><p className="eyebrow">Controlled incident command center</p><h1>Evidence first.<br/>One incident at a time.</h1><p>Start a controlled local scenario or open one persisted incident record. OpsPilot only shows the Kubernetes, Prometheus, and audit evidence bound to that incident.</p><div className="empty-scenarios" aria-label="Start a controlled incident"><article><span>01 · Controlled P1</span><h2>Checkout 5xx</h2><p>A response failure on <code>GET /checkout</code> with live route telemetry and deployment context.</p><button disabled={demoBusy} type="button" onClick={() => void startDemoScenario("p1")}>{demoBusy ? "Starting…" : "Inject P1 incident"}</button></article><article><span>02 · Controlled P2</span><h2>Memory pressure</h2><p>A workload restart scenario with Kubernetes events and restart-stability verification.</p><button disabled={demoBusy} className="ghost-button" type="button" onClick={() => void startDemoScenario("p2")}>{demoBusy ? "Starting…" : "Inject P2 incident"}</button></article><article className="open-record-card"><span>Existing record</span><h2>Review the trail</h2><p>Paste an incident ID above to reopen its evidence, approval history, recovery result, and RCA.</p></article></div></div><aside className="source-boundary"><strong>Evidence boundaries</strong><span><b>Prometheus</b><em>Route rate, 5xx trend, recovery threshold</em></span><span><b>Kubernetes</b><em>Workload state, events, and deployment revisions</em></span><span><b>Local incident store</b><em>Alert payload, evidence, approvals, and audit events</em></span><span><b>GitHub</b><em>Not connected in this controlled demo</em></span></aside>{queue.length ? <section className="empty-queue" aria-label="Open controlled incidents"><p className="eyebrow">Open controlled incidents</p><p>Persisted local records are available for review.</p><ul className="queue-bullets">{queue.slice(0, 5).map((item) => <li key={item.id}><button type="button" aria-label={`Open ${item.alert_name} incident ${item.id.slice(-6)}`} onClick={() => void openIncident(item.id)}><span className={`queue-severity severity-${item.severity.toLowerCase()}`}/><span><strong>{item.alert_name}</strong><small>{item.service} · {displayState(item.lifecycle_state)} · {incidentAge(item.created_at)} · #{item.id.slice(-6)}</small></span></button></li>)}</ul></section> : <p className="muted empty-queue-note">No open controlled incidents. Start P1 or P2 above to begin.</p>}</section>}

    {incident && dashboard && <>
      <section className="incident-banner"><div><p className="eyebrow">Active incident</p><h1>{dashboard.service} <span>/ {dashboard.alert_name}</span></h1><p className="incident-id">{incident.id}</p></div><div className="incident-age"><span>Time since received</span><strong>{elapsed(dashboard.incident_age_seconds)}</strong></div><div className={`severity severity-${dashboard.severity.toLowerCase()}`}>{dashboard.severity}</div><div className="state-pill">{displayState(incident.lifecycle_state)}</div></section>

      <section className="situation-grid" aria-label="Incident situation and next step"><article className="situation-card"><p className="eyebrow">Current situation</p><p>{dashboard.situation_summary}</p></article><article className="next-step-card"><p className="eyebrow">Next safe step</p><p>{dashboard.next_step}</p></article></section>

      <section className="metric-grid" aria-label="Current controlled signals">
        <Metric label="5xx requests / second" value={metric(telemetry?.error_rate)} detail={telemetry?.status === "live" ? `${dashboard.service} ${telemetry.method} ${telemetry.route} · HTTP 5xx · ${telemetry.rate_window} rate` : "Telemetry not connected"} tone="danger"/>
        <Metric label="Request rate" value={metric(telemetry?.request_rate)} detail={`${dashboard.service} ${telemetry?.method ?? "GET"} ${telemetry?.route ?? "/checkout"} · all statuses · ${telemetry?.rate_window ?? "1m"} rate`} tone="neutral"/>
        <Metric label="Error ratio" value={telemetry?.error_ratio === undefined || telemetry?.error_ratio === null ? "—" : `${percentage(telemetry.error_ratio)} failing`} detail={`${dashboard.service} ${telemetry?.method ?? "GET"} ${telemetry?.route ?? "/checkout"} · current ${telemetry?.rate_window ?? "1m"} ratio`} tone={telemetry?.error_ratio && telemetry.error_ratio > 0 ? "danger" : "success"}/>
        <Metric label="Workload health" value={dashboard.workload ? `${dashboard.workload.ready_replicas}/${dashboard.workload.desired_replicas}` : "—"} detail={dashboard.workload ? `${dashboard.workload.restart_count} pod restarts observed` : "Kubernetes read unavailable"} tone={dashboard.workload?.ready_replicas === dashboard.workload?.desired_replicas ? "success" : "warning"}/>
        <Metric label={`Recovery gate · ${telemetry?.recovery_state?.toUpperCase() ?? "UNKNOWN"}`} value={metric(telemetry?.recovery_error_rate)} detail={`${dashboard.service} ${telemetry?.method ?? "GET"} ${telemetry?.route ?? "/checkout"} · 5xx ≤ 0.010/s over ${telemetry?.recovery_window ?? "15s"}`} tone={telemetry?.recovery_state === "passing" ? "success" : telemetry?.recovery_state === "failing" ? "danger" : "warning"}/>
        <Metric label={report?.hypotheses[0] ? (report.mode === "controlled_simulation" ? "Simulation confidence" : "Model confidence") : "Investigation"} value={report?.hypotheses[0] ? `${Math.round(report.hypotheses[0].confidence * 100)}%` : "Not run"} detail={report ? report.provenance : dashboard.investigation_mode === "controlled_simulation" ? "Deterministic rehearsal mode · not GPT-5.6" : "Run a live investigation below"} tone="accent"/>
      </section>

      <section className="command-layout">
        <aside className="lifecycle-rail"><section className="queue-rail" aria-label="Open incident queue"><div className="queue-rail-heading"><p className="rail-title">{queueHeading}</p><button className="ghost-button" type="button" onClick={() => void refreshQueue()}>Refresh</button></div>{queue.length ? <ul className="queue-bullets">{queue.slice(0, 5).map((item) => <li key={item.id}><button className={item.id === incident?.id ? "selected" : ""} type="button" aria-label={`Open ${item.alert_name} incident ${item.id.slice(-6)}`} onClick={() => void openIncident(item.id)}><span className={`queue-severity severity-${item.severity.toLowerCase()}`}/><span><strong>{item.alert_name}</strong><small>{queueServices.length > 1 ? `${item.service} · ` : ""}{displayState(item.lifecycle_state)} · {incidentAge(item.created_at)} · #{item.id.slice(-6)}</small></span></button></li>)}</ul> : <p className="queue-empty">{queueLoaded ? "No open incidents" : "Loading queue…"}</p>}{queue.length > 5 && <p className="queue-more">Priority set · refresh for newer alerts</p>}</section><p className="rail-title">Incident lifecycle</p>{lifecycle.map((stage, index) => <div className={`lifecycle-step ${index < currentStage ? "complete" : ""} ${index === currentStage ? "current" : ""}`} key={stage}><span>{index < currentStage ? "✓" : index + 1}</span><strong>{displayState(stage)}</strong></div>)}<div className="rail-note">Server-owned transitions prevent a model or browser from marking remediation complete.</div></aside>

        <section className="main-pane">
          <section className="panel telemetry-panel"><div className="panel-heading"><div><p className="eyebrow">Live telemetry · controlled route</p><h2>{dashboard.service} {telemetry?.method ?? "GET"} {telemetry?.route ?? "/checkout"} · HTTP 5xx trend</h2></div><div className="telemetry-actions"><span>Auto-refresh · 5s · {formatTime(dashboard.observed_at)}</span><button className="ghost-button" type="button" onClick={refreshDashboard}>Refresh</button></div></div><RateTrend points={telemetry?.error_rate_trend ?? []} service={dashboard.service} method={telemetry?.method ?? "GET"} route={telemetry?.route ?? "/checkout"}/><div className="chart-footer"><span>15-minute trend · {telemetry?.recovery_window ?? "15s"} 5xx rate · Prometheus</span><strong>{telemetry?.status === "live" ? metric(telemetry.error_rate) : telemetry?.message ?? "Not configured"}</strong></div></section>

          <section className="three-column">
            <section className="panel"><p className="eyebrow">Service context</p><h2>{dashboard.service_context.namespace}/{dashboard.service_context.workload}</h2><div className="context-facts"><span>Image</span><strong>{dashboard.service_context.image ?? "Not returned"}</strong><span>Revision</span><strong>{dashboard.service_context.revision ? `${dashboard.service_context.revision} · ${formatTime(dashboard.service_context.revision_observed_at ?? undefined)}` : "Not returned"}</strong><span>Readiness</span><strong>{dashboard.service_context.ready_replicas ?? "—"}/{dashboard.service_context.desired_replicas ?? "—"} ready</strong>{Object.entries(dashboard.service_context.controlled_config).map(([name, value]) => <div className="context-config" key={name}><span>Controlled setting</span><strong>{name}={value}</strong></div>)}</div></section>
            <section className="panel"><p className="eyebrow">Scope / blast radius</p><h2>Known controlled traffic path</h2><div className="scope-card"><strong>{dashboard.blast_radius.namespace}/{dashboard.blast_radius.workload}</strong><span>Directly affected endpoint · {dashboard.blast_radius.method} {dashboard.blast_radius.route}</span></div><div className="scope-list"><span>Configured traffic source</span>{dashboard.blast_radius.configured_callers.map((caller) => <strong key={caller}>{caller}</strong>)}<span>Downstream dependencies</span>{dashboard.blast_radius.downstream_dependencies.length ? dashboard.blast_radius.downstream_dependencies.map((dependency) => <strong key={dependency}>{dependency}</strong>) : <strong>None instrumented in this scenario</strong>}</div><p className="muted">{dashboard.blast_radius.message}</p></section>
            <section className="panel"><p className="eyebrow">SLI and recovery policy</p><h2>No SLO invented</h2><p className="muted">{dashboard.slo_message}</p><div className="policy-line"><span>Recovery verification</span><strong>{actionType === "restore_memory_mode" || actionType === "restart" ? "Readiness + 30s restart stability" : "Readiness + 15s 5xx threshold"}</strong></div></section>
          </section>

          <section className="panel investigation-panel"><p className="eyebrow">Conversational investigation</p><h2>Ask a follow-up question</h2><p className="muted">Each question takes a fresh Prometheus and Kubernetes evidence snapshot. The model can cite it, but can never execute a remediation.</p>{dashboard.investigation_mode === "controlled_simulation" ? <p className="simulation-notice">Controlled rehearsal reports are enabled. They are deterministic evidence summaries, not GPT-5.6 output.</p> : <p className="muted">Live-model mode is active. If API access is unavailable, restart the local API with <code>run-console.ps1 -ControlledSimulation</code> for an explicitly labelled rehearsal.</p>}<div className="question-chips"><button type="button" onClick={() => setQuestion("What changed before the alert?")}>What changed?</button><button type="button" onClick={() => setQuestion("What evidence supports the current root-cause hypothesis?")}>Show evidence</button><button type="button" onClick={() => setQuestion("What is the confirmed affected scope?")}>What is affected?</button><button type="button" onClick={() => setQuestion("What are the current 5xx rate, request rate, readiness, and restarts?")}>Current health</button></div><div className="chat-compose"><input value={question} onChange={(event) => setQuestion(event.target.value)} aria-label="Ask about the incident"/><button disabled={loading} type="button" onClick={investigate}>{loading ? "Collecting evidence…" : "Investigate"}</button></div>
            {conversation.length ? conversation.map((turn, turnIndex) => <div className="conversation-turn" key={`${turn.question}-${turnIndex}`}><div className="chat-message engineer"><span>ON</span><div><small>On-call engineer</small><p>{turn.question}</p></div></div><ReportMessage report={turn.report}/></div>) : report ? <div className="persisted-investigation"><p className="eyebrow">Latest persisted investigation</p><ReportMessage report={report}/></div> : <div className="empty-investigation">{dashboard.investigation_mode === "controlled_simulation" ? "Controlled rehearsal reports are enabled. They are deterministic evidence summaries, not GPT-5.6 outputs." : "No model report recorded. Run a live GPT-5.6 investigation; no hypothesis or confidence is fabricated here."}</div>}
          </section>

          <section className="panel timeline-panel"><p className="eyebrow">Incident trail</p><h2>Evidence and state changes</h2><p className="muted">Showing the latest {recentTimeline.length} events from the persisted audit trail.</p><ol className="timeline">{recentTimeline.map((item, index) => <li key={`${item.occurred_at}-${index}`}><time>{formatTime(item.occurred_at)}</time><span className={`timeline-kind ${item.event_type}`}>{item.event_type}</span><p>{item.detail}</p></li>)}</ol></section>
        </section>

        <aside className="evidence-rail"><section className="panel compact"><p className="eyebrow">Evidence stream</p><h2>{evidence.length} persisted records</h2><p className="muted">Showing latest {recentEvidence.length}</p><div className="evidence-list">{recentEvidence.map((item) => <article id={`evidence-${item.id}`} key={item.id}><div><span className={`source-badge ${item.source_type}`}>{item.source_type}</span><time>{formatTime(item.observed_at)}</time></div><p>{item.summary}</p><code>E-{shortId(item.id)} · {item.source_ref}</code></article>)}</div></section>
          <section className="panel compact"><p className="eyebrow">Cluster events</p><h2>Current Kubernetes context</h2>{dashboard.events.length ? <div className="event-list">{dashboard.events.map((event, index) => <article key={`${event.observed_at}-${index}`}><span>{event.event_type}</span><strong>{event.reason}</strong><p>{event.message}</p><time>{formatTime(event.observed_at)} · {event.involved_object}</time></article>)}</div> : <p className="muted">No matching Kubernetes events were returned for this workload.</p>}</section>
          <section className="panel compact"><p className="eyebrow">Deployment history</p><h2>Revisions · current first</h2>{dashboard.deployment_history.length ? <div className="deployment-list">{dashboard.deployment_history.map((revision) => <div key={revision.revision}><strong>Revision {revision.revision}</strong><span>{revision.images.join(", ")}</span>{Object.entries(revision.controlled_config).map(([name, value]) => <span key={name}>{name}={value}</span>)}<time>ReplicaSet created {formatTime(revision.observed_at)}</time></div>)}</div> : <p className="muted">Deployment history is unavailable.</p>}</section>
          <section className="panel approval-panel"><p className="eyebrow">Human approval gate</p><h2>Propose a controlled restoration</h2><p className="muted">A preview validates an allowlisted Kubernetes patch but does not apply it. An explicit human approval is required before execution.</p>{incident.lifecycle_state === "Triaging" && <p className="state-message">Investigation is read-only and keeps the incident in triage. Review the evidence, then create a dry-run preview to move to Action Proposed.</p>}<select value={actionType} onChange={(event) => setActionType(event.target.value)} aria-label="Allowlisted action"><option value="restore_response_mode">Restore checkout response mode</option><option value="restore_memory_mode">Restore controlled memory mode</option><option value="restart">Restart checkout workload</option><option value="scale">Scale checkout workload</option></select><button disabled={loading || incident.lifecycle_state === "Resolved"} type="button" onClick={previewAction}>Create dry-run preview</button>{incident.lifecycle_state === "Resolved" && <p className="muted">This incident is resolved. New remediation proposals are correctly blocked.</p>}
            {plan && <article className="action-plan"><div className="action-plan-heading"><span className="status-chip">{plan.status}</span><span className="plan-expiry">plan {shortId(plan.fingerprint)} · {expiresIn(plan.proposal.expires_at, now)}</span></div><h3>{actionLabel(plan.proposal.action_type)}</h3><p className="plan-target">Target: {plan.preview.target ?? "Controlled workload target"}</p><p className="plan-meta">Requested by {plan.proposal.requested_by ?? "local-oncall"} (self-declared local identity){plan.proposal.proposed_at ? ` · plan created ${formatTime(plan.proposal.proposed_at)}` : ""}</p><div className="plan-rationale"><strong>Evidence binding</strong><p>This plan is tied to persisted records from this incident; review the records before approving.</p><div className="evidence-chips">{plan.proposal.evidence_ids.map((id) => <a href={`#evidence-${id}`} key={id} title={evidenceById.get(id)?.summary ?? "Persisted incident evidence"}>E-{shortId(id)}</a>)}</div></div><div className="planned-changes"><strong>What will change</strong><p>Current deployment values read by the server; the dry-run validates the same patch. Nothing has been applied yet.</p>{plan.preview.changes?.length ? plan.preview.changes.map((change) => <div className="change-row" key={change.field}><code>{change.field}</code><span>{change.before}</span><b>→</b><span>{change.after}</span>{change.effect && <small>{change.effect}</small>}</div>) : <p className="muted">The dry-run succeeded, but this adapter did not return a normalized before/after value.</p>}</div><p className={plan.preview.dry_run ? "preview-success" : "preview-warning"}>{plan.preview.dry_run ? "Preview successful — nothing applied yet. Approving authorizes the exact change above." : "Preview data is unavailable; do not approve this plan."}</p><div className="verification-contract"><strong>Verified after apply</strong><p>Checked by the independent recovery verifier, not by the model.</p>{plan.preview.verification_plan?.checks?.map((check) => <div className="verification-check" key={check.kind}><strong>{check.kind.replaceAll("_", " ")}</strong><span>{check.condition ?? `${check.query} ≤ ${decimal(check.maximum)} / ${check.window ?? "—"}`}</span></div>)}</div><p className="binding-caption">Approval is bound to plan {shortId(plan.fingerprint)} and target version {plan.proposal.expected_resource_version}. Execution is refused if either changes first.</p>{plan.status === "Previewed" && <button type="button" onClick={() => setApprovalOpen(true)}>Review exact plan</button>}{plan.status === "Approved" && <><p className="state-message">Approved by {plan.approved_by}. Execution has not started.</p><button disabled={loading} type="button" onClick={() => updateAction("execute")}>Run approved controlled action</button></>}{plan.status === "Executed" && <><p className="state-message">Action applied at {formatTime(plan.executed_at ?? undefined)}. Independent verification is still required.</p>{recovery?.pending && <p className="state-message">Verification observation active: {recovery.reason}</p>}<button disabled={loading} type="button" onClick={() => updateAction("verify")}>{recovery?.pending ? "Check stability again" : "Verify recovery now"}</button></>}{plan.status === "Verified" && <p className="verification-success">Recovery verified{recovery?.reason ? `: ${recovery.reason}` : "."}</p>}{plan.status === "Failed" && <p className="preview-warning">Recovery was not verified{recovery?.reason ? `: ${recovery.reason}` : "."} The incident returned to triage.</p>}{plan.status === "Rejected" && <p className="state-message">Rejected by {plan.rejected_by}{plan.rejection_reason ? `: ${plan.rejection_reason}` : ""}. No cluster change was applied.</p>}{plan.status === "Expired" && <p className="preview-warning">This preview expired. Nothing was applied; create a new preview from triage.</p>}</article>}
          </section>
          <section className="panel compact postmortem-card"><p className="eyebrow">Audit-derived postmortem</p><p>Creates a factual RCA draft from the persisted incident record after recovery is verified.</p><button className="ghost-button" disabled={incident.lifecycle_state !== "Resolved" && incident.lifecycle_state !== "RCA"} type="button" onClick={loadPostmortem}>Draft RCA</button>{incident.lifecycle_state !== "Resolved" && incident.lifecycle_state !== "RCA" && <p className="muted">Available after verified recovery.</p>}</section>
          {dashboard.collection_notes.length > 0 && <section className="collection-notes">{dashboard.collection_notes.map((note) => <p key={note}>{note}</p>)}</section>}
        </aside>
      </section>
    </>}

    {approvalOpen && plan && <div className="modal-backdrop" role="presentation"><section className="modal approval-modal" role="dialog" aria-modal="true" aria-labelledby="approval-title"><p className="eyebrow">Explicit human approval required</p><h2 id="approval-title">Approve {actionLabel(plan.proposal.action_type)}?</h2><p>This approves only plan {shortId(plan.fingerprint)}. It does not authorize another command, and execution is refused if the target version changes.</p><dl><div><dt>Target</dt><dd>{plan.preview.target ?? "Controlled workload"}</dd></div><div><dt>Requested by</dt><dd>{plan.proposal.requested_by ?? "local-oncall"} (self-declared local identity)</dd></div><div><dt>Evidence binding</dt><dd>{plan.proposal.evidence_ids.length} persisted records</dd></div><div><dt>Expires</dt><dd>{expiresIn(plan.proposal.expires_at, now)}</dd></div></dl><section className="modal-change-summary"><strong>Exact planned change</strong>{plan.preview.changes?.map((change) => <p key={change.field}><code>{change.field}</code> {change.before} → {change.after}</p>)}</section><label htmlFor="approver">Your on-call identity (self-declared in this local demo)</label><input id="approver" value={operatorName} onChange={(event) => setOperatorName(event.target.value)}/><label htmlFor="rejection-reason">If rejecting, optional reason</label><input id="rejection-reason" value={rejectionReason} onChange={(event) => setRejectionReason(event.target.value)} placeholder="Need another signal"/><div className="modal-actions"><button className="ghost-button" type="button" onClick={() => setApprovalOpen(false)}>Cancel</button><button className="danger-button" disabled={loading} type="button" onClick={rejectAction}>Reject plan</button><button disabled={loading || Date.parse(plan.proposal.expires_at) <= now} type="button" onClick={approveAction}>Approve exact plan</button></div></section></div>}

    {postmortem && <div className="modal-backdrop" role="presentation"><section className="modal postmortem-modal" role="dialog" aria-modal="true" aria-labelledby="postmortem-title"><p className="eyebrow">Structured incident record</p><h2 id="postmortem-title">RCA draft</h2><p>{postmortem.summary}</p><div className="rca-sections">{postmortem.sections.map((section) => <section key={section.heading}><h3>{section.heading}</h3><p>{section.body}</p></section>)}</div><h3>Timeline</h3><ol>{postmortem.timeline.map((item, index) => <li key={`${item.occurred_at}-${index}`}>{formatTime(item.occurred_at)} — {item.detail}</li>)}</ol><h3>Recorded actions</h3><ul>{postmortem.actions.map((action) => <li key={action.action_id}>{action.action_type} · {action.status}{action.approved_by ? ` · approved by ${action.approved_by}` : ""}</li>)}</ul><div className="modal-actions"><button className="ghost-button" type="button" onClick={() => setPostmortem(null)}>Close draft</button>{incident?.lifecycle_state === "RCA" && <button type="button" onClick={publishPostmortem}>Mark RCA published</button>}</div></section></div>}
  </main>;
}

function Metric({ label, value, detail, tone }: { label: string; value: string; detail: string; tone: string }) {
  return <article className={`metric-card ${tone}`}><p>{label}</p><strong>{value}</strong><span>{detail}</span></article>;
}

function ReportMessage({ report }: { report: Report }) {
  return <div className="chat-message opspilot"><span>OP</span><div><small>OpsPilot · evidence-backed report</small>{report.mode === "controlled_simulation" && <p className="simulation-notice">Controlled simulation — deterministic local evidence report, not GPT-5.6.</p>}<p>{report.summary}</p>{report.hypotheses.map((item, index) => <article className="hypothesis" key={`${item.root_cause}-${index}`}><div><strong>{item.root_cause}</strong><b>{Math.round(item.confidence * 100)}% confidence</b></div><div className="confidence-track"><span style={{ width: `${Math.round(item.confidence * 100)}%` }}/></div><p>Evidence: {item.evidence_ids.map((id) => <code key={id}>{id.slice(0, 8)}</code>)}</p>{item.contradictory_evidence_ids.length > 0 && <p>Contradictory evidence: {item.contradictory_evidence_ids.map((id) => <code key={id}>{id.slice(0, 8)}</code>)}</p>}{item.next_evidence_needed && <p className="muted">Next evidence: {item.next_evidence_needed}</p>}</article>)}<p className="next-step">Next safe step: {report.recommended_next_step}</p></div></div>;
}

const rootContainer = document.getElementById("root");
if (!rootContainer) {
  throw new Error("OpsPilot root container is missing");
}
const root = window.__opspilotReactRoot ?? createRoot(rootContainer);
window.__opspilotReactRoot = root;
root.render(<StrictMode><App /></StrictMode>);
