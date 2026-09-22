# API Key Rotation Runbook

Follow these steps to rotate any secret without downtime. Work through them in order —
never restart an agent until every agent has the new value.

---

## INTERNAL_API_KEY rotation

This key authenticates agent-to-agent calls (outreach → crm-dashboard, etc.).
All agents share the same key and must be updated atomically.

**Affected agents (7):** arv-mao, intake, list-puller, skip-tracer, outreach,
deal-marketing, crm-dashboard.

1. **Generate a new key** (run locally or in any Replit shell):
   ```
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
2. **Stage the value** — copy it to your clipboard. Do NOT update any agent yet.
3. **Update all agents in Replit Secrets** — open each agent's Replit workspace,
   go to Secrets, and set `INTERNAL_API_KEY` to the new value. Do this for all 7
   agents before restarting any of them.
4. **Restart agents one by one** — outreach last (it is the most critical).
   Each restart takes ~10 s. Watch the Replit console log for `startup check passed`.
5. **Revoke the old key** — it is now safe to delete the old value from any notes
   or scratch files.
6. **Smoke test** — send a test SMS in dry_run mode and verify the response returns
   200 OK (not 401).

---

## ANTHROPIC_API_KEY rotation

1. In the Anthropic Console, create a new API key. Note it down.
2. Set `ANTHROPIC_API_KEY` in every agent's Replit Secrets (all 7 agents).
3. Restart agents one by one.
4. In the Anthropic Console, delete the old key.

---

## TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN rotation

1. In the Twilio Console, rotate the auth token (you can generate a secondary token
   first, then primary-swap after all agents are updated).
2. Update `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` in the Replit Secrets of:
   - agents/outreach
   - agents/deal-marketing
   - agents/crm-dashboard
3. Restart those three agents.
4. The outreach agent will run `twilio_startup_check()` on startup — if it fails,
   the agent will not start and will log `Twilio self-check FAILED`. Fix the credentials
   before proceeding.
5. In the Twilio Console, invalidate the old auth token.

---

## ATTOM_API_KEY / TRACERFY_API_KEY / GOOGLE_MAPS_API_KEY rotation

These are single-agent keys. Update the secret in the relevant agent's Replit workspace
and restart that agent only.

| Key | Agent |
|-----|-------|
| `ATTOM_API_KEY` | list-puller, arv-mao |
| `TRACERFY_API_KEY` | skip-tracer |
| `GOOGLE_MAPS_API_KEY` | arv-mao |

---

## Notes

- Never commit real secret values. `.env` is gitignored. Use `.env.example` as the
  template (it contains no real values).
- In dry_run mode (`SYSTEM_MODE=dry_run`), Twilio calls are skipped, so a Twilio
  credential failure will not surface until you switch to live mode. Always test
  credentials in live mode against a test number before enabling live outreach.
- The crm-dashboard's `INTERNAL_API_KEY` check is fail-fast: the app will refuse to
  start if the variable is missing, catching rotation errors immediately.
