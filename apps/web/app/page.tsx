"use client";

import { useState } from "react";
import { useUser } from "../lib/user";

export default function Home() {
  const { userId, signIn, clear } = useUser();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "register">("register");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function go() {
    setError("");
    setBusy(true);
    try {
      await signIn(email.trim(), password, mode);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!userId) {
    return (
      <main className="container">
        <div className="card">
          <h1>Adaptive Study Platform</h1>
          <p className="muted">Upload material, study with generated questions, track mastery.</p>
          <div className="row">
            <button className={mode === "login" ? "btn" : "btn btn-secondary"} onClick={() => setMode("login")}>Log in</button>
            <button className={mode === "register" ? "btn" : "btn btn-secondary"} onClick={() => setMode("register")}>Register</button>
          </div>
          <label htmlFor="email">Email</label>
          <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <label htmlFor="pw">Password (8+ characters)</label>
          <input id="pw" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          <button className="btn" disabled={busy || !email || password.length < 8} onClick={go}>
            {busy ? "…" : mode === "login" ? "Log in" : "Create account"}
          </button>
          {error && <p className="error">{error}</p>}
        </div>
      </main>
    );
  }

  return (
    <main className="container">
      <div className="card">
        <h1>Adaptive Study Platform</h1>
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
