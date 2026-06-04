# WHOLESALE REAL ESTATE & BRRRR AGENTIC SYSTEM
## MASTER CONTEXT & REFERENCE DOCUMENT
Claude Project Knowledge Base | Version 1.4 — Claude Code + GitHub + Replit workflow | 2026

---

## WHAT'S NEW IN v1.4

Version 1.4 migrates the build workflow from "prompt Replit Agent" to a professional pipeline: Claude Code writes production code locally, GitHub holds the source of truth as a private monorepo, and Replit becomes the runtime environment that imports each agent from a GitHub subfolder.

Changes are surgical and limited to:
- Section 1.4 — Project Instructions rewritten for the new workflow
- Section 3.2 — Agent Architecture updated to Claude Code → GitHub → Replit
- Section 8.1 — Tech Stack table updated to separate dev environment from runtime
- Section 8.2 — Repl structure replaced with monorepo structure
- Section 10 — New Phase 0 added for repo and CI foundation
- Section 11.3 — Change log updated

Unchanged (still authoritative):
- Section 2 — Business logic, MAO/ARV formulas, repair categories
- Section 4 — ATTOM API reference
- Section 5 — Tracerfy API reference
- Section 6 — Twilio + A2P 10DLC + TCPA compliance
- Section 7 — Indiana legal compliance (HB 1068)
- Section 9 — Agent system prompts
- Section 11.1–11.2 — Quick reference formulas and API base URLs

---

# 1. CLAUDE PROJECTS — BEST PRACTICES

## 1.1 What Claude Projects Are

Claude Projects are persistent, self-contained workspaces with their own chat histories, knowledge bases, and custom instructions. Every file uploaded to the Project's knowledge base is available in every conversation — no re-explaining context each time. This document IS that knowledge base.

In v1.4, this document is also committed to the GitHub monorepo at docs/master-context-v1.4.md so Claude Code reads it on every session. Both consumers (this Claude Project and Claude Code) work from the same source of truth.

## 1.2 How to Get the Best Results

| Practice | Why It Matters |
|---|---|
| Short, specific instructions | Claude responds better to direct rules than long prose. "Use Python + FastAPI" beats a paragraph of justification. |
| Keep this doc in BOTH places | Upload to Claude Project for chat sessions, commit to GitHub for Claude Code. Update both when the doc changes. |
| One project per system | Each Claude Project has its own memory. Keep the wholesale/BRRRR system in one dedicated project. |
| Update when things change | Stale docs produce stale outputs. Update this document when MAO formula, stack, or market changes — and increment the version number. |
| Ask Claude to plan before coding | For any non-trivial build, ask for a plan first. Review it, then approve execution. Prevents wasted cycles in Claude Code. |
| Use handoff summaries | At end of long sessions, ask Claude to summarize what was built, what works, and what's next. Save the summary in the repo's docs/ folder. |

## 1.3 Document Types to Keep in This Project

- This master context file (always current, versioned)
- Agent-specific prompt files (one per agent)
- Database schema (shared/schema/migrate.sql from the monorepo)
- Sample monorepo structure and scaffolding decisions
- API response examples (ATTOM, Tracerfy, Twilio)
- Closed deal examples with real numbers
- Compliance checklist (updated quarterly)

## 1.4 Project Instructions (paste into Claude Project Settings) — UPDATED IN v1.4

You are an expert real estate wholesaler, BRRRR investor, and agentic systems developer. This project builds an AI agent system for wholesale real estate and BRRRR investing.

Development environment: Claude Code (writes all production code locally)
Source control: GitHub (private monorepo, one repo for all 7 agents)
Runtime environment: Replit (each agent is a separate Repl, imported from a GitHub subfolder)
Tech stack: Python 3.11+, FastAPI, Replit PostgreSQL, Claude API, Twilio (SMS), ATTOM (property data), Tracerfy (skip trace)

