"use client";

import { use, useState } from "react";
import { useRouter } from "next/navigation";
import { SessionGoal, startSession } from "../../../lib/api";
import { RequireUser } from "../../../lib/user";

function StudyInner({ userId, documentId }: { userId: string; documentId: string }) {
  const router = useRouter();
  const [minutes, setMinutes] = useState(25);
  const [goal, setGoal] = useState<SessionGoal>("mixed");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function start() {
    setError("");
    setBusy(true);
    try {
      const res = await startSession(userId, documentId, minutes, goal);
      router.push(`/session/${res.session_id}`);
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  return (
    <main className="container">
      <div className="card">
        <h1>Start session</h1>
        <label htmlFor="goal">Goal</label>
        <select id="goal" value={goal} onChange={(e) => setGoal(e.target.value as SessionGoal)}>
          <option value="mixed">Mixed</option>
          <option value="weak_focus">Focus on weaknesses</option>
          <option value="reinforce">Reinforce due reviews</option>
          <option value="explore">Explore new material</option>
        </select>
        <label htmlFor="mins">Available minutes</label>
        <input id="mins" type="number" min={5} max={180} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} />
        <button className="btn" disabled={busy} onClick={start}>{busy ? "Starting…" : "Start studying"}</button>
        {error && <p className="error">{error}</p>}
      </div>
    </main>
  );
}

export default function Study({ params }: { params: Promise<{ documentId: string }> }) {
  const { documentId } = use(params);
  return <RequireUser>{(userId) => <StudyInner userId={userId} documentId={documentId} />}</RequireUser>;
}
