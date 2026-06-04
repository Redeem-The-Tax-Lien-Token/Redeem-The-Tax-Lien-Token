# Deal Marketing Agent

Triggered when a PSA is signed. Segments the buyer database by buy-box and
area, DNC-scrubs buyer phones, and blasts the deal via SMS.

The street address is **never included** in the blast message — buyers who
reply YES get it through a direct follow-up. This complies with Indiana HB 1068
private-list marketing rules (§ 7.3 of master-context).

---

## Endpoint Contract

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | none | Health check |
| POST | `/run` | X-Internal-Key | Blast a deal to matched buyers |
| GET | `/version` | none | Git commit hash |

### POST /run — Request

```json
{
  "deal_id":    42,
  "max_buyers": 100,
  "max_tier":   2,
  "dry_run":    false
}
```

| Field | Default | Notes |
|-------|---------|-------|
| `deal_id` | required | Must have `psa_signed_at` set and `arv_mid`, `offer_amount`, `assignment_fee` populated |
| `max_buyers` | `100` | Cap on buyers blasted per run |
| `max_tier` | `3` | `1` = A-tier only, `2` = A + B, `3` = all tiers |
| `dry_run` | `false` | Builds message and matches buyers but skips Twilio + DNC scrub |

### POST /run — Response

```json
{
  "deal_id":             42,
  "buyers_matched":      47,
  "buyers_blasted":      44,
  "skipped_dnc":         2,
  "skipped_no_phone":    1,
  "skipped_quiet_hours": false,
  "send_errors":         0,
  "dry_run":             false,
  "message_preview":     "DEAL ALERT - Indianapolis 46201: ARV ~$165,000 ...",
  "buyer_results": {
    "7":  "SM1234abcd...",
    "12": "skipped_dnc",
    "19": "SM5678efgh..."
  }
}
```

---

## Deal Blast Message Format

```
DEAL ALERT - {city} {zip}: {beds}BR/{baths}BA SFR | ARV ~${arv_mid} ({confidence}) |
Repairs ~${repairs} | Price ${offer} | Fee ${assignment_fee} |
Reply YES for address & details. Reply STOP to opt out.
```

Street address is withheld from the blast. Interested buyers reply YES and get
the address via direct follow-up (CRM dashboard → Manual SMS).

---

## Buyer Segmentation

Buyers are matched by all of:

| Filter | Rule |
|--------|------|
| Buy box | `buy_box_min <= offer_amount <= buy_box_max` (NULL = no box set = any) |
| Area | `zip IN buyer.areas` (NULL/empty = any area) |
| Strategy | `strategy IN ('wholesale', 'any')` or NULL |
| Tier | `tier <= max_tier` |

Results sorted: **A-tier first**, then by `last_active_at DESC` (most recently
active buyers at the top of the blast).

After a successful blast, `buyers.last_active_at` is updated for all blasted buyers.

---

## Workflow

```
PSA signed (CRM dashboard / manual)
    ↓ set deals.psa_signed_at, deals.status = 'under_contract'
    ↓
POST /run  {deal_id: 42}
    ↓ validates psa_signed_at + required deal fields
    ↓ builds deal blast message (no street address)
    ↓ queries buyers table (segmented + sorted)
    ↓ DNC scrubs all buyer phones (fail-closed)
    ↓ sends SMS to each clean phone
    ↓ updates buyers.last_active_at
    ↓ logs to agent_events
    ↓
Buyers reply YES → intake agent classifies → CRM dashboard shows HOT buyer
    ↓
Manually send address + details to YES buyers via CRM manual SMS sender
    ↓
Assignment signed → update deals.status = 'assigned', deals.assignment_fee
```

---

## Adding Buyers to Your Database

Buyers are inserted directly into the `buyers` table. Use the CRM dashboard
(once built) or a direct SQL insert:

```sql
INSERT INTO buyers (name, phone, email, buy_box_min, buy_box_max, areas, strategy, tier)
VALUES
  ('John Investor', '+13175550100', 'john@example.com',
   50000, 150000, ARRAY['46201','46218','46222'], 'wholesale', 1),
  ('Jane Flipper', '+13175550200', 'jane@example.com',
   75000, 200000, NULL, 'any', 2);
```

**Tier guide:**
- `1` (A) — Verified closers. Have closed at least one deal with you.
- `2` (B) — Active buyers. In your pipeline, responsive, not yet closed.
- `3` (C) — Cold list. Expressed interest but no active engagement.

---

## Secrets Required

| Secret | Description |
|--------|-------------|
| `DATABASE_URL` | Replit PostgreSQL — auto-injected |
| `INTERNAL_API_KEY` | Shared secret for X-Internal-Key header |
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_FROM_NUMBER` | Your registered 10DLC number (E.164) |
| `TRACERFY_API_KEY` | Tracerfy API key (for buyer DNC scrub) |

---

## Local Development

```bash
bash build_repl.sh agents/deal-marketing
cd agents/deal-marketing
pip install -r requirements.txt
export $(grep -v '^#' .env | xargs)
uvicorn main:app --reload --port 8005
```

Dry-run test:
```bash
curl -s -X POST http://localhost:8005/run \
  -H "Content-Type: application/json" \
  -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -d '{"deal_id": 1, "dry_run": true, "max_tier": 1}' | python -m json.tool
```

---

## Replit Deploy Checklist

- [ ] Populate the `buyers` table before first blast
- [ ] Create Repl → Import from GitHub → `agents/deal-marketing/`
- [ ] Run `bash build_repl.sh agents/deal-marketing`, commit `shared/` copy
- [ ] Add 6 secrets in Replit Secrets panel
- [ ] Test with `dry_run: true, max_tier: 1` first — verify message preview
- [ ] First live blast: `max_tier: 1, max_buyers: 10` (A-tier only)
- [ ] Scale to all tiers after confirming A-tier responses
