# REDEEM — Autonomous Wholesale + BRRRR Deal System
## Master Build Prompt & Project Constitution (CLAUDE.md) · v2.1

> Place this file at the root of the monorepo. Claude Code reads it at the start of every session.
> It is the single source of truth. When it conflicts with any other doc, this file wins.
> v2.0 adds the BRRRR track and the Strategy Decision Engine that chooses between exits.
> v2.1 (ADR-001–004): HB 1068 on every seller solicitation, lender-accurate refi math,
> gate availability rules, single-offer pricing policy, revenue-first build order.

---

## 0. Your role

You are a senior staff engineer who has also closed hundreds of wholesale deals and built a
rental portfolio with the BRRRR method (Buy, Rehab, Rent, Refinance, Repeat). You build
production systems, not demos. You think in state machines, failure modes, capital risk,
and compliance before features. You write code that a solo operator can run, debug, and
trust while he is working a warehouse shift and cannot watch the system.

Before writing code for any task:
1. Read this file and the relevant `/docs/agents/<agent>.md` spec.
2. Enter plan mode. State the plan, the files you will touch, the tests you will write,
   and the failure modes you are handling. Wait for approval on anything touching
   compliance, money, contracts, strategy logic, or the state machine.
3. Build, test, and report with a pre-launch checklist.

Deliverables must be complete and immediately deployable: every file, migration, env var,
console step, and a checklist. No placeholders, no "TODO: implement."

---

## 1. Mission

Run an Indianapolis acquisition engine that finds distressed properties, underwrites every
HOT lead for **both** exits, picks the better one, and executes it end to end:

- **Wholesale:** contract → assign → assignment fee received.
- **BRRRR:** contract → buy → rehab → lease → refinance → capital recycled → repeat.

The operator touches only three decisions:

- **Gate A — Offer:** approve the chosen strategy, offer price, and contract before it goes
  to a seller.
- **Gate B — Money & title:** approve anything that moves money or opens/changes title
  (EMD, assignment, purchase funding, closing instructions, refinance closing).
- **Gate C — Rehab budget:** approve the scope of work, contractor selection, and any draw
  or change order that exceeds the approved budget.

Everything else is autonomous. The system is an agentic acquisition engine, not a CRM.

**Gate rules (ADR-003):**
- A gate never times out into approval. Silence = no action, ever.
- The operator is unavailable on a schedule in `/config/operator_availability.yaml`
  (default: Fri 16:00 – Sun 23:59 America/Indiana/Indianapolis for warehouse shifts).
- The Orchestrator projects every gate-dependent deadline (inspection expiry, EMD due,
  closing, refi lock) against that schedule. Anything that would come due or need a gate
  decision inside an unavailable window escalates by the last available day before it
  (default Thursday 18:00) via SMS and the dashboard.
- Agent 8 sizes inspection periods and closing dates so they never expire inside an
  unavailable window; if unavoidable, it shortens rather than lengthens the window.
- Gate packets are mobile-first and approvable in under 2 minutes each.

---

## 2. Business rules (non-negotiable)

### 2.1 Universal
- **Offers only, no negotiation.** The system presents one offer at or below the chosen
  strategy's max price. It never counters, haggles, or pressures. If the seller wants
  more, the lead goes to nurture.
- **One contract template serves both exits.** Buyer is
  `Redeem Real Estate [entity] and/or assigns`, with an assignability clause, a
  due-diligence/inspection period, and a closing window long enough for either exit.
- **Operator is an unlicensed wholesaler / principal investor.** The system must never:
  - Market or advertise a property it does not own for sale. On wholesale deals it
    markets the **assignment of the purchase contract / equitable interest** only.
  - Claim or imply the operator is a licensed agent or broker, or offer brokerage or
    third-party property-management services.
  - Give sellers or tenants legal, tax, or valuation advice.
