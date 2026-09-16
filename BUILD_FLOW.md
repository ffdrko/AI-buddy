# Adaptive Study Platform — Agent Build Flow

> This document is the canonical instruction set for any agent (human or AI) building this system.
> Follow phases in order. Do not begin a phase until all gates in the previous phase are cleared.
> Each phase ends with explicit **gate checks** — mandatory before proceeding.

---

## Guiding Principles

These apply to every decision in every phase.

1. **The LLM is never the source of truth.** It observes; the backend decides.
   - LLM output is an *input* to state transitions, not the transition itself.
   - Always store LLM output with provenance: model version, chunk references, timestamp.
2. **`answers` is the event log.** Every derived metric (mastery, accuracy, streak) is a projection of answer history. Never increment a counter when you can recompute from the log.
3. **No fine-grained UI telemetry.** Discard click/scroll/view events. Keep: answer submissions, hint requests, time-per-question, session boundaries.
4. **Question provenance is mandatory.** Every generated question links to the chunk(s) it was generated from.
5. **The LLM never modifies schema or state directly.** All writes go through backend services.
6. **Core loop first.** Streaks, gamification, and achievements are additive, not foundational.

---

## System Overview

```
Upload → Understand → Study → Practice → Evaluate → Update Learner Model → Adapt Next Session
```

### Components

| Component | Responsibility |
|---|---|
| **Content Engine** | Parse, chunk, embed, retrieve |
| **Learning Engine** | Mastery, weakness detection, session planning |
| **AI Engine** | Tutor, question generation, answer evaluation |
| **FastAPI** | Orchestration, auth, session state |
| **Next.js / React** | Web client |
| **PostgreSQL + pgvector** | All persistent state |
| **Object Storage** | Raw PDFs, raw extracted text |
| **Worker Queue** | Async ingestion pipeline |

---

## Phase 0 — Foundations

**Goal:** Repository structure, tooling, CI, and empty service skeletons. No business logic.

### Tasks

- [ ] Initialise monorepo (recommended: `apps/web`, `apps/api`, `packages/db`, `packages/shared`)
- [ ] `apps/api`: FastAPI skeleton with health endpoint, CORS, structured logging (JSON), and error handler
- [ ] `apps/web`: Next.js skeleton with ESLint, Prettier, and a single `/` route
- [ ] `packages/db`: Alembic for migrations, SQLAlchemy models module (empty), connection pool config
- [ ] Docker Compose with services: `api`, `web`, `db` (Postgres 16 + pgvector), `worker`, `minio` (object storage), `redis` (queue broker)
- [ ] `.env.example` with all required environment variables documented
- [ ] CI pipeline: lint → type check → test → build on every PR
- [ ] Pre-commit hooks: black, isort, mypy, ruff (Python); eslint, prettier (TS)

### Gate Checks

- [ ] `docker compose up` starts all services cleanly
- [ ] `GET /health` returns `200` with version and git SHA
- [ ] Migrations run and roll back cleanly
- [ ] CI passes on an empty commit

---

## Phase 1 — Data Layer

**Goal:** Complete database schema. All tables, indexes, and constraints in place before any service logic is written.

### Schema

#### Users & Preferences

```sql
users
  id            uuid PK default gen_random_uuid()
  email         text UNIQUE NOT NULL
  display_name  text
  timezone      text NOT NULL          -- IANA timezone string; required for streak correctness
  created_at    timestamptz default now()

goals
  id            uuid PK
  user_id       uuid FK users
  description   text
  target_date   date
  created_at    timestamptz

preferences
  user_id       uuid PK FK users      -- one row per user
  daily_study_minutes  int default 30
  preferred_session_length_minutes int default 25
  notification_enabled bool default true
  updated_at    timestamptz
```

#### Content

