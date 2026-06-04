# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A self-hosted, multi-agent real estate wholesaling system targeting Indianapolis, IN. Seven independent FastAPI agents share one PostgreSQL database and communicate via HTTP with a shared `X-Internal-Key` secret.

## Commands

### Syntax check (CI equivalent)
```bash
python -m compileall -q shared/ db-bootstrap/ agents/*/
```

### Validate migration SQL (all 6 tables present)
```bash
python -c "
import pathlib
sql = pathlib.Path('shared/schema/migrate.sql').read_text()
for t in ['leads','outreach_log','lead_scores','deals','buyers','agent_events']:
    assert f'CREATE TABLE IF NOT EXISTS {t}' in sql, f'Missing: {t}'
print('OK')
"
```

### Bootstrap the database (run once after provisioning Postgres)
```bash
DATABASE_URL="postgresql://..." python db-bootstrap/migrate.py
```
Script is idempotent — safe to re-run. Verifies all 6 tables after applying DDL.

### Run any agent locally
```bash
cd agents/<agent-name>
pip install -r requirements.txt
DATABASE_URL="..." INTERNAL_API_KEY="..." python -m uvicorn main:app --reload --port 8000
```

### Prepare an agent folder for Replit deploy
```bash
bash build_repl.sh agents/<agent-name>
```
Copies `shared/` into the agent folder so Replit's single-subfolder import works. Run before committing a deploy branch.

## Architecture

### Monorepo layout
```
shared/          ← imported by every agent (copied in at build time for Replit)
  db.py          ← SQLAlchemy engine + session helpers
  schema/        ← migrate.sql (canonical DDL, source of truth)
  compliance/    ← TCPA/HB 1068 disclosure constants
db-bootstrap/    ← one-shot migration runner
agents/
  arv-mao/       ← ATTOM comps + Claude ARV/MAO calculator
  intake/        ← Twilio inbound webhook + Claude reply classifier
  list-puller/   ← ATTOM distressed-property list importer
  skip-tracer/   ← Tracerfy owner lookup + DNC scrub
  outreach/      ← 3-touch SMS cadence sender
  deal-marketing/← PSA blast to buyer list
  crm-dashboard/ ← Human UI (FastAPI + Jinja2, session auth)
```

### `shared/` modules
- **`db.py`**: `get_db()` (FastAPI Depends), `session_ctx()` (scripts), `get_lead()`, `update_lead_status()`, `log_agent_event()`. Pool settings: `pool_pre_ping=True`, `pool_recycle=3600` (handles Replit 5-min sleep).
- **`schema/migrate.sql`**: 6 tables, 14 indexes, 4 triggers. Never define schema anywhere else.
- **`compliance/disclosures.py`**: `build_sms_message()` appends opt-out footer. `TCPA_QUIET_HOURS` = 08:00–21:00. `INDIANA_HB1068_DISCLOSURE` required in every PSA.

### Database triggers (critical — enforced at DB layer)
- `set_updated_at` — auto-stamps `updated_at` on leads and deals
- `block_outbound_to_dnc` — RAISES EXCEPTION on any outbound SMS to a DNC lead (TCPA fail-closed)
- `sync_dnc_to_lead` — auto-flips lead.status → 'dnc' when lead_scores.classification = 'DNC'

### Agent-to-agent calls
All calls are `POST /run` with header `X-Internal-Key: $INTERNAL_API_KEY`. Every call is logged to `agent_events` via `log_agent_event()`. No agent talks to another's database directly.

### Import path pattern
Every `main.py` inserts both paths into `sys.path`:
```python
_here = Path(__file__).parent
_repo_root = _here.parent.parent
sys.path.insert(0, str(_here))       # after build_repl.sh copies shared/
sys.path.insert(0, str(_repo_root))  # for local monorepo dev
```

## Business Logic

