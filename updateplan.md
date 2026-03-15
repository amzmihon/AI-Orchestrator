# Orchestrator Frontend Update Plan

> **Date:** March 12, 2026  
> **Updated:** March 13, 2026  
> **Goal:** Add 12 admin module pages to the orchestrator SPA dashboard  
> **Status:** ✅ COMPLETE — All 4 Phases Implemented  

---

## 1. Current State

### Backend — ✅ COMPLETE
- `routers/admin_dashboard.py` — 49 endpoints across 12 modules (prefix: `/api/admin/dashboard/`)
- `routers/admin_auth.py` — 6 admin auth endpoints (prefix: `/api/admin/auth/`)
- `admin_db.py` — Independent admin.db with PBKDF2 hashing
- `auth/admin_auth.py` — Dual JWT (orchestrator + ERP bridge)
- `main.py` — All 7 routers wired up

### Documentation — ✅ COMPLETE
- `ORCHESTRATOR_DOCUMENTATION.md` — v2.0.0, 18 sections, all modules documented

### Frontend — ✅ COMPLETE
**File:** `static/index.html` (2,533 lines) — Backup at `index_backup_v4.html`

**Implemented (March 12-13, 2026):**
- ✅ 12 admin module pages (HTML containers + JS API functions)
- ✅ Updated home page with 12 module cards (color-coded)
- ✅ Updated sidebar: 5 sections (Home, AI Config, AI Data, Admin, Tools)
- ✅ Updated `navTo()` routing with 12 new routes (19 total)
- ✅ ~40 new JS functions for all 12 modules
- ✅ 2 new modals (Create Admin User, Add Skill)
- ✅ Auth helper functions (`authHdr()`, `authGet()`)

**Preserved (working, untouched):**
- Login overlay (JWT + username/password auth)
- Toast notification system
- Modal system
- 63 original JS functions for existing features
- All 9 original SPA routes

---

## 2. Existing Routes & Pages (KEEP)

| Route Name | Sidebar Label | Page Function |
|------------|---------------|---------------|
| `home` | Dashboard | Home cards + quick stats |
| `chat` | AI Chat | Chat interface + sessions |
| `assist` | Writing Assistant | Text polishing tool |
| `dashboard` | System Health | Service monitoring |
| `ollama` | Ollama Engine | Model management |
| `litellm` | LiteLLM Proxy | API gateway |
| `users` | Users | User CRUD (proxied) |
| `apikeys` | API Keys | LiteLLM key management |
| `settings` | Settings | Config info |

---

## 3. New Admin Module Pages (ADD)

| # | Route Name | Card Title | Subtitle | Backend Prefix | Key Endpoints |
|---|-----------|------------|----------|----------------|---------------|
| 1 | `connections` | CONNECTIONS | LLM, DB & Auth | `/connections` | GET list, POST create, PUT update, DELETE, POST activate |
| 2 | `schema-map` | SCHEMA MAP | Teach AI Data | `/schema` | GET full, GET table, PUT table desc, PUT column desc, POST ai-describe |
| 3 | `system-prompt` | AI SYSTEM PROMPT | Skills Manager | `/system-prompt`, `/skills` | GET prompt, PUT prompt, GET/POST/DELETE skills |
| 4 | `permissions` | AI PERMISSIONS | Role-to-Table | `/rbac` | GET roles, PUT role, POST toggle table |
| 5 | `intent-tuner` | INTENT TUNER | Question Routing | `/intents` | GET patterns, POST custom, DELETE custom, POST test |
| 6 | `ai-memory` | AI MEMORY | Facts & Episodes | `/memories` | GET list, PUT update, DELETE entry |
| 7 | `security` | SECURITY | PII & Topics | `/pii` | GET/PUT config, POST/DELETE blocked topics, POST test |
| 8 | `data-sync` | DATA SYNC | Staging DB | `/freshness`, `/sync/*` | GET status, GET/PUT schedule, POST force, GET log |
| 9 | `chat-sessions` | CHAT SESSIONS | All Users | `/sessions` | GET list, GET detail, DELETE session |
| 10 | `rag-db` | RAG DB | Database Manage | `/rag` | GET status, POST vacuum, POST clear table |
| 11 | `admin-users` | ADMIN USERS | User Management | `/admin/auth/users` | GET list, POST create, PUT update, DELETE |
| 12 | `log` | LOG | Activity Log | `/logs` | GET logs |

