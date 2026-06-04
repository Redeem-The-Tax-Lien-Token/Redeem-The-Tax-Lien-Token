# Intake Agent

Receives inbound SMS replies via Twilio webhook, classifies them with Claude
(HOT / WARM / COLD / DNC / OTHER), logs every message, and updates the lead's
pipeline status. This is the only agent that must run **Always-On** in Replit
because it is a live webhook receiver.

---

## Endpoint Contract

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | none | Health check — `{"status":"ok","agent":"intake"}` |
| POST | `/run` | X-Internal-Key | Manual/synchronous classification |
| GET | `/version` | none | Git commit hash |
| POST | `/sms/inbound` | Twilio signature | Twilio webhook — classify inbound SMS |

### POST /sms/inbound

Twilio POSTs here on every inbound reply. Returns TwiML immediately; all DB
writes and Claude calls happen in a background task.

**Twilio form fields received:**
- `From` — sender phone in E.164 (`+13175550100`)
- `Body` — raw SMS text

**TwiML response:**
```xml
<Response>
  <Message>Thank you! A team member will follow up shortly.</Message>
</Response>
```

**Background task flow:**
1. Look up lead by phone (tolerates format differences via digit-only match)
2. If no lead found, create a minimal `status=contacted` row so nothing is lost
3. Log the inbound message to `outreach_log`
4. Call Claude → classify as HOT / WARM / COLD / DNC / OTHER
5. Write result to `lead_scores`
6. Update `leads.status` if the transition is valid per `ALLOWED_TRANSITIONS`
   (DNC auto-handled by the `sync_dnc_to_lead` DB trigger)

### POST /run — Manual Classification

```json
{
  "from_number": "+13175550100",
  "body": "Yes I'm interested, what's your offer?",
  "lead_id": 42
}
```

`lead_id` is optional. If provided, writes the score and updates status.

**Response:**
```json
{
  "classification": "HOT",
  "reason": "Seller explicitly expressed interest and asked for an offer.",
  "suggested_reply": "Great! I'll put together an offer shortly. What's the best number to reach you?",
  "db_warning": null
}
```

---

## State Machine

Valid status transitions enforced in `ALLOWED_TRANSITIONS`:

```
new → traced / contacted / hot / warm / cold / dnc
traced → contacted / hot / warm / cold / dnc
contacted → hot / warm / cold / dnc
hot → under_contract / warm / cold / dead / dnc
warm → hot / cold / dead / dnc
cold → warm / dead / dnc
dnc → (terminal)
under_contract → closed / dead
closed → (terminal)
dead → (terminal)
```

Invalid transitions are skipped with a warning log — the lead status is not
changed. Use the CRM dashboard force-override to manually move a lead past a
guard.

---

## Secrets Required (Replit Secrets panel)

| Secret | Description |
|--------|-------------|
| `DATABASE_URL` | Replit PostgreSQL — auto-injected |
| `INTERNAL_API_KEY` | Shared secret for X-Internal-Key header |
| `ANTHROPIC_API_KEY` | Claude API key |
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token (also used to validate webhook signatures) |
| `TWILIO_FROM_NUMBER` | Your registered 10DLC number |
| `CLAUDE_MODEL` | Optional — defaults to `claude-sonnet-4-20250514` |
| `TWILIO_VALIDATE_SIGNATURE` | Set `false` for local dev only — defaults to `true` |

---

## Twilio Console Setup

1. Go to **Phone Numbers → Manage → Active Numbers**
2. Select your 10DLC number
3. Under **Messaging Configuration → A MESSAGE COMES IN**:
   - Webhook: `https://<your-repl-url>/sms/inbound`
   - Method: `HTTP POST`
4. Save

---

## Local Development

```bash
# From repo root
bash build_repl.sh agents/intake
cd agents/intake
pip install -r requirements.txt

# Disable signature validation for local testing
export TWILIO_VALIDATE_SIGNATURE=false
export $(grep -v '^#' .env | xargs)

uvicorn main:app --reload --port 8001
```

Test with curl:
```bash
curl -s -X POST http://localhost:8001/run \
  -H "Content-Type: application/json" \
  -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -d '{"from_number":"+13175550100","body":"Yes, what is your offer?"}'
```

Simulate a Twilio webhook (no signature check in dev):
```bash
curl -s -X POST http://localhost:8001/sms/inbound \
  -d "From=%2B13175550100&Body=Yes+I+am+interested"
```

---

## Replit Deploy Checklist

- [ ] Enable **Always-On** — this agent must stay live for Twilio webhooks
- [ ] Create Repl → Import from GitHub → `agents/intake/`
- [ ] Run `bash build_repl.sh agents/intake` locally, commit the `shared/` copy
- [ ] Add all 7 secrets in Replit Secrets panel
- [ ] Set Twilio webhook URL to `https://<repl-url>/sms/inbound`
- [ ] Send a test SMS to your 10DLC number — verify classification appears in DB
- [ ] Confirm DNC reply (`STOP`) flips `leads.status` to `dnc`
