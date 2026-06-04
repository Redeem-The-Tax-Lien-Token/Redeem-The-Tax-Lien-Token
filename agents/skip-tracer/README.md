# Skip Tracer Agent

Pulls untraced leads from the `leads` table, calls Tracerfy instant trace,
batch DNC-scrubs all returned phones, and updates each lead with the best
clean phone and email. Does not need Always-On — run on-demand or on a schedule.

---

## Endpoint Contract

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | none | Health check |
| POST | `/run` | X-Internal-Key | Run skip trace batch |
| GET | `/version` | none | Git commit hash |

### POST /run — Request

```json
{
  "lead_ids":   null,
  "max_leads":  50,
  "batch_size": 50
}
```

| Field | Default | Notes |
|-------|---------|-------|
| `lead_ids` | `null` | Trace specific IDs. If `null`, auto-pulls `status IN ('new','traced') AND skip_traced_at IS NULL` |
| `max_leads` | `50` | Max leads to process per run (when using auto-pull) |
| `batch_size` | `50` | Max phones per DNC scrub call — 1 credit/phone |

### POST /run — Response

```json
{
  "processed":          50,
  "traced_ok":          38,
  "deceased_skipped":   2,
  "litigator_skipped":  1,
  "no_phone_found":     5,
  "all_dnc":            4,
  "trace_errors":       0,
  "leads_updated": {
    "101": "traced→+13175550100",
    "102": "deceased→dead",
    "103": "all_phones_dnc→dnc",
    "104": "litigator→dnc",
    "105": "trace_error"
  }
}
```

---

## Processing Logic

For each lead, in order:

1. **Instant trace** via Tracerfy `/v1/trace/instant`
2. **Deceased check** → if `deceased = true`, set `status = 'dead'`, stop
3. **Litigator check** → if `litigator_flag = true`, set `status = 'dnc'`, stop
4. **Batch DNC scrub** (one call for all phones across all leads — most efficient)
5. **Phone selection** → pick rank-1 clean mobile from scrubbed set
6. **All-DNC check** → if phones existed but none passed scrub, set `status = 'dnc'`
7. **No-phone check** → if trace returned no phones, mark `skip_traced_at` only
8. **Happy path** → update `phone`, `email`, `skip_traced_at`, `dnc_checked = TRUE`, `status = 'traced'`

Status updates only apply when the transition is valid — the `UPDATE` query includes `WHERE status NOT IN ('dnc', 'closed', 'dead')` to protect terminal states.

---

## DNC Scrub — FAIL-CLOSED

Per §5.5 of master-context: if the Tracerfy DNC scrub API errors for any reason,
`scrub_phones()` returns an **empty set**. This means zero phones pass through
to the outreach agent. This is intentional — a failed scrub must never let
unverified phones into the SMS pipeline.

---

## Credit Cost Reference

| Operation | Credits |
|-----------|---------|
| Instant trace — hit | 5 |
| Instant trace — miss | 0 |
| DNC scrub per phone | 1 |

Example: 50 leads × 5 credits + 150 phones × 1 credit = 400 credits per run.

---

## Secrets Required

| Secret | Description |
|--------|-------------|
| `DATABASE_URL` | Replit PostgreSQL — auto-injected |
| `INTERNAL_API_KEY` | Shared secret for X-Internal-Key header |
| `TRACERFY_API_KEY` | Tracerfy API key |

---

## Suggested Schedule

Run after `list-puller` finishes (allow 30 min gap for ATTOM data to settle):

- **Monday 8 AM** — trace `max_leads: 200` (after Monday list pull)
- **Wednesday 8 AM** — trace `max_leads: 200`

Or trigger manually: run list-puller → wait → run skip-tracer → wait → run outreach.

---

## Local Development

```bash
bash build_repl.sh agents/skip-tracer
cd agents/skip-tracer
pip install -r requirements.txt
export $(grep -v '^#' .env | xargs)
uvicorn main:app --reload --port 8003
```

Test with specific lead IDs (no ATTOM calls needed):
```bash
curl -s -X POST http://localhost:8003/run \
  -H "Content-Type: application/json" \
  -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -d '{"lead_ids": [1, 2, 3], "max_leads": 3}' | python -m json.tool
```

---

## Replit Deploy Checklist

- [ ] Create Repl → Import from GitHub → `agents/skip-tracer/`
- [ ] Run `bash build_repl.sh agents/skip-tracer` locally, commit `shared/` copy
- [ ] Add 3 secrets in Replit Secrets panel
- [ ] Test with `lead_ids: [<one real id>]` before running a full batch
- [ ] Verify `leads.phone` is populated and `leads.dnc_checked = true` after run
- [ ] Set up Replit Scheduled Jobs to run after list-puller