Architectural principles (non-negotiable):
- Zero vendor lock-in: code must run identically on Replit, Railway, Render, or local Docker
- Monorepo with shared utilities — one source of truth for DB schema and connection logic
- Each agent is a standalone FastAPI app, communicating via HTTP with an X-Internal-Key shared secret
- The CRM is fully self-hosted — no GoHighLevel, no REsimpli, no Podio
- We do NOT negotiate with sellers. We make offers only. Sellers accept or decline.

Always write production-quality Python code with error handling. Always reference uploaded project files before answering questions about stack, formulas, or processes. When building agents, follow the agent specs in this document exactly.

---

# 2. BUSINESS LOGIC & FORMULAS

UNCHANGED FROM v1.3. This section contains the formulas that drive every offer. Do not modify without explicit version bump and review.

## 2.1 Core Business Model

| Item | Value |
|---|---|
| Primary Strategy | Wholesale Real Estate + BRRRR |
| Wholesale Model | Make offer → Get under contract → Assign to cash buyer for fee |
| BRRRR Model | Buy distressed → Rehab → Rent → Refinance → Repeat |
| Offer Policy | NO negotiation. Single offer sent. Seller accepts or declines. |
| Base Market | Indianapolis, Indiana (and surrounding markets) |
| Target Properties | Single-family, distressed, motivated sellers, off-market |
| Assignment Fee Target | $10,000 (range: $5,000 – $25,000) |
| Primary Data Source | ATTOM API |
| Skip Trace Provider | Tracerfy API |
| SMS Platform | Twilio API |
| Build Environment (v1.4) | Claude Code (local) → GitHub (monorepo) → Replit (runtime) |

## 2.2 MAO Formula — Wholesale

MAO = (ARV x 70%) - Estimated Repairs - Assignment Fee

Example:
  ARV:              $200,000
  x 70%:            $140,000
  - Repairs:        -$30,000
  - Assignment Fee: -$10,000
  = MAO:            $100,000

The 70% breaks down as: 10% buyer costs + 20% buyer profit = 30% discount off ARV.
Competitive A/B markets: use 75%-80%. Uncertain/rural markets: use 65%.

## 2.3 MAO Formula — BRRRR

Max Purchase Price = (ARV x 75%) - Repair Costs

Target: All-in cost (purchase + rehab + closing + holding) <= 75% of ARV
Refinance target: 75% LTV refi should return most or all capital invested

Key BRRRR metrics to track per deal:
- All-in cost (purchase + rehab + closing + holding)
- Stabilized monthly rent
- NOI (Net Operating Income)
- DSCR (must be >= 1.20 for most lenders)
- Cash-out amount after refi
- Cash left in deal after refi
- Cash-on-cash return
- Equity multiple

1% Rule quick filter: Monthly rent >= 1% of purchase price (e.g., $1,000/mo on $100K purchase)

## 2.4 ARV Methodology

- Pull sold comps within 0.5-mile radius (expand to 1 mile if <3 comps)
- Comps must be same property type (SFR to SFR)
- Sold within last 6 months (expand to 12 if market thin)
- Within 20% of subject property square footage
- Same bed/bath count or within +/-1
- Weight closest, most recent comps most heavily
- Output: Low / Mid / High ARV with confidence score
- Use ATTOM /property/v2/SalesComparables endpoint for comp pulls

## 2.5 Repair Estimate Categories

| Category | Items / Typical Range |
|---|---|
| Cosmetic | Paint, flooring, fixtures, landscaping — $5K – $20K |
| Functional | HVAC, plumbing, electrical, roof — $10K – $40K |
| Structural | Foundation, framing, load-bearing walls — $20K – $80K+ |
| Full Gut | All of the above + kitchen + baths — $40K – $100K+ |

---

# 3. MVP AGENT SYSTEM (START HERE)

## 3.1 The 6 Core Agents to Build First