```sql
documents
  id            uuid PK
  user_id       uuid FK users
  title         text
  original_filename text
  storage_key   text NOT NULL          -- object storage path for raw PDF
  raw_text_key  text                   -- object storage path for extracted text
  content_hash  sha256 UNIQUE NOT NULL -- skip re-ingestion on duplicate upload
  page_count    int
  status        text NOT NULL          -- pending | extracting | chunking | embedding | ready | failed
  error_message text
  created_at    timestamptz

document_chunks
  id            uuid PK
  document_id   uuid FK documents
  chunk_index   int NOT NULL
  content       text NOT NULL
  page_start    int
  page_end      int
  section_heading text              -- nearest heading above this chunk
  token_count   int
  created_at    timestamptz

embeddings
  id            uuid PK
  chunk_id      uuid FK document_chunks UNIQUE
  model_name    text NOT NULL       -- e.g. 'text-embedding-3-small'
  model_dim     int NOT NULL
  vector        vector(1536)        -- adjust dim to match model_name
  embedded_at   timestamptz

-- concepts extracted per chunk (enables topic-level mastery)
chunk_concepts
  id            uuid PK
  chunk_id      uuid FK document_chunks
  concept       text NOT NULL       -- normalised concept label
  confidence    float               -- extraction model confidence
```

**Indexes:** `HNSW` on `embeddings.vector` (cosine); composite `(document_id, chunk_index)` on `document_chunks`; `(user_id, content_hash)` on documents.

#### Learner Model

```sql
-- One row per user per concept. The mastery table is a cache / projection.
-- Ground truth is always recomputable from answers.
user_topic_state
  id            uuid PK
  user_id       uuid FK users
  concept       text NOT NULL
  mastery_score float NOT NULL default 0.0   -- 0.0–1.0
  evidence_count int NOT NULL default 0       -- number of answers that informed this
  confidence    float NOT NULL default 0.0   -- widens with sparse data; show to UI
  last_updated  timestamptz
  UNIQUE (user_id, concept)

-- Spaced repetition scheduling state per chunk
user_chunk_state
  id            uuid PK
  user_id       uuid FK users
  chunk_id      uuid FK document_chunks
  stability     float                 -- FSRS stability parameter
  difficulty    float                 -- FSRS difficulty parameter
  next_due_at   timestamptz           -- when to next surface this chunk
  last_reviewed_at timestamptz
  review_count  int default 0
  UNIQUE (user_id, chunk_id)
```

> `weaknesses` is a query, not a table: `WHERE mastery_score < 0.5 AND evidence_count >= 3 ORDER BY mastery_score ASC`.
> `study_capacity` is a function of preference + recent session performance, computed at session-plan time.

#### Study Sessions

```sql
sessions
  id            uuid PK
  user_id       uuid FK users
  document_id   uuid FK documents
  status        text NOT NULL         -- planned | active | completed | abandoned
  planned_duration_minutes int
  actual_duration_seconds  int        -- written on session end
  started_at    timestamptz
  ended_at      timestamptz

questions
  id            uuid PK
  session_id    uuid FK sessions
  chunk_id      uuid FK document_chunks   -- provenance: which chunk generated this
  question_text text NOT NULL
  question_type text NOT NULL         -- mcq | free_text | true_false
  options       jsonb                 -- for MCQ: [{id, text}]
  correct_option_id text              -- for MCQ
  rubric_chunk_ids uuid[]             -- chunk IDs passed to evaluator as grounding
  llm_model     text                  -- model that generated the question
  generated_at  timestamptz

answers
  id            uuid PK
  question_id   uuid FK questions
  user_id       uuid FK users
  session_id    uuid FK sessions
  response_text text                  -- raw answer
  selected_option_id text             -- for MCQ
  hints_used    int default 0
  time_seconds  int                   -- time to answer
  llm_grade     float                 -- 0.0–1.0, from Evaluator
  llm_feedback  text                  -- explanation from Evaluator
  llm_model     text                  -- model that graded
  graded_at     timestamptz
  created_at    timestamptz
```

> `answers` is the **event log**. `performance`, `accuracy`, `completion_rate` are views or computed on read.

#### Progress (Projections)

```sql
-- Materialized cache. Always has a recompute path from sessions + answers.
daily_study_log
  id            uuid PK
  user_id       uuid FK users
  study_date    date NOT NULL         -- date in user's timezone
  total_seconds int default 0
  questions_answered int default 0
  correct_count int default 0
  UNIQUE (user_id, study_date)

-- Derived from daily_study_log. Recomputable.
streak_cache
  user_id       uuid PK FK users
  current_streak int default 0
  longest_streak int default 0
  last_study_date date               -- date in user's timezone; drives streak logic
  updated_at    timestamptz
```

### Gate Checks

- [ ] All migrations run and roll back cleanly in order
- [ ] Foreign key constraints enforced
- [ ] HNSW index created successfully; `SELECT` against it returns results
- [ ] `content_hash` uniqueness enforced — duplicate insert fails at DB level
- [ ] Schema reviewed against principles: no LLM-written mastery scores stored without `evidence_count`