### Formulas
```
Wholesale MAO  = (ARV × 0.70) − Repair Estimate − $10,000
BRRRR Max Buy  = (ARV × 0.75) − Repair Costs
```

### ARV comps methodology
Primary: 0.5 mi radius, 6-month window. Auto-expand to 1 mi / 12 months if < 3 comps found.

### Lead status state machine
```python
ALLOWED_TRANSITIONS = {
    "new":            {"traced","contacted","hot","warm","cold","dnc","dead"},
    "traced":         {"contacted","hot","warm","cold","dnc","dead"},
    "contacted":      {"hot","warm","cold","dnc","dead"},
    "hot":            {"under_contract","warm","cold","dead","dnc"},
    "warm":           {"hot","cold","dead","dnc"},
    "cold":           {"warm","dead","dnc"},
    "under_contract": {"closed","dead"},
    "dnc":            set(),   # terminal
    "closed":         set(),   # terminal
    "dead":           set(),   # terminal
}
```
Force-override (CRM only): stamps `manual_override_at` / `manual_override_by` and bypasses transition guard.

### DNC scrub (skip-tracer + outreach)
Tracerfy `scrub_phones()` is **fail-closed**: any exception returns an empty set, treating all phones as DNC. Two-phase pattern: run all instant traces first, then one batch DNC scrub at the end.

### Outreach 3-touch cadence
- Touch 1: status='traced', 0 prior outbound
- Touch 2: status='contacted', exactly 1 outbound, no inbound, last send ≥ 3 days
- Touch 3: status='contacted', exactly 2 outbound, no inbound, last send ≥ 7 days

### TCPA quiet hours
`America/Indiana/Indianapolis` via `zoneinfo`. No SMS before 08:00 or after 21:00 local. Gate is the first check in every outreach function — returns immediately with `skipped_quiet_hours=true` if outside window.

### Indiana HB 1068
Wholesaler disclosure (`INDIANA_HB1068_DISCLOSURE`) is required in every PSA. Deal-marketing agent sends city+zip but **never the street address** publicly — only to the pre-built private buyer list.

## Required Environment Variables

| Variable | Used by |
|---|---|
| `DATABASE_URL` | all agents |
| `INTERNAL_API_KEY` | all agents (inter-agent auth header) |
| `ANTHROPIC_API_KEY` | arv-mao, intake |
| `CLAUDE_MODEL` | arv-mao, intake (default: `claude-sonnet-4-20250514`) |
| `ATTOM_API_KEY` | arv-mao, list-puller |
| `TRACERFY_API_KEY` | skip-tracer, outreach, deal-marketing |
| `TWILIO_ACCOUNT_SID` | intake, outreach, deal-marketing, crm-dashboard |
| `TWILIO_AUTH_TOKEN` | intake, outreach, deal-marketing, crm-dashboard |
| `TWILIO_FROM_NUMBER` | outreach, deal-marketing, crm-dashboard |
| `SESSION_SECRET_KEY` | crm-dashboard only |
| `TWILIO_VALIDATE_SIGNATURE` | intake (default `true`; set `false` for local dev) |
| `ARV_MAO_URL`, `INTAKE_URL`, etc. | filled after each Repl is deployed |

Generate secrets: `python -c "import secrets; print(secrets.token_hex(32))"`

## CI

`.github/workflows/ci.yml` runs on push/PR to `main`:
1. `python -m compileall -q shared/ db-bootstrap/` — syntax check
2. Python script asserting all 6 `CREATE TABLE IF NOT EXISTS` statements exist in `migrate.sql`

No test suite exists yet — CI is lint + schema validation only.

## Deployment Order

Build and deploy agents in this order (dependency-driven):
1. `arv-mao` → 2. `intake` → 3. `list-puller` → 4. `skip-tracer` → 5. `outreach` → 6. `deal-marketing` → 7. `crm-dashboard`

After all 7 are deployed, fill in the `*_URL` env vars in each agent's Replit Secrets panel.
