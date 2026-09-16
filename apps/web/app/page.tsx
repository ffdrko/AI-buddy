"use client";

import { useState } from "react";
import { useUser } from "../lib/user";

export default function Home() {
  const { userId, setUserId, clear } = useUser();
  const [draft, setDraft] = useState("");

  if (!userId) {
    return (
      <main className="container">
        <div className="card">
          <h1>Adaptive Study Platform</h1>
          <p className="muted">Upload material, study with generated questions, track mastery.</p>
          <label htmlFor="uid">User id (dev auth — Phase 7 adds JWT)</label>
          <input id="uid" placeholder="e.g. demo-user-1" value={draft} onChange={(e) => setDraft(e.target.value)} />
          <button className="btn" disabled={!draft.trim()} onClick={() => setUserId(draft.trim())}>
            Continue
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="container">
      <div className="card">
        <h1>Adaptive Study Platform</h1>
        <p className="muted">Signed in as {userId}</p>
        <div className="row">
          <a className="btn" href="/dashboard">Dashboard</a>
          <a className="btn btn-secondary" href="/documents/upload">Upload PDF</a>
          <a className="btn btn-secondary" href="/progress">Progress</a>
          <button className="btn btn-secondary" onClick={clear}>Sign out</button>
        </div>
      </div>
    </main>
  );
}
