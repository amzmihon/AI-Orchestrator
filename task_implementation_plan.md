# Task Implementation Plan: Multi-LLM Orchestrator with HA and Configurable Routing

> **Created:** April 4, 2026
> **Last Updated:** April 4, 2026
> **Status:** 🔄 Planning — Deep analysis complete, ready for implementation

---

## 0. Executive Summary

This plan transforms the ATL-AI Orchestrator from a **single-LLM system with basic multi-LLM scaffolding** into a **production-grade multi-LLM platform** with high availability, intelligent routing, cost controls, and a full admin experience. The analysis below reflects what the codebase already has, what's incomplete/broken, and what needs to be built from scratch.

---

## 0a. Current State Assessment (What Already Exists)

| Component | Status | Notes |
|-----------|--------|-------|
| `LLMConfig` model | ✅ Exists | `config.py` L9-18 — name, base_url, api_key, model, role, capabilities, temperature, max_tokens, timeout |
| `LLMManager` class | ⚠️ Partial | `llm_manager.py` — has register, health tracking, primary election. BUT: `check_llm_health` method is defined OUTSIDE the class (L10-32), import uses `from .config` (relative import but file is at root level = broken import) |
| `llm_health_task.py` | ⚠️ Broken | Uses `from .llm_manager` relative import — will fail since it's a root-level file, not a package |
| `llm_client/__init__.py` | ✅ Works | Has LLM selection: by name → primary → legacy fallback. Good foundation. |
| Legacy single-LLM config | ✅ Exists | `settings.llm_base_url`, `llm_api_key`, `llm_model`, etc. — backward compatible |
| `llms` list in Settings | ✅ Exists | But currently defaults to empty `[]`, so `llm_manager` always initializes with 0 LLMs |
| Admin UI (12 modules) | ✅ Complete | `static/index.html` — 219KB, all 12 dashboard modules working |
| Connections management | ✅ Exists | `connections.json` + admin_dashboard endpoints — but manages legacy single-LLM only, not multi-LLM |
| Routing engine | ❌ Missing | No task-type-based routing to specific LLMs. `llm_client` always uses primary or legacy. |
| Cost/token tracking | ❌ Missing | No per-LLM budget, no usage counters, no cost enforcement |
| Failover/cooldown logic | ❌ Missing | `primary_llm()` has basic promote-on-failure but no cooldown, no demotion rules, no N-level chain |
| Notification system | ❌ Missing | No admin alerts for failover, degradation, or outages |
| Audit logging | ❌ Missing | No structured logging of routing decisions, LLM selections, or admin actions |
| Config versioning | ❌ Missing | No backup/restore, no config history |
| Chaos/simulation mode | ❌ Missing | No way to test failover without real failures |

---

## 0b. Critical Bugs Discovered During Analysis

> [!CAUTION]
> These MUST be fixed before any new features are built.

1. **`llm_manager.py` L6:** `from .config import settings, LLMConfig` — relative import in a root-level file. Will cause `ImportError: attempted relative import with no known parent package`.
2. **`llm_manager.py` L10-32:** `check_llm_health()` and `heartbeat_all()` are defined OUTSIDE the `LLMManager` class body (they appear before `class LLMStatus` at L38). They're orphan methods.
3. **`llm_health_task.py` L5:** `from .llm_manager import llm_manager` — same relative import issue. `main.py` L13 imports it as `from llm_health_task import ...` which confirms it's a root-level file.
4. **`settings.llms` defaults to `[]`** — The LLMManager initializes with an empty list, so no multi-LLM support actually works at runtime unless manually configured.
5. **`connections.json`** manages LLM providers separately from `settings.llms` — there's no bridge between the connections admin UI and the `LLMManager`.

---

## 1. Delivery Phases (Revised & Prioritized)

| Phase | Focus | Priority | Estimated Effort |
|-------|-------|----------|-----------------|
| **Phase 0** | Bug fixes & structural repair | 🔴 Critical | 1 day |
| **Phase 1** | Backend foundation, config unification, LLM lifecycle | 🔴 High | 3-4 days |
| **Phase 2** | Routing engine, health monitoring, failover policies | 🔴 High | 4-5 days |
| **Phase 3** | Admin UI for multi-LLM, dashboards, monitoring | 🟡 Medium | 3-4 days |
| **Phase 4** | Token/cost control, compliance, budget enforcement | 🟡 Medium | 3-4 days |
| **Phase 5** | Chaos testing, simulation, production hardening | 🟢 Normal | 2-3 days |
| **Phase 6** | Analytics, auto-tuning, disaster recovery | 🟢 Normal | 2-3 days |