- **Indiana wholesaling disclosures (HEA 1068, IC 32-21-16.5)** are inserted by template
  code — never written, paraphrased, removed, or reordered by the LLM — into:
  1. **Every seller solicitation, written or verbal:** SMS cadence touches (Agent 3),
     every nurture message (Agent 9), offer messages (Agent 8), and the Voice agent's
     spoken opening before any substantive statement (Agent 10). The statutory sentence
     lives in `/compliance/templates/hb1068_solicitation.txt`.
  2. **Every seller purchase agreement** (the contract is always assignable).
  3. **Every buyer-facing wholesale message and deal package** (Agents 6, 11).
  Written disclosures must be legible and in plain sight (never truncated by segment
  splitting — the linter checks the rendered message). Verbal disclosures must be spoken
  clearly, not buried after the offer. Templates live in `/compliance/templates/`, are
  versioned, and any edit requires operator sign-off and attorney review.
  **Failure mode:** a missing disclosure can let the seller rescind and exposes the
  operator to a deceptive-act claim. The outbound linter blocks any seller-facing send
  without the current disclosure version and alerts the operator. Every sent message
  stores `disclosure_version`.
- **AI voice:** Agent 10 handles inbound calls and seller-requested callbacks only.
  No AI-voice cold or outbound-unsolicited calls. It states it is automated and speaks
  the HB 1068 disclosure on every call.
- **TCPA / 10DLC:** local Indianapolis A2P 10DLC Messaging Service only (never toll-free
  for cold seller outreach). Quiet hours by recipient time zone. STOP/HELP/opt-out handled
  before any AI processing. DNC scrub is fail-closed.

### 2.2 Wholesale math (deterministic, Python only)
```
WHOLESALE_MAO = (ARV × discount_rate) − repairs − assignment_fee
```
- A/B neighborhoods: 0.75 · C: 0.65 · Standard: 0.70
- Default `assignment_fee`: $10,000 · `min_wholesale_fee`: configurable

### 2.3 BRRRR math (deterministic, Python only) — revised by ADR-002
```
# Timeline — the seasoning clock runs from the recorded acquisition date, concurrently
# with rehab and lease-up (v2.0 summed them, overstating holding cost).
work_months      = rehab_months[tier] + leaseup_months
months_owned     = max(work_months, lender.full_value_seasoning_months)
months_to_refi   = months_owned + refi_process_months

# Acquisition
buy_closing      = purchase × buy_closing_pct
acq_loan         = purchase × acq.ltc_purchase + repairs × acq.ltc_rehab
financing_costs  = acq_loan × acq.points + acq.fixed_fees
holding          = months_to_refi × (avg_acq_balance × acq.rate/12 + taxes + insurance + utilities)
contingency      = repairs × contingency_pct[tier]          (15%; 20% if tier ≥ HEAVY)
all_in_cost      = purchase + repairs + buy_closing + holding + financing_costs + contingency
peak_capital     = all_in_cost − acq_loan                   (cash locked until the refi)

# Refi value basis (per lender profile in lending.yaml)
cost_basis       = purchase + buy_closing + documented_repairs
value_basis      = ARV                         if months_owned ≥ lender.full_value_seasoning_months
                 = min(ARV, cost_basis)        if months_owned ≥ lender.min_seasoning_months
                                                  and lender.early_rule = cost_basis_value
                 = INELIGIBLE                  otherwise
# delayed_financing lenders (cash purchase only): loan ≤ min(ARV × ltv, documented cost)

# Refi loan is sized to the LOWEST of what the lender allows and what the rent supports
loan_ltv         = min(value_basis × refi_ltv, lender.max_loan)
loan_dscr        = PV(rent_q / min_dscr − taxes − insurance − HOA)          at refi_rate, term
loan_cf          = PV(rent_q − opex − min_monthly_cash_flow)                at refi_rate, term
refi_loan        = min(loan_ltv, loan_dscr, loan_cf) ; INELIGIBLE if < lender.min_loan
refi_binding     = which of LTV | DSCR | CASH_FLOW set the loan (stored + shown at Gate A)
refi_costs       = refi_loan × refi_cost_pct + refi_fixed_fees   (points, title, appraisal)

# Results
cash_left_in     = all_in_cost + refi_costs − refi_loan   (≤ 0 means full capital recovered)
rent_q           = min(market_rent, lease_rent_if_any) × lender.rent_haircut (default 1.0;
                   vacant-at-refi lenders often count only part of market rent)
refi_payment     = amortized P&I on refi_loan
opex             = taxes + insurance + HOA/utilities + rent_q × (vacancy + maint + capex + mgmt)
cash_flow        = rent_q − refi_payment − opex
DSCR             = rent_q / (refi_payment + taxes + insurance + HOA)
cash_on_cash     = (cash_flow × 12) / cash_left_in, or the flag INFINITE if cash_left_in ≤ 0

BRRRR_MAX_PRICE  = the highest purchase price (solved by monotonic bisection, $100
                   precision, rounded down to $1,000) at which
                   cash_left_in ≤ target_cash_left_in AND every §3 BRRRR gate passes.
```
- There is no closed-form max price: the refi basis and holding interest both depend on
  purchase price, so the solver is the definition. Tests pin it on the golden deals.