| Agent | Primary Function |
|---|---|
| 1. List Puller | Query ATTOM API for distressed/motivated seller lists by zip, filter by equity, ownership duration, property type |
| 2. Skip Tracer | Send property records to Tracerfy API, retrieve owner phone numbers and emails, append to CRM |
| 3. Outreach Agent | Send personalized SMS via Twilio to owner contacts, manage multi-touch follow-up cadences |
| 4. Intake Agent | Receive inbound Twilio webhook replies, classify response (Hot/Warm/Cold/DNC), create CRM record |
| 5. ARV/MAO Agent | Pull ATTOM comps for subject property, calculate ARV, run MAO formula, return offer number |
| 6. Deal Marketing Agent | Generate deal package when PSA signed, blast segmented buyer list via Twilio/email |

Note on build order: although the agents are NUMBERED in business-workflow order above, the BUILD ORDER is dependency-driven, not workflow-driven. See Section 10 (Phase 1) for the actual build sequence — ARV/MAO is built first because it has no database write dependencies and validates the ATTOM + Claude integration.

## 3.2 Agent Architecture: Claude Code → GitHub → Replit — UPDATED IN v1.4

Development flow:
1. Claude Code writes production code in a local monorepo
2. Push to GitHub (private repo, branch per agent, squash-merge PRs)
3. Replit imports each agent folder as a separate Repl via GitHub integration
4. All 7 Repls share one Replit PostgreSQL database (DATABASE_URL auto-injected)
5. Agents communicate via HTTP using internal Replit URLs + X-Internal-Key header

## 3.3 Shared Database Schema (Replit PostgreSQL)

Core tables for the MVP CRM. Full DDL lives in shared/schema/migrate.sql in the monorepo. db-bootstrap/migrate.py runs it once to provision the database.

- leads (id, address, city, state, zip, owner_name, phone, email, attom_id, motivation_type, equity_pct, list_source, skip_traced_at, dnc_checked, status, created_at, updated_at)
- outreach_log (id, lead_id, message, channel, direction, status, twilio_sid, from_number, to_number, sent_at)
- lead_scores (id, lead_id, score, classification, raw_reply, reason, classified_at)
- deals (id, lead_id, arv_low, arv_mid, arv_high, arv_confidence, repair_estimate, mao, offer_amount, offer_sent_at, psa_signed_at, assignment_fee, status, closed_at, notes, created_at)
- buyers (id, name, phone, email, buy_box_min, buy_box_max, areas, strategy, tier, last_active_at, created_at)
- agent_events (id, source_agent, target_agent, payload, response, status_code, created_at)

## 3.4 The 7th Repl — Self-Hosted CRM Dashboard

The six MVP agents handle the data. The 7th Repl is your operator-facing CRM dashboard — a web app you log into to view leads, deals, send manual messages, and monitor the pipeline. This is what replaces GoHighLevel, REsimpli, Podio, etc.

Because all 7 Repls share the same Replit PostgreSQL database, the dashboard always shows real-time data from your agents.

The 7th Repl: CRM Dashboard
Repl name:       crm-dashboard
Stack:           FastAPI + Jinja2 templates (HTML)
Database:        Same Replit PostgreSQL as the agents (DATABASE_URL)
Authentication:  Session-based bcrypt login (you only, or your team)

Core dashboard views:
- Leads pipeline (new → traced → contacted → hot → under contract)
- Deal view (ARV, MAO, repairs, offer status, assignment fee)
- Buyer database (segmented by buy box, tier, last activity)
- Outreach log (every SMS sent and received with classification)
- KPI dashboard (leads per week, conversion rates, revenue per deal)
- Manual SMS sender (send one-off messages outside automation)

State machine integrity (non-negotiable):
- ALLOWED_TRANSITIONS dict enforces valid status changes
- Force-override checkbox required for invalid transitions, with logging
- manual_override_at and manual_override_by stamps tell agents to back off

---

# 4. ATTOM API — INTEGRATION REFERENCE

UNCHANGED FROM v1.3.

## 4.1 Authentication

All requests require your API key passed as a request header. Never expose in client-side code.