---

## 4. Implementation Strategy

### Phase 1: Structure (Home + Sidebar + Routing)
**Estimated: ~200 lines changed**

- [x] **1a.** Replace 8 home cards with 12 admin module cards
  - Grid: `grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4`
  - Each card: icon (SVG), title, subtitle, status badge, `onclick="navTo('route')"`
  - Keep quick stats row (Orchestrator, Data Freshness, LLM, Main App)

- [x] **1b.** Restructure sidebar nav sections
  - **HOME**: Dashboard
  - **AI ADMIN** (new section): Connections, Schema Map, System Prompt, Permissions, Intent Tuner
  - **AI DATA** (new section): Memory, Security, Data Sync, Chat Sessions, RAG DB
  - **MANAGEMENT**: Admin Users, Log
  - **TOOLS** (existing, collapsed): AI Chat, Writing Assistant, System Health, Ollama, LiteLLM, API Keys, Settings

- [x] **1c.** Update `navTo()` routing
  - Add 12 new `if (page === '...')` loader calls
  - Add 12 new `<div id="page-{route}" class="page hidden">` containers

### Phase 2: Simple Display Pages (Read-Only)
**Estimated: ~400 lines added**

These modules primarily load and display data with minimal interaction:

- [x] **2a.** LOG page — `loadLogs()` → GET `/api/admin/dashboard/logs` → render table
- [x] **2b.** RAG DB page — `loadRagStatus()` → GET `/api/admin/dashboard/rag-status` → stats cards + vacuum/clear buttons
- [x] **2c.** DATA SYNC page — `loadSyncStatus()` → GET freshness + schedule + log → status + force sync button
- [x] **2d.** CHAT SESSIONS page — `loadAllSessions()` → GET `/api/admin/dashboard/all-sessions` → session table with expand/delete

### Phase 3: Editor Pages (Read + Write)
**Estimated: ~800 lines added**

These modules have CRUD forms and interactive editors:

- [x] **3a.** CONNECTIONS page — Multi-tab (LLM/DB/Auth) connection cards + add/edit modal
- [x] **3b.** SCHEMA MAP page — Table list → click → column editor → AI describe button
- [x] **3c.** SYSTEM PROMPT page — Textarea editor + skills list with install/remove
- [x] **3d.** AI PERMISSIONS page — Role list → expandable table matrix with toggles
- [x] **3e.** INTENT TUNER page — Pattern list + custom phrase form + test classifier
- [x] **3f.** AI MEMORY page — Memory table with edit/delete + filter by user
- [x] **3g.** SECURITY page — PII toggle config + blocked topics list + test input
- [x] **3h.** ADMIN USERS page — User table + create modal (uses admin_auth endpoints, NOT admin proxy)

### Phase 4: Verification
- [x] **4a.** Start orchestrator, verify login works
- [x] **4b.** Verify all 12 home cards render and navigate correctly
- [x] **4c.** Verify sidebar navigation for all modules
- [x] **4d.** Test each module page loads data from its API
- [x] **4e.** Test CRUD operations (create/update/delete) on at least 3 modules
- [x] **4f.** Verify existing pages (Chat, Assist, Health) still work

---

## 5. API Endpoint Map (JS Function → Backend)

### Module 1: CONNECTIONS
```
loadConnections()      → GET  /api/admin/dashboard/connections
saveConnection()       → POST /api/admin/dashboard/connections
updateConnection(id)   → PUT  /api/admin/dashboard/connections/{id}
deleteConnection(id)   → DELETE /api/admin/dashboard/connections/{id}
activateConnection(id) → POST /api/admin/dashboard/connections/{id}/activate
```

