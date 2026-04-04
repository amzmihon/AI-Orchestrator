## Connecting BitNet b1.58 2B4T as LLM

You can use a local BitNet b1.58 2B4T model (or any OpenAI-compatible LLM) as the backend for the Orchestrator. Follow these steps:

### Step-by-Step Setup

1. **Open the configuration file:**
  - Edit `AI-Orchestrator/config.py`.

2. **Set the LLM base URL:**
  - Change the `llm_base_url` to your BitNet endpoint:
    ```python
    llm_base_url: str = "http://127.0.0.1:9999"
    ```

3. **Set the model name:**
  - Update `llm_model` to match the model name BitNet expects (e.g., "bitnet-b1.58-2b4t" or as required by your BitNet API).

4. **Restart the orchestrator:**
  - Save changes and restart your orchestrator service.

5. **Test the connection:**
  - Use the orchestrator’s chat or API to verify BitNet is responding.

**Note:**
- Model selection is currently handled via config, not the UI. If you need a UI option to select BitNet, see the project issues or request this feature.
# ATL-AI Orchestrator — Complete Technical Documentation

> **Version:** 2.2.0  
> **Framework:** Python 3.12 + FastAPI  
> **Purpose:** Private AI-Powered Corporate Intelligence Engine  
> **Container Port:** 8000 (mapped to host 7001)  
> **Dev Admin UI Port:** 8100  
> **Last Updated:** March 19, 2026  

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Technology Stack](#3-technology-stack)
4. [Folder Structure](#4-folder-structure)
5. [How It Works — Request Flow](#5-how-it-works--request-flow)
6. [Three Processing Pipelines](#6-three-processing-pipelines)
7. [API Reference](#7-api-reference)
8. [External Connections](#8-external-connections)
9. [Authentication System](#9-authentication-system)
10. [Admin Authentication (Independent)](#10-admin-authentication-independent)
11. [Role-Based Access Control (RBAC)](#11-role-based-access-control-rbac)
12. [Security & Guardrails](#12-security--guardrails)
13. [Memory System (3-Layer)](#13-memory-system-3-layer)
14. [Admin Dashboard — 12 Modules](#14-admin-dashboard--12-modules)
15. [Configuration Reference](#15-configuration-reference)
16. [Connecting to Another Application](#16-connecting-to-another-application)
17. [Docker Deployment](#17-docker-deployment)
18. [Module Reference](#18-module-reference)

---

## 1. Overview

The **ATL-AI Orchestrator** is a self-contained AI backend that converts natural-language questions into SQL queries, executes them against a read-only staging database, and returns professional human-readable summaries.

### What It Does

- Accepts a user's question in plain English (e.g., *"Show me top 5 sales reps this quarter"*)
- Classifies the intent: is this a **data query**, **text processing**, or **complex multi-step analysis**?
- For data queries: generates PostgreSQL SQL → validates it → runs it → summarizes the results
- For text processing: directly responds using the LLM (greetings, email drafts, summaries)
- For complex analysis: uses chain-of-thought reasoning with episodic memory
- All interactions are secured with JWT authentication, RBAC, PII detection, and SQL injection prevention

### Key Features

| Feature | Description |
|---------|-------------|
| **Natural Language to SQL** | Converts questions to PostgreSQL queries using Qwen 3.5 LLM |
| **3-Intent Classification** | Routes to text_processing, data_query, or multi_step_analysis |
| **Role-Based Access (RBAC)** | 8 role profiles control table/column visibility |
| **PII Detection & Redaction** | Scans input for emails, SSNs, credit cards, phone numbers |
| **SQL Guardrails** | Blocks INSERT/DELETE/DROP, injection patterns, unauthorized tables |
| **Iterative SQL Refinement** | Auto-corrects failed SQL queries (up to N retries) |
| **3-Layer Memory** | Short-term (session) + Long-term (RAG) + Episodic (learning) |
| **AI Writing Assistant** | Field-level text enhancement with tone control |
| **Data Freshness Monitoring** | Reports how current staging data is (sync lag) |
| **Output Validation** | Detects hallucination markers and quality issues |
| **Rate Limiting** | Sliding window per-user protection (10 req/min) |
| **Independent Admin Auth** | Separate admin.db with PBKDF2-SHA256 hashing + own JWT + ERP bridge |
| **12-Module Admin Dashboard** | Full SPA admin panel: connections, schema, prompts, RBAC, intent, memory, security, sync, sessions, RAG, users, logs |

---

## 2. Architecture

### High-Level Data Flow

```
┌──────────────┐     ┌──────────────────────────────────────────────────────────────────────┐
│              │     │                       ORCHESTRATOR                                    │
│  Client App  │────▶│  Auth → PII Scan → Intent Classify → RBAC Filter                    │
│  (Main App)  │     │    → Schema Filter → LLM SQL Gen → Guardrail → DB Execute           │
│  Port 7000   │◀────│    → SQL Refine (if fail) → LLM Summarize → Output Validate         │
│              │     │    → Memory Extract → Response                                       │
└──────────────┘     └───────────┬──────────────────────────┬──────────────────┬─────────────┘
                                 │                          │                  │
                          ┌──────▼──────┐           ┌───────▼──────┐   ┌──────▼──────┐
                          │  LiteLLM    │           │  Staging DB  │   │   SQLite    │
                          │  Proxy      │           │  (Postgres)  │   │  (Sessions  │
                          │  Port 7002  │           │  Port 5433   │   │  & Memory)  │
                          └──────┬──────┘           └──────────────┘   └─────────────┘
                                 │
                          ┌──────▼──────┐
                          │   Ollama    │
                          │  (LLM)     │
                          │  Port 11434│
                          └─────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                        ADMIN DASHBOARD (12-Module SPA)                                   │
│                        Served at :8100 (dev) / :7001 (prod)                              │
│                                                                                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │CONNECTION│ │ SCHEMA   │ │ SYSTEM   │ │   AI     │ │ INTENT   │ │   AI     │          │
│  │   S      │ │  MAP     │ │ PROMPT   │ │ PERMS    │ │ TUNER    │ │ MEMORY   │          │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │SECURITY  │ │DATA SYNC │ │  CHAT    │ │  RAG DB  │ │  ADMIN   │ │   LOG    │          │
│  │          │ │          │ │ SESSIONS │ │  MANAGE  │ │  USERS   │ │          │          │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘          │
│                                                                                          │
│  Auth: Independent admin.db (PBKDF2-SHA256) + Own JWT + ERP-BGS Token Bridge             │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### Request Pipeline (Detailed)

```
User Question
     │
     ▼
[1] Authentication (JWT decode or HTTP callback to Main App)
     │
     ▼
[2] PII Detection & Redaction (emails, SSN, credit cards, etc.)
     │
     ▼
[3] Session Management (create or load existing chat session)
     │
     ▼
[4] Memory Retrieval (RAG — retrieve relevant past facts/preferences)
     │
     ▼
[5] Intent Classification (text_processing | data_query | multi_step_analysis)
     │
     ├─── text_processing ──▶ Build text prompt → LLM → Direct response
     │
     ├─── data_query ──▶ [6] RBAC (filter tables/columns by role)
     │                       │
     │                       ▼
     │                  [7] Schema Injection (build filtered schema text)
     │                       │
     │                       ▼
     │                  [8] SQL Generation (LLM produces PostgreSQL)
     │                       │
     │                       ▼
     │                  [9] SQL Guardrail (validate: SELECT-only, allowed tables, no injection)
     │                       │
     │                       ▼
     │                  [10] DB Execution (run on staging database)
     │                       │
     │                       ├── Success ──▶ [11] LLM Summarization
     │                       │
     │                       └── Failure ──▶ [10a] SQL Refinement (re-prompt LLM with error)
     │                                           │
     │                                           ▼
     │                                      Retry steps [9]–[10] up to N times
     │
     └─── multi_step_analysis ──▶ Same as data_query but with:
                                   • Chain-of-thought prompting
                                   • Sub-task decomposition
                                   • Episodic memory hints (past failures/successes)
                                   • Enhanced multi-step summarization

     ▼
[12] Output Validation (check for hallucination markers, quality)
     │
     ▼
[13] Save assistant response to session history
     │
     ▼
[14] Auto-extract long-term memories from the exchange
     │
     ▼
[15] Inject data freshness status into metadata
     │
     ▼
Return JSON Response
```

---

## 3. Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Web Framework** | FastAPI 0.115 | Async REST API with auto-docs |
| **Language** | Python 3.12 | Runtime |
| **Database Driver** | asyncpg + SQLAlchemy 2.0 (async) | PostgreSQL connection pooling |
| **Session Storage** | aiosqlite | Local SQLite for chat history & memory |
| **HTTP Client** | httpx 0.28 | Async calls to LLM proxy & Main App |
| **Auth** | python-jose (JWT) | Local JWT decode (HS256/RS256) |
| **LLM Gateway** | LiteLLM Proxy | OpenAI-compatible API to Ollama |
| **LLM Engine** | Ollama + Qwen 3.5 (9B/4B) | Local inference (CPU or GPU) |
| **Logging** | structlog | Structured JSON logging |
| **Config** | pydantic-settings | Environment-variable-based config |
| **Testing** | pytest + pytest-asyncio | 412+ automated tests |
| **Container** | Docker (python:3.12-slim) | Deployment |

### Python Dependencies (`requirements.txt`)

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic==2.10.4
pydantic-settings==2.7.1
asyncpg==0.30.0
sqlalchemy[asyncio]==2.0.36
aiosqlite==0.22.1
httpx==0.28.1
python-jose[cryptography]==3.3.0
structlog==24.4.0
pytest==8.3.4
pytest-asyncio==0.25.0
pytest-cov==6.0.0
python-dotenv==1.0.1
```

---

## 4. Folder Structure

```
orchestrator/
├── main.py                          # FastAPI app entry point
├── config.py                        # All config (env vars, defaults)
├── db.py                            # Async PostgreSQL engine (SQLAlchemy)
├── admin_db.py                      # Independent admin user DB (SQLite admin.db)
├── data_freshness.py                # Staging DB sync status checker
├── schema_map.json                  # Full database schema definition
├── requirements.txt                 # Python dependencies
├── Dockerfile                       # Container build instructions
├── pytest.ini                       # Test configuration
├── chat_history.db                  # SQLite database (auto-created at runtime)
├── admin.db                         # Admin users SQLite DB (auto-created at runtime)
│
├── auth/                            # Authentication module
│   ├── __init__.py
│   ├── admin_auth.py                # Admin JWT creation & verification (orchestrator + ERP bridge)
│   ├── dependencies.py              # FastAPI dependencies: get_current_user + get_admin_user
│   └── token_verify.py              # JWT/HTTP hybrid token verification + cache
│
├── rbac/                            # Role-Based Access Control
│   ├── __init__.py
│   ├── permissions.py               # Table/column filtering by role
│   └── role_config.json             # 8 role definitions with permissions
│
├── llm_client/                      # LLM communication layer
│   ├── __init__.py                  # generate() function + health check
│   └── client.py                    # Re-export convenience file
│
├── query_engine/                    # Core AI processing pipeline
│   ├── __init__.py
│   ├── intent_classifier.py         # 3-intent classifier + episodic memory
│   ├── prompt_builder.py            # 4 specialized LLM prompt templates
│   ├── sql_guardrail.py             # SQL security validation
│   ├── db_executor.py               # Execute SQL on staging DB
│   ├── response_formatter.py        # LLM-based result summarization
│   ├── schema_loader.py             # Role-filtered schema injection
│   ├── pii_detector.py              # PII scan & redaction engine
│   ├── output_validator.py          # Quality & hallucination checks
│   └── sql_refiner.py               # Iterative SQL error correction
│
├── middleware/                      # HTTP middleware
│   ├── __init__.py
│   └── rate_limiter.py              # Per-user sliding window rate limiter
│
├── chat_history/                    # Session & memory storage
│   └── __init__.py                  # SQLite-backed sessions, messages, memories
│
├── routers/                         # API endpoint definitions
│   ├── __init__.py
│   ├── chat.py                      # POST /api/chat — main Q&A pipeline
│   ├── assist.py                    # POST /api/assist — AI Writing Assistant
│   ├── health.py                    # GET /api/health, /api/data-freshness
│   ├── sessions.py                  # Session & memory CRUD endpoints
│   ├── admin.py                     # Admin service proxies (LiteLLM, Ollama, Main App)
│   ├── admin_auth.py                # Admin login, user CRUD (independent auth)
│   └── admin_dashboard.py           # 12-module dashboard API (~1600 lines)
│
├── static/                          # Admin UI
│   └── index.html                   # 12-module SPA admin panel (Tailwind CSS)
│
└── tests/                           # Test suite (412+ tests)
    ├── __init__.py
    ├── test_auth.py
    ├── test_chat_history.py
    ├── test_guardrail.py
    ├── test_intent_classifier.py
    ├── test_prompt_builder.py
    ├── test_rbac.py
    └── test_security_stress.py
```

**Self-Containment:** All code, config, and data files are inside the `orchestrator/` folder. The only external dependencies are runtime services (database, LLM proxy, auth provider) accessed via network.

---

## 5. How It Works — Request Flow

### Step-by-Step Example

**User asks:** *"How many employees are in the Sales department?"*

| Step | Component | Action |
|------|-----------|--------|
| **1. Auth** | `auth/dependencies.py` → `token_verify.py` | Extracts JWT from `Authorization: Bearer <token>` header. Decodes locally (HS256). Returns `VerifiedUser(role="admin", department="Management")` |
| **2. PII Scan** | `query_engine/pii_detector.py` | Scans question for emails, SSN, credit cards. None found — passes through |
| **3. Session** | `chat_history/__init__.py` | No `session_id` provided → auto-creates new session. Saves user message |
| **4. Memory** | `chat_history/__init__.py` | Retrieves RAG memories matching "employees" + "Sales" keywords. Injects into prompt |
| **5. Classify** | `query_engine/intent_classifier.py` | Pattern match: "how many" → data_query (confidence 0.85). Sub-tasks: ["Generate SQL", "Execute", "Summarize"] |
| **6. RBAC** | `rbac/permissions.py` | Admin role → all tables allowed. No denied columns |
| **7. Schema** | `query_engine/schema_loader.py` | Loads `schema_map.json`, filters to allowed tables. Produces text schema for prompt |
| **8. SQL Gen** | `llm_client/__init__.py` → LiteLLM → Ollama | LLM generates: `SELECT COUNT(*) FROM employees WHERE department = 'Sales'` |
| **9. Guardrail** | `query_engine/sql_guardrail.py` | Validates: SELECT-only ✓, allowed tables ✓, no injection ✓ |
| **10. Execute** | `query_engine/db_executor.py` | Runs SQL on staging DB. Returns: `[{"count": 15}]`, 12ms |
| **11. Summarize** | `query_engine/response_formatter.py` → LLM | LLM produces: "There are **15 employees** in the Sales department." |
| **12. Validate** | `query_engine/output_validator.py` | No hallucination markers, quality OK |
| **13. Save** | `chat_history/__init__.py` | Saves assistant response + SQL + data to session |
| **14. Memory** | `chat_history/__init__.py` | Auto-extracts: "Sales department has 15 employees" as a fact |
| **15. Freshness** | `data_freshness.py` | Checks staging DB sync: "Data synced 12 minutes ago" |

### Response JSON

```json
{
  "answer": "There are **15 employees** in the Sales department.",
  "sql": "SELECT COUNT(*) AS employee_count FROM employees WHERE department = 'Sales'",
  "data": [{"employee_count": 15}],
  "metadata": {
    "execution_time_ms": 3245.67,
    "tables_accessed": ["employees"],
    "user_role": "admin",
    "intent": "data_query",
    "session_id": "abc-123-def",
    "memories_used": 2,
    "complexity_score": 0.15,
    "sub_tasks": ["Generate SQL query", "Execute and validate", "Summarize results"],
    "sql_refinement_attempts": 0,
    "quality_warnings": [],
    "data_freshness": {
      "status": "fresh",
      "message": "Data last synced: 12 minutes ago",
      "minutes_ago": 12
    }
  }
}
```

---

## 6. Three Processing Pipelines

### 6.1 Text Processing (`text_processing`)

**When:** Greetings ("hi", "how are you"), writing tasks ("draft an email..."), summaries, translations, brainstorming, explanations.

**Flow:** Question → LLM (direct response) → Output validation → Response

**No database access.** The LLM responds directly using its language capabilities.

**Pattern triggers:**
- Greetings: `hi`, `hello`, `how are you`, `thanks`, `bye`
- Writing: `write`, `draft`, `compose`, `rephrase`, `rewrite`
- Summarization: `summarize`, `TLDR`
- Translation, brainstorming, grammar correction
- Long pasted text (>200 chars with 4+ lines)
- Quoted text blocks

### 6.2 Data Query (`data_query`)

**When:** Factual questions about company data ("show me sales", "how many employees", "top revenue by region").

**Flow:** Question → RBAC check → Schema filter → SQL generation (LLM) → SQL guardrail → Execute on staging DB → Summarize results (LLM) → Response

**Pattern triggers:**
- Action verbs: `show`, `list`, `get`, `fetch`
- Aggregations: `how many`, `total`, `average`, `top`, `rank`
- Time references: `last month`, `this quarter`, `past year`
- Entity references: `employees`, `sales`, `revenue`, `expenses`
- Report requests: `breakdown by`, `overview per`

### 6.3 Multi-Step Analysis (`multi_step_analysis`)

**When:** Complex questions requiring chain-of-thought reasoning, what-if scenarios, trend analysis, cross-department comparisons.

**Flow:** Same as data_query but with enhanced prompts including sub-task decomposition, episodic memory hints, CTE-based SQL, and detailed multi-step summarization.

**Pattern triggers:**
- Projections: `if sales drop 10%`, `forecast`, `predict`
- Comparisons: `compare across departments`, `benchmark`
- Trends: `growth`, `year-over-year`, `month-over-month`
- Root cause: `why did revenue decline`, `explain the drop`
- Scenarios: `what if`, `suppose`, `assuming`
- Anomaly detection: `find outliers`, `unusual patterns`

### 6.4 AI Writing Assistant (`POST /api/assist`)

A separate endpoint for inline text enhancement within application fields.

**Flow:** Field context (label, text, tone, nearby fields) → PII scan → LLM → Professional rewrite or completion

**Features:**
- 5 tone options: `professional`, `formal`, `friendly`, `concise`, `executive`
- Detects whether input is a prompt ("write a...") or existing text to rewrite
- Receives neighboring field values for context-aware suggestions
- Uses the fast 4B model for responsiveness

---

## 7. API Reference

### 7.1 Chat — Main Q&A Pipeline

```
POST /api/chat
```

**Auth:** Required (Bearer token)

**Request:**
```json
{
  "question": "Show me the top 5 sales reps this quarter",
  "session_id": "optional-existing-session-id"
}
```
- `question`: 1–5000 characters
- `session_id`: Optional. If omitted, a new session is auto-created.

**Response:**
```json
{
  "answer": "Here are the top 5 sales representatives...",
  "sql": "SELECT ... FROM sales JOIN employees ...",
  "data": [{"rep_name": "John", "total_sales": 125000.00}, ...],
  "metadata": {
    "execution_time_ms": 4500.00,
    "tables_accessed": ["sales", "employees"],
    "user_role": "admin",
    "intent": "data_query",
    "session_id": "abc-123",
    "memories_used": 1,
    "complexity_score": 0.25,
    "sub_tasks": ["Generate SQL query", "Execute and validate", "Summarize results"],
    "sql_refinement_attempts": 0,
    "pii_redacted": false,
    "quality_warnings": [],
    "data_freshness": {"status": "fresh", "message": "...", "minutes_ago": 8}
  }
}
```

### 7.2 Writing Assistant

```
POST /api/assist
```

**Auth:** Required (Bearer token)

**Request:**
```json
{
  "field_label": "Notes",
  "current_text": "need 3 days off next week for family emergency",
  "context": [
    {"label": "Leave Type", "value": "Emergency Leave"},
    {"label": "Department", "value": "Engineering"}
  ],
  "app_section": "HR Leave Application",
  "tone": "professional",
  "max_length": 500
}
```

**Response:**
```json
{
  "suggestion": "I am writing to request three days of emergency leave...",
  "type": "rewrite",
  "tone": "professional",
  "metadata": {
    "execution_time_ms": 2100.50,
    "field_label": "Notes",
    "app_section": "HR Leave Application",
    "input_length": 52,
    "output_length": 187,
    "pii_redacted": false
  }
}
```

### 7.3 Health & Monitoring

```
GET /api/health
```
Returns connectivity status of all dependent services:
```json
{
  "status": "healthy",
  "components": {
    "staging_db": "connected",
    "llm_server": "connected",
    "main_app_auth": "reachable"
  },
  "version": "1.0.0"
}
```

```
GET /api/data-freshness
```
Returns staging database sync recency:
```json
{
  "status": "fresh",
  "last_updated": "2026-03-06T10:23:00Z",
  "minutes_ago": 12,
  "message": "Data last synced: 12 minutes ago",
  "table_details": { ... }
}
```

### 7.4 Session & Memory Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/sessions` | List user's chat sessions |
| `POST` | `/api/sessions` | Create a new session |
| `GET` | `/api/sessions/{id}` | Get session with all messages |
| `PATCH` | `/api/sessions/{id}` | Rename a session |
| `DELETE` | `/api/sessions/{id}` | Delete a session |
| `GET` | `/api/memory` | List user's long-term memories |
| `POST` | `/api/memory` | Manually save a memory |
| `DELETE` | `/api/memory/{id}` | Delete a specific memory |
| `DELETE` | `/api/memory` | Clear all user's memories |

### 7.5 Admin Authentication

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/admin/auth/login` | Public | Authenticate with username/password → admin JWT |
| `GET` | `/api/admin/auth/me` | Admin | Get current admin user info |
| `GET` | `/api/admin/auth/users` | Superadmin | List all admin users |
| `POST` | `/api/admin/auth/users` | Superadmin | Create a new admin user |
| `PUT` | `/api/admin/auth/users/{id}` | Superadmin | Update admin user |
| `DELETE` | `/api/admin/auth/users/{id}` | Superadmin | Delete admin user |

### 7.6 Admin Service Proxies

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/admin/services` | All services health status |
| `GET` | `/api/admin/config` | System configuration (non-sensitive) |
| `GET` | `/api/admin/litellm-health` | LiteLLM proxy status |
| `GET` | `/api/admin/ollama-health` | Ollama engine status |
| `GET` | `/api/admin/mainapp-health` | Main App status |
| `GET` | `/api/admin/ollama/models` | List installed LLM models |
| `POST` | `/api/admin/ollama/pull` | Pull a new model |
| `DELETE` | `/api/admin/ollama/delete` | Delete a model |
| `POST` | `/api/admin/llm/test` | Send a test prompt to the LLM |
| `GET` | `/api/admin/users` | List users (proxied to Main App) |
| `POST` | `/api/admin/users` | Create user (proxied) |
| `PATCH` | `/api/admin/users/{id}` | Update user (proxied) |
| `DELETE` | `/api/admin/users/{id}` | Delete user (proxied) |

### 7.7 Admin Dashboard (12 Modules — 49 Endpoints)

All endpoints are prefixed with `/api/admin/dashboard/` and require admin JWT authentication.

#### 1. Connections — LLM, DB & Auth (8 endpoints)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/connections` | List all configured connections (all types) |
| `POST` | `/connections/{conn_type}` | Add a new connection (type: `llm`/`database`/`auth`) |
| `PUT` | `/connections/{conn_type}/{conn_id}` | Update a connection by type and ID |
| `POST` | `/connections/{conn_type}/{conn_id}/activate` | Activate a connection |
| `DELETE` | `/connections/{conn_type}/{conn_id}` | Delete a connection |
| `PUT` | `/connections/llm` | Update active LLM connection settings |
| `PUT` | `/connections/database` | Update active database connection settings |
| `PUT` | `/connections/auth` | Update active auth connection settings |

#### 2. Schema Map — Teach AI Data (6 endpoints)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/schema` | Get full schema map |
| `GET` | `/schema/{table_name}` | Get table details |
| `PUT` | `/schema/{table_name}` | Update table description |
| `POST` | `/schema/{table_name}/columns/{column_name}` | Update column description |
| `POST` | `/schema/{table_name}/ai-generate` | AI auto-generate table/column descriptions |
| `POST` | `/schema/ai-generate-all` | AI auto-describe all tables |

#### 3. AI System Prompt & Skills (5 endpoints)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/system-prompt` | Get current system prompt + skills list |
| `PUT` | `/system-prompt` | Update system prompt |
| `POST` | `/skills` | Install a new skill (file upload) |
| `GET` | `/skills/{skill_name}` | Get skill file content (name with or without extension) |
| `DELETE` | `/skills/{skill_name}` | Remove a skill (name with or without extension) |

#### 4. AI Permissions — RBAC Matrix (3 endpoints)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/rbac` | Get all role configs with allowed tables |
| `PUT` | `/rbac/{role_name}` | Update role permissions |
| `POST` | `/rbac/{role_name}/toggle-table` | Toggle table access for a role (body: `{table}`) |

#### 5. Intent Tuner — Question Routing (4 endpoints)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/intents` | Get built-in + custom intent patterns |
| `POST` | `/intents/{intent_type}` | Add custom trigger phrase for intent type |
| `DELETE` | `/intents/{intent_type}/{index}` | Remove custom trigger by type and index |
| `POST` | `/intents/test` | Test intent classification on a question |

#### 6. AI Memory — Facts & Episodes (5 endpoints)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/memories` | List user memories (admin view) |
| `PUT` | `/memories/{memory_id}` | Update a memory entry |
| `DELETE` | `/memories/{memory_id}` | Delete a memory entry |
| `GET` | `/episodic` | List episodic memories |
| `DELETE` | `/episodic/{episode_id}` | Delete an episodic memory |

#### 7. Security — PII & Topics (5 endpoints)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/pii` | Get PII detection config (enabled, action, types) |
| `PUT` | `/pii` | Update PII settings |
| `POST` | `/pii/blocked-topics` | Add a blocked topic (body: `{name, pattern}`) |
| `DELETE` | `/pii/blocked-topics/{index}` | Remove a blocked topic by index |
| `POST` | `/pii/test` | Test PII detection on sample text |

#### 8. Data Sync — Staging DB (6 endpoints)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/freshness` | Data freshness status |
| `GET` | `/freshness/history` | Data freshness check history |
| `GET` | `/sync/schedule` | Get sync schedule config |
| `PUT` | `/sync/schedule` | Update sync schedule |
| `POST` | `/sync/force` | Force re-introspect staging DB |
| `GET` | `/sync/log` | Get sync history log |

#### 9. Chat Sessions — All Users (3 endpoints)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/sessions` | List all sessions (all users) |
| `GET` | `/sessions/{session_id}` | Get session with messages |
| `DELETE` | `/sessions/{session_id}` | Delete a session |

#### 10. RAG Database Management (3 endpoints)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/rag` | RAG DB stats (size, row counts) |
| `POST` | `/rag/vacuum` | Vacuum/optimize RAG DB |
| `POST` | `/rag/clear/{table}` | Clear a RAG table |

#### 11. Admin Users (via Admin Auth Router)

See [Section 10](#10-admin-authentication-independent) for admin user CRUD endpoints (`/admin/auth/users`).

#### 12. Application Logs (1 endpoint)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/logs` | Get admin activity logs with filtering |

---

## 8. External Connections

The orchestrator connects to **3 external services**. All connections are configurable via environment variables.

### 8.1 Staging Database (PostgreSQL)

| Property | Value |
|----------|-------|
| **Purpose** | Read-only business data (employees, sales, projects, etc.) |
| **Driver** | `asyncpg` via SQLAlchemy async engine |
| **Default URL** | `postgresql+asyncpg://ai_reader:changeme@localhost:5433/staging` |
| **Pool Size** | 10 connections (max overflow: 5) |
| **Access** | Read-only SELECT queries only |
| **Schema** | 13 tables defined in `schema_map.json` |

**Tables:** employees, sales, attendance, projects, leave_requests, monthly_revenue, departments, customers, employee_skills, project_assignments, expenses, payroll, performance_reviews

**Env Vars:**
```bash
ATL_STAGING_DB_HOST=localhost    # Database host
ATL_STAGING_DB_PORT=5433        # Database port
ATL_STAGING_DB_NAME=staging     # Database name
ATL_STAGING_DB_USER=ai_reader   # Database user
ATL_STAGING_DB_PASSWORD=changeme # Database password
```

### 8.2 LLM Server (LiteLLM Proxy → Ollama)

| Property | Value |
|----------|-------|
| **Purpose** | AI text generation (SQL, summaries, writing) |
| **Protocol** | OpenAI-compatible REST API |
| **Endpoint** | `POST /v1/chat/completions` |
| **Auth** | Bearer token (API key) |
| **Models** | `qwen-sql` (9B, deep reasoning) and `qwen-sql-fast` (4B, fast) |

**Env Vars:**
```bash
ATL_LLM_BASE_URL=http://localhost:7002    # LiteLLM proxy URL
ATL_LLM_API_KEY=sk-master-key-change-me   # API key for LiteLLM
ATL_LLM_MODEL=qwen-sql                    # Default model (9B)
ATL_LLM_FAST_MODEL=qwen-sql-fast          # Fast model (4B)
ATL_LLM_TEMPERATURE=0.1                   # LLM temperature
ATL_LLM_MAX_TOKENS=4096                   # Max output tokens
ATL_LLM_REQUEST_TIMEOUT=120               # Timeout (seconds)
```

**How the LLM chain works:**
```
Orchestrator → HTTP POST → LiteLLM Proxy (port 7002)
                               │
                               ▼
                         Route by model name:
                           "qwen-sql" → Ollama qwen3.5:9b
                           "qwen-sql-fast" → Ollama qwen3.5:4b
                               │
                               ▼
                         Ollama (port 11434) — local inference
```

**Per-Department API Keys (optional):**
```bash
ATL_LLM_DEPARTMENT_KEYS='{"HR":"sk-hr-key","Sales":"sk-sales-key"}'
```
Each department can have its own API key for usage tracking in LiteLLM.

### 8.3 Main Application (Auth Provider)

| Property | Value |
|----------|-------|
| **Purpose** | User authentication (JWT issuer or HTTP verification) |
| **Protocol** | REST API |
| **Endpoint** | `GET /verify-user` (HTTP mode) |
| **Auth Modes** | `jwt` (local decode), `http` (API call), `hybrid` (try both) |

**Env Vars:**
```bash
ATL_MAIN_APP_BASE_URL=http://localhost:7000     # Main App URL
ATL_AUTH_MODE=hybrid                             # jwt | http | hybrid
ATL_JWT_SECRET_OR_PUBLIC_KEY=your-jwt-secret     # For local JWT decode
ATL_JWT_ALGORITHM=HS256                          # HS256 or RS256
ATL_JWT_AUDIENCE=atl-ai                          # Expected JWT audience
ATL_JWT_ISSUER=main-app                          # Expected JWT issuer
```

---

## 9. Authentication System

### How Authentication Works

The orchestrator supports **three authentication modes**, controlled by `ATL_AUTH_MODE`:

#### Mode 1: JWT (Fastest — Zero Network Calls)

```
Client sends: Authorization: Bearer <jwt-token>
Orchestrator: Decodes JWT locally using shared secret → Extracts user info from claims
```

Expected JWT payload:
```json
{
  "sub": "42",
  "username": "ahmed.rashid",
  "role": "admin",
  "department": "Management",
  "permissions": ["employees", "sales", "projects"],
  "exp": 1740000000,
  "iss": "main-app",
  "aud": "atl-ai"
}
```

#### Mode 2: HTTP (API Callback)

```
Client sends: Authorization: Bearer <any-token>
Orchestrator: Calls GET http://main-app:3000/verify-user with same Bearer token
Main App: Returns user_id, username, role, department, permissions
```

#### Mode 3: Hybrid (Default — Best of Both)

```
1. Try JWT decode first (fast, no network)
2. If JWT decode fails → Fall back to HTTP callback
```

### Token Caching

Verified tokens are cached for `ATL_TOKEN_CACHE_TTL_SECONDS` (default 300s = 5 min) to avoid repeated verification.

### FastAPI Integration

Every protected endpoint uses:
```python
from auth.dependencies import get_current_user

@router.post("/chat")
async def chat(user: VerifiedUser = Depends(get_current_user)):
    # user.user_id, user.username, user.role, user.department
```

---

## 10. Admin Authentication (Independent)

The orchestrator has its own **independent authentication system** for the admin dashboard, separate from the ERP-BGS user authentication used for chat/API access.

### Why Independent Auth?

The orchestrator needs to manage its own admin users even when disconnected from the main ERP application. This enables:
- Standalone deployment without ERP dependency
- Separate admin credentials from application users
- ERP-BGS admin users can still access via token bridge

### Admin Database (`admin_db.py`)

- **Storage:** SQLite (`admin.db` — auto-created on first run)
- **Password Hashing:** PBKDF2-SHA256 with random salt (100,000 iterations)
- **Default User:** `admin` / `admin123` (created on first startup)

**Schema — `admin_users` table:**

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `username` | TEXT UNIQUE | Login username |
| `password_hash` | TEXT | PBKDF2-SHA256 hash |
| `display_name` | TEXT | Display name |
| `role` | TEXT | `superadmin` or `admin` |
| `is_active` | BOOLEAN | Account enabled/disabled |
| `source` | TEXT | `local` or `erp_bridge` |
| `erp_user_id` | INTEGER | Linked ERP user ID (if bridged) |
| `created_at` | TEXT | ISO timestamp |
| `last_login` | TEXT | Last login timestamp |

### Admin JWT Tokens (`auth/admin_auth.py`)

Orchestrator-issued admin tokens are distinct from ERP tokens:

```json
{
  "sub": "1",
  "username": "admin",
  "role": "superadmin",
  "source": "local",
  "iss": "atl-ai-orchestrator",
  "aud": "atl-ai-admin",
  "exp": 1740028800
}
```

| Property | Value |
|----------|-------|
| **Issuer** | `atl-ai-orchestrator` |
| **Audience** | `atl-ai-admin` |
| **Algorithm** | HS256 |
| **Default TTL** | 8 hours |
| **Secret** | `ATL_ADMIN_JWT_SECRET` env var |

### Dual Token Verification (`auth/dependencies.py`)

The `get_admin_user()` dependency tries two verification paths:

```
Admin Request with Bearer Token
     │
     ├── Try 1: Decode as orchestrator admin JWT
     │          (iss=atl-ai-orchestrator, aud=atl-ai-admin)
     │          → If valid: return AdminUser from admin.db
     │
     └── Try 2: Decode as ERP-BGS JWT
                (check role ∈ admin, owner, gm)
                → If valid: auto-provision user in admin.db via find_or_create_erp_user()
                → Return AdminUser with source=erp_bridge
```

### ERP Bridge — Auto-Provisioning

When an ERP-BGS admin (role: admin, owner, or gm) accesses the orchestrator dashboard for the first time, their account is automatically created in `admin.db` with `source=erp_bridge`. This is seamless — no manual setup required.

### Login Flow

```
POST /api/admin/auth/login
Body: {"username": "admin", "password": "admin123"}

Response: {
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "user": {"id": 1, "username": "admin", "role": "superadmin", "display_name": "Administrator"}
}
```

### Admin Auth Endpoints

| Method | Endpoint | Auth Required | Description |
|--------|----------|---------------|-------------|
| `POST` | `/api/admin/auth/login` | None | Login with username/password |
| `GET` | `/api/admin/auth/me` | Admin JWT | Get current admin info |
| `GET` | `/api/admin/auth/users` | Superadmin | List all admin users |
| `POST` | `/api/admin/auth/users` | Superadmin | Create admin user |
| `PUT` | `/api/admin/auth/users/{id}` | Superadmin | Update admin user |
| `DELETE` | `/api/admin/auth/users/{id}` | Superadmin | Delete admin user (cannot delete self) |

---

## 11. Role-Based Access Control (RBAC)

Defined in `rbac/role_config.json`. Each role specifies which tables and columns the user can access.

### Role Profiles

| Role | Allowed Tables | Denied Columns |
|------|---------------|----------------|
| **admin** | All tables (`*`) | None |
| **hr_manager** | employees, leave_requests, attendance, departments, projects, project_assignments, employee_skills, performance_reviews | employees: salary, bank_account, ssn, national_id |
| **hr_staff** | employees, leave_requests, attendance, employee_skills | employees: salary, bank_account, ssn, national_id, emergency_contact |
| **sales_manager** | sales, monthly_revenue, projects, project_assignments, customers, departments | None |
| **sales_staff** | sales, projects, customers | sales: commission_rate |
| **project_manager** | projects, project_assignments, employees, employee_skills, attendance | employees: salary, bank_account, ssn, national_id, emergency_contact |
| **finance** | sales, monthly_revenue, expenses, employees, projects, departments, payroll | employees: ssn, national_id, emergency_contact |
| **employee** | leave_requests, attendance, projects | None |

### How RBAC Filters Data

1. User's role is extracted from JWT/token
2. `rbac/permissions.py` looks up allowed tables and denied columns
3. `schema_loader.py` builds a schema description with **only** the allowed tables/columns
4. This filtered schema is injected into the LLM prompt — the LLM literally cannot see unauthorized tables
5. After SQL generation, the guardrail double-checks that only allowed tables are referenced

---

## 12. Security & Guardrails

### 12.1 PII Detection (`query_engine/pii_detector.py`)

Scans user input before processing. Detects:

| PII Type | Example | Action |
|----------|---------|--------|
| Email | `john@example.com` | Redact → `[REDACTED_EMAIL]` |
| Phone | `+1-555-123-4567` | Redact → `[REDACTED_PHONE]` |
| SSN | `123-45-6789` | Redact → `[REDACTED_SSN]` |
| Credit Card | `4111-1111-1111-1111` | Redact (Luhn validated) |
| IP Address | `192.168.1.1` | Redact → `[REDACTED_IP]` |
| Date of Birth | `DOB: 01/15/1990` | Redact |
| National ID | `passport: AB123456` | Redact |

**Blocked Topics** (completely rejected):

Built-in topics (cannot be removed):
- Salary queries by name ("salary of John")
- Password/credential requests
- Data exfiltration ("dump all employee records")

Custom blocked topics can be added via the admin dashboard (`POST /pii/blocked-topics`) with a `name` and regex `pattern`. Each custom topic defines a regex pattern that triggers rejection when matched against user input.

### 12.2 SQL Guardrail (`query_engine/sql_guardrail.py`)

Validates every LLM-generated SQL before execution:

1. **Must start with SELECT or WITH** — rejects any other statement type
2. **Blocked keywords**: DROP, DELETE, UPDATE, INSERT, ALTER, TRUNCATE, GRANT, REVOKE, CREATE, EXEC, MERGE, REPLACE
3. **Blocked patterns**: stacked statements (`;DROP`), SQL comments (`--`), `pg_sleep`, `xp_cmdshell`, `INFORMATION_SCHEMA`, `pg_catalog`, file I/O attempts
4. **Table whitelist**: Only tables from RBAC-allowed list. CTE aliases are excluded from the check
5. **Markdown cleanup**: Strips ` ```sql ``` ` code fences the LLM might add

### 12.3 Output Validation (`query_engine/output_validator.py`)

Checks LLM responses for quality issues:

- **Hallucination detection**: Flags phrases like "I don't have access to", "as an AI language model", "according to my training data"
- **Quality checks**: Response length limits, JSON structure validation
- **Confidence scoring**: Rates response reliability

### 12.4 SQL Refinement (`query_engine/sql_refiner.py`)

When SQL execution fails (e.g., column not found, syntax error):

1. Classifies the error type (COLUMN_NOT_FOUND, SYNTAX_ERROR, GROUP_BY_ERROR, etc.)
2. Builds a refinement prompt with the original SQL + error message + schema
3. Asks the LLM to fix it
4. Validates the new SQL through guardrails
5. Retries execution (up to `ATL_MAX_SQL_RETRIES`, default 2)
6. Records the attempt in episodic memory for future learning

### 12.5 Rate Limiting (`middleware/rate_limiter.py`)

- Sliding window counter: 10 requests per minute per user (configurable)
- Only applies to `POST /api/chat` (the expensive LLM endpoint)
- Identifies users by Authorization header; falls back to IP address
- Returns `429 Too Many Requests` when exceeded

---

## 13. Memory System (3-Layer)

### 13.1 Short-Term Memory (Session History)

- **Storage:** SQLite (`chat_history.db` → `chat_sessions` + `chat_messages` tables)
- **Scope:** Per-session, per-user
- **Purpose:** Enables multi-turn conversation ("same but for Q2", "compare that to last year")
- **Context window:** Last 20 messages injected into LLM prompt
- **Auto-title:** First message generates a session title using keyword extraction

### 13.2 Long-Term Memory (RAG)

- **Storage:** SQLite (`user_memories` table)
- **Scope:** Per-user, persists across sessions
- **Purpose:** Remembers user preferences and important facts
- **Categories:** preference, fact, context, instruction
- **Retrieval:** Keyword overlap scoring with importance weighting
- **Injection:** Top 10 most relevant memories added to system prompt
- **Sources:** Auto-extracted from conversations, or manually added via API

### 13.3 Episodic Memory (Learning)

- **Storage:** In-memory (`intent_classifier.py` → `EpisodicMemory` class) + SQLite backup
- **Scope:** Per-user reasoning history
- **Purpose:** Learns from past successes and failures
- **Features:**
  - Records: question, intent, SQL, success/failure, error type, execution time
  - Boosts confidence for intents that previously succeeded
  - Avoids SQL patterns that previously failed
  - Suggests multi-step decomposition based on past successes
  - Keeps last 50 episodes per user

---

## 14. Admin Dashboard — 12 Modules

The orchestrator ships with a **standalone Single-Page Application (SPA)** admin dashboard served at `/` on the orchestrator port. In development it runs at `localhost:8100`, in production at the mapped container port.

### UI Design

| Property | Value |
|----------|-------|
| **Framework** | Vanilla HTML/CSS/JS (single `index.html`, ~2,500 lines) |
| **CSS** | Tailwind CSS via CDN |
| **Font** | Inter (Google Fonts) |
| **Theme** | White sidebar, `bg-gray-50` body, `indigo-600` primary color |
| **Cards** | `rounded-2xl`, `shadow-sm`, `hover:shadow-md` transitions |
| **Nav** | Fixed sidebar (w-64) with hamburger toggle + SPA hash-based routing |
| **JS Functions** | ~103 total (page loaders, renderers, CRUD operations, auth helpers) |
| **Modals** | 2 (Create Admin User, Add Skill) |

### Sidebar Structure

The sidebar is organized into 5 collapsible sections:

| Section | Items |
|---------|-------|
| **Home** | Dashboard home (12 module cards) |
| **AI Config** | Connections, Schema Map, System Prompt, Permissions, Intent Tuner |
| **AI Data** | Memory, Security, Data Sync, Chat Sessions, RAG DB |
| **Admin** | Admin Users, Activity Log |
| **Tools** | Collapsible section with 8 utility links |

### Module Overview

| # | Module | Card Color | Description |
|---|--------|-----------|-------------|
| 1 | **CONNECTIONS** | Blue | Configure AI providers, databases, and auth connections. Multi-provider support with activate/deactivate. |
| 2 | **SCHEMA MAP** | Purple | Browse 130+ mapped tables, edit descriptions, AI auto-generate column descriptions via LLM. |
| 3 | **AI SYSTEM PROMPT** | Emerald | View and edit the core system prompt. Install, remove, and browse AI skill files. |
| 4 | **AI PERMISSIONS** | Amber | Visual RBAC editor showing which roles can access which tables. Toggle access per role. |
| 5 | **INTENT TUNER** | Rose | View built-in intent patterns, add custom trigger phrases, test classification on example questions. |
| 6 | **AI MEMORY** | Indigo | Browse and manage user memories (RAG facts). Edit importance, delete stale entries. |
| 7 | **SECURITY** | Red | Configure PII detection sensitivity, manage blocked topics (name + regex pattern), test PII detection. |
| 8 | **DATA SYNC** | Teal | Monitor data freshness, configure sync schedule, force re-introspection, view sync history log. |
| 9 | **CHAT SESSIONS** | Cyan | Admin view of all user chat sessions organized by username. View messages, delete sessions. |
| 10 | **RAG DB** | Violet | Monitor RAG database stats (size, row counts), vacuum/optimize, clear specific tables. |
| 11 | **ADMIN USERS** | Orange | Manage orchestrator admin users. Create, update, deactivate accounts. Separate from ERP users. |
| 12 | **LOG** | Gray | View admin activity and system event logs with filtering. |

### Home Page

The home page displays all 12 modules as color-coded cards in a responsive grid (3 columns on desktop, 1 on mobile). Each card shows:
- Module icon (SVG)
- Module title and descriptive subtitle
- Color-coded accent (left border or background)
- Live status indicator (e.g., "130 tables mapped", "PII detection active", "3 roles configured")

### SPA Navigation

Navigation is handled client-side using hash-based routing with 19 routes:

```javascript
// Example: clicking "Schema Map" card
navTo('schema-map');
// URL becomes: http://localhost:8100/#schema-map
// Content area updates without page reload
```

**Routes:**
`home`, `connections`, `schema-map`, `system-prompt`, `permissions`, `intent-tuner`, `memory`, `security`, `data-sync`, `chat-sessions`, `rag-db`, `admin-users`, `log`, `tools`, `settings`, `invite`, `audit`, `api-keys`, `backup`

**Auth helpers:** `authHdr()` returns JWT authorization header, `authGet(url)` wraps authenticated fetch calls.

### Backend Router — `admin_dashboard.py`

The dashboard's backend is a single FastAPI router (`routers/admin_dashboard.py`, ~1700 lines) containing 49 endpoints for the 12 modules. All endpoints are protected by the `get_admin_user` dependency (requires admin JWT or ERP admin token).

Prefix: `/api/admin/dashboard/`

```python
# main.py router registration
app.include_router(
    admin_dashboard_router,
    prefix="/api/admin/dashboard",
    dependencies=[Depends(get_admin_user)]
)
```

---

## 15. Configuration Reference

All settings are in `config.py` using `pydantic-settings`. Env vars use the `ATL_` prefix.

### Core Settings

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `ATL_APP_NAME` | "ATL-AI Orchestrator" | Application name |
| `ATL_APP_VERSION` | "1.0.0" | Version string |
| `ATL_DEBUG` | false | Enable debug mode + SQL echo |

### Authentication

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `ATL_AUTH_MODE` | "hybrid" | `jwt`, `http`, or `hybrid` |
| `ATL_JWT_SECRET_OR_PUBLIC_KEY` | "" | Secret for JWT decode |
| `ATL_JWT_ALGORITHM` | "HS256" | `HS256` or `RS256` |
| `ATL_JWT_AUDIENCE` | "" | Expected JWT audience claim |
| `ATL_JWT_ISSUER` | "" | Expected JWT issuer claim |
| `ATL_MAIN_APP_BASE_URL` | "http://localhost:7000" | Main App URL (HTTP auth fallback) |
| `ATL_TOKEN_CACHE_TTL_SECONDS` | 300 | Token cache duration |

### Database

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `ATL_STAGING_DB_HOST` | "localhost" | PostgreSQL host |
| `ATL_STAGING_DB_PORT` | 5433 | PostgreSQL port |
| `ATL_STAGING_DB_NAME` | "staging" | Database name |
| `ATL_STAGING_DB_USER` | "ai_reader" | Database user |
| `ATL_STAGING_DB_PASSWORD` | "changeme" | Database password |

### LLM

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `ATL_LLM_BASE_URL` | "http://localhost:7002" | LiteLLM proxy URL |
| `ATL_LLM_API_KEY` | "sk-change-me" | LiteLLM API key |
| `ATL_LLM_MODEL` | "qwen-sql" | Default model (deep/9B) |
| `ATL_LLM_FAST_MODEL` | "qwen-sql-fast" | Fast model (4B) |
| `ATL_LLM_TEMPERATURE` | 0.1 | Generation temperature |
| `ATL_LLM_MAX_TOKENS` | 4096 | Max output tokens |
| `ATL_LLM_REQUEST_TIMEOUT` | 120 | Request timeout (seconds) |
| `ATL_LLM_CONTEXT_WINDOW` | 131072 | Max context window (tokens) |
| `ATL_LLM_DEPARTMENT_KEYS` | "{}" | JSON: department→API key |
| `ATL_OLLAMA_URL` | "http://localhost:11434" | Ollama engine URL |

### Guardrails

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `ATL_PII_DETECTION_ENABLED` | true | Enable PII scanning |
| `ATL_OUTPUT_VALIDATION_ENABLED` | true | Enable output quality checks |
| `ATL_MAX_SQL_RETRIES` | 2 | Max SQL refinement attempts |
| `ATL_EPISODIC_MEMORY_ENABLED` | true | Enable episodic learning |
| `ATL_EPISODIC_MEMORY_MAX_EPISODES` | 50 | Max episodes per user |
| `ATL_RATE_LIMIT_PER_MINUTE` | 10 | Rate limit per user |

### Admin Authentication

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `ATL_ADMIN_JWT_SECRET` | (auto-generated) | Secret key for orchestrator admin JWTs |
| `ATL_ADMIN_JWT_TTL` | 28800 | Admin token TTL in seconds (8 hours) |
| `ATL_ADMIN_DEFAULT_USERNAME` | "admin" | Default admin username (first startup) |
| `ATL_ADMIN_DEFAULT_PASSWORD` | "admin123" | Default admin password (first startup) |
| `ATL_ERP_ADMIN_ROLES` | "admin,owner,gm" | Comma-separated ERP roles allowed dashboard access |

### Env File Loading

Config loads from (in priority order):
1. Environment variables (highest priority)
2. `.env` file in current directory
3. `../.env` file (parent directory)

---

## 16. Connecting to Another Application

The orchestrator is designed to be **plugged into any application** as a backend AI service. Here's how:

### Step 1: Authentication Integration

Your application must issue JWT tokens that the orchestrator can verify. The JWT must contain these claims:

```json
{
  "sub": "user_id_or_number",
  "username": "display_name",
  "role": "one_of_the_rbac_roles",
  "department": "user_department",
  "permissions": [],
  "exp": 1740000000
}
```

**Option A — Shared JWT Secret:**
Set `ATL_JWT_SECRET_OR_PUBLIC_KEY` to your app's JWT signing secret. The orchestrator decodes tokens locally.

**Option B — HTTP Verification:**
Set `ATL_AUTH_MODE=http` and `ATL_MAIN_APP_BASE_URL` to your app's URL. Implement a `GET /verify-user` endpoint that:
- Accepts `Authorization: Bearer <token>` header
- Returns `{"user_id": int, "username": str, "role": str, "department": str}`
- Returns 401 for invalid tokens

### Step 2: Database Connection

Point the orchestrator at your PostgreSQL database:

```bash
ATL_STAGING_DB_HOST=your-db-host
ATL_STAGING_DB_PORT=5432
ATL_STAGING_DB_NAME=your_database
ATL_STAGING_DB_USER=readonly_user
ATL_STAGING_DB_PASSWORD=your_password
```

**Important:** Create a READ-ONLY database user. The orchestrator only runs SELECT queries, but defense-in-depth is critical.

### Step 3: Schema Definition

Edit `schema_map.json` to describe your database tables:

```json
{
  "database": "your_database",
  "dialect": "postgresql",
  "tables": {
    "your_table": {
      "description": "Human-readable description for the LLM",
      "columns": {
        "id": {"type": "integer", "primary_key": true, "description": "Primary key"},
        "name": {"type": "varchar(100)", "description": "Record name"},
        "amount": {"type": "numeric(12,2)", "description": "Dollar amount"}
      }
    }
  }
}
```

The LLM uses these descriptions to understand your schema. Better descriptions = better SQL generation.

### Step 4: RBAC Configuration

Edit `rbac/role_config.json` to define your application's roles:

```json
{
  "roles": {
    "your_role": {
      "description": "What this role can access",
      "allowed_tables": ["table1", "table2"],
      "denied_columns": {
        "table1": ["sensitive_column"]
      }
    }
  },
  "all_tables": ["table1", "table2", "table3"]
}
```

### Step 5: LLM Setup

You need an LLM inference server. Options:

**Option A — Ollama + LiteLLM (recommended):**
```bash
# Install Ollama (https://ollama.com)
ollama pull qwen3.5:9b
ollama serve

# Configure LiteLLM proxy with your models
ATL_LLM_BASE_URL=http://your-litellm-host:7002
ATL_LLM_API_KEY=your-key
```

**Option B — Any OpenAI-compatible API:**
```bash
ATL_LLM_BASE_URL=http://your-api-host
ATL_LLM_API_KEY=your-api-key
ATL_LLM_MODEL=your-model-name
```

The orchestrator uses the OpenAI chat completions format (`/v1/chat/completions`), so any compatible server works.

### Step 6: Frontend Integration

Your frontend sends requests to the orchestrator:

```javascript
// Chat — ask a question
const response = await fetch('http://orchestrator-host:7001/api/chat', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${userJwtToken}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    question: 'How many active projects do we have?',
    session_id: existingSessionId || null  // null = auto-create
  })
});
const data = await response.json();
// data.answer — human-readable response
// data.sql — the SQL that was executed
// data.data — raw query results
// data.metadata — execution details

// Writing Assistant — enhance text in a field
const assist = await fetch('http://orchestrator-host:7001/api/assist', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${userJwtToken}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    field_label: 'Description',
    current_text: 'fix the bug in login page',
    app_section: 'Issue Tracker',
    tone: 'professional'
  })
});
const suggestion = await assist.json();
// suggestion.suggestion — polished text
// suggestion.type — "rewrite" or "completion"
```

### Step 7: Docker Deployment

```bash
# Build and run
docker build -t my-orchestrator ./orchestrator
docker run -d \
  -p 7001:8000 \
  -e ATL_STAGING_DB_HOST=your-db \
  -e ATL_LLM_BASE_URL=http://your-llm \
  -e ATL_LLM_API_KEY=your-key \
  -e ATL_JWT_SECRET_OR_PUBLIC_KEY=your-secret \
  -e ATL_AUTH_MODE=jwt \
  my-orchestrator
```

---

## 17. Docker Deployment

### Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### docker-compose.yml (orchestrator service)

```yaml
orchestrator:
  build:
    context: ./orchestrator
    dockerfile: Dockerfile
  container_name: atl-orchestrator
  restart: unless-stopped
  ports:
    - "7001:8000"
  env_file:
    - .env
  environment:
    ATL_STAGING_DB_HOST: staging-db        # Docker service name
    ATL_STAGING_DB_PORT: 5432              # Internal container port
    ATL_LLM_BASE_URL: http://litellm:4000  # Docker service name
    ATL_MAIN_APP_BASE_URL: http://main-app:3000
    ATL_JWT_SECRET_OR_PUBLIC_KEY: ${JWT_SECRET}
    ATL_AUTH_MODE: jwt
    ATL_OLLAMA_URL: http://host.docker.internal:11434
  depends_on:
    staging-db:
      condition: service_healthy
    main-app:
      condition: service_healthy
    litellm:
      condition: service_started
```

### Health Check

```bash
curl http://localhost:7001/api/health
# {"status":"healthy","components":{"staging_db":"connected","llm_server":"connected","main_app_auth":"reachable"}}
```

---

## 18. Module Reference

### `main.py` — Application Entry Point
- Creates FastAPI app with lifespan hooks
- Registers CORS middleware (all origins)
- Registers rate limiting middleware
- Mounts 7 routers: health, chat, assist, sessions, admin, admin_auth, admin_dashboard
- Initializes admin.db on startup (creates default admin user)
- Serves static admin UI at `/`

### `config.py` — Centralized Configuration
- `Settings` class (pydantic-settings) with ~35 env vars
- All settings prefixed with `ATL_`
- Includes admin auth settings (JWT secret, TTL, default credentials, ERP admin roles)
- Loads from env vars → `.env` → `../.env`
- Singleton: `settings = Settings()`

### `admin_db.py` — Independent Admin Database
- SQLite `admin.db` for orchestrator admin users
- PBKDF2-SHA256 password hashing (100k iterations)
- CRUD: authenticate, create, update, delete, list users
- ERP bridge: `find_or_create_erp_user()` auto-provisions ERP admins
- Schema auto-creation on `init_admin_db()`

### `db.py` — Database Engine
- SQLAlchemy async engine with `asyncpg` driver
- Connection pool: size=10, max_overflow=5, pre_ping=True
- `check_connection()` health check with 3s timeout
- `dispose_engine()` for graceful shutdown

### `data_freshness.py` — Sync Status Monitor
- Queries 7 key tables for latest timestamps
- Reports: "fresh" (<45 min), "slightly_stale" (45-120 min), "stale" (>120 min)
- Parallel queries with 3s timeout each

### `auth/admin_auth.py` — Admin JWT Management
- `AdminUser` dataclass: id, username, role, source, display_name, erp_user_id
- `create_admin_token()`: issues orchestrator admin JWT (iss=atl-ai-orchestrator)
- `decode_admin_token()`: verifies orchestrator-issued tokens
- `decode_erp_admin_token()`: verifies ERP-BGS tokens with admin role check

### `auth/token_verify.py` — Token Verification
- JWT decode (HS256/RS256) using `python-jose`
- HTTP callback (GET /verify-user) using `httpx`
- Hybrid mode: JWT first, HTTP fallback
- TTL cache prevents repeated verification

### `auth/dependencies.py` — FastAPI Auth Dependency
- Extracts Bearer token from Authorization header
- `get_current_user()`: for chat/session routes (uses `verify_token()`)
- `get_admin_user()`: for admin routes — tries orchestrator admin JWT first, then ERP-BGS admin token bridge
- Returns `VerifiedUser` or `AdminUser` dataclass
- Raises 401 on invalid/missing token

### `rbac/permissions.py` — Access Control
- Loads `role_config.json` (cached)
- `get_allowed_tables(role)` → list of table names
- `get_denied_columns(role)` → dict of table→denied columns
- `check_table_access(role, tables)` → list of unauthorized tables

### `llm_client/__init__.py` — LLM Communication
- `generate(messages, department, model)` → sends to LiteLLM proxy
- Per-department API key routing
- Error handling: timeout, connection, HTTP errors → `LLMClientError`
- `check_llm_reachable()` → health check

### `query_engine/intent_classifier.py` — Intent Classification
- 3 intents: text_processing, data_query, multi_step_analysis
- Pattern banks: ~10 text patterns, ~11 data patterns, ~12 multi-step patterns
- Heuristics: pasted text length, quoted blocks, clause counting
- Episodic memory calibration (boosts/reduces confidence based on history)
- Sub-task decomposition for multi-step queries
- Complexity scoring (0.0–1.0)

### `query_engine/prompt_builder.py` — LLM Prompts
- 4 prompt templates: SQL, multi-step SQL, text processing, summarization
- Shared AI Orchestrator identity injected into all prompts
- Agentic preamble for structured reasoning
- Schema injection with role-based filtering
- Episodic hints for error avoidance

### `query_engine/sql_guardrail.py` — SQL Security
- SELECT/WITH-only enforcement
- 14 blocked keywords (DML/DDL)
- 10 blocked regex patterns (injection, DoS, file I/O)
- Table whitelist validation (CTE-alias aware)
- Markdown code fence cleanup

### `query_engine/db_executor.py` — SQL Execution
- Executes validated SQL on staging DB
- Returns rows as list of dicts + execution time
- Type sanitization: Decimal→float, datetime→ISO string

### `query_engine/response_formatter.py` — Result Summarization
- Calls LLM a second time to produce human-readable summary
- Standard mode: concise executive summary
- Multi-step mode: detailed analytical response
- Fallback: basic data description if LLM fails

### `query_engine/schema_loader.py` — Schema Management
- Loads `schema_map.json` (cached with `lru_cache`)
- `get_filtered_schema(allowed_tables, denied_columns)` → text description for LLM
- `reload_schema()` to clear cache after edits

### `query_engine/pii_detector.py` — PII Scanning
- 7 PII pattern types with regex detection
- 3 blocked topic patterns (canonical safety rules)
- Actions: REDACT, BLOCK, or WARN
- Luhn algorithm for credit card validation

### `query_engine/output_validator.py` — Quality Checks
- 5 hallucination marker patterns
- SQL syntax pre-validation
- Response length and quality scoring
- Severity levels: ERROR, WARNING, INFO

### `query_engine/sql_refiner.py` — Error Recovery
- 6+ error category classifiers (column not found, syntax error, group by, etc.)
- Builds refinement prompt with error context
- Re-validates through guardrails before retry
- Records attempts for episodic learning

### `middleware/rate_limiter.py` — Rate Limiting
- Sliding window algorithm
- Per-user identification (auth header or IP)
- Only applies to POST /api/chat
- Returns 429 when exceeded

### `chat_history/__init__.py` — Storage Layer
- SQLite with WAL mode for concurrent reads
- 4 tables: chat_sessions, chat_messages, user_memories, episodic_memories
- Full CRUD for sessions and memories
- RAG retrieval with keyword overlap + importance scoring
- Auto-extraction of facts from conversations
- Session title generation from question keywords

### `routers/chat.py` — Main Chat Pipeline
- `POST /api/chat` — 15-step pipeline (auth→PII→session→memory→classify→RBAC→schema→SQL→guardrail→execute→refine→summarize→validate→save→freshness)
- 3 internal handlers: `_handle_text_processing`, `_handle_data_query`, `_handle_multi_step`

### `routers/assist.py` — Writing Assistant
- `POST /api/assist` — Field-level text enhancement
- System prompt with field context, nearby fields, tone
- Uses fast 4B model for responsiveness
- PII scanning on input text

### `routers/health.py` — Health Monitoring
- `GET /api/health` — checks DB, LLM, Main App connectivity
- `GET /api/data-freshness` — staging DB sync recency

### `routers/sessions.py` — Session & Memory CRUD
- Full session lifecycle (create, list, get, rename, delete)
- Memory management (list, add, delete, clear)

### `routers/admin.py` — Admin Service Proxies
- Proxies to LiteLLM, Ollama, Main App management APIs
- System config endpoint
- LLM test probe
- Main App user management (list, create, update, toggle, reset-password, delete)

### `routers/admin_auth.py` — Admin Authentication Router
- `POST /admin/auth/login`: authenticate with username/password → JWT
- `GET /admin/auth/me`: current admin user info
- `GET /admin/auth/users`: list all admin users (superadmin only)
- `POST /admin/auth/users`: create admin user (superadmin only)
- `PUT /admin/auth/users/{id}`: update admin user (superadmin only)
- `DELETE /admin/auth/users/{id}`: delete admin user (superadmin only, cannot delete self)

### `routers/admin_dashboard.py` — 12-Module Dashboard API (~1700 lines, 49 endpoints)
- **Connections**: Multi-provider LLM/DB/Auth config CRUD with activate/deactivate (8 endpoints)
- **Schema Map**: Table/column description editor with AI auto-generation (6 endpoints)
- **System Prompt & Skills**: System prompt editor + skill file management; skills GET/DELETE support names with or without file extension (5 endpoints)
- **RBAC Matrix**: Role permission editor with table toggle via POST body (3 endpoints)
- **Intent Tuner**: Custom pattern management by intent type + classification testing (4 endpoints)
- **Memory Browser**: Admin view of user memories and episodic memories with edit/delete (5 endpoints)
- **PII & Security**: PII config editor (enabled/action/types) + blocked topics (name + regex pattern) + detection testing (5 endpoints)
- **Data Sync**: Freshness monitoring with history, sync scheduling, force sync, sync log (6 endpoints)
- **Chat Sessions**: Admin view of all user sessions with messages (3 endpoints)
- **RAG DB**: Stats, vacuum, table clearing (3 endpoints)
- **Logs**: Activity log with filtering (1 endpoint)

---

*Generated for ATL-AI Orchestrator v2.2.0 — Last updated March 19, 2026*