```python
headers = {
    "apikey": "YOUR_ATTOM_API_KEY",
    "Accept": "application/json"
}

BASE_URL = "https://api.gateway.attomdata.com/propertyapi/v1.0.0"
V2_URL   = "https://api.gateway.attomdata.com/property/v2"
```

## 4.2 Key Endpoints for This System

| Endpoint | Use Case |
|---|---|
| /property/detail | Full property characteristics: beds, baths, sqft, year built, owner info, loan data |
| /property/snapshot | Quick summary of properties by city/zip — used for list building |
| /attomavm | Automated Valuation Model — quick ARV estimate with confidence score |
| /v2/SalesComparables | Pull sold comps by address with radius, date range, bed/bath filters — primary ARV source |
| /sale/snapshot | Recent sales history in a zip code — market trend analysis |
| /assessment | Tax assessed value and property tax data |
| /owner/basicprofile | Owner name, mailing address, length of ownership |
| /preforeclosure | Properties in pre-foreclosure — motivated seller list source |
| /property/search | Filter properties by zip, equity, tax delinquency, vacancy flags |

## 4.3 Sales Comparables Example (Python)

```python
import requests

def get_comps(address, city, state, zip_code, radius=0.5, months=6, max_comps=10):
    url = f'{V2_URL}/SalesComparables/Address/{address}/{city}/-/{state}/{zip_code}'
    params = {
        'searchType': 'Radius',
        'miles': radius,
        'saleDateRange': months,
        'maxComps': max_comps,
        'ownerOccupied': 'Both',
        'distressed': 'IncludeDistressed'
    }
    response = requests.get(url, headers=headers, params=params)
    return response.json()
```

## 4.4 Property Detail Example (Python)

```python
def get_property_detail(address1, address2):
    url = f'{BASE_URL}/property/detail'
    params = {'address1': address1, 'address2': address2}
    response = requests.get(url, headers=headers, params=params)
    return response.json()
```

## 4.5 ATTOM Response Structure

Successful responses return HTTP 200 with JSON body. Key fields to extract:

| Field Path | Description |
|---|---|
| property[0].summary.proptype | Property type: SFR, Condo, MF |
| property[0].building.rooms.beds | Bedroom count |
| property[0].building.rooms.bathstotal | Total bathroom count |
| property[0].building.size.universalsize | Square footage |
| property[0].summary.yearbuilt | Year built |
| property[0].avm.amount.value | AVM estimated value |
| property[0].owner.owner1.fullname | Owner name |
| property[0].assessment.assessed.assdttlvalue | Tax assessed value |
| property[0].sale.amount.saleamt | Last sale price |
| property[0].sale.amount.saledisclosuretype | Sale type (arms-length, foreclosure, etc.) |

Error handling: only 200-code responses count against monthly quota. Always check status.code in response body (0 = success).

---

# 5. TRACERFY API — SKIP TRACE REFERENCE

UNCHANGED FROM v1.3.

Important: The skip trace tool is 'Tracerfy' (tracerfy.com), NOT 'Tracify' (tracify.ai) which is an unrelated e-commerce attribution platform. Always reference tracerfy.com for docs and support.

## 5.1 Authentication

```python
TRACERFY_BASE = "https://app.tracerfy.com/api"
headers = {
    "Authorization": "Bearer YOUR_TRACERFY_API_KEY",
    "Content-Type": "application/json"
}
```

## 5.2 Key Endpoints

| Endpoint | Description |
|---|---|
| POST /v1/trace/instant | Synchronous single-address lookup. Returns immediately. 5 credits/hit, 0 on miss. Rate limit: 500 RPM. |
| POST /v1/queues | Batch skip trace. Upload list, get queue_id, webhook fires on completion. |
| GET /v1/queues | List all trace jobs and their status. |
| POST /v1/dnc-scrub | DNC scrub: Federal DNC, State DNC, DMA, TCPA Litigator databases. 1 credit/phone. |

## 5.3 Instant Trace Example (Python)

```python
def skip_trace_single(address, city, state):
    url = f'{TRACERFY_BASE}/v1/trace/instant'
    payload = {
        'address': address,
        'city': city,
        'state': state,
        'find_owner': True
    }
    response = requests.post(url, headers=headers, json=payload)
    return response.json()
```