---

## Phase 2 — Content Engine (Ingestion Pipeline)

**Goal:** PDF upload → processed chunks + embeddings in DB. Fully async.

### Pipeline Steps

```
PDF upload
  → validate (size, mime type, page count)
  → store raw PDF in object storage
  → compute content_hash → deduplicate check
  → enqueue ingestion job
  → return document_id + status: pending

Worker picks up job:
  → detect PDF type (digital / scanned / mixed) per page
  → extract text (PyMuPDF for digital; OCR for scanned pages)
  → store raw extracted text in object storage (preservation copy)
  → clean text (dehyphenate, fix ligatures, collapse whitespace — conservative)
  → structure-aware chunking (heading boundaries first; subdivide oversized sections with overlap)
  → extract metadata per chunk (page range, nearest heading)
  → extract concepts per chunk (LLM call; store with confidence)
  → batch embed chunks (50–100 per call; retry with backoff)
  → write document_chunks + embeddings + chunk_concepts to DB
  → set document.status = ready
  → on any step failure: set status = failed + error_message, do not retry destructively
```

### Implementation Notes

- **Deduplication:** `content_hash` is SHA-256 of raw PDF bytes. On match, link existing `document_id` to the new user; do not re-run pipeline.
- **Chunking:** target 300–400 tokens per chunk; 50-token overlap between adjacent chunks.
- **Parent/child chunks:** embed child chunks (for retrieval precision); store parent section reference on chunk row for context expansion at query time.
- **Embedding versions:** always write `model_name` and `model_dim` to `embeddings`. When model changes, run backfill migration; old rows with stale model version are invalid for retrieval.
- **Status polling:** `GET /documents/{id}/status` returns current pipeline stage. Web client polls until `ready`.

### Endpoints

```
POST   /documents/upload          -- multipart; returns document_id
GET    /documents/{id}/status     -- pipeline status
GET    /documents/{id}            -- full document metadata
GET    /documents                 -- list user's documents
DELETE /documents/{id}            -- soft delete; marks chunks inactive
```

### Gate Checks

- [ ] A 50-page digital PDF completes the pipeline without error
- [ ] A scanned PDF page triggers OCR path
- [ ] Uploading the same PDF twice returns the same `document_id`; pipeline does not re-run
- [ ] Chunk → embedding → concept rows all present and linked
- [ ] Pipeline failure on any step sets `status = failed` with a useful error message; does not leave partial state
- [ ] Embedding model version is recorded on every row

---

## Phase 3 — AI Engine Contracts

**Goal:** Define and implement AI Engine as a self-contained service with explicit input/output contracts. No business logic here — that lives in the Learning Engine.

> The AI Engine accepts structured context assembled by the backend. It never reads the DB directly.

### Tutor

```
Input:
  user_query: str
  retrieved_chunks: [{ chunk_id, content, section_heading, page_range }]
  conversation_history: [{ role, content }]   -- last N turns only

Output:
  response_text: str
  source_chunk_ids: [uuid]    -- which chunks were cited
  model: str
  tokens_used: int
```

### Question Generator

```
Input:
  chunk_id: uuid
  chunk_content: str
  section_heading: str
  question_type: mcq | free_text | true_false
  concepts: [str]              -- from chunk_concepts; steer question toward these
  avoid_question_ids: [uuid]   -- questions already asked from this chunk this session

Output:
  question_text: str
  question_type: str
  options: [{id, text}] | null
  correct_option_id: str | null
  rubric_chunk_ids: [uuid]    -- chunks to pass to Evaluator when grading this question
  model: str
```

### Evaluator

```
Input:
  question_text: str
  user_response: str
  rubric_chunks: [{ chunk_id, content }]   -- grounding material
  question_type: str
  correct_option_id: str | null

Output:
  grade: float           -- 0.0–1.0
  is_correct: bool       -- grade >= threshold (backend decides threshold)
  feedback: str          -- explanation citing rubric
  model: str

Note: grade is an OBSERVATION. The Learning Engine decides what to do with it.
```

### Session Planner

