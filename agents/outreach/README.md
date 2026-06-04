# Outreach Agent

Sends personalized SMS to traced leads via Twilio. Enforces TCPA quiet hours,
re-scrubs all phones for DNC before each send (fail-closed), and logs every
message to `outreach_log`. Supports a 3-touch cadence.

Does not need Always-On — run on demand or on a schedule during calling hours.

---

## Endpoint Contract

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | none | Health check |
| POST | `/run` | X-Internal-Key | Send outreach batch |
| GET | `/version` | none | Git commit hash |

### POST /run — Request

```json
{
  "touch_number": 1,
  "lead_ids":     null,
  "max_leads":    50,
  "dry_run":      false
}
```

| Field | Default | Notes |
|-------|---------|-------|
| `touch_number` | `1` | 1 = initial, 2 = day-3 follow-up, 3 = day-7 final |
| `lead_ids` | `null` | Explicit IDs override auto-pull |
| `max_leads` | `50` | Cap when using auto-pull |
| `dry_run` | `false` | Logs message but skips Twilio call — safe for testing |

### POST /run — Response

```json
{
  "sent":                38,
  "skipped_dnc":         4,
  "skipped_no_phone":    2,
  "skipped_quiet_hours": false,
  "send_errors":         0,
  "dry_run":             false,
  "leads_sent": {
    "101": "SM1234abcd5678efgh",
    "102": "skipped_dnc",
    "103": "SM9876..."
  }
}
```

If called outside 8 AM–9 PM Indianapolis time, returns immediately with
`skipped_quiet_hours: true` and `sent: 0` — no Twilio calls are made.

---

## Cadence Logic

| Touch | Auto-pull criteria |
|-------|-------------------|
| 1 | `status = 'traced'`, zero prior outbound messages |
| 2 | `status = 'contacted'`, exactly 1 outbound, no inbound reply, last send ≥ 3 days ago |
| 3 | `status = 'contacted'`, exactly 2 outbound, no inbound reply, last send ≥ 7 days ago |

Touch 1 advances `leads.status` from `traced` → `contacted`.
Touches 2 and 3 leave status unchanged (lead is already `contacted`).

---

## SMS Templates

| Touch | Message |
|-------|---------|
| 1 | Introduction — cash offer interest, no repairs, fast close |
| 2 | Follow-up — 2-week close reminder, still interested? |
| 3 | Final — low-pressure exit, door left open |

All messages include the TCPA opt-out footer: `Reply STOP to unsubscribe.`
First-name personalization from `leads.owner_name`; falls back to "there".

---

## TCPA Compliance

- **Quiet hours**: No sends outside 8:00 AM – 9:00 PM Indianapolis (Eastern) time
- **DNC re-scrub**: Every campaign re-scrubs all phones via Tracerfy, even phones
  skip-tracer already cleared. **Fail-closed**: a scrub API error blocks all sends.
- **Opt-out footer**: `Reply STOP to unsubscribe.` is appended to every message
- **A2P 10DLC**: Register your brand + campaign in Twilio Console before sending
  (see Section 6.4 of master-context)

Any lead whose phone fails the DNC re-scrub is immediately flipped to `status = 'dnc'`
in the DB — it will never receive another outbound message.

---

## Secrets Required

| Secret | Description |
|--------|-------------|
| `DATABASE_URL` | Replit PostgreSQL — auto-injected |
| `INTERNAL_API_KEY` | Shared secret for X-Internal-Key header |
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_FROM_NUMBER` | Your registered 10DLC number (E.164) |
| `TRACERFY_API_KEY` | Tracerfy API key (for DNC re-scrub) |

---

## Suggested Run Schedule (Replit Scheduled Jobs)

Run after skip-tracer. All times in Indianapolis Eastern:

| Schedule | Touch | Notes |
|----------|-------|-------|
| Monday 10 AM | 1 | Initial outreach after Monday list/trace |
| Wednesday 10 AM | 1 | Mid-week initial batch |
| Thursday 10 AM | 2 | Follow-up to Monday's touch-1 |
| Friday 10 AM | 3 | Final touch for week-old leads |

---

## Local Development

```bash
bash build_repl.sh agents/outreach
cd agents/outreach
pip install -r requirements.txt
export $(grep -v '^#' .env | xargs)

# Always test with dry_run: true first
uvicorn main:app --reload --port 8004
```

Dry-run test (no Twilio calls, no real sends):
```bash
curl -s -X POST http://localhost:8004/run \
  -H "Content-Type: application/json" \
  -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -d '{
    "touch_number": 1,
    "max_leads": 5,
    "dry_run": true
  }' | python -m json.tool
```

---

## Replit Deploy Checklist

- [ ] Register A2P 10DLC brand + campaign in Twilio Console before any live send
- [ ] Create Repl → Import from GitHub → `agents/outreach/`
- [ ] Run `bash build_repl.sh agents/outreach` locally, commit `shared/` copy
- [ ] Add 6 secrets in Replit Secrets panel
- [ ] Run a `dry_run: true` test first — verify leads and messages look correct
- [ ] Send touch-1 to 5 real leads manually before scheduling bulk runs
- [ ] Set up Replit Scheduled Jobs per schedule above