### Module 2: SCHEMA MAP
```
loadSchema()              → GET  /api/admin/dashboard/schema
loadTableDetail(table)    → GET  /api/admin/dashboard/schema/{table}
saveTableDesc(table)      → PUT  /api/admin/dashboard/schema/{table}
saveColumnDesc(table,col) → PUT  /api/admin/dashboard/schema/{table}/{column}
aiDescribeTable(table)    → POST /api/admin/dashboard/schema/{table}/ai-describe
aiDescribeAll()           → POST /api/admin/dashboard/schema/ai-describe-all
```

### Module 3: SYSTEM PROMPT & SKILLS
```
loadSystemPrompt()    → GET  /api/admin/dashboard/system-prompt
saveSystemPrompt()    → PUT  /api/admin/dashboard/system-prompt
loadSkills()          → GET  /api/admin/dashboard/skills
installSkill()        → POST /api/admin/dashboard/skills
getSkill(name)        → GET  /api/admin/dashboard/skills/{name}
removeSkill(name)     → DELETE /api/admin/dashboard/skills/{name}
```

### Module 4: AI PERMISSIONS
```
loadRbac()                    → GET  /api/admin/dashboard/rbac
saveRolePermissions(role)     → PUT  /api/admin/dashboard/rbac/{role}
toggleTableAccess(role,table) → POST /api/admin/dashboard/rbac/{role}/toggle/{table}
```

### Module 5: INTENT TUNER
```
loadIntents()       → GET  /api/admin/dashboard/intents
addCustomIntent()   → POST /api/admin/dashboard/intents
deleteIntent(id)    → DELETE /api/admin/dashboard/intents/{id}
testIntent()        → POST /api/admin/dashboard/intents/test
```

### Module 6: AI MEMORY
```
loadAdminMemories()    → GET  /api/admin/dashboard/memories
updateMemory(id)       → PUT  /api/admin/dashboard/memories/{id}
deleteAdminMemory(id)  → DELETE /api/admin/dashboard/memories/{id}
```

### Module 7: SECURITY
```
loadPiiConfig()        → GET  /api/admin/dashboard/pii
savePiiConfig()        → PUT  /api/admin/dashboard/pii
addBlockedTopic()      → POST /api/admin/dashboard/pii/blocked-topics
deleteBlockedTopic(i)  → DELETE /api/admin/dashboard/pii/blocked-topics/{index}
testPiiDetection()     → POST /api/admin/dashboard/pii/test
```

### Module 8: DATA SYNC
```
loadFreshnessAdmin()  → GET  /api/admin/dashboard/freshness
loadSyncSchedule()    → GET  /api/admin/dashboard/sync/schedule
saveSyncSchedule()    → PUT  /api/admin/dashboard/sync/schedule
forceSync()           → POST /api/admin/dashboard/sync/force
loadSyncLog()         → GET  /api/admin/dashboard/sync/log
```

### Module 9: CHAT SESSIONS
```
loadAllSessions()       → GET  /api/admin/dashboard/sessions
getSessionDetail(id)    → GET  /api/admin/dashboard/sessions/{id}
deleteAdminSession(id)  → DELETE /api/admin/dashboard/sessions/{id}
```

### Module 10: RAG DB
```
loadRagStatus()   → GET  /api/admin/dashboard/rag
vacuumRag()       → POST /api/admin/dashboard/rag/vacuum
clearRagTable(t)  → POST /api/admin/dashboard/rag/clear/{table}
```

### Module 11: ADMIN USERS
```
loadAdminUsers()       → GET  /api/admin/auth/users
createAdminUser()      → POST /api/admin/auth/users
updateAdminUser(id)    → PUT  /api/admin/auth/users/{id}
deleteAdminUser(id)    → DELETE /api/admin/auth/users/{id}
```

### Module 12: LOG
```
loadLogs()  → GET  /api/admin/dashboard/logs
```

---

## 6. UI Design Spec

