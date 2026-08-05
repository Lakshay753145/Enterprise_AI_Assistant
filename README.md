# Enterprise AI Assistant

An internal, department-isolated knowledge assistant for ** Technologies
Limited**. Employees ask questions in plain language; the assistant answers
**only** from their own department's approved documentation, cites the source
passage for every fact, and refuses when the answer is not documented.

---

## Contents

- [What it does](#what-it-does)
- [How department isolation is enforced](#how-department-isolation-is-enforced)
- [The answer pipeline](#the-answer-pipeline)
- [Setup](#setup)
- [Loading the knowledge base](#loading-the-knowledge-base)
- [Running it](#running-it)
- [Deploying with Docker](#deploying-with-docker)
- [Project layout](#project-layout)
- [Tuning accuracy](#tuning-accuracy)
- [Logs and audit trail](#logs-and-audit-trail)
- [Troubleshooting](#troubleshooting)

---

## What it does

| Capability | How |
|---|---|
| **Department isolation** | Five independent knowledge domains. Enforced in five layers, not one. |
| **Sign-in** | Username **+ password + department**. All three must match. |
| **Document ingestion** | Docling parses PDFs with layout and table-structure recognition, plus OCR for scans. |
| **Chunking** | Docling `HybridChunker` — structure-aware *and* tokenizer-aware, with heading-breadcrumb enrichment. |
| **Hybrid retrieval** | pgvector dense search **+** Postgres full-text keyword search, fused with Reciprocal Rank Fusion. |
| **Reranking** | A BGE cross-encoder reorders candidates and gates on confidence. |
| **Query rewriting** | An agent translates shop-floor phrasing into the documentation's terminology before searching. |
| **Refuses out-of-scope questions** | A relevance gate classifies every question before spending retrieval. |
| **Refuses when undocumented** | Below the confidence threshold it says so rather than inventing a figure. |
| **Streaming** | Token-by-token over Server-Sent Events, with live stage indicators. |
| **Citations** | Every fact carries a `[n]` marker linked to document, page and section. |
| **Timings** | Per-stage latency shown on every answer (gate, rewrite, retrieval, rerank, generation, verification). |
| **Chat history** | Last 10 messages per conversation, scoped to the signed-in user. |
| **Long-term archive** | Every exchange appended to `logs/chat/<Department>/<user>/<YYYY-MM>.jsonl`. |
| **SQL agent** | LangChain SQL agent answers questions *about* the document collection, over read-only department-scoped views. |

**Stack:** FastAPI · LangChain · LangGraph · PostgreSQL + pgvector · Ollama ·
Docling · sentence-transformers · React + Vite + Tailwind + shadcn/ui

---

## How department isolation is enforced

This is the requirement everything else is built around, so it is enforced five
times over. A bug in any one layer is caught by the next.

| # | Layer | Where |
|---|---|---|
| 1 | **Department is in the JWT**, stamped at login from the database row. The client never supplies it. | `backend/security/jwt_handler.py` |
| 2 | **Every SQL query filters on it** explicitly. | `backend/repositories/`, `backend/retrieval/hybrid_search.py` |
| 3 | **Postgres Row-Level Security** re-applies the same predicate inside the database, with `FORCE ROW LEVEL SECURITY` so even the table owner is subject to it. | `alembic/versions/0001_initial_schema.py` |
| 4 | **Post-retrieval assertion** re-checks every record. A single foreign row raises, logs `CRITICAL`, and writes an audit event. | `backend/security/isolation.py` |
| 5 | **The SQL agent** connects as a read-only role granted on *nothing* but per-department views that hard-code their own `WHERE department = ...`. | `scripts/setup_db_roles.sql` |

The RLS policy **fails closed**: with no department context set, protected
tables return **zero** rows.

Verify it against your live database at any time:

```bash
python -m scripts.verify_isolation
```

Exit code `0` means every check passed. Run it after every deployment.

> A note on `admin`: a department admin administers **their own department**.
> The role is not a cross-department escape hatch. Only `super_admin` — intended
> for one or two IT custodians — reads across boundaries.

---

## The answer pipeline

```
                         ┌──────────────┐
  question ─────────────▶│ relevance    │──▶ out of scope ──▶ refuse
                         │ gate         │──▶ chitchat ──────▶ greet
                         └──────┬───────┘──▶ unsafe ────────▶ refuse
                                │ in scope
                         ┌──────▼───────┐
                         │ query        │  "how many days off do I get"
                         │ rewriter     │      ↓
                         └──────┬───────┘  "annual leave entitlement policy…"
                         ┌──────▼───────┐
                         │ router       │──▶ sql ──▶ SQL agent ──▶ answer
                         └──────┬───────┘
                                │ knowledge_base
              ┌─────────────────▼─────────────────┐
              │  HYBRID RETRIEVAL (dept-filtered) │
              │  pgvector cosine  +  tsvector FTS │
              │            ↓ RRF fusion           │
              └─────────────────┬─────────────────┘
                         ┌──────▼───────┐
                         │ cross-encoder│──▶ below threshold ──▶ refuse
                         │ reranker     │
                         └──────┬───────┘
                         ┌──────▼───────┐
                         │ generate     │──▶ streamed to browser, cited
                         └──────┬───────┘
                         ┌──────▼───────┐
                         │ verify       │──▶ faithfulness recorded
                         └──────────────┘
```

Implemented as a LangGraph state machine in `backend/agents/graph.py`.

**Why hybrid retrieval.** Semantic search finds *"how do I claim travel
expenses"* against a section titled *"Reimbursement of Official Travel"*.
Lexical search finds *"AS9100 Rev D clause 8.5.1"* exactly. Aerolloy's corpus is
full of both prose policy and hard identifiers — alloy grades, spec numbers,
form codes — so neither alone is sufficient. Fusion uses RRF rather than
weighted score blending, because cosine similarity and `ts_rank_cd` are not on
comparable scales and any hand-tuned weighting needs constant retuning.

**Why a cross-encoder reranker.** The vector index embeds query and passage
*independently*, so it can only tell that two texts are about similar things. A
cross-encoder reads both together and scores real relevance — the difference
between *"these 20 passages are about heat treatment"* and *"this passage
answers the question about solution-annealing temperature"*. Its score is also
calibrated enough to gate on, which is what makes the refusal behaviour
trustworthy.

---

## Setup

### Prerequisites

| | Version | Notes |
|---|---|---|
| Python | **3.12** | 3.13 has patchy ML wheel coverage. |
| PostgreSQL | **16+** | With the `pgvector` extension. |
| Node.js | **20+** | For the frontend. |
| Ollama | latest | https://ollama.com/download |
| RAM | 16 GB min | 32 GB comfortable. A GPU makes the 14B model interactive. |

### 1. Database

```bash
# Create the database
psql -U postgres -c "CREATE DATABASE \"Enterprise_AI\";"

# Install pgvector (https://github.com/pgvector/pgvector#installation)
psql -U postgres -d Enterprise_AI -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### 2. Configuration

```bash
cp .env.example .env
python scripts/generate_secret.py    # paste the output into .env
```

Set `DATABASE_URL` to your Postgres credentials. **URL-encode the password** —
`@` becomes `%40`.

### 3. Python dependencies

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r backend/requirements.txt
```

On a GPU host, install the CUDA build of torch **first**, then set
`EMBEDDING_DEVICE=cuda` and `RERANKER_DEVICE=cuda` in `.env`:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install -r backend/requirements.txt
```

### 4. Migrations

```bash
alembic upgrade head
```

This creates the tables, the HNSW and GIN indexes, the RLS policies, and the
per-department views.

### 5. Database roles for the SQL agent

Edit the password at the top of `scripts/setup_db_roles.sql`, then:

```bash
psql -U postgres -d Enterprise_AI -f scripts/setup_db_roles.sql
```

Put the same password into `SQL_AGENT_DATABASE_URL` in `.env`.
(To skip the SQL agent entirely, set `SQL_AGENT_ENABLED=False`.)

### 6. Language models

```bash
ollama pull qwen2.5:14b-instruct    # writes the answers
ollama pull qwen2.5:3b-instruct     # gate / rewrite / verify
```

The small model handles the classification steps that run on *every* question;
putting those on the large model would roughly double latency for no accuracy
gain. On a machine without a GPU, `qwen2.5:7b-instruct` is a reasonable
compromise for `OLLAMA_MODEL`.

### 7. User accounts

```bash
python -m scripts.seed_users
```

Prints one admin and one user per department, plus a super admin. **The
passwords are shown once and are not recoverable** — save them.

There is deliberately **no self-service signup**. An account grants access to a
department's confidential documentation, so accounts are provisioned by IT.

### 8. Frontend

```bash
cd frontend
npm install
```

---

## Loading the knowledge base

Put each department's PDFs in its own folder:

```
knowledge_base/
  Finance/     financial-policy.pdf   ...
  HR/          employee-handbook.pdf  ...
  IT/          it-security-policy.pdf ...
  Production/  quality-manual.pdf     ...
  Purchase/    procurement-policy.pdf ...
```

Then ingest:

```bash
python -m scripts.ingest_knowledge_base                  # everything
python -m scripts.ingest_knowledge_base --department HR  # one department
python -m scripts.ingest_knowledge_base --department HR --replace
```

The department comes from the **folder**, never from the document's contents, so
a mis-named file cannot land in the wrong department.

The first run downloads the Docling layout/table models and the embedding model
(~2 GB). Expect roughly 30–90 seconds per 50-page PDF on CPU, considerably
longer for scanned documents that need OCR.

Admins can also upload through the web UI (**Knowledge base** page), which runs
the same pipeline in the background.

---

## Running it

Two terminals:

```bash
# Terminal 1 — API
python -m backend.run

# Terminal 2 — web
cd frontend && npm run dev
```

Open **http://localhost:5173** and sign in with a seeded account.

| | |
|---|---|
| Web app | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Liveness | http://localhost:8000/health |
| Readiness | http://localhost:8000/api/v1/health/ready |

The readiness probe reports whether Postgres, pgvector and Ollama are actually
reachable, and names any model that has not been pulled.

---

## Deploying with Docker

```bash
cd docker
cp ../.env.example ../.env      # then edit it

# Required by docker-compose:
#   POSTGRES_PASSWORD, READONLY_PASSWORD, SECRET_KEY
docker compose up -d --build
```

Brings up Postgres+pgvector, Ollama (pulling both models on first start), the
API, and nginx serving the built frontend on port 80.

Then, once:

```bash
docker compose exec backend alembic upgrade head
docker compose exec -T postgres psql -U postgres -d Enterprise_AI < ../scripts/setup_db_roles.sql
docker compose exec backend python -m scripts.seed_users
docker compose exec backend python -m scripts.ingest_knowledge_base
docker compose exec backend python -m scripts.verify_isolation
```

**Before going live:** set `ENVIRONMENT=production` and `DEBUG=False`, set
`CORS_ORIGINS` to your real origin, terminate TLS in front of nginx and
uncomment the HSTS header in `nginx/nginx.conf`. The config layer refuses to
start if `DEBUG` is on or `CORS_ORIGINS` is `*` in production.

For GPU inference, uncomment the `deploy.resources` block on the `ollama`
service in `docker-compose.yml`.

---

## Project layout

```
backend/
  agents/          LangGraph pipeline: state, nodes, graph, SQL agent
  api/             Route handlers (auth, chat, documents, admin, health)
  config/          Settings, validated at import
  core/            Departments, roles, exceptions, logging, audit, archive
  database/        Async + sync engines, RLS context helpers
  embeddings/      BGE embedding service
  ingestion/       Docling parser, hybrid chunker, ingestion pipeline
  llm/             Ollama client, structured-output helpers
  middleware/      Request IDs, access logging, security headers
  models/          SQLAlchemy models
  prompts/         Every prompt, in one reviewable file
  repositories/    Data access, department-scoped
  retrieval/       Hybrid search, RRF, cross-encoder reranker
  schemas/         Pydantic request/response models
  security/        JWT, hashing, dependencies, isolation guards
  services/        Auth service, chat orchestration + SSE

frontend/src/
  components/      ui/ primitives, chat/, layout/
  context/         Auth and theme providers
  hooks/           useChat — the streaming state machine
  lib/             API client (incl. the SSE reader), formatting helpers
  pages/           Login, Chat, Documents, Admin

alembic/versions/  Schema, indexes, RLS policies, department views
scripts/           seed_users · ingest_knowledge_base · verify_isolation
                   setup_db_roles.sql · generate_secret
knowledge_base/    Source documents, one folder per department
logs/              Application, access, audit, ingestion, chat archive
docker/ nginx/     Deployment
```

---

## Tuning accuracy

All of these live in `.env`.

| Setting | Default | Raise it to… | Lower it to… |
|---|---|---|---|
| `MIN_CONFIDENCE_THRESHOLD` | `0.35` | refuse more readily (fewer wrong answers, more "not documented") | answer more questions (more risk of a weak answer) |
| `RERANK_SCORE_THRESHOLD` | `0.30` | keep only strongly relevant passages | keep more context |
| `FINAL_TOP_K` | `6` | give the model more context (slower, risks dilution) | tighter, faster context |
| `VECTOR_TOP_K` / `KEYWORD_TOP_K` | `25` | improve recall on a large corpus | speed up retrieval |
| `CHUNK_MAX_TOKENS` | `512` | keep long procedures intact | sharper retrieval on short factual lookups |

**If it refuses questions it should answer:** the answer is usually not in the
index. Check `Knowledge base → passages` is non-zero, then lower
`MIN_CONFIDENCE_THRESHOLD` to `0.25` and re-test before changing anything else.

**If answers are vague:** lower `FINAL_TOP_K` to `4` and raise
`RERANK_SCORE_THRESHOLD` to `0.4`. Too much marginal context dilutes a good
answer.

**If tables come out garbled:** the document needs better parsing, not better
prompting. Confirm Docling's `TableFormerMode.ACCURATE` path is active in
`backend/ingestion/docling_parser.py` and reprocess the document.

After changing `EMBEDDING_MODEL` or `EMBEDDING_DIMENSION`, **all existing
embeddings must be rebuilt** — the vector column width is fixed by the
migration. Re-run ingestion with `--replace`.

---

## Logs and audit trail

```
logs/
  app/         application_YYYY-MM-DD.log       runtime
  error/       error_YYYY-MM-DD.log             WARNING+, retained >= 1 year
  access/      access_YYYY-MM-DD.log            HTTP, with user + department
  audit/       audit_YYYY-MM-DD.jsonl           security events (JSON Lines)
  ingestion/   ingestion_YYYY-MM-DD.log         document processing
  chat/        <Dept>/<user>/<YYYY-MM>.jsonl    permanent transcript archive
```

The chat archive is partitioned **by department first**, so filesystem
permissions can be applied per department and an auditor reviewing one
department never reads another's.

Audit events include: `login_success`, `login_failed`, `login_blocked_locked`,
`document_uploaded`, `document_deleted`, `sql_agent_query`,
`chat_unsafe_query_blocked`, `chat_answer_ungrounded`, `transcript_accessed`,
and — the one to alert on — **`department_isolation_violation`**.

Find every failed sign-in for a user:

```bash
grep '"login_failed"' logs/audit/*.jsonl | grep '"username": "production.user"'
```

Every request carries an `X-Request-ID`, echoed in the response header and
stamped into every log line and audit event it produces — so a user reporting
"it said something odd at 3pm" is traceable from a single value.

---

## Troubleshooting

**`SECRET_KEY is a placeholder or too short`**
Run `python scripts/generate_secret.py` and paste the result into `.env`.

**`DATABASE_URL must use the psycopg3 driver`**
It must start `postgresql+psycopg://` — not `postgresql://` or
`postgresql+psycopg2://`.

**`type "vector" does not exist`**
pgvector is not installed in that database:
`psql -U postgres -d Enterprise_AI -c "CREATE EXTENSION vector;"`

**Ollama unreachable / models missing**
`curl http://localhost:11434/api/tags` — if it fails, start Ollama. Check
`/api/v1/health/ready`; it names any model that has not been pulled.

**"The knowledge base has not been set up yet"**
That department has zero indexed chunks. Run
`python -m scripts.ingest_knowledge_base --department <Name>` and confirm the
documents show **Indexed** on the Knowledge base page.

**Answers appear all at once instead of streaming**
Something is buffering the response. Behind nginx, confirm `proxy_buffering off`
on the `/api/v1/chat/stream` location. Behind another proxy, disable response
buffering there too.

**First question of the day is very slow**
The embedding model, reranker and Docling converter load lazily. Startup warms
them in the background; give the API ~60 seconds after boot before the first
question, or watch for `Embedding model warmed up` in the log.

**`verify_isolation` reports rows visible without context**
RLS is not fail-closed. Confirm the migration ran (`alembic current`) and that
`FORCE ROW LEVEL SECURITY` is set on all four protected tables. **Do not deploy
until this passes.**

---

