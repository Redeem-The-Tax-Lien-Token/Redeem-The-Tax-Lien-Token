# ARV/MAO Agent

Pulls sold comparables from ATTOM, sends them to Claude for analysis, and returns
Low/Mid/High ARV estimates plus the Wholesale MAO (`ARV_mid × 70% − repairs − $10K`).

---

## Endpoint Contract

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | none | Health check — `{"status":"ok","agent":"arv-mao"}` |
| POST | `/run` | X-Internal-Key | Run ARV/MAO analysis (see request/response below) |
| GET | `/version` | none | Git commit hash of running deploy |

### POST /run — Request

```json
{
  "address":         "1234 N Sherman Dr",
  "city":            "Indianapolis",
  "state":           "IN",
  "zip":             "46201",
  "repair_estimate": 25000,
  "lead_id":         null
}
```

`lead_id` is optional. If provided, the agent writes an `arv_mid / mao` row to the
`deals` table and returns `deal_id` in the response.

### POST /run — Response

```json
{
  "arv_low":         145000,
  "arv_mid":         162500,
  "arv_high":        178000,
  "arv_confidence":  "medium",
  "repair_estimate": 25000,
  "mao":             88750,
  "comps_used":      5,
  "notes":           "Weighted 3 closest comps within 0.3 mi sold in last 4 months.",
  "address":         "1234 N Sherman Dr",
  "city":            "Indianapolis",
  "state":           "IN",
  "zip":             "46201",
  "comps":           [...],
  "deal_id":         null,
  "db_warning":      null
}
```

**MAO formula** (§ 2.2 of master-context):
`MAO = (ARV_mid × 0.70) − repair_estimate − 10000`

---

## Secrets Required (Replit Secrets panel)

| Secret | Description |
|--------|-------------|
| `DATABASE_URL` | Replit PostgreSQL — auto-injected |
| `INTERNAL_API_KEY` | Shared secret for X-Internal-Key header |
| `ANTHROPIC_API_KEY` | Claude API key |
| `ATTOM_API_KEY` | ATTOM property data API key |
| `CLAUDE_MODEL` | Optional — defaults to `claude-sonnet-4-20250514` |

---

## Local Development

```bash
# From repo root — shared/ is already on the Python path this way
pip install -r agents/arv-mao/requirements.txt
export $(cat .env | xargs)
uvicorn agents.arv-mao.main:app --reload
```

Or use `build_repl.sh` to copy `shared/` into the agent folder:

```bash
bash build_repl.sh agents/arv-mao
cd agents/arv-mao
pip install -r requirements.txt
uvicorn main:app --reload
```

## Test curl

```bash
curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -d '{
    "address": "1234 N Sherman Dr",
    "city": "Indianapolis",
    "state": "IN",
    "zip": "46201",
    "repair_estimate": 25000
  }' | python -m json.tool
```

---

## Replit Deploy Checklist

- [ ] Create Repl → Import from GitHub → `agents/arv-mao/`
- [ ] Run `bash build_repl.sh agents/arv-mao` locally first, commit the `shared/` copy
- [ ] Add all 5 secrets in Replit Secrets panel
- [ ] Test with curl against the Replit URL
- [ ] Confirm `deal_id` is populated when `lead_id` is passed