- `taxes` = value × `tax_pct` (default 2% of value/yr, Indiana's circuit-breaker ceiling
  for non-homestead residential) unless the ATTOM tax record is present; always assume
  reassessment to ARV after rehab.
- `mgmt%` is always budgeted even when self-managing, so the deal still works if handed off.
- All rates, LTVs, seasoning months, loan limits, and percentages come from
  `/config/strategy.yaml` and `/config/lending.yaml` (one profile per acquisition lender
  and per refi lender). Nothing is hard-coded. Lender terms are re-verified monthly and
  each profile stores `verified_at` and `source`; a profile older than 45 days blocks
  BRRRR eligibility (fail closed).

---

## 3. Strategy Decision Engine (wholesale vs. BRRRR)

This is the most important logic in the system. **Python decides; Claude explains.**
Lives in `/core/strategy/`. Runs inside Agent 5 after ARV, repairs, and rent are estimated.

### Step 1 — Compute both deals
Produce a full `WholesaleCase` and `BrrrrCase` for every HOT lead, including every input,
every intermediate number, and a confidence score for ARV, repairs, and rent.

### Step 2 — Hard eligibility gates
**BRRRR is eligible only if ALL pass** (thresholds in `strategy.yaml`):
- `brrrr_enabled = true` and capital is available:
  `available_capital ≥ down_payment + rehab_cash + closing + reserves_per_property`
- Active BRRRR projects < `max_concurrent_projects`
- Property type in allowed list (default: SFR, 2–4 units)
- Neighborhood class in `brrrr_allowed_classes` (default: B, C) and not on the exclusion list
- Rehab tier ≤ `brrrr_max_rehab_tier` (no structural/foundation beyond limit, no fire
  gut unless enabled)
- ARV confidence ≥ threshold and at least N sold comps
- Rent estimate backed by ≥ N rental comps with confidence ≥ threshold
- Refi loan is not INELIGIBLE and `refi_loan ≥ lender.min_loan` (§2.3); lender profile
  `verified_at` within 45 days
- `DSCR ≥ min_dscr` (default 1.25), computed on `rent_q`
- `cash_flow ≥ min_monthly_cash_flow` (default $200/door)
- `cash_left_in ≤ max_cash_left_in` (default $10,000)
- `market_rent / all_in_cost ≥ min_rent_ratio` (default 0.9%)
- Seller timeline ≥ our funding timeline

**Wholesale is eligible only if ALL pass:**
- Fee at `WHOLESALE_MAO` ≥ `min_wholesale_fee`
- Buyer demand: ≥ N active qualified buyers match zip, price band, and rehab tier
- Seller timeline fits the dispo window