```
Input:
  user_id: uuid
  document_id: uuid
  user_topic_state: [{ concept, mastery_score, confidence }]
  user_chunk_state: [{ chunk_id, next_due_at, review_count }]
  available_minutes: int
  session_goal: reinforce | explore | weak_focus | mixed

Output:
  recommended_chunk_ids: [uuid]    -- ordered
  estimated_duration_minutes: int
  rationale: str

Note: backend validates and may override this recommendation.
      LLM plan is a suggestion, not a state transition.
```

### Gate Checks

- [ ] Each AI Engine function is an isolated service function with typed inputs/outputs
- [ ] Every output includes `model` and `tokens_used` for cost tracking
- [ ] Evaluator is tested against known correct and incorrect answers with rubric chunks present
- [ ] Question Generator tested: no questions generated without `chunk_id` and `rubric_chunk_ids`
- [ ] All LLM calls have retry logic and timeout; never block a request indefinitely

---

## Phase 4 — Learning Engine

**Goal:** All mastery, weakness, and session state logic. Reads from `answers`; writes projections to `user_topic_state`, `user_chunk_state`, `daily_study_log`, `streak_cache`.

### Mastery Update (called after every graded answer)

```python
def update_mastery(user_id, concept, grade, evidence_count):
    # Bayesian-ish update; never trust a single answer
    prior = current_mastery_score or 0.5
    alpha = min(evidence_count / 10.0, 1.0)   # weight toward data as evidence grows
    new_score = (1 - alpha) * prior + alpha * grade
    confidence = 1 - (1 / (1 + evidence_count))   # grows with evidence; show to UI
    write user_topic_state(concept, new_score, evidence_count + 1, confidence)
```

> Cold-start note: with `evidence_count < 3`, display confidence band to UI rather than a raw percentage. Never show "0%" after one wrong answer.

### Spaced Repetition Scheduling

- Implement FSRS algorithm (or SM-2 as V1 fallback)
- On each answer, update `stability`, `difficulty`, `next_due_at` in `user_chunk_state`
- Session Planner queries `next_due_at <= now()` for due items, ranked by overdueness

### Streak Logic

```python
def update_streak(user_id, study_date):
    # study_date is a date in the user's stored timezone
    log = upsert daily_study_log(user_id, study_date, ...)
    cache = get streak_cache(user_id)
    yesterday = study_date - 1 day
    if cache.last_study_date == yesterday:
        new_streak = cache.current_streak + 1
    elif cache.last_study_date == study_date:
        new_streak = cache.current_streak   # same day, no change
    else:
        new_streak = 1   # streak broken
    update streak_cache(current_streak=new_streak, last_study_date=study_date)
```

> Streaks are computed from `daily_study_log`, not incremented blindly. If cache is ever wrong, recompute from log.

### Weakness Detection

Not a table — a query run at session-plan time:

```sql
SELECT concept, mastery_score, evidence_count
FROM user_topic_state
WHERE user_id = $1
  AND mastery_score < 0.5
  AND evidence_count >= 3
ORDER BY mastery_score ASC
LIMIT 5;
```

### Gate Checks

- [ ] Mastery update function is unit-tested with known grade sequences
- [ ] Cold-start: first answer does not produce mastery = 0.0 or 1.0 without confidence band
- [ ] Streak recompute from `daily_study_log` matches `streak_cache` for all test users
- [ ] FSRS `next_due_at` advances correctly on correct and incorrect answers
- [ ] Weakness query returns empty for users with `evidence_count < 3` on all concepts

---

## Phase 5 — Session Flow (API Layer)

**Goal:** Wire all engines together into the study session lifecycle.

### Session Lifecycle

```
POST /sessions/start
  ← { document_id, available_minutes, session_goal }
  → call Session Planner with user_topic_state + user_chunk_state
  → create sessions row (status: active)
  → pre-generate first 3 questions (async, not blocking)
  → return { session_id, estimated_duration_minutes }

GET /sessions/{id}/next-question
  → retrieve next question from queue (or generate on demand if queue empty)
  → return question (without correct answer or rubric)

POST /sessions/{id}/answer
  ← { question_id, response_text | selected_option_id, time_seconds, hints_used }
  → write answers row
  → call Evaluator with question + response + rubric_chunks
  → store llm_grade + llm_feedback on answer row
  → call Learning Engine: update_mastery, update_chunk_state
  → call update_streak + daily_study_log
  → return { grade, feedback, correct_answer_explanation }

POST /sessions/{id}/end
  → set sessions.status = completed, ended_at = now()
  → compute session summary from answers in this session
  → return { accuracy, questions_answered, study_time_seconds, concepts_covered }
```