---

## Phase 0: Critical Bug Fixes

### 0.1 Fix `llm_manager.py` imports & class structure
- [ ] Change `from .config import settings, LLMConfig` → `from config import settings, LLMConfig`
- [ ] Move `check_llm_health()` and `heartbeat_all()` INSIDE the `LLMManager` class body (after L62)
- [ ] Move `LLMStatus` class ABOVE `LLMManager` (it's currently below the class methods that reference it)

### 0.2 Fix `llm_health_task.py` imports
- [ ] Change `from .llm_manager import llm_manager` → `from llm_manager import llm_manager`

### 0.3 Bridge `connections.json` ↔ `LLMManager`
- [ ] When connections are loaded/updated, sync `llm_providers` into `settings.llms` list
- [ ] Ensure `LLMManager` reinitializes when connections change

**Acceptance Criteria:** Orchestrator starts without import errors, health checks run every 30s, LLM providers from `connections.json` are usable by `LLMManager`.

---

## Phase 1: Backend Foundation

### 1.1 Enhanced LLM Metadata Model
Extend `LLMConfig` in `config.py` with:
- [ ] `cost_per_1k_input_tokens: float = 0.0` — for cost tracking
- [ ] `cost_per_1k_output_tokens: float = 0.0`
- [ ] `privacy_level: str = "standard"` — `internal | standard | external`
- [ ] `max_concurrent_requests: int = 10`
- [ ] `priority: int = 0` — for weighted routing (higher = preferred)
- [ ] `tags: list[str] = []` — for capability matching (`sql`, `summarization`, `coding`, etc.)
- [ ] `cooldown_seconds: int = 60` — how long to wait before retrying after failure
- [ ] `max_fail_count: int = 3` — failures before demotion
- [ ] `provider_type: str = "openai"` — `openai | ollama | anthropic | groq | custom`
- [ ] `enabled: bool = True` — soft disable without removal

### 1.2 Persistent LLM Status & Metrics
Create `llm_status_store.py`:
- [ ] Persist LLM status to `llm_status.db` (SQLite) — survives restarts
- [ ] Track per-LLM: total_requests, total_tokens_in, total_tokens_out, total_cost, avg_latency_ms, error_count, last_error, uptime_percent
- [ ] Add status history table for time-series (latency, availability over last 24h/7d)
- [ ] Expose `get_llm_stats(name)` and `get_all_stats()` methods

### 1.3 Dynamic LLM Registration API
Create new endpoints in a dedicated `routers/llm_management.py`:
- [ ] `GET /api/llms` — list all LLMs with status & metrics
- [ ] `POST /api/llms` — register a new LLM (validates connectivity before adding)
- [ ] `PUT /api/llms/{name}` — update LLM config
- [ ] `DELETE /api/llms/{name}` — remove LLM (fail if it's the only healthy primary)
- [ ] `POST /api/llms/{name}/test` — run a live connectivity + response quality test
- [ ] `POST /api/llms/{name}/enable` / `POST /api/llms/{name}/disable` — soft toggle
- [ ] `GET /api/llms/{name}/stats` — detailed metrics for a specific LLM
- [ ] `GET /api/llms/{name}/history` — latency/availability time-series

### 1.4 Provider Adapter System
Create `llm_client/adapters/`:
- [ ] `base.py` — `BaseLLMAdapter` abstract class (connect, generate, health_check, get_models)
- [ ] `openai_adapter.py` — for OpenAI-compatible APIs (LiteLLM, Groq, OpenRouter, vLLM)
- [ ] `ollama_adapter.py` — for local Ollama models (different API format)
- [ ] `anthropic_adapter.py` — for Claude API (different message format)
- [ ] Adapter auto-selection based on `provider_type` field in `LLMConfig`
- [ ] Unified error handling and retry logic per adapter

### 1.5 Config Migration & Legacy Bridge
- [ ] Auto-migrate legacy single-LLM `.env` vars into `settings.llms[0]` on startup if `llms` is empty
- [ ] Bridge `connections.json` `llm_providers` list ↔ `settings.llms` ↔ `LLMManager`
- [ ] Write migration script for existing deployments
- [ ] Ensure `llm_client/__init__.py` continue working with legacy config (no breaking changes)

---

## Phase 2: Routing Engine, Health Monitoring & Failover

### 2.1 Intelligent Routing Engine
Create `routing_engine.py`:
- [ ] **Task-type routing:** Map intent types to preferred LLMs
  - `text_processing` → fast/cheap LLM
  - `data_query` → SQL-specialized LLM
  - `multi_step_analysis` → most capable LLM
- [ ] **Capability-based routing:** Match task requirements to LLM `tags`/`capabilities`
- [ ] **Round-robin routing:** Distribute load across equally-capable LLMs
- [ ] **Weighted routing:** Use `priority` field to prefer certain LLMs
- [ ] **Privacy-aware routing:** Route sensitive queries to `internal` privacy-level LLMs only
- [ ] **Cost-aware routing:** Prefer cheaper LLMs when task is simple
- [ ] **Latency-aware routing:** Prefer fastest responding LLM
- [ ] **Per-department routing:** Allow admin to assign specific LLMs to departments
- [ ] **Per-user override:** Allow admin to pin a user to a specific LLM
- [ ] **Time-of-day policies:** Different routing during peak vs off-peak hours
- [ ] **Fallback chain:** If preferred LLM is down, try next in priority order

### 2.2 Routing Policy Configuration
Create `routing_policies.json` + admin endpoints:
- [ ] Define named policies (e.g., "cost-optimized", "speed-first", "privacy-first", "balanced")
- [ ] Store active policy + custom overrides
- [ ] `GET /api/routing/policies` — list available policies
- [ ] `PUT /api/routing/active-policy` — switch active policy
- [ ] `GET /api/routing/overrides` — per-department/user overrides
- [ ] `PUT /api/routing/overrides/{scope}/{id}` — set override
- [ ] `POST /api/routing/simulate` — simulate routing decision without executing

### 2.3 Enhanced Health Monitoring
Upgrade `llm_health_task.py`:
- [ ] Configurable health check interval per LLM (fast for primary, slower for standby)
- [ ] Health check types: `/health` endpoint, test inference, latency probe
- [ ] Adaptive intervals: check more frequently when an LLM was recently unhealthy
- [ ] Record health history for trend analysis
- [ ] Detect degradation (latency >3x normal) separately from outage (no response)
- [ ] Expose WebSocket endpoint for real-time health status updates to admin UI

### 2.4 Automatic Failover & Recovery
Upgrade `llm_manager.py`:
- [ ] **N-level failover chain:** primary → secondary → tertiary → standby pool
- [ ] **Configurable promotion rules:** Fastest healthy LLM, lowest cost, specific order
- [ ] **Demotion logic:** When primary recovers, don't auto-demote current primary (configurable)
- [ ] **Cooldown periods:** After failure, wait `cooldown_seconds` before retrying
- [ ] **Gradual recovery:** Send test traffic to recovered LLM before full promotion (canary)
- [ ] **Circuit breaker pattern:** Open → Half-open → Closed state machine per LLM
- [ ] **Failover event log:** Record every failover with timestamp, reason, from/to LLM

### 2.5 Notification System
Create `notifications.py`:
- [ ] **In-app notifications:** Store alerts in `admin.db` for admin UI display
- [ ] **Notification types:**
  - LLM went down / recovered
  - Failover triggered (from → to)
  - Latency degradation detected
  - Token/cost budget threshold reached (80%, 90%, 100%)
  - Health check failures exceeding threshold
- [ ] **Severity levels:** info, warning, critical
- [ ] `GET /api/notifications` — list recent notifications
- [ ] `POST /api/notifications/{id}/dismiss` — mark as read
- [ ] **Future:** Webhook/email integration (Phase 6)

### 2.6 Integrate Routing into Chat Pipeline
Modify `llm_client/__init__.py` `generate()`:
- [ ] Replace current "by name → primary → legacy" selection with routing engine call
- [ ] Pass task context (intent, department, user_id, privacy requirements) to routing engine
- [ ] Log routing decisions with reasoning
- [ ] Handle fallback chain transparently (try next LLM on failure)
- [ ] Report which LLM was actually used in response metadata

Modify `routers/chat.py`:
- [ ] Add `llm_used` field to `ChatResponseMetadata`
- [ ] Add `routing_reason` field to explain why that LLM was chosen
- [ ] Add `fallback_count` to show how many LLMs were tried

---

## Phase 3: Admin UI & Experience

### 3.1 Multi-LLM Management Page
Add new admin module (Module 13: LLM FLEET):
- [ ] Visual card grid showing all registered LLMs with:
  - Health status indicator (green/yellow/red)
  - Current role badge (primary/secondary/standby)
  - Live latency display
  - Request count and cost totals
  - Enable/disable toggle
- [ ] "Add LLM" modal with:
  - Provider type dropdown (OpenAI, Ollama, Groq, Anthropic, Custom)
  - Dynamic form fields based on provider
  - "Test Connection" button before saving
- [ ] "Edit LLM" modal with all config fields
- [ ] Drag-and-drop to reorder failover priority
- [ ] One-click promote/demote actions

### 3.2 Routing Policy Editor
Add new admin module (Module 14: ROUTING):
- [ ] Visual routing policy selector (preset cards)
- [ ] Custom rule builder: IF [condition] THEN [route to LLM]
  - Conditions: intent type, department, user, time range, cost threshold
- [ ] Per-department LLM assignment matrix
- [ ] Routing simulation tool: enter a query and see which LLM would handle it
- [ ] Active routing statistics: pie chart of LLM usage distribution

### 3.3 Health & Monitoring Dashboard
Upgrade existing System Health page or add Module 15: MONITORING:
- [ ] Real-time health status panel (auto-refresh every 10s)
- [ ] Latency time-series chart per LLM (last 1h/24h/7d)
- [ ] Availability percentage per LLM (uptime)
- [ ] Failover event timeline
- [ ] Active notification panel with dismiss actions
- [ ] Token usage charts (daily/weekly/monthly per LLM)
- [ ] Cost breakdown charts

### 3.4 Preset Templates & Setup Wizard
- [ ] First-time setup wizard (detected when 0 LLMs configured):
  - Step 1: Add your first LLM (with guided form)
  - Step 2: Optionally add a second (for HA)
  - Step 3: Choose a routing policy
  - Step 4: Verify with test query
- [ ] Preset templates:
  - "Solo" — 1 LLM, no HA
  - "Basic HA" — 2 LLMs (primary + standby)
  - "Performance" — 3 LLMs (fast + smart + fallback)
  - "Enterprise" — 4-6 LLMs (specialized roles + HA + cost optimization)
- [ ] Import/export configuration as JSON

### 3.5 Onboarding & Contextual Help
- [ ] Tooltips on all multi-LLM configuration fields
- [ ] "Learn More" links to documentation sections
- [ ] Status bar showing overall system health
- [ ] Guided tour for first-time admin users

---

## Phase 4: Token, Cost & Compliance Management

### 4.1 Token & Cost Tracking
Create `cost_tracker.py`:
- [ ] Hook into `llm_client` response to extract `usage.prompt_tokens` and `usage.completion_tokens`
- [ ] Calculate cost per request using `LLMConfig.cost_per_1k_*` rates
- [ ] Persist to `llm_status.db`: per-LLM, per-department, per-user, per-day
- [ ] `GET /api/costs` — aggregate cost data with filters (date range, LLM, department)
- [ ] `GET /api/costs/breakdown` — detailed per-department per-LLM breakdown

### 4.2 Budget Enforcement
Create `budget_manager.py`:
- [ ] Define budgets: per-LLM daily/monthly, per-department daily/monthly, global monthly
- [ ] Budget actions at thresholds:
  - 80%: warning notification to admin
  - 90%: switch to cheaper LLM
  - 100%: block requests (or fallback to free/local model)
- [ ] `GET /api/budgets` — list all budgets and current usage
- [ ] `PUT /api/budgets/{scope}/{id}` — set/update budget
- [ ] `GET /api/budgets/alerts` — list budget alerts

### 4.3 Compliance & Privacy Controls
Extend routing engine:
- [ ] Define data classification levels: public, internal, confidential, restricted
- [ ] Route by classification: confidential data only goes to `internal` privacy LLMs
- [ ] Compliance modes:
  - `GDPR`: No PII sent to external LLMs
  - `HIPAA`: All data stays on self-hosted LLMs
  - `INTERNAL_ONLY`: Block all external/cloud LLM usage
- [ ] Compliance audit log: record every routing decision with data classification
- [ ] `GET /api/compliance/mode` — current compliance mode
- [ ] `PUT /api/compliance/mode` — set compliance mode
- [ ] `GET /api/compliance/audit` — compliance audit trail

### 4.4 Config Versioning & Backup
Create `config_versioning.py`:
- [ ] Snapshot all config files on every change (connections, routing, llms, rbac, pii)
- [ ] Store in `config_history/` directory with timestamps
- [ ] `GET /api/config/versions` — list config versions
- [ ] `GET /api/config/versions/{id}` — view a specific version
- [ ] `POST /api/config/restore/{id}` — restore to a previous version
- [ ] `GET /api/config/export` — export all config as downloadable JSON
- [ ] `POST /api/config/import` — import config from JSON

---

## Phase 5: Chaos Testing, Simulation & Production Hardening

### 5.1 Simulation Mode
Create `simulation.py`:
- [ ] Simulated LLM endpoints that return canned responses with configurable delays
- [ ] Toggle simulation mode per LLM (test failover without affecting real LLMs)
- [ ] Simulate failure scenarios: timeout, 500 error, slow response, partial failure
- [ ] `POST /api/simulate/fail/{llm_name}` — force-fail an LLM
- [ ] `POST /api/simulate/recover/{llm_name}` — force-recover
- [ ] `POST /api/simulate/degrade/{llm_name}` — simulate high latency

### 5.2 Chaos Testing Framework
Create `chaos_tests.py`:
- [ ] Pre-built scenarios:
  - Scenario 1: Primary goes down → verify failover to secondary
  - Scenario 2: All LLMs go down → verify graceful error
  - Scenario 3: Primary recovers → verify correct re-election behavior
  - Scenario 4: Slow primary (>5s) → verify demotion and routing shift
  - Scenario 5: Budget exceeded → verify routing to fallback
- [ ] `POST /api/chaos/run/{scenario}` — execute a chaos test
- [ ] `GET /api/chaos/results` — view past chaos test results
- [ ] Automated regression suite (run as part of CI)

### 5.3 Load Testing
- [ ] Script to simulate N concurrent users hitting the chat endpoint
- [ ] Verify round-robin and weighted routing under load
- [ ] Verify failover behavior under load
- [ ] Performance benchmarks: requests/second, P50/P95/P99 latency

### 5.4 Production Hardening
- [ ] Graceful degradation when all LLMs are down (cached responses / offline mode)
- [ ] Request queuing when all LLMs are overloaded
- [ ] Connection pooling for LLM HTTP clients (reuse httpx clients)
- [ ] Request timeout escalation (shorter for health checks, longer for generation)
- [ ] Rate limiting per LLM (respect provider rate limits)

---

## Phase 6: Analytics, Auto-Tuning & Next Steps

### 6.1 Analytics & Recommendations
- [ ] Dashboard showing optimal LLM assignment recommendations
- [ ] Identify underused/overused LLMs
- [ ] Cost-per-query trends
- [ ] Quality-per-LLM trends (based on user feedback or response validation)
- [ ] Suggest LLM additions/removals based on usage patterns

### 6.2 Auto-Tuning
- [ ] Automatically adjust routing weights based on real-time performance
- [ ] Learn from episodic memory: which LLM produces best results for which query type
- [ ] A/B testing framework: route % of traffic to new LLM and compare quality

### 6.3 Disaster Recovery
- [ ] Full system state backup (all DBs + config files)
- [ ] One-command restore procedure
- [ ] Document RTO/RPO targets
- [ ] Runbook for common failure scenarios

### 6.4 Future Integrations
- [ ] Webhook notifications (Slack, Teams, email)
- [ ] Prometheus/Grafana metrics export
- [ ] OpenTelemetry tracing for request lifecycle
- [ ] Multi-tenant support (separate LLM pools per tenant)
- [ ] LLM response caching (semantic dedup for identical queries)

---

## Dependency Graph

```
Phase 0 (Bug Fixes)
  └──→ Phase 1 (Foundation)
         ├──→ Phase 2 (Routing & HA)
         │      ├──→ Phase 3 (Admin UI)
         │      └──→ Phase 5 (Testing)
         └──→ Phase 4 (Cost & Compliance)
                └──→ Phase 6 (Analytics)
```

---

## File Impact Map

| File / Module | Phase | Change Type | Description |
|---------------|-------|-------------|-------------|
| `llm_manager.py` | 0, 1, 2 | MODIFY | Fix bugs, add failover chain, circuit breaker, cooldown |
| `llm_health_task.py` | 0, 2 | MODIFY | Fix imports, add adaptive intervals, degradation detection |
| `config.py` | 1 | MODIFY | Extend `LLMConfig` with cost, privacy, priority fields |
| `llm_client/__init__.py` | 1, 2 | MODIFY | Integrate routing engine, fallback chain, usage tracking |
| `llm_client/adapters/` | 1 | NEW | Provider adapter system (OpenAI, Ollama, Anthropic) |
| `routing_engine.py` | 2 | NEW | Full routing policy engine |
| `routing_policies.json` | 2 | NEW | Routing policy configuration |
| `notifications.py` | 2 | NEW | In-app notification system |
| `routers/llm_management.py` | 1, 2 | NEW | LLM CRUD + stats REST API |
| `routers/routing.py` | 2 | NEW | Routing policy REST API |
| `cost_tracker.py` | 4 | NEW | Token/cost tracking and persistence |
| `budget_manager.py` | 4 | NEW | Budget enforcement system |
| `config_versioning.py` | 4 | NEW | Config snapshot and restore |
| `simulation.py` | 5 | NEW | Simulation mode for testing |
| `chaos_tests.py` | 5 | NEW | Chaos testing scenarios |
| `llm_status.db` | 1 | NEW | Persistent LLM metrics database |
| `static/index.html` | 3 | MODIFY | Add 3 new admin modules (LLM Fleet, Routing, Monitoring) |
| `main.py` | 1, 2 | MODIFY | Wire new routers, startup initialization |
| `routers/chat.py` | 2 | MODIFY | Add LLM used + routing info to response metadata |
| `routers/admin_dashboard.py` | 2, 4 | MODIFY | Add notification + cost endpoints |
| `.env.example` | 1 | MODIFY | Add multi-LLM config examples |
| `tests/` | All | NEW | Test files for each phase |

---

## Testing Strategy

### Unit Tests (Each Phase)
- `test_llm_manager.py` — LLM registration, health tracking, failover logic, circuit breaker
- `test_routing_engine.py` — All routing strategies (task-type, capability, cost, privacy, weighted)
- `test_cost_tracker.py` — Token counting, cost calculation, budget enforcement
- `test_adapters.py` — Each LLM adapter's generate/health_check methods
- `test_notifications.py` — Notification creation, severity, dismissal

### Integration Tests
- `test_failover_integration.py` — Full failover flow: register 3 LLMs → fail primary → verify routing shifts
- `test_routing_integration.py` — Full routing flow: configure policy → send queries → verify correct LLM selection
- `test_chat_with_routing.py` — End-to-end chat with multi-LLM routing

### Manual Verification
- Start orchestrator with 2+ LLMs configured
- Verify admin UI shows all LLMs with live status
- Simulate failure and verify failover happens within 30s
- Verify cost tracking shows correct numbers after test queries
- Run chaos test scenarios from admin UI

---

## Risk Matrix

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Breaking existing single-LLM users | Medium | 🔴 High | Phase 0 legacy bridge ensures 100% backward compat |
| LLM provider API differences | High | 🟡 Medium | Adapter pattern isolates provider-specific logic |
| Admin UI file too large (already 219KB) | Medium | 🟡 Medium | Consider splitting into modular JS files in Phase 3 |
| Health checks overwhelming LLM providers | Low | 🟡 Medium | Adaptive intervals, lightweight health endpoints |
| Config versioning disk usage | Low | 🟢 Low | Retention policy (keep last 50 versions) |
| Concurrent failover race conditions | Medium | 🔴 High | Mutex/lock on LLM state transitions |
| Budget enforcement blocking critical queries | Low | 🟡 Medium | Emergency override + fallback to free/local LLM |

---

## Success Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Failover time | < 30 seconds | Time from LLM failure detection to traffic rerouting |
| Config migration | 0 breaking changes | Existing `.env` files work without modification |
| Admin LLM management | < 2 minutes to add new LLM | User testing |
| Routing accuracy | > 95% correct LLM selection | Routing simulation tests |
| Cost tracking accuracy | < 1% error | Compare tracked costs vs provider invoices |
| System uptime with HA | > 99.9% | Monitoring dashboard |
| Test coverage | > 80% for new code | pytest coverage report |

---

*Plan created: April 4, 2026 — Deep analysis of tasks.md + existing codebase*
*Analyzed: 15 source files, 4 routers, 10 query_engine modules, 13 existing tests*