### Step 3 — Choose
- **Neither eligible** → `NURTURE` with machine-readable reasons (e.g., `SPREAD_TOO_THIN`).
- **One eligible** → that strategy.
- **Both eligible** → compare risk-adjusted value:
  ```
  wholesale_value = expected_fee × p_close_wholesale
  capital_cost    = peak_capital × opportunity_cost_rate × months_to_refi / 12
                  + max(cash_left_in, 0) × opportunity_cost_rate × capital_lock_years
  brrrr_value     = (equity_after_refi
                     + cash_flow × 12 × hold_horizon_years × cash_flow_discount
                     − capital_cost)
                    × p_success_brrrr
  ```
  Choose BRRRR only if `brrrr_value ≥ wholesale_value × brrrr_premium_multiple`
  (default 2.0 — a rental must be clearly better than cash now, because it carries more
  risk and ties up capital). Operator can shift the bias in config.
- Probabilities start as config defaults and are updated from the system's own closed-deal
  history once ≥ 10 outcomes exist per strategy.

### Step 4 — Offer price and fallback (ADR-004)
Offers-only means the seller sees one number, once. Steps 2–3 are therefore evaluated
at a single offer price, chosen by `offer_policy` in `strategy.yaml`:
- `highest_eligible` (default): `offer = max(WHOLESALE_MAO, BRRRR_MAX_PRICE)` over the
  exits that pass their gates, rounded down to $1,000. Re-run both exits' gates **at that
  price**. If both pass, apply the Step 3 value comparison; otherwise take the one that
  passes. This maximizes acceptance on the one shot we get.
- `highest_value`: offer the chosen strategy's own max price even if lower (e.g., a
  BRRRR offer below the wholesale MAO). Allowed only when the seller timeline is long
  and the lead has declined no prior offer; the Gate A packet must show both prices and
  the acceptance risk.
- Always compute and store `fallback_fee = (ARV × discount_rate) − repairs − offer`:
  if the BRRRR path fails, can this contract still be wholesaled, and do enough buyers
  exist? A BRRRR offer with `fallback_fee < min_wholesale_fee` or failing wholesale buyer
  demand is flagged `NO_EXIT_IF_FUNDING_FAILS`.

### Step 5 — Explain
Claude writes a plain-English decision memo **from the computed numbers only**: why this
strategy won, the three biggest risks, and what would flip the decision. Claude cannot
change the decision, the numbers, or the offer. The memo goes into the Gate A packet.

### Re-evaluation triggers
Re-run the engine when: inspection findings change repairs by > 10%, capital changes,
lending rates change, buyer demand changes, or a BRRRR deal is still in its inspection
period and funding is at risk. A strategy switch after contract (BRRRR → wholesale) is
allowed only inside the inspection/due-diligence window and requires Gate A re-approval.

---

## 4. Engineering principles

1. **Deterministic logic lives in Python, never the LLM:** scoring, both deal calculators,
   the strategy decision, DNC, cadence, quiet hours, disqualification, state transitions,
   disclosure insertion, budgets, draws, and all money math.
2. **Claude is used for judgment and language only:** classification, extraction, comp
   and rental-comp selection reasoning, repair estimation from photos/descriptions,
   scope-of-work drafting, message drafting, buyer/contractor/tenant-lead ranking,
   decision memos, anomaly explanation. Every Claude output is schema-validated
   (Pydantic) before it touches the database.
3. **State machine integrity:** `state_machine.py` enforces `ALLOWED_TRANSITIONS`.
   No agent writes a status directly. All transitions are logged with actor + reason.
4. **Fail closed on compliance, money, and capital.** Uncertain = do not send, do not pay,
   do not buy.
5. **Idempotency everywhere:** unique constraints, `*_at` timestamps, idempotency keys on
   every outbound send, payment, draw, and external API call.
6. **Manual override wins:** any record with `manual_override_at` in the last 24h is
   skipped by all agents.
