-- wholesale-brrrr-system: Canonical database schema
-- Single source of truth. Run once via: python db-bootstrap/migrate.py
-- Tables: leads, outreach_log, lead_scores, deals, buyers, agent_events

-- ─── EXTENSIONS ──────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─── TABLE: leads ─────────────────────────────────────────────────────────────
-- Central CRM record. One row per property / seller contact.
CREATE TABLE IF NOT EXISTS leads (
    id                   SERIAL PRIMARY KEY,
    address              TEXT NOT NULL,
    city                 TEXT,
    state                TEXT NOT NULL DEFAULT 'IN',
    zip                  TEXT,
    owner_name           TEXT,
    phone                TEXT,
    email                TEXT,
    attom_id             TEXT UNIQUE,
    motivation_type      TEXT,              -- tax_delinquent | pre_foreclosure | vacant | high_equity
    equity_pct           NUMERIC(5,2),
    list_source          TEXT,              -- attom_distressed | list_puller | manual | csv_import
    segment              TEXT,              -- user-defined group tag (e.g. 'Q3-Indy-NE', 'csv-batch-1')
    skip_traced_at       TIMESTAMPTZ,
    dnc_checked          BOOLEAN NOT NULL DEFAULT FALSE,
    status               TEXT NOT NULL DEFAULT 'new'
                         CHECK (status IN (
                             'new','traced','contacted',
                             'hot','warm','cold','dnc',
                             'under_contract','closed','dead'
                         )),
    manual_override_at   TIMESTAMPTZ,
    manual_override_by   TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── TABLE: outreach_log ──────────────────────────────────────────────────────
-- Every SMS / email / voicemail sent to or received from a lead.
CREATE TABLE IF NOT EXISTS outreach_log (
    id          SERIAL PRIMARY KEY,
    lead_id     INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    message     TEXT,
    channel     TEXT NOT NULL DEFAULT 'sms'
                CHECK (channel IN ('sms','email','voicemail','direct_mail')),
    direction   TEXT NOT NULL
                CHECK (direction IN ('outbound','inbound')),
    status      TEXT NOT NULL DEFAULT 'sent'
                CHECK (status IN ('sent','delivered','failed','received','undelivered')),
    twilio_sid  TEXT,
    from_number TEXT,
    to_number   TEXT,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── TABLE: lead_scores ───────────────────────────────────────────────────────
-- Intake Agent's Claude classification result per inbound reply.
CREATE TABLE IF NOT EXISTS lead_scores (
    id             SERIAL PRIMARY KEY,
    lead_id        INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    score          INTEGER CHECK (score BETWEEN 0 AND 100),
    classification TEXT NOT NULL
                   CHECK (classification IN ('HOT','WARM','COLD','DNC','OTHER')),
    raw_reply      TEXT,
    reason         TEXT,
    classified_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── TABLE: deals ─────────────────────────────────────────────────────────────
-- One row per deal attempt (ARV run + offer). A lead may have multiple deals.
CREATE TABLE IF NOT EXISTS deals (
    id              SERIAL PRIMARY KEY,
    lead_id         INTEGER NOT NULL REFERENCES leads(id) ON DELETE RESTRICT,
    arv_low         NUMERIC(12,2),
    arv_mid         NUMERIC(12,2),
    arv_high        NUMERIC(12,2),
    arv_confidence  TEXT CHECK (arv_confidence IN ('high','medium','low')),
    repair_estimate NUMERIC(12,2),
    mao             NUMERIC(12,2),      -- computed: (arv_mid * 0.70) - repair_estimate - 10000
    offer_amount    NUMERIC(12,2),
    offer_sent_at   TIMESTAMPTZ,
    psa_signed_at   TIMESTAMPTZ,
    assignment_fee  NUMERIC(12,2),
    status          TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN (
                        'new','offer_sent','under_contract',
                        'assigned','closed','dead'
                    )),
    closed_at       TIMESTAMPTZ,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── TABLE: buyers ────────────────────────────────────────────────────────────
-- Cash buyer database. Segmented for deal-marketing blasts.
CREATE TABLE IF NOT EXISTS buyers (
    id             SERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    phone          TEXT,
    email          TEXT,
    buy_box_min    NUMERIC(12,2),
    buy_box_max    NUMERIC(12,2),
    areas          TEXT[],             -- array of zip codes or neighborhood slugs
    strategy       TEXT CHECK (strategy IN ('wholesale','brrrr','fix_flip','rental','any')),
    tier           INTEGER NOT NULL DEFAULT 3
                   CHECK (tier BETWEEN 1 AND 3),    -- 1=A (verified closers), 2=B, 3=C
    last_active_at TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── TABLE: agent_events ──────────────────────────────────────────────────────
-- Audit log of every HTTP call between agents.
CREATE TABLE IF NOT EXISTS agent_events (
    id           SERIAL PRIMARY KEY,
    source_agent TEXT NOT NULL,
    target_agent TEXT NOT NULL,
    payload      JSONB,
    response     JSONB,
    status_code  INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── INDEXES ──────────────────────────────────────────────────────────────────

-- leads: pipeline queries, phone lookups, dedup on attom_id
CREATE INDEX IF NOT EXISTS idx_leads_status      ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_zip         ON leads(zip);
CREATE INDEX IF NOT EXISTS idx_leads_phone       ON leads(phone);
CREATE INDEX IF NOT EXISTS idx_leads_attom_id    ON leads(attom_id);
CREATE INDEX IF NOT EXISTS idx_leads_created_at  ON leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_segment     ON leads(segment);

-- additive migration: add segment column to existing deployments
ALTER TABLE leads ADD COLUMN IF NOT EXISTS segment TEXT;

-- outreach_log: per-lead history, inbound reply feed
CREATE INDEX IF NOT EXISTS idx_outreach_lead_id    ON outreach_log(lead_id);
CREATE INDEX IF NOT EXISTS idx_outreach_direction  ON outreach_log(direction, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_outreach_twilio_sid ON outreach_log(twilio_sid)
    WHERE twilio_sid IS NOT NULL;

-- lead_scores: latest classification per lead
CREATE INDEX IF NOT EXISTS idx_scores_lead_id        ON lead_scores(lead_id);
CREATE INDEX IF NOT EXISTS idx_scores_classification ON lead_scores(classification);

-- deals: per-lead and pipeline status
CREATE INDEX IF NOT EXISTS idx_deals_lead_id ON deals(lead_id);
CREATE INDEX IF NOT EXISTS idx_deals_status  ON deals(status);

-- buyers: segmentation for deal-marketing blast
CREATE INDEX IF NOT EXISTS idx_buyers_tier     ON buyers(tier);
CREATE INDEX IF NOT EXISTS idx_buyers_strategy ON buyers(strategy);

-- agent_events: debugging and audit trail
CREATE INDEX IF NOT EXISTS idx_events_source     ON agent_events(source_agent, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON agent_events(created_at DESC);

-- ─── TRIGGER FUNCTION: set_updated_at ─────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS leads_set_updated_at ON leads;
CREATE TRIGGER leads_set_updated_at
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS deals_set_updated_at ON deals;
CREATE TRIGGER deals_set_updated_at
    BEFORE UPDATE ON deals
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─── TRIGGER FUNCTION: block_outbound_to_dnc ──────────────────────────────────
-- Fail-closed: raises an exception if any agent tries to log an outbound SMS
-- to a lead whose status is 'dnc'. Prevents accidental TCPA violations at the
-- database layer regardless of which agent is inserting.
CREATE OR REPLACE FUNCTION block_outbound_to_dnc()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.direction = 'outbound' AND NEW.channel = 'sms' THEN
        IF EXISTS (
            SELECT 1 FROM leads WHERE id = NEW.lead_id AND status = 'dnc'
        ) THEN
            RAISE EXCEPTION
                'TCPA guard: outbound SMS blocked for DNC lead (lead_id=%)', NEW.lead_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS outreach_dnc_guard ON outreach_log;
CREATE TRIGGER outreach_dnc_guard
    BEFORE INSERT ON outreach_log
    FOR EACH ROW EXECUTE FUNCTION block_outbound_to_dnc();

-- ─── TRIGGER FUNCTION: sync_dnc_to_lead ───────────────────────────────────────
-- When Intake Agent stores a DNC classification, automatically flip the lead
-- status to 'dnc' so the outreach_dnc_guard fires on any future insert.
CREATE OR REPLACE FUNCTION sync_dnc_to_lead()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.classification = 'DNC' THEN
        UPDATE leads
        SET status = 'dnc'
        WHERE id = NEW.lead_id
          AND status != 'dnc';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS score_sync_dnc ON lead_scores;
CREATE TRIGGER score_sync_dnc
    AFTER INSERT ON lead_scores
    FOR EACH ROW EXECUTE FUNCTION sync_dnc_to_lead();