## 5.4 Response Fields

| Field | Description |
|---|---|
| name | Owner full name |
| age | Owner age |
| deceased | Boolean — skip if True |
| litigator_flag | Boolean — skip if True (TCPA litigator risk) |
| phones[].number | Phone number |
| phones[].dnc_status | Boolean — skip if True |
| phones[].carrier | Carrier name (Verizon, AT&T, etc.) |
| phones[].type | mobile / landline / voip |
| phones[].rank | 1 = best/most current |
| emails[].address | Email address |
| mailing_address | Owner mailing address (may differ from property) |

## 5.5 DNC Scrub Before Every SMS Campaign

CRITICAL: Always run DNC scrub on all phone numbers before sending any SMS campaign. Skip any number where dnc_status = true or litigator_flag = true. DNC scrub is FAIL-CLOSED: if the API errors, treat all phones as DNC.

```python
def scrub_phones(phone_list):
    url = f'{TRACERFY_BASE}/v1/dnc-scrub'
    payload = {'phones': phone_list}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return [p for p in response.json()['results'] if not p['is_dnc']]
    except Exception:
        return []  # Fail closed — no scrub means no send
```

---

# 6. TWILIO SMS API — INTEGRATION REFERENCE

UNCHANGED FROM v1.3.

## 6.1 Authentication & Setup

```python
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse

TWILIO_ACCOUNT_SID = os.environ['TWILIO_ACCOUNT_SID']
TWILIO_AUTH_TOKEN  = os.environ['TWILIO_AUTH_TOKEN']
TWILIO_FROM_NUMBER = os.environ['TWILIO_FROM_NUMBER']  # Your 10DLC number

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
```

## 6.2 Send Outbound SMS

```python
def send_sms(to_number: str, message: str) -> str:
    msg = client.messages.create(
        body=message,
        from_=TWILIO_FROM_NUMBER,
        to=to_number
    )
    return msg.sid  # Store in outreach_log table
```

## 6.3 Inbound Webhook (FastAPI)

Twilio fires a POST request to your webhook URL every time someone replies to your number. Configure this URL in the Twilio Console under Phone Numbers > Messaging.

```python
from fastapi import FastAPI, Form, Response, BackgroundTasks
from twilio.twiml.messaging_response import MessagingResponse

app = FastAPI()

@app.post('/sms/inbound')
async def inbound_sms(
    background_tasks: BackgroundTasks,
    From: str = Form(...),
    Body: str = Form(...)
):
    # Acknowledge fast (Twilio expects <5s response)
    background_tasks.add_task(classify_and_route, From, Body)
    resp = MessagingResponse()
    resp.message('Thank you! A team member will follow up shortly.')
    return Response(content=str(resp), media_type='application/xml')
```

## 6.4 A2P 10DLC — MANDATORY COMPLIANCE

As of February 2025, ALL major US carriers BLOCK 100% of unregistered 10DLC traffic — not throttle, BLOCK. Registration is handled through your CSP (Twilio) via The Campaign Registry (TCR).

Steps to register (do this BEFORE sending any messages):
1. Register your Brand in Twilio Console (business name, EIN, website)
2. Register a Campaign (use case: "Real Estate - Motivated Seller Outreach")
3. Assign your 10-digit phone number to the campaign
4. Approval takes 2-10 business days

Non-compliance penalties:
- $500 per message (TCPA violation)
- Up to $1,500 per willful violation
- T-Mobile: $10,000 per content violation
- 100% message blocking

## 6.5 TCPA Compliance Rules

- Never text before 8:00 AM or after 9:00 PM in the recipient's local time zone
- Always include opt-out language: 'Reply STOP to unsubscribe'
- Honor STOP replies immediately — remove from all future campaigns
- One-to-one consent only — no shared/bulk consent forms
- Retain proof of consent for each contact
- Virginia SB 1339 (Jan 2026): Honor opt-outs for 10 years
- Texas SB 140 (Sept 2025): Texts = telephone solicitations; DTPA treble damages apply
- Indiana: No specific mini-TCPA, federal TCPA applies

