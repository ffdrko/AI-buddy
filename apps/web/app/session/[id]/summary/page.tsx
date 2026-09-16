"use client";

import { use, useEffect, useState } from "react";
import { SessionSummary, getSessionSummary } from "../../../../lib/api";
import { RequireUser } from "../../../../lib/user";

function SummaryInner({ userId, sessionId }: { userId: string; sessionId: string }) {
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getSessionSummary(userId, sessionId).then(setSummary).catch((e) => setError(String(e)));
  }, [userId, sessionId]);

  if (error) return <main className="container"><p className="error">{error}</p></main>;
  if (!summary) return <main className="container"><p className="muted">Loading summary…</p></main>;

  return (
    <main className="container">
      <div className="card">
        <h1>Session summary</h1>
        <p>Accuracy: <strong>{Math.round(summary.accuracy * 100)}%</strong> ({summary.correct_count}/{summary.questions_answered})</p>
        <p className="muted">Study time: {summary.study_time_seconds}s</p>
        <p>Concepts covered:</p>
        {summary.concepts_covered.length === 0 && <p className="muted">None recorded.</p>}
        {summary.concepts_covered.map((c) => <span key={c} className="pill">{c}</span>)}
        <div className="row" style={{ marginTop: 12 }}>
          <a className="btn" href="/dashboard">Dashboard</a>
          <a className="btn btn-secondary" href="/progress">Progress</a>
        </div>
      </div>
    </main>
  );
}

export default function Summary({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <RequireUser>{(userId) => <SummaryInner userId={userId} sessionId={id} />}</RequireUser>;
}
