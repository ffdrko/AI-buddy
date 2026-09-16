"use client";

import { use, useState } from "react";
import { TutorSource, askTutor, sourceLabel } from "../../../lib/api";
import { RequireUser } from "../../../lib/user";

interface Msg {
  role: "user" | "assistant";
  content: string;
  sources?: TutorSource[];
}

function TutorInner({ userId, documentId }: { userId: string; documentId: string }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function send() {
    if (!draft.trim() || busy) return;
    setError("");
    setBusy(true);
    const history = [...msgs, { role: "user" as const, content: draft.trim() }];
    setMsgs(history);
    setDraft("");
    try {
      const res = await askTutor(
        userId,
        documentId,
        history[history.length - 1].content,
        history.slice(0, -1).map((m) => ({ role: m.role, content: m.content })),
      );
      setMsgs([...history, { role: "assistant", content: res.response_text, sources: res.sources }]);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="container">
      <div className="card">
        <h1>Tutor</h1>
        {msgs.length === 0 && <p className="muted">Ask anything about this document. Answers cite their sources.</p>}
        {msgs.map((m, i) => (
          <div key={i} className="chat-bubble">
            <strong>{m.role === "user" ? "You" : "Tutor"}</strong>
            <p>{m.content}</p>
            {m.sources && m.sources.length > 0 && (
              <p className="cite">Sources: {m.sources.map(sourceLabel).join(" · ")}</p>
            )}
          </div>
        ))}
        <div className="row">
          <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Ask a question…"
            onKeyDown={(e) => { if (e.key === "Enter") send(); }} />
        </div>
        <button className="btn" disabled={busy || !draft.trim()} onClick={send}>{busy ? "Thinking…" : "Send"}</button>
        {error && <p className="error">{error}</p>}
      </div>
    </main>
  );
}

export default function Tutor({ params }: { params: Promise<{ documentId: string }> }) {
  const { documentId } = use(params);
  return <RequireUser>{(userId) => <TutorInner userId={userId} documentId={documentId} />}</RequireUser>;
}
