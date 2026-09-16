"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AnswerResult, PublicQuestion, endSession, nextQuestion, submitAnswer } from "../../../lib/api";
import { RequireUser } from "../../../lib/user";

function elapsed(start: number): string {
  const s = Math.floor((Date.now() - start) / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; // display only
}

function SessionInner({ userId, sessionId }: { userId: string; sessionId: string }) {
  const router = useRouter();
  const [question, setQuestion] = useState<PublicQuestion | null>(null);
  const [answerText, setAnswerText] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const qStart = useRef(Date.now());
  const clockStart = useRef(Date.now());

  const load = useCallback(async () => {
    setError("");
    setResult(null);
    setAnswerText("");
    setSelected(null);
    try {
      const q = await nextQuestion(userId, sessionId);
      qStart.current = Date.now();
      setQuestion(q);
    } catch (e) {
      setError(String(e));
    }
  }, [userId, sessionId]);

  useEffect(() => {
    load();
    const t = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, [load]);
  void tick;

  async function submit() {
    if (!question) return;
    setBusy(true);
    setError("");
    try {
      const res = await submitAnswer(userId, sessionId, {
        question_id: question.id,
        response_text: question.question_type === "mcq" ? undefined : answerText,
        selected_option_id: question.question_type === "mcq" ? (selected ?? undefined) : undefined,
        time_seconds: Math.floor((Date.now() - qStart.current) / 1000),
        hints_used: 0,
      });
      setResult(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function finish() {
    try {
      await endSession(userId, sessionId);
      router.push(`/session/${sessionId}/summary`);
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <main className="container">
      <div className="row">
        <span className="pill">Session timer (display only): {elapsed(clockStart.current)}</span>
        <button className="btn btn-secondary" onClick={finish}>End session</button>
      </div>
      {!question && !error && <p className="muted">Loading question…</p>}
      {error && <p className="error">{error}</p>}
      {question && (
        <div className="card">
          {question.section_heading && <p className="muted">{question.section_heading}</p>}
          <h2>{question.question_text}</h2>
          {question.question_type === "mcq" && question.options?.map((o) => (
            <button key={o.id} className="option" onClick={() => setSelected(o.id)}
              style={selected === o.id ? { borderColor: "var(--accent)" } : undefined}>
              {o.id}. {o.text}
            </button>
          ))}
          {question.question_type === "true_false" && (
            <div className="row">
              <button className="option" onClick={() => { setSelected("true"); }}>True</button>
              <button className="option" onClick={() => { setSelected("false"); }}>False</button>
            </div>
          )}
          {question.question_type === "free_text" && (
            <textarea value={answerText} onChange={(e) => setAnswerText(e.target.value)} placeholder="Your answer…" />
          )}
          {!result && (
            <button className="btn" disabled={busy || (question.question_type === "mcq" ? !selected : !answerText.trim())} onClick={submit}>
              {busy ? "Grading…" : "Submit"}
            </button>
          )}
        </div>
      )}
      {result && (
        <div className="card">
          <h3 className={result.is_correct ? "good" : "bad"}>
            {result.is_correct ? "Correct" : "Not quite"} — {Math.round(result.grade * 100)}%
          </h3>
          <p>{result.feedback}</p>
          <p className="muted">Explanation: {result.correct_answer_explanation}</p>
          <button className="btn" onClick={load}>Next question</button>
        </div>
      )}
    </main>
  );
}

export default function Session({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <RequireUser>{(userId) => <SessionInner userId={userId} sessionId={id} />}</RequireUser>;
}