---

# 7. INDIANA WHOLESALING — LEGAL COMPLIANCE

UNCHANGED FROM v1.3.

## 7.1 Is Wholesaling Legal in Indiana?

Yes. Wholesaling real estate is legal in Indiana provided you follow state requirements. As a wholesaler, you market the rights and obligations of a contract — not the property itself. Only licensed Indiana real estate agents or homeowners can market a house directly.

## 7.2 Indiana HB 1068 — Mandatory Disclosure

Indiana law requires WRITTEN DISCLOSURE to the seller before contract execution:
- You must disclose that you are a wholesaler
- You must disclose your intent to assign the contract
- Failure to disclose = 'deceptive act' under Indiana consumer protection laws
- Seller may cancel the deal if proper disclosure was not made

Sample disclosure language (have your attorney review):
"Buyer is a real estate investor who may assign this contract to a third party. Buyer does not intend to occupy the property. The purchase price may be below market value. Seller acknowledges this disclosure."

## 7.3 License Requirements

- No real estate license required to wholesale in Indiana
- You CANNOT market the physical property — only the contract rights
- NEVER post photos of the property publicly (Facebook Marketplace, Zillow, etc.)
- Market only to a private, pre-built buyer list via direct email/text
- The Indiana Real Estate Commission oversees licensure — consult for edge cases

## 7.4 Market Conditions (Indianapolis 2026)

| Metric | Current Data |
|---|---|
| Median Home Price | ~$225,000 (up ~9.9% YoY) |
| Top Wholesale Markets | Indianapolis, Fort Wayne |
| Active Foreclosures | ~4,847 properties |
| Bank Owned (REO) | ~365 properties |
| Headed to Auction | ~1,057 properties |
| Opportunity | Growing pool of motivated sellers and distressed properties |

## 7.5 Recommended Attorney Checklist

- Have an Indiana real estate attorney review your standard PSA template
- Have the attorney draft or review your assignment of contract template
- Ensure the HB 1068 disclosure language is correct and included in PSA
- Verify your entity structure (LLC) is set up properly for Indiana

---

# 8. TECH STACK & ARCHITECTURE

## 8.1 Confirmed Tech Stack — UPDATED IN v1.4

| Layer | Tool / Platform |
|---|---|
| Development environment | Claude Code (local) |
| Source control | GitHub (private monorepo) |
| CI / preview (optional) | GitHub Actions for tests on PR |
| Runtime / Hosting | Replit Deployments (Always-On for webhook agents) |
| Database / CRM | Replit PostgreSQL (shared across all 7 Repls) |
| Language | Python 3.11+ |
| Web Framework | FastAPI + Uvicorn |
| SMS | Twilio API |
| Skip Tracing | Tracerfy API |
| Property Data | ATTOM API |
| AI Agent Brain | Claude API (model name via CLAUDE_MODEL env var) |
| Automation / Cron | Replit Scheduled Jobs |
| Direct Mail | Click2Mail or YellowLetterHQ |
| Documents / eSign | DocuSign or HelloSign |
| Dialers (RVM) | Batch Dialer |
| Accounting | QuickBooks or Stessa |

## 8.2 Monorepo Structure (GitHub) — UPDATED IN v1.4

Source of truth lives in one private GitHub repo. Each agent folder is imported into Replit as its own Repl.

Shared utilities pattern:

Each agent imports from shared/ for DB connection, compliance helpers, and the master schema. Two options for making shared/ available inside a Repl:
1. Symlink approach: shared/ symlinked into each agent folder (works on Linux, may not survive Replit import)
2. Build-time copy: a build_repl.sh script copies shared/ into each agent folder before push. Cleanest and most portable. RECOMMENDED.

Claude Code defaults to option 2. See build_repl.sh at the repo root.

## 8.3 Requirements.txt Template (per agent)