### Design System (existing, follow exactly)
- **Font:** Inter (Google Fonts)
- **Primary:** `indigo-600` / `indigo-700` hover
- **Background:** `bg-gray-50`
- **Cards:** `bg-white rounded-2xl shadow-sm hover:shadow-md transition-shadow`
- **Buttons:** `bg-indigo-600 text-white rounded-lg px-4 py-2 hover:bg-indigo-700`
- **Danger:** `bg-red-600 hover:bg-red-700`
- **Tables:** `min-w-full divide-y divide-gray-200` with `hover:bg-gray-50` rows
- **Inputs:** `border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500`
- **Badges:** `text-xs font-medium px-2.5 py-0.5 rounded-full`
  - Green: `bg-green-100 text-green-800`
  - Yellow: `bg-yellow-100 text-yellow-800`
  - Red: `bg-red-100 text-red-800`
  - Blue: `bg-blue-100 text-blue-800`

### Page Layout Pattern
```html
<div id="page-{route}" class="page hidden">
  <!-- Header -->
  <div class="flex items-center justify-between mb-6">
    <div>
      <h1 class="text-2xl font-bold text-gray-900">{Title}</h1>
      <p class="text-sm text-gray-500 mt-1">{Subtitle}</p>
    </div>
    <button onclick="{action}()" class="bg-indigo-600 ...">{Action Button}</button>
  </div>
  
  <!-- Content -->
  <div class="bg-white rounded-2xl shadow-sm p-6">
    <div id="{route}-content">
      <p class="text-gray-400">Loading...</p>
    </div>
  </div>
</div>
```

### Home Card Pattern
```html
<div onclick="navTo('{route}')" class="bg-white rounded-2xl shadow-sm p-6 hover:shadow-md transition-shadow cursor-pointer">
  <div class="flex items-center gap-3 mb-3">
    <div class="w-10 h-10 bg-{color}-100 rounded-xl flex items-center justify-center">
      {SVG icon}
    </div>
    <div>
      <h3 class="font-semibold text-gray-900">{Title}</h3>
      <p class="text-xs text-gray-500">{Subtitle}</p>
    </div>
  </div>
  <div id="stat-{route}" class="text-xs text-gray-400">Loading...</div>
</div>
```

---

## 7. File Size Estimate

| Component | Current Lines | Added Lines | Notes |
|-----------|--------------|-------------|-------|
| HTML structure | 400 | +750 | 12 page containers + sidebar + modals |
| Home cards | 80 | +60 | Replaced 8 → 12 cards |
| Phase 2 pages (simple) | 0 | +400 | 4 display pages |
| Phase 3 pages (editors) | 0 | +800 | 8 interactive pages |
| JS functions | 500 | +650 | ~40 new API functions + helpers |
| **Total** | **1,388** | **~2,533** | **2,533 lines actual** |

---

## 8. Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Breaking existing pages | Touch only navTo() routing, sidebar, and home section. All existing page divs/functions untouched. |
| File too large | Single index.html is the established pattern. Keep it. Alternative (splitting JS) adds complexity without value. |
| Backend endpoint mismatch | All endpoints verified from admin_dashboard.py source. Plan includes exact paths. |
| Auth token issues | Admin modules use same `TOKEN` variable as existing pages. `get()` helper already handles Bearer auth. |
| Inconsistent UI | Every new page follows the exact same Tailwind patterns documented in Section 6 above. |

---

## 9. Execution Order

```
Step 1: Backup current index.html → index_backup_v4.html
Step 2: Phase 1 — Home cards + Sidebar + Routing (structural changes)
Step 3: Phase 2 — Simple display pages (Log, RAG DB, Data Sync, Chat Sessions)
Step 4: Phase 3 — Editor pages (Connections, Schema, Prompt, Permissions, Intent, Memory, Security, Admin Users)
Step 5: Phase 4 — Verification (start server, test each page)
```

**Completed:** 2,533 lines total file, ~1,145 new lines across 4 phases.

---

*Plan created: March 12, 2026 — ATL-AI Orchestrator v2.0.0 Frontend Update*  
*Updated: March 13, 2026 — All phases complete. Bugs fixed: loadSkills endpoint, skills GET/DELETE extension fallback, PII Security UI (config rendering + blocked topics name/pattern fields).*  
*CRUD verified: Module 3 (System Prompt + Skills), Module 7 (PII Security), Module 11 (Admin Users) — all pass.*
