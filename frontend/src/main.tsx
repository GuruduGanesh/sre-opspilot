import { FormEvent, StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Incident = {
  id: string;
  incident_key: string;
  lifecycle_state: string;
  created_at: string;
  updated_at: string;
};

type TimelineItem = {
  occurred_at: string;
  event_type: "evidence" | "lifecycle";
  detail: string;
};

function App() {
  const [incidentId, setIncidentId] = useState("");
  const [incident, setIncident] = useState<Incident | null>(null);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [message, setMessage] = useState("Enter a scenario incident ID to inspect its persisted evidence.");
  const [loading, setLoading] = useState(false);

  async function loadIncident(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const id = incidentId.trim();
    if (!id) return;
    setLoading(true);
    setMessage("");
    try {
      const [incidentResponse, timelineResponse] = await Promise.all([
        fetch(`http://127.0.0.1:8000/api/v1/incidents/${encodeURIComponent(id)}`),
        fetch(`http://127.0.0.1:8000/api/v1/incidents/${encodeURIComponent(id)}/timeline`),
      ]);
      if (!incidentResponse.ok || !timelineResponse.ok) {
        throw new Error("Incident not found. Start the local API and send a scenario alert first.");
      }
      setIncident((await incidentResponse.json()) as Incident);
      setTimeline((await timelineResponse.json()) as TimelineItem[]);
      setMessage("");
    } catch (error) {
      setIncident(null);
      setTimeline([]);
      setMessage(error instanceof Error ? error.message : "Unable to load the incident.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main>
      <p className="eyebrow">OpsPilot · controlled demo environment</p>
      <h1>Incident console</h1>
      <p>Inspect the server-owned lifecycle and evidence timeline for a scenario incident.</p>
      <form onSubmit={loadIncident}>
        <label htmlFor="incident-id">Incident ID</label>
        <div className="input-row">
          <input
            id="incident-id"
            value={incidentId}
            onChange={(event) => setIncidentId(event.target.value)}
            placeholder="Paste an incident ID"
          />
          <button disabled={loading} type="submit">{loading ? "Loading…" : "Open"}</button>
        </div>
      </form>
      {message && <p className="notice">{message}</p>}
      {incident && (
        <section aria-label="Incident detail">
          <div className="incident-header">
            <div>
              <p className="eyebrow">Current lifecycle</p>
              <h2>{incident.lifecycle_state}</h2>
            </div>
            <code>{incident.id}</code>
          </div>
          <h3>Evidence timeline</h3>
          <ol className="timeline">
            {timeline.map((item, index) => (
              <li key={`${item.occurred_at}-${index}`}>
                <time>{new Date(item.occurred_at).toLocaleString()}</time>
                <strong>{item.event_type}</strong>
                <span>{item.detail}</span>
              </li>
            ))}
          </ol>
          <p className="boundary">
            Action approval, execution, and postmortem views are not implemented yet.
          </p>
        </section>
      )}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