```
fastapi==0.111.0
uvicorn==0.29.0
anthropic==0.25.0
twilio==8.13.0
psycopg2-binary==2.9.9
sqlalchemy==2.0.25
requests==2.31.0
python-dotenv==1.0.1
pydantic==2.7.0
```

Pin every version. Different agents may need different deps (the dashboard adds Jinja2, the others don't). Keep each requirements.txt minimal.

## 8.4 Replit PostgreSQL — Connection Pattern

Replit's built-in PostgreSQL auto-injects DATABASE_URL into every Repl in your workspace. shared/db.py is committed once to the monorepo and used identically by all 7 agents.

## 8.5 Claude API Call Pattern

```python
import anthropic, os

claude = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
MODEL = os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-20250514')

def call_claude(system_prompt: str, user_message: str) -> str:
    response = claude.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=system_prompt,
        messages=[{'role': 'user', 'content': user_message}]
    )
    return response.content[0].text
```

Model name is environment-variable-driven so you can swap models without code changes.

## 8.6 Replit Deployment Checklist (per agent)

- Create Repl → Import from GitHub → point to specific subfolder
- Add all secrets in Replit Secrets panel (never .env in production)
- Enable Always-On for webhook-receiving agents (Replit Core required)
- For Intake Repl: set Twilio webhook to https://<repl-url>/sms/inbound
- Test with a real phone number before blasting any list
- Monitor Replit logs for errors during the first 48 hours of operation

---

# 9. AGENT SYSTEM PROMPTS (SEED VERSIONS)

UNCHANGED FROM v1.3. Tune these based on actual performance data after each agent's first 100 messages.

## 9.1 Intake Agent System Prompt

```
You are a real estate lead intake classifier for a wholesale investor in Indianapolis, Indiana. Your job is to read an inbound SMS reply from a property owner and classify it.

Classify the reply as one of:
  HOT    - Seller is interested, wants to talk, asked for an offer, or gave any positive signal
  WARM   - Seller is curious, not sure, asked a question, or is neutral
  COLD   - Seller said no, not interested, or left
  DNC    - Seller said STOP, remove me, do not contact
  OTHER  - Unclear, unrelated, or spam

Return ONLY a JSON object:
{"classification": "HOT", "reason": "one sentence", "suggested_reply": "short reply text"}

No preamble, no markdown.
```

## 9.2 ARV/MAO Agent System Prompt

```
You are a real estate deal analyst. Given a set of comparable sales from ATTOM, calculate the ARV and MAO.

ARV Methodology:
- Weight closest and most recent comps most heavily
- Adjust for sqft differences (use price/sqft of comps)
- Output Low, Mid, High ARV estimates

MAO Formula: (ARV_mid x 0.70) - repair_estimate - 10000

Return ONLY a JSON object:
{"arv_low": 0, "arv_mid": 0, "arv_high": 0, "confidence": "high/medium/low", "mao": 0, "comps_used": 0, "notes": "brief explanation"}

No preamble, no markdown, no extra text.
```

## 9.3 Skip Tracer Agent System Prompt

Reserved — to be populated from the seeded prompt in skip-tracer/agents.py once that agent is reviewed for the v1.4 monorepo.

---

# 10. BUILD PHASES & PROGRESS TRACKER — UPDATED IN v1.4

## Phase 0 — Foundation (Day 1)

- [x] Create GitHub repo (private): wholesale-brrrr-system
- [x] Initialize monorepo structure per Section 8.2
- [x] Set up .gitignore, .env.example pattern, branch protection on main
- [ ] Enable GitHub Secret Scanning + Push Protection
- [x] Claude Code writes shared/db.py, shared/schema/migrate.sql, db-bootstrap/migrate.py
- [x] Commit master-context-v1.4.md to docs/
- [ ] Provision Replit PostgreSQL (Replit Core upgrade)
- [ ] Import db-bootstrap as first Repl, run migration once
- [ ] Verify 6 tables created in Replit Database UI

## Phase 1 — First Agent Live (Days 2-7)

Build order is dependency-driven. ARV/MAO first because it has no DB write dependencies and validates ATTOM + Claude integration.

- [ ] Claude Code builds agents/arv-mao/ (main.py, agents.py, attom_utils.py)
- [ ] PR to main, merge
- [ ] Import agents/arv-mao/ as Repl, configure Secrets (ATTOM_API_KEY, ANTHROPIC_API_KEY, INTERNAL_API_KEY)
- [ ] Curl test against one Indianapolis address — confirm JSON output
- [ ] Claude Code builds agents/intake/
- [ ] Repeat: PR → merge → Repl import → Secrets → test

## Phase 2 — Deal Flow (Weeks 2-6)

- [ ] Build agents/list-puller/ (ATTOM distressed property queries)
- [ ] Build agents/skip-tracer/ (Tracerfy batch processing + DNC scrub)
- [ ] Build agents/outreach/ (Twilio SMS campaigns with cadences)
- [ ] Build agents/deal-marketing/ (buyer blast on signed PSA)
- [ ] Build agents/crm-dashboard/ (the 7th Repl — pipeline view, lead detail, manual SMS)
- [ ] Run end-to-end smoke test from tests/smoke/end_to_end.py
- [ ] First real SMS campaign to live seller list
- [ ] First closed wholesale deal

## Phase 3 — BRRRR Stack (Months 2-4)

- [ ] BRRRR Underwriting Agent
- [ ] Rental Comp Analysis Agent
- [ ] Contractor Management Agent
- [ ] Lease-Up / Stabilization Agent
- [ ] Refinance Coordination Agent

## Phase 4 — Portfolio & Scale (Months 4+)

- [ ] Portfolio Management Dashboard
- [ ] Financial Reporting Agent
- [ ] KPI Dashboard expansion
- [ ] Capital Raise / Investor Relations Agent

---

# 11. QUICK REFERENCE

## 11.1 Key Formulas at a Glance

UNCHANGED FROM v1.3.

| Formula | Calculation |
|---|---|
| Wholesale MAO | (ARV x 70%) - Repairs - $10K assignment fee |
| BRRRR Max Purchase | (ARV x 75%) - Repair Costs |
| 1% Rule (quick filter) | Monthly Rent >= 1% of Purchase Price |
| DSCR | NOI / Annual Debt Service (target >= 1.20) |
| Cash-on-Cash | Annual Cash Flow / Total Cash Invested |
| Cap Rate | NOI / Property Value |

## 11.2 Key API Base URLs

UNCHANGED FROM v1.3.

| API | Base URL |
|---|---|
| ATTOM v1 | https://api.gateway.attomdata.com/propertyapi/v1.0.0 |
| ATTOM v2 | https://api.gateway.attomdata.com/property/v2 |
| Tracerfy | https://app.tracerfy.com/api |
| Twilio | Use twilio Python SDK (no manual base URL needed) |
| Claude API | Use anthropic Python SDK (no manual base URL needed) |
| Replit PostgreSQL | Auto-injected DATABASE_URL env var |

## 11.3 Document Change Log

| Version | Changes |
|---|---|
| 1.0 | Initial blueprint — 31 agents across 6 divisions |
| 1.1 | Confirmed stack: Tracerfy (skip trace), Twilio (SMS), ATTOM (data) |
| 1.2 | Master context document created — all research compiled for Claude Project |
| 1.3 | Confirmed self-hosted CRM on Replit PostgreSQL. Added 7th Repl (CRM Dashboard). Replaced Supabase references with Replit PostgreSQL. |
| 1.4 | Workflow migration to Claude Code + GitHub + Replit. Added monorepo structure (3.2, 8.2). Separated dev environment from runtime in tech stack (8.1). Added Phase 0 for repo and CI foundation (10). Business logic, formulas, API references, and compliance sections unchanged from v1.3. |

---

Master Context Document v1.4 | Wholesale & BRRRR Agentic System | Indianapolis, IN
