"use client";

import { useEffect, useState } from "react";
import { ProgressSummary, getProgress, masteryBand } from "../../lib/api";
import { RequireUser } from "../../lib/user";

function ProgressInner({ userId }: { userId: string }) {
  const [data, setData] = useState<ProgressSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getProgress(userId).then(setData).catch((e) => setError(String(e)));
  }, [userId]);

  if (error) return <main className="container"><p className="error">{error}</p></main>;
  if (!data) return <main className="container"><p className="muted">Loading…</p></main>;

  const maxSecs = Math.max(1, ...data.daily_logs.map((d) => d.total_seconds));

  return (
    <main className="container">
      <div className="card">
        <h1>Progress</h1>
        <p>Current streak: <strong>{data.streak.current_streak}</strong> · Longest: {data.streak.longest_streak}</p>
      </div>
      <div className="card">
        <h2>Mastery by concept</h2>
        {data.topics.length === 0 && <p className="muted">Answer questions to build mastery data.</p>}
        {data.topics.map((t) => {
          const { band, showPercent } = masteryBand(t);
          return (
            <div key={t.concept} style={{ marginBottom: 12 }}>
              <div className="row">
                <strong>{t.concept}</strong>
                <span className="pill">{band}</span>
                {showPercent
                  ? <span className="muted">{Math.round(t.mastery_score * 100)}% · confidence {Math.round(t.confidence * 100)}%</span>
                  : <span className="muted">collecting evidence ({t.evidence_count}/3) — no score shown yet</span>}
              </div>
              {showPercent && <div className="bar"><div style={{ width: `${Math.round(t.mastery_score * 100)}%` }} /></div>}
            </div>
          );
        })}
      </div>
      <div className="card">
        <h2>Study time (last 30 days)</h2>
        {data.daily_logs.length === 0 && <p className="muted">No study logged yet.</p>}
        {data.daily_logs.map((d) => (
          <div key={d.study_date} style={{ marginBottom: 8 }}>
            <span className="muted">{d.study_date} · {Math.round(d.total_seconds / 60)} min · {d.questions_answered} Q</span>
            <div className="bar"><div style={{ width: `${Math.round((d.total_seconds / maxSecs) * 100)}%` }} /></div>
          </div>
        ))}
      </div>
    </main>
  );
}

export default function Progress() {
  return <RequireUser>{(userId) => <ProgressInner userId={userId} />}</RequireUser>;
}