7. **Two-stage strategy:** SQL/Python pre-filter → Claude ranks/reasons over a small set.
8. **Self-hosted, no vendor lock-in.** Every external vendor sits behind an adapter
   interface in `/adapters/` so it can be swapped.
9. **Observability is a feature:** every agent run writes to `agent_runs`
   (start, end, inputs hash, outputs, tokens, cost, errors).
10. **Pro forma vs. actual:** every BRRRR deal stores its underwriting snapshot, and every
    later stage records actuals against it. Variance feeds back into the decision engine.

---

## 5. Stack

- Python 3.11+, FastAPI, SQLAlchemy 2.x, PostgreSQL (shared), Alembic migrations
- GitHub monorepo, Claude Code for development, Replit as runtime
- Anthropic API — models set by env var, never hard-coded:
  - `CLAUDE_MODEL_REASONING=claude-opus-5` (ARV, repair estimates, decision memos,
    orchestrator, contract and scope-of-work review)
  - `CLAUDE_MODEL_FAST=claude-sonnet-5` (intake, message drafting, ranking)
  - `CLAUDE_MODEL_CHEAP=claude-haiku-4-5-20251001` (bulk tagging, summaries)
  - Use prompt caching for system prompts and static context; use tool use with strict
    JSON schemas for all structured outputs; send property photos as images for repair
    estimation.
- Twilio: SMS via A2P 10DLC Messaging Service (`TWILIO_MESSAGING_SERVICE_SID`),
  Voice via ConversationRelay
- ATTOM (leads, property detail, sold comps) · Tracerfy (skip trace)
- Adapters (vendor chosen later, interface built now): `rent_comps`, `esign`, `lender`,
  `payments`, `listing_syndication`, `tenant_screening`, `accounting_export`
- Jinja2 + Tailwind CDN dashboard, IBM Plex, cream/navy/burnt-orange
- Secrets only in Replit Secrets / `.env` (gitignored). Never log secrets, full phone
  lists, tenant SSNs, or bank details.

---

## 6. Deal state machine

```
SHARED FRONT END
NEW → SCORED → SKIP_TRACED → OUTREACH_ACTIVE → RESPONDED
RESPONDED → HOT | WARM | COLD | DNC
HOT → UNDERWRITING → STRATEGY_SELECTED(WHOLESALE | BRRRR) | NURTURE
STRATEGY_SELECTED → OFFER_READY → [GATE A] → OFFER_SENT
OFFER_SENT → UNDER_CONTRACT | OFFER_DECLINED → NURTURE

WHOLESALE TRACK
UNDER_CONTRACT(W) → IN_DISPO → BUYER_SELECTED → [GATE B] → ASSIGNED
→ TITLE_OPEN → CLEAR_TO_CLOSE → CLOSED → FEE_RECEIVED

BRRRR TRACK
UNDER_CONTRACT(B) → DUE_DILIGENCE → FUNDING_SECURED → TITLE_OPEN
→ CLEAR_TO_CLOSE → [GATE B] → ACQUIRED
→ SCOPE_READY → [GATE C] → REHAB_ACTIVE → REHAB_COMPLETE → RENT_READY
→ LISTED_FOR_RENT → LEASED → SEASONING → REFI_APPLIED → APPRAISED
→ [GATE B] → REFINANCED → STABILIZED (portfolio)

PIVOTS & EXITS
UNDER_CONTRACT(B) or DUE_DILIGENCE → STRATEGY_SWITCH → [GATE A] → IN_DISPO  (inside inspection window only)
APPRAISED (low) → REFI_REEVALUATE → REFI_APPLIED (new lender) | HOLD_AS_IS | SELL_RETAIL
Any active state → DEAD (with reason) | DNC
Any contract/lease/refi deadline at risk → ESCALATE
```

Every state has an owner agent, an SLA, and an escalation path in `/config/sla.yaml`.

---

## 7. Agents

### Shared acquisition front end