### Retrieval (for Tutor mode)

```
POST /tutor/ask
  ← { document_id, query, conversation_history }
  → embed query with same model as document chunks
  → vector search: top-k chunks filtered by document_id
  → re-rank by recency of user exposure (deprioritise chunks already well-known)
  → call Tutor with assembled context
  → return { response_text, source_chunk_ids }
```

### Gate Checks

- [ ] Full session (start → 5 questions → end) completes without error
- [ ] `answers` table contains all events; session summary is derivable from it
- [ ] Evaluator output is stored with model version; mastery updated from backend logic, not LLM claim
- [ ] Streak updates correctly after session end
- [ ] Tutor response always includes `source_chunk_ids` — never a response without grounding

---

## Phase 6 — Web Client

**Goal:** Functional UI for the core loop. No gamification yet.

### Pages & Components

```
/                     -- landing / auth
/dashboard            -- document list, streak, recent sessions
/documents/upload     -- file upload with progress and pipeline status polling
/study/{document_id}  -- session start: set goal + available time
/session/{id}         -- active session: question → answer → feedback loop
/session/{id}/summary -- end-of-session summary
/tutor/{document_id}  -- chat interface with source citations
/progress             -- mastery by concept, study time chart, streak calendar
```

### UI Rules

- **Never display mastery % without confidence band** when `evidence_count < 3`
- **Cite sources** in tutor responses (section heading + page range, not chunk IDs)
- **Session timer** is display-only; backend computes `actual_duration_seconds`
- **Streak display** derives from `streak_cache`; show "keep going" only if last session was today or yesterday

### Gate Checks

- [ ] Upload → pipeline status polling → "ready" state works end to end
- [ ] Session flow: start → question → answer → feedback → next question → end → summary
- [ ] Tutor shows source citations on every response
- [ ] Mastery view suppresses raw % for low-evidence concepts
- [ ] Works on mobile viewport (minimum 375px)

---

## Phase 7 — Hardening

**Goal:** Production readiness. No new features.

### Tasks

- [ ] Auth: JWT + refresh token, route guards on all protected endpoints
- [ ] Rate limiting on LLM-calling endpoints (per user, per minute)
- [ ] Cost tracking: log `tokens_used` per AI Engine call; aggregate by user and model
- [ ] Background job for daily `streak_cache` recompute (guard against drift)
- [ ] Background job to flag `embeddings` rows with stale `model_name` after model upgrade
- [ ] `GET /documents/{id}/status` returns job queue position when `status = pending`
- [ ] Error boundaries in web client; no blank screens on API failure
- [ ] Structured logging on all API errors with `document_id`, `session_id`, `user_id` in context
- [ ] Database connection pooling tuned (PgBouncer or SQLAlchemy pool config)
- [ ] `docker compose` prod profile with secrets management

### Gate Checks

- [ ] Load test: 10 concurrent sessions with question answering; p95 response < 2s (excluding LLM time)
- [ ] Re-upload same PDF by same user: idempotent, no duplicate processing
- [ ] Token cost per session logged and queryable
- [ ] Streak recompute job matches live cache for all users

---

## Deferred to Post-V1

These are explicitly out of scope. Do not implement. Do not design schema for them yet.

- Streaks gamification, badges, achievements
- Social / leaderboard features
- Multi-document sessions (cross-document retrieval)
- Adaptive study-time scheduling (full FSRS tuning loop)
- Concept prerequisite graph
- Mobile app
- Teacher / classroom mode

---

## Appendix: Key Invariants

These must hold at all times. Any code that would violate them needs a design review first.

| # | Invariant |
|---|---|
| 1 | `answers` is never modified after write. Corrections are new rows. |
| 2 | Mastery in `user_topic_state` is always recomputable from `answers + chunk_concepts`. |
| 3 | Every `questions` row has a non-null `chunk_id` and non-empty `rubric_chunk_ids`. |
| 4 | No LLM output is stored as truth without backend recomputation or provenance fields. |
| 5 | `streak_cache` is always recomputable from `daily_study_log` + `users.timezone`. |
| 6 | `embeddings.model_name` matches the model used to embed queries at retrieval time. |
| 7 | Document pipeline is idempotent: re-running on the same `content_hash` is a no-op. |
