const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type QuestionType = "mcq" | "free_text" | "true_false";
export type SessionGoal = "reinforce" | "explore" | "weak_focus" | "mixed";

export interface PublicQuestion {
  id: string;
  question_text: string;
  question_type: QuestionType;
  options: { id: string; text: string }[] | null;
  section_heading: string | null;
}

export interface AnswerResult {
  grade: number;
  is_correct: boolean;
  feedback: string;
  correct_answer_explanation: string;
}

export interface SessionSummary {
  accuracy: number;
  questions_answered: number;
  correct_count: number;
  study_time_seconds: number;
  actual_duration_seconds?: number | null;
  concepts_covered: string[];
  status?: string;
}

export interface TutorSource {
  chunk_id: string;
  section_heading: string | null;
  page_start: number | null;
  page_end: number | null;
}

export interface TopicState {
  concept: string;
  mastery_score: number;
  evidence_count: number;
  confidence: number;
}

async function req<T>(path: string, userId: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "X-User-Id": userId, "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${path}: ${body}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function uploadDocument(userId: string, file: File, title?: string) {
  const form = new FormData();
  form.append("file", file);
  if (title) form.append("title", title);
  const res = await fetch(`${BASE}/documents/upload`, {
    method: "POST",
    headers: { "X-User-Id": userId },
    body: form,
  });
  if (!res.ok) throw new Error(`${res.status} /documents/upload: ${await res.text()}`);
  return (await res.json()) as { document_id: string; status: string; deduped: boolean };
}

export const getDocumentStatus = (u: string, id: string) =>
  req<{ document_id: string; status: string; error_message: string | null }>(`/documents/${id}/status`, u);

export interface DocumentItem {
  id: string;
  title: string | null;
  status: string;
  page_count: number | null;
  created_at: string | null;
}

export const listDocuments = (u: string) => req<{ documents: DocumentItem[] }>(`/documents`, u);

export const startSession = (u: string, document_id: string, available_minutes: number, session_goal: SessionGoal) =>
  req<{ session_id: string; estimated_duration_minutes: number; recommended_chunk_ids: string[] }>(`/sessions/start`, u, {
    method: "POST",
    body: JSON.stringify({ document_id, available_minutes, session_goal }),
  });

export const nextQuestion = (u: string, sessionId: string) =>
  req<PublicQuestion>(`/sessions/${sessionId}/next-question`, u);

export const submitAnswer = (
  u: string,
  sessionId: string,
  body: { question_id: string; response_text?: string; selected_option_id?: string; time_seconds?: number; hints_used?: number },
) => req<AnswerResult>(`/sessions/${sessionId}/answer`, u, { method: "POST", body: JSON.stringify(body) });

export const endSession = (u: string, sessionId: string) =>
  req<SessionSummary>(`/sessions/${sessionId}/end`, u, { method: "POST" });

export const getSessionSummary = (u: string, sessionId: string) =>
  req<SessionSummary>(`/sessions/${sessionId}/summary`, u);

export const askTutor = (u: string, document_id: string, query: string, conversation_history: { role: string; content: string }[]) =>
  req<{ response_text: string; source_chunk_ids: string[]; sources: TutorSource[] }>(`/tutor/ask`, u, {
    method: "POST",
    body: JSON.stringify({ document_id, query, conversation_history }),
  });

export interface ProgressSummary {
  user_id: string;
  streak: { current_streak: number; longest_streak: number; last_study_date: string | null };
  topics: TopicState[];
  daily_logs: { study_date: string; total_seconds: number; questions_answered: number; correct_count: number }[];
  recent_sessions: { id: string; document_id: string; status: string; started_at: string | null; ended_at: string | null; questions_answered: number }[];
}

export const getProgress = (u: string) => req<ProgressSummary>(`/progress/summary`, u);

/** Mastery display rule (mirrors backend): no raw % below 3 evidence. */
export function masteryBand(t: TopicState): { band: string; showPercent: boolean } {
  if (t.evidence_count < 3) return { band: "emerging", showPercent: false };
  if (t.mastery_score < 0.5) return { band: "developing", showPercent: true };
  if (t.mastery_score < 0.8) return { band: "proficient", showPercent: true };
  return { band: "mastered", showPercent: true };
}

export function sourceLabel(s: TutorSource): string {
  const where = s.section_heading ?? "source";
  const pages =
    s.page_start != null ? (s.page_end && s.page_end !== s.page_start ? `pp. ${s.page_start}–${s.page_end}` : `p. ${s.page_start}`) : "";
  return `${where}${pages ? ` (${pages})` : ""}`;
}
