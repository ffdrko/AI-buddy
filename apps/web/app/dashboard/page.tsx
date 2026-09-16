"use client";

import { useEffect, useState } from "react";
import { getProgress, listDocuments, DocumentItem, ProgressSummary } from "../../lib/api";
import { RequireUser } from "../../lib/user";

function DashboardInner({ userId }: { userId: string }) {
  const [docs, setDocs] = useState<DocumentItem[] | null>(null);
  const [progress, setProgress] = useState<ProgressSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const [d, p] = await Promise.all([listDocuments(userId), getProgress(userId)]);
        setDocs(d.documents);
        setProgress(p);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, [userId]);

  if (error) return <main className="container"><p className="error">{error}</p></main>;
  if (!docs || !progress) return <main className="container"><p className="muted">Loading…</p></main>;

  const s = progress.streak;
  const showKeepGoing = s.last_study_date != null; // refined by recency in Phase 7
  return (
    <main className="container">
      <div className="card">
        <h2>Streak: {s.current_streak} day{s.current_streak === 1 ? "" : "s"}</h2>
        <p className="muted">Longest: {s.longest_streak} · Last studied: {s.last_study_date ?? "never"}</p>
        {showKeepGoing && s.current_streak > 0 && <p className="good">Keep going — study today to extend it.</p>}
      </div>
      <div className="card">
        <h2>Documents</h2>
        {docs.length === 0 && <p className="muted">No documents yet. <a href="/documents/upload">Upload a PDF</a>.</p>}
        {docs.map((d) => (
          <div key={d.id} className="card">
            <div className="row">
              <strong>{d.title ?? d.id.slice(0, 8)}</strong>
              <span className="pill">{d.status}</span>
            </div>
            {d.status === "ready" && (
              <div className="row" style={{ marginTop: 8 }}>
                <a className="btn" href={`/study/${d.id}`}>Study</a>
                <a className="btn btn-secondary" href={`/tutor/${d.id}`}>Tutor</a>
              </div>
            )}
            {d.status === "failed" && <p className="bad">Processing failed.</p>}
          </div>
        ))}
      </div>
      <div className="card">
        <h2>Recent sessions</h2>
        {progress.recent_sessions.length === 0 && <p className="muted">No sessions yet.</p>}
        {progress.recent_sessions.map((r) => (
          <p key={r.id} className="muted">
            {r.status} · {r.questions_answered} questions · {r.started_at ?? ""}
            {r.status === "completed" ? <a href={`/session/${r.id}/summary`}> — summary</a> : null}
          </p>
        ))}
      </div>
    </main>
  );
}

export default function Dashboard() {
  return <RequireUser>{(userId) => <DashboardInner userId={userId} />}</RequireUser>;
}