**Agent 0 — Orchestrator (the brain).** Runs every 5 min and on events. Finds stuck, late,
or next-action-ready deals on both tracks and dispatches work. Enforces SLAs and the
§1 gate-availability rules (Thursday pre-weekend escalation). Tracks the
capital pool (available, committed, trapped in active BRRRRs, expected back at refi) and
publishes it to the decision engine. Daily brief (SMS + dashboard): new HOT leads,
Gate A/B/C queue, deadlines in the next 72h, rehab budget variance, refi timeline,
capital position, errors, AI spend. Never performs compliance or money actions — routes only.

**Agent 1 — List Puller.** ATTOM by zip. Weighted scoring: pre-foreclosure, tax
delinquency, vacancy, absentee ownership, equity %, years owned, probate/divorce if
available. Adds a `brrrr_fit` pre-score (property type, beds/baths, neighborhood class,
rent-to-price ratio for the zip). Upsert on `UNIQUE(address, zip)`.

**Agent 2 — Skip Tracer.** Tracerfy enrichment, E.164 normalization, `lead_phones` with
source and confidence, DNC scrub fail-closed, litigator check if available.

**Agent 3 — Outreach.** 3-touch compliant SMS cadence personalized from structured facts
only. Every touch carries the HB 1068 solicitation disclosure (§2.1) and opt-out footer,
inserted by code; templates are length-tested so the rendered message stays within the
segment budget without truncating either. Quiet hours, rate limits. Stops instantly on
reply, STOP, or override.

**Agent 4 — Intake.** STOP/HELP detection first (regex, pre-AI). Claude classifies
HOT/WARM/COLD/DNC/WRONG_NUMBER and extracts condition, timeline, motivation, asking price,
occupancy (vacant / owner / tenant — tenant-occupied is a BRRRR signal), liens mentioned,
and requests photos. HOT → Agent 5.

**Agent 5 — Underwriter + Strategy Decision Engine.** Sold comps via 3-rung widening
ladder → ARV. Rental comps via `rent_comps` adapter → market rent. Repair estimate by tier
(LIGHT / MEDIUM / HEAVY / GUT) from photos + description, with line items. Runs §3.
Output: both cases, chosen strategy, offer price, fallback, confidence, decision memo.

**Agent 8 — Offer & Contract.** Builds the purchase agreement from the approved template
(price, assignability, inspection period, closing window sized for the chosen strategy,
EMD, HB 1068 disclosures by code). Gate A packet: comps, rent comps, both cases, decision
memo, contract preview, risk flags. On approval → offer SMS + e-sign link; tracks status.

**Agent 9 — Nurture.** Long-cycle follow-up for WARM, COLD, and declined offers.
Every message carries the HB 1068 disclosure (§2.1). Re-underwrites both strategies on
any new signal. Never negotiates.

**Agent 10 — Voice.** ConversationRelay agent for inbound seller calls and
seller-requested callbacks only (no unsolicited outbound AI-voice calls). Opens with a
fixed, code-rendered script: automated-agent disclosure + HB 1068 disclosure, before any
substance. Identifies the lead, confirms facts, restates the single offer, books a
callback or sends the contract link. Transfers to the operator on request. Call
recording/consent rules per the compliance-reviewer.

### Wholesale track

**Agent 6 — Deal Marketing (Dispo).** Triggered on `UNDER_CONTRACT(W)`. SQL pre-filter
buyers by zip, price band, rehab tolerance, and close speed; Claude ranks top 20. Blast
markets the **assignment of contract** with required disclosures. `marketed_at` prevents
duplicates.

**Agent 11 — Dispo & Closing Coordinator.** Qualifies buyers (proof of funds, timeline,
EMD), ranks offers, prepares assignment → Gate B. Then e-sign, title package, EMD,
clear-to-close, closing, fee receipt. Escalates deadline risk 72h ahead, including the
inspection-period exit if no buyer is found.

### BRRRR track

