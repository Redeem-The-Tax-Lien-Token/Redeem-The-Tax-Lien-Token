# List Puller Agent

Queries ATTOM for distressed / motivated-seller property lists by zip code,
deduplicates against the existing `leads` table, and batch-inserts new records.
This is a scheduled/manual trigger — it does not need Always-On.

---

## Endpoint Contract

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | none | Health check |
| POST | `/run` | X-Internal-Key | Pull list from ATTOM and write new leads |
| GET | `/version` | none | Git commit hash |

### POST /run — Request

```json
{
  "zip_codes":        ["46201", "46202", "46218", "46222"],
  "motivation_types": ["pre_foreclosure", "high_equity"],
  "property_type":    "SFR",
  "min_equity_pct":   30,
  "min_years_owned":  2,
  "max_per_zip":      50
}
```

| Field | Default | Notes |
|-------|---------|-------|
| `zip_codes` | required | Indianapolis zips — see reference below |
| `motivation_types` | `["pre_foreclosure","high_equity"]` | Any combo of 4 types |
| `property_type` | `"SFR"` | ATTOM type string |
| `min_equity_pct` | `30.0` | Minimum equity % filter for `high_equity` pulls |
| `min_years_owned` | `2` | Passed to ATTOM; filters short-term owners |
| `max_per_zip` | `50` | Cap per zip × motivation combination (max 200) |

**Motivation types:**

| Type | ATTOM Endpoint | Description |
|------|---------------|-------------|
| `pre_foreclosure` | `/preforeclosure/snapshot` | LIS, NOD, headed to auction |
| `high_equity` | `/property/snapshot?mineq=` | Equity ≥ `min_equity_pct` |
| `tax_delinquent` | `/property/snapshot?taxdelinquent=Y` | Tax-delinquent owners |
| `vacant` | `/property/snapshot?vacant=Y` | Vacant properties |

### POST /run — Response

```json
{
  "new_leads":          47,
  "skipped_duplicates": 12,
  "attom_errors":       0,
  "zips_processed":     4,
  "motivation_summary": {
    "pre_foreclosure": 23,
    "high_equity":     24
  },
  "sample_addresses": [
    "1234 N Sherman Dr, Indianapolis 46201",
    "4512 E 25th St, Indianapolis 46218"
  ]
}
```

All new leads are inserted with `status = 'new'` and `list_source = 'list_puller'`.
Duplicate `attom_id` values are skipped via `ON CONFLICT DO NOTHING`.

---

## Indianapolis Zip Code Reference

Core wholesale target zips (update as market changes):

```
46201 46202 46203 46204 46205 46208
46218 46219 46220 46222 46224 46226
46228 46229 46235 46236 46239 46241
```

---

## Secrets Required (Replit Secrets panel)

| Secret | Description |
|--------|-------------|
| `DATABASE_URL` | Replit PostgreSQL — auto-injected |
| `INTERNAL_API_KEY` | Shared secret for X-Internal-Key header |
| `ATTOM_API_KEY` | ATTOM property data API key |

---

## Suggested Run Schedule (Replit Scheduled Jobs)

| Schedule | Motivation Types | Zips |
|----------|-----------------|------|
| Weekly Monday 7 AM | `pre_foreclosure` | All core zips |
| Weekly Wednesday 7 AM | `high_equity`, `tax_delinquent` | All core zips |
| Monthly 1st | `vacant` | All core zips |

Configure in Replit → Tools → Scheduled Jobs.

---

## Local Development

```bash
bash build_repl.sh agents/list-puller
cd agents/list-puller
pip install -r requirements.txt
export $(grep -v '^#' .env | xargs)
uvicorn main:app --reload --port 8002
```

Test curl:
```bash
curl -s -X POST http://localhost:8002/run \
  -H "Content-Type: application/json" \
  -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -d '{
    "zip_codes": ["46201"],
    "motivation_types": ["pre_foreclosure"],
    "max_per_zip": 10
  }' | python -m json.tool
```

---

## Replit Deploy Checklist

- [ ] Create Repl → Import from GitHub → `agents/list-puller/`
- [ ] Run `bash build_repl.sh agents/list-puller` locally, commit `shared/` copy
- [ ] Add 3 secrets in Replit Secrets panel
- [ ] Test with one zip + `max_per_zip: 5` before a full run
- [ ] Set up Replit Scheduled Jobs for automated weekly pulls
- [ ] After first run, verify `leads` table has new rows in Replit DB UI
