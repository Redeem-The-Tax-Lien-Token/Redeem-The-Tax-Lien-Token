# Wholesale & BRRRR Agentic System

A self-hosted, multi-agent platform for wholesale real estate and BRRRR investing in Indianapolis, Indiana. Seven independent FastAPI agents share one PostgreSQL database and communicate via authenticated HTTP.

---

## Architecture

```
Claude Code (dev) → GitHub (monorepo) → Replit (runtime, 7 Repls)
                                            ↓
                              Replit PostgreSQL (shared DB)
```

All agents are standalone FastAPI apps. No agent imports another agent's code — they call each other's `/run` endpoint over HTTP using an `X-Internal-Key` header. The monorepo exists so the DB schema and shared utilities have a single source of truth.

---

## The Seven Agents

| # | Folder | Role |
|---|--------|------|
| 1 | `agents/list-puller` | Query ATTOM API for distressed/motivated seller lists by zip |
| 2 | `agents/skip-tracer` | Send records to Tracerfy, retrieve owner phones/emails, DNC scrub |
| 3 | `agents/outreach` | Send personalized SMS via Twilio, manage multi-touch cadences |
| 4 | `agents/intake` | Receive Twilio inbound webhooks, classify replies (HOT/WARM/COLD/DNC) |
| 5 | `agents/arv-mao` | Pull ATTOM comps, calculate ARV, run MAO formula, return offer |
| 6 | `agents/deal-marketing` | Generate deal package on signed PSA, blast segmented buyer list |
| 7 | `agents/crm-dashboard` | Operator-facing web CRM — pipeline, deals, buyers, manual SMS |

**Build order** (dependency-driven, not workflow order): `arv-mao` → `intake` → `list-puller` → `skip-tracer` → `outreach` → `deal-marketing` → `crm-dashboard`.

---

## Key Formulas

| Formula | Calculation |
|---------|-------------|
| Wholesale MAO | `(ARV × 70%) − Repairs − $10,000 assignment fee` |
| BRRRR Max Purchase | `(ARV × 75%) − Repair Costs` |
| 1% Rule (quick filter) | Monthly Rent ≥ 1% of Purchase Price |

---

## Database

Six PostgreSQL tables, all provisioned by a single migration file:

| Table | Purpose |
|-------|---------|
| `leads` | Central CRM record per property/seller |
| `outreach_log` | Every SMS/email sent and received |
| `lead_scores` | Intake Agent's Claude classification per reply |
| `deals` | ARV runs, MAO calculations, offer and PSA tracking |
| `buyers` | Cash buyer database, segmented by buy-box and tier |
| `agent_events` | Audit log of every inter-agent HTTP call |

Schema lives at `shared/schema/migrate.sql`. Never edit it directly in an agent folder.

---

## Repository Layout

```
.
├── .github/workflows/ci.yml       # Lint + tests on every PR
├── .gitignore
├── .env.example                   # Key names only — values go in Replit Secrets
├── shared/
│   ├── db.py                      # SQLAlchemy engine, session helpers
│   ├── schema/migrate.sql         # Authoritative DDL (tables, indexes, triggers)
│   └── compliance/disclosures.py  # HB 1068 + TCPA opt-out language
├── agents/
│   ├── arv-mao/
│   ├── list-puller/
│   ├── skip-tracer/
│   ├── intake/
│   ├── outreach/
│   ├── deal-marketing/
│   └── crm-dashboard/
├── db-bootstrap/
│   ├── migrate.py                 # One-shot migration runner
│   └── migrate.sql                # Symlink → ../shared/schema/migrate.sql
├── docs/
│   └── master-context-v1.4.md
└── tests/smoke/end_to_end.py
```

---

## Shared Utilities Pattern

Replit does not natively resolve imports across sibling folders. Use the `build_repl.sh` script to copy `shared/` into each agent folder before committing a deploy push:

```bash
bash build_repl.sh agents/arv-mao
```

This copies `shared/` → `agents/arv-mao/shared/` so `from shared.db import get_db` works inside the Repl without any path hacks.

---

## Bootstrapping the Database

Run this **once** after provisioning Replit PostgreSQL:

```bash
pip install sqlalchemy psycopg2-binary
DATABASE_URL="postgresql://..." python db-bootstrap/migrate.py
```

Expected output:

```
[1/3] Connecting to database …
[2/3] Executing migration SQL …
      Migration SQL executed successfully.
[3/3] Verifying tables …
      [OK]  agent_events
      [OK]  buyers
      [OK]  deals
      [OK]  lead_scores
      [OK]  leads
      [OK]  outreach_log

All 6 tables verified. Database is ready.
```

---

## Standard Endpoint Contract

Every agent exposes the same three routes:

| Route | Description |
|-------|-------------|
| `GET /` | Health check — `{"status": "ok", "agent": "<name>"}` |
| `POST /run` | Main action; body is agent-specific; requires `X-Internal-Key` header |
| `GET /version` | Returns git commit hash for verifying which build is live |

---

## Environment Variables

All secrets live in Replit Secrets (never in `.env` files in the repo). See `.env.example` at the repo root for the full list of required keys per agent.

| Variable | Used By |
|----------|---------|
| `DATABASE_URL` | All agents (auto-injected by Replit PostgreSQL) |
| `INTERNAL_API_KEY` | All agents (X-Internal-Key header validation) |
| `ANTHROPIC_API_KEY` | arv-mao, intake |
| `CLAUDE_MODEL` | arv-mao, intake (default: `claude-sonnet-4-20250514`) |
| `ATTOM_API_KEY` | arv-mao, list-puller |
| `TRACERFY_API_KEY` | skip-tracer, outreach |
| `TWILIO_ACCOUNT_SID` | outreach, intake, deal-marketing |
| `TWILIO_AUTH_TOKEN` | outreach, intake, deal-marketing |
| `TWILIO_FROM_NUMBER` | outreach, intake, deal-marketing |

---

## Deploying an Agent to Replit

1. Create Repl → **Import from GitHub** → point to `agents/<agent-name>/`
2. Add all required secrets in the Replit Secrets panel
3. Run `build_repl.sh` first if `shared/` utilities are needed
4. Enable **Always-On** for `intake` (webhook receiver)
5. Set Twilio webhook URL to `https://<intake-repl-url>/sms/inbound`

---

## Legal Compliance

- **Indiana HB 1068**: Written wholesaler disclosure required in every PSA. See `shared/compliance/disclosures.py`.
- **TCPA / A2P 10DLC**: Register brand + campaign in Twilio before any SMS. DNC scrub is fail-closed (no scrub = no send). No messages 9 PM – 8 AM local time.
- **No license required** to wholesale in Indiana, but you market contract rights only — never the physical property.

---

## Master Reference Document

Full business logic, all API references (ATTOM, Tracerfy, Twilio), formulas, compliance details, and build phases: `docs/master-context-v1.4.md`.