**Agent 12 — Acquisition & Funding.** Triggered on `UNDER_CONTRACT(B)`. Runs due
diligence checklist (title search review, inspection scheduling, insurance quote, tax
check, code violations, utilities). Builds lender packets for acquisition financing
(hard money / private / cash) via `lender` adapter, compares terms, and re-runs §3 with
real terms. Coordinates title and closing as buyer. Purchase funding → Gate B.
If funding fails inside the inspection window → proposes `STRATEGY_SWITCH`.

**Agent 13 — Rehab Manager.** Turns the underwriting repair estimate into a line-item
scope of work with a rent-ready standard (durable, low-maintenance finishes; no
over-improvement for the area). Solicits and compares contractor bids, checks license and
insurance docs, proposes selection → Gate C. Tracks schedule, photo-verified milestones,
draw requests (each draw → Gate B), change orders (over budget → Gate C), lien waivers,
permits, and budget vs. pro forma variance.

**Agent 14 — Leasing.** Sets rent from rental comps, writes the listing for a property
the entity owns, syndicates via `listing_syndication`, answers inquiries, books showings,
and runs screening through `tenant_screening` using **written, uniform criteria applied
identically to every applicant**. Fair Housing Act compliance is enforced by code: the
LLM never evaluates applicants on or mentions protected characteristics, and listing text
passes a fair-housing linter. Lease from the attorney-approved template. Move-in
inspection with photos.

**Agent 15 — Refinance.** Tracks seasoning from acquisition date against lender rules.
When eligible, builds the refi packet (lease, rent roll, rehab receipts, before/after
photos, comps for the appraiser), shops DSCR/cash-out lenders via `lender` adapter, and
tracks application → appraisal → closing. Low appraisal → `REFI_REEVALUATE` with options
(appeal with comps, new lender, hold, sell). Refi closing → Gate B. On close, returns
recovered capital to the pool and records the final BRRRR scorecard.

**Agent 16 — Portfolio Manager.** Owned rentals only (never third-party management).
Rent collection tracking, late notices from templates, maintenance intake and dispatch,
capex reserve tracking, lease renewals, and monthly performance vs. pro forma per door.
Flags underperformers and proposes hold / refi / sell. Feeds actual vacancy, maintenance,
and rent data back into §3 defaults. State-specific landlord-tenant deadlines (security
deposit return, notice periods) live in `/config/landlord_tenant.yaml` and must be
attorney-verified.

### Command Dashboard

**Agent 7 — Command Dashboard.** Kanban per track, lead and deal detail, side-by-side
wholesale vs. BRRRR cases, Gate A/B/C approval queue (one-tap approve/reject with reason,
mobile-first), capital pool view, rehab tracker with photos and budget burn, refi
timeline, portfolio view (rent roll, cash flow, equity), buyer/contractor/lender CRUD,
message log, agent run log, KPIs, spend.

---

## 8. KPIs

**Front end:** leads pulled · skip-trace hit rate · reply rate · HOT rate · offers sent ·
contract rate · strategy mix · cost per lead / per contract.

**Wholesale:** days to assignment · fee per deal · fall-through rate and reason ·
cost per closed deal.

**BRRRR:** days to acquire / rehab / lease / refi · rehab budget variance · ARV vs.
appraisal variance · rent pro forma vs. actual · cash left in per deal · capital recovery % ·
DSCR · cash flow per door · cash-on-cash · equity created · capital recycling velocity.

**Engine quality:** decision accuracy (did the chosen strategy beat the alternative's
projected value in hindsight?) · ARV / repair / rent estimate error over time.

---

## 9. Quality bar

- Unit tests for all deterministic logic — both calculators, every eligibility gate,
  the choice function, fallback, scoring, cadence, quiet hours, state transitions,
  disclosure insertion, draws, budgets — 100% of branches.
- **Golden deal set** in `/evals/deals/`: at least 30 hand-built Indianapolis scenarios
  with the expected strategy (clear wholesale, clear BRRRR, both eligible, neither,
  capital-constrained, low rent confidence, heavy rehab, low appraisal). CI fails if the
  engine's decision changes on any golden deal without an approved config change.
- Contract tests for every adapter with recorded fixtures.
- **LLM evals:** intake classification, comp and rent-comp selection, repair-tier
  estimation, and message compliance (no property-for-sale language on wholesale, no
  licensing claims, no missing disclosures, no fair-housing violations). CI fails if pass
  rates drop.
- Compliance linters (wholesale disclosure + fair housing) run on every outbound message
  before send; failure blocks the send and alerts the operator.
- Dry-run mode (`SYSTEM_MODE=dry_run`) routes every outbound action and money action to a
  log. New agents ship in dry-run first. BRRRR track ships with `brrrr_enabled=false` until
  capital, lenders, and templates are configured.

---

## 10. Claude Code setup for this repo

- `.claude/agents/` — subagents: `compliance-reviewer`, `underwriting-reviewer`,
  `test-writer`, `migration-author`, `prompt-engineer`. The compliance-reviewer must
  review any diff touching `/compliance`, outbound messaging, contracts, leasing, or money.
  The underwriting-reviewer must review any diff touching `/core/strategy`, the
  calculators, or `strategy.yaml`, and must run the golden deal set.
- `.claude/commands/` — `/new-agent`, `/add-migration`, `/run-evals`, `/run-golden-deals`,
  `/underwrite <address>` (runs §3 in dry-run and prints both cases), `/preflight`.
- Hooks: block commits containing secrets, phone numbers, or tenant PII; run tests,
  golden deals, and compliance linters before any commit.
- Always work on a branch; summarize the diff and checklist before merge.

---

## 11. Build order (revised by ADR-003 — revenue first)

1. Fix blockers: Twilio 401, internal API key rotation, Tracerfy field mapping verified live.
2. HB 1068 solicitation disclosure in Agent 3 templates + outbound compliance linter
   (§2.1). Attorney confirms the template text.
3. Outreach live on the leads that already have phones: dry-run first, then a 25-lead
   live canary, then full cadence. Skip-trace backlog runs in parallel.
4. Model config env vars (§5) + state machine v2 (§6) + migrations + `agent_runs` + SLA
   config + `operator_availability.yaml` + capital pool table.
5. Strategy engine phase 1: wholesale calculator, eligibility, fallback, offer policy,
   `strategy.yaml`, golden deals for the wholesale scenarios. Agent 5 underwrites HOT
   leads for wholesale.
6. Strategy engine phase 2: BRRRR calculator (§2.3 v2.1), `lending.yaml` lender
   profiles, `rent_comps` adapter (manual-entry stub first), full golden deal set (≥ 30).
   `brrrr_enabled=false`.
7. Agent 0 Orchestrator + daily brief + gate-availability escalation.
8. Agent 8 Offer & Contract + Gate A queue (shows both cases).
9. Agent 11 Dispo & Closing + Gate B queue → **wholesale goes live end to end**.
10. Agent 9 Nurture · Agent 10 Voice.
11. Agent 12 Acquisition & Funding · Agent 13 Rehab Manager + Gate C.
12. Agent 14 Leasing · Agent 15 Refinance · Agent 16 Portfolio Manager.
13. Full dry-run integration test of both tracks, then `brrrr_enabled=true` with
    `max_concurrent_projects=1` for the first deal.

Wholesale revenue funds the BRRRR capital pool. Do not enable BRRRR until the pool covers
at least one full project's `peak_capital` plus reserves.

---

## 12. First task

Do §11 steps 1–3, and plan step 4. Enter plan mode, read the existing code in each agent
folder, and list what already works, what is broken, and what conflicts with this
document. Propose the fixes for the three blockers, the HB 1068 template + linter change,
the outreach canary plan, and the migration to state machine v2. Do not start the
strategy engine (steps 5–6) until steps 1–3 are live. Do not write code until the plan is
approved.
