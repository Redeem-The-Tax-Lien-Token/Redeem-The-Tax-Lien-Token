"""
CRM Dashboard Agent — FastAPI + Jinja2 web app.

Routes:
  GET  /health                 health check (JSON)
  GET  /version                git commit hash (JSON)
  GET  /login  POST /login     login form
  GET  /logout                 clear session
  GET  /                       leads pipeline
  GET  /leads/{id}             lead detail
  POST /leads/{id}/status      update lead status (with force-override)
  POST /leads/{id}/sms         send manual SMS from lead detail page
  GET  /deals                  all deals
  GET  /buyers                 buyer list
  POST /buyers                 add a buyer
  GET  /outreach               outreach log
  GET  /sms/send               manual SMS sender
  POST /sms/send               send manual SMS
  GET  /kpi                    KPI dashboard
"""

import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware
from twilio.rest import Client

# ── Import path ───────────────────────────────────────────────────────────────
_here      = Path(__file__).parent
_repo_root = _here.parent.parent
for _p in [str(_here), str(_repo_root)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.db import session_ctx   # noqa: E402
from auth import (                  # noqa: E402
    flash, is_authenticated, pop_flash, require_auth, verify_password,
    DASHBOARD_USERNAME, _LoginRedirect,
)

# ── Config ────────────────────────────────────────────────────────────────────

AGENT_NAME          = "crm-dashboard"
SESSION_SECRET_KEY  = os.environ["SESSION_SECRET_KEY"]
TWILIO_ACCOUNT_SID  = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER  = os.environ.get("TWILIO_FROM_NUMBER", "")
INTERNAL_API_KEY    = os.environ.get("INTERNAL_API_KEY", "")
_INDY_TZ            = ZoneInfo("America/Indiana/Indianapolis")

log = logging.getLogger(AGENT_NAME)
logging.basicConfig(level=logging.INFO)

_twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID else None

app = FastAPI(title="CRM Dashboard")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)

templates = Jinja2Templates(directory=str(_here / "templates"))

# ── State machine (mirrors intake agent + migrate.sql CHECK constraint) ───────
ALLOWED_TRANSITIONS: dict[str, frozenset] = {
    "new":            frozenset({"traced", "contacted", "hot", "warm", "cold", "dnc", "dead"}),
    "traced":         frozenset({"contacted", "hot", "warm", "cold", "dnc", "dead"}),
    "contacted":      frozenset({"hot", "warm", "cold", "dnc", "dead"}),
    "hot":            frozenset({"under_contract", "warm", "cold", "dead", "dnc"}),
    "warm":           frozenset({"hot", "cold", "dead", "dnc"}),
    "cold":           frozenset({"warm", "dead", "dnc"}),
    "dnc":            frozenset(),
    "under_contract": frozenset({"closed", "dead"}),
    "closed":         frozenset(),
    "dead":           frozenset(),
}

ALL_STATUSES = list(ALLOWED_TRANSITIONS.keys())
OPT_OUT = "Reply STOP to unsubscribe."


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ctx(request: Request, extra: dict = None) -> dict:
    """Base template context with flash message."""
    base = {
        "request":    request,
        "flash":      pop_flash(request),
        "user":       request.session.get("user", ""),
        "all_statuses": ALL_STATUSES,
        "allowed":    {s: sorted(t) for s, t in ALLOWED_TRANSITIONS.items()},
    }
    if extra:
        base.update(extra)
    return base


def _is_calling_hours() -> bool:
    now = datetime.now(_INDY_TZ)
    return 8 <= now.hour < 21


def _login_redirect():
    return RedirectResponse("/login", status_code=302)


# ── Standard JSON endpoints ───────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "agent": AGENT_NAME}


@app.get("/version")
def version():
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, cwd=str(_repo_root),
        ).decode().strip()
    except Exception:
        commit = "unknown"
    return {"agent": AGENT_NAME, "commit": commit}


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", _ctx(request))


@app.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if username == DASHBOARD_USERNAME and verify_password(password):
        request.session["user"] = username
        return RedirectResponse("/", status_code=302)
    flash(request, "Invalid credentials", "error")
    return templates.TemplateResponse("login.html", _ctx(request), status_code=401)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ── Pipeline ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def pipeline(request: Request, status: str = "", q: str = "", segment: str = ""):
    if not is_authenticated(request):
        return _login_redirect()
    with session_ctx() as db:
        # Counts per status for the summary bar
        counts_rows = db.execute(
            text("SELECT status, COUNT(*) FROM leads GROUP BY status ORDER BY status")
        ).fetchall()
        counts = {r[0]: r[1] for r in counts_rows}

        # Distinct segments with counts for the segment filter bar
        seg_rows = db.execute(
            text("""
                SELECT segment, COUNT(*) FROM leads
                WHERE segment IS NOT NULL AND segment != ''
                GROUP BY segment ORDER BY segment
            """)
        ).fetchall()
        segments = [{"name": r[0], "count": r[1]} for r in seg_rows]

        # Leads query with optional filters
        where_clauses = []
        params: dict = {}
        if status:
            where_clauses.append("status = :status")
            params["status"] = status
        if segment:
            where_clauses.append("segment = :segment")
            params["segment"] = segment
        if q:
            where_clauses.append(
                "(owner_name ILIKE :q OR address ILIKE :q OR phone ILIKE :q OR zip ILIKE :q)"
            )
            params["q"] = f"%{q}%"
        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        leads = db.execute(
            text(f"""
                SELECT id, address, city, zip, owner_name, phone,
                       status, motivation_type, equity_pct, segment,
                       created_at, updated_at
                FROM   leads
                {where}
                ORDER BY
                  CASE status
                    WHEN 'hot'            THEN 1
                    WHEN 'under_contract' THEN 2
                    WHEN 'warm'           THEN 3
                    WHEN 'contacted'      THEN 4
                    WHEN 'traced'         THEN 5
                    WHEN 'new'            THEN 6
                    WHEN 'cold'           THEN 7
                    WHEN 'dead'           THEN 8
                    WHEN 'dnc'            THEN 9
                    WHEN 'closed'         THEN 10
                    ELSE 11
                  END,
                  updated_at DESC
                LIMIT 200
            """),
            params,
        ).fetchall()
        leads = [dict(r._mapping) for r in leads]

    return templates.TemplateResponse(
        "pipeline.html",
        _ctx(request, {
            "leads":          leads,
            "counts":         counts,
            "segments":       segments,
            "filter_status":  status,
            "filter_segment": segment,
            "q":              q,
        }),
    )


# ── Lead detail ───────────────────────────────────────────────────────────────

@app.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_detail(request: Request, lead_id: int):
    if not is_authenticated(request):
        return _login_redirect()
    with session_ctx() as db:
        lead = db.execute(
            text("SELECT * FROM leads WHERE id = :id"), {"id": lead_id}
        ).fetchone()
        if not lead:
            flash(request, f"Lead {lead_id} not found", "error")
            return RedirectResponse("/", status_code=302)
        lead = dict(lead._mapping)

        outreach = db.execute(
            text("""
                SELECT * FROM outreach_log WHERE lead_id = :id
                ORDER BY sent_at DESC LIMIT 50
            """),
            {"id": lead_id},
        ).fetchall()

        scores = db.execute(
            text("""
                SELECT * FROM lead_scores WHERE lead_id = :id
                ORDER BY classified_at DESC LIMIT 10
            """),
            {"id": lead_id},
        ).fetchall()

        deal = db.execute(
            text("""
                SELECT * FROM deals WHERE lead_id = :id
                ORDER BY created_at DESC LIMIT 1
            """),
            {"id": lead_id},
        ).fetchone()

    return templates.TemplateResponse(
        "lead_detail.html",
        _ctx(request, {
            "lead":    lead,
            "outreach": [dict(r._mapping) for r in outreach],
            "scores":   [dict(r._mapping) for r in scores],
            "deal":     dict(deal._mapping) if deal else None,
            "calling_hours": _is_calling_hours(),
        }),
    )


@app.post("/leads/{lead_id}/status")
async def update_lead_status(
    request:        Request,
    lead_id:        int,
    new_status:     str  = Form(...),
    force_override: bool = Form(False),
):
    if not is_authenticated(request):
        return _login_redirect()

    with session_ctx() as db:
        row = db.execute(
            text("SELECT status FROM leads WHERE id = :id"), {"id": lead_id}
        ).fetchone()
        if not row:
            flash(request, "Lead not found", "error")
            return RedirectResponse(f"/leads/{lead_id}", status_code=302)

        current = row[0]
        valid   = new_status in ALLOWED_TRANSITIONS.get(current, frozenset())

        if not valid and not force_override:
            flash(
                request,
                f"Invalid transition: {current} → {new_status}. "
                "Check 'Force override' to bypass.",
                "error",
            )
            return RedirectResponse(f"/leads/{lead_id}", status_code=302)

        if not valid and force_override:
            # Stamp override fields — tells automated agents to back off
            db.execute(
                text("""
                    UPDATE leads
                    SET    status             = :status,
                           manual_override_at = NOW(),
                           manual_override_by = :by
                    WHERE  id = :id
                """),
                {"status": new_status, "by": request.session.get("user"), "id": lead_id},
            )
            flash(request, f"Force-override: {current} → {new_status} (logged)", "success")
        else:
            db.execute(
                text("UPDATE leads SET status = :status WHERE id = :id"),
                {"status": new_status, "id": lead_id},
            )
            flash(request, f"Status updated: {current} → {new_status}", "success")

    return RedirectResponse(f"/leads/{lead_id}", status_code=302)


@app.post("/leads/{lead_id}/sms")
async def lead_sms(
    request: Request,
    lead_id: int,
    message: str = Form(...),
):
    if not is_authenticated(request):
        return _login_redirect()

    with session_ctx() as db:
        lead = db.execute(
            text("SELECT phone, status FROM leads WHERE id = :id"), {"id": lead_id}
        ).fetchone()
        if not lead or not lead[0]:
            flash(request, "Lead has no phone number", "error")
            return RedirectResponse(f"/leads/{lead_id}", status_code=302)
        if lead[1] == "dnc":
            flash(request, "Cannot send SMS to DNC lead", "error")
            return RedirectResponse(f"/leads/{lead_id}", status_code=302)

        phone = lead[0]

    full_msg = message if OPT_OUT in message else f"{message} {OPT_OUT}"
    sid = _send_twilio(phone, full_msg)
    if sid:
        with session_ctx() as db:
            db.execute(
                text("""
                    INSERT INTO outreach_log
                        (lead_id, message, channel, direction, status, twilio_sid,
                         from_number, to_number)
                    VALUES
                        (:lid, :msg, 'sms', 'outbound', 'sent', :sid, :from_n, :to_n)
                """),
                {"lid": lead_id, "msg": full_msg, "sid": sid,
                 "from_n": TWILIO_FROM_NUMBER, "to_n": phone},
            )
        flash(request, "SMS sent", "success")
    else:
        flash(request, "Twilio send failed — check logs", "error")

    return RedirectResponse(f"/leads/{lead_id}", status_code=302)


# ── Deals ─────────────────────────────────────────────────────────────────────

@app.get("/deals", response_class=HTMLResponse)
def deals_view(request: Request):
    if not is_authenticated(request):
        return _login_redirect()
    with session_ctx() as db:
        rows = db.execute(
            text("""
                SELECT d.*, l.address, l.city, l.zip, l.owner_name
                FROM   deals d
                JOIN   leads l ON l.id = d.lead_id
                ORDER  BY d.created_at DESC
                LIMIT  200
            """)
        ).fetchall()
    return templates.TemplateResponse(
        "deals.html",
        _ctx(request, {"deals": [dict(r._mapping) for r in rows]}),
    )


# ── Buyers ────────────────────────────────────────────────────────────────────

@app.get("/buyers", response_class=HTMLResponse)
def buyers_view(request: Request):
    if not is_authenticated(request):
        return _login_redirect()
    with session_ctx() as db:
        rows = db.execute(
            text("""
                SELECT * FROM buyers
                ORDER BY tier ASC, last_active_at DESC NULLS LAST
            """)
        ).fetchall()
    return templates.TemplateResponse(
        "buyers.html",
        _ctx(request, {"buyers": [dict(r._mapping) for r in rows]}),
    )


@app.post("/buyers")
async def add_buyer(
    request:     Request,
    name:        str   = Form(...),
    phone:       str   = Form(...),
    email:       str   = Form(""),
    buy_box_min: str   = Form(""),
    buy_box_max: str   = Form(""),
    areas:       str   = Form(""),   # comma-separated zip codes
    strategy:    str   = Form("wholesale"),
    tier:        int   = Form(3),
):
    if not is_authenticated(request):
        return _login_redirect()

    areas_arr = [z.strip() for z in areas.split(",") if z.strip()] if areas else []
    with session_ctx() as db:
        db.execute(
            text("""
                INSERT INTO buyers
                    (name, phone, email, buy_box_min, buy_box_max, areas, strategy, tier)
                VALUES
                    (:name, :phone, :email, :min, :max, :areas, :strategy, :tier)
            """),
            {
                "name":     name,
                "phone":    phone,
                "email":    email or None,
                "min":      float(buy_box_min) if buy_box_min else None,
                "max":      float(buy_box_max) if buy_box_max else None,
                "areas":    areas_arr,
                "strategy": strategy,
                "tier":     tier,
            },
        )
    flash(request, f"Buyer '{name}' added", "success")
    return RedirectResponse("/buyers", status_code=302)


# ── Outreach log ──────────────────────────────────────────────────────────────

@app.get("/outreach", response_class=HTMLResponse)
def outreach_view(request: Request, direction: str = ""):
    if not is_authenticated(request):
        return _login_redirect()
    where = "WHERE o.direction = :dir" if direction else ""
    params = {"dir": direction} if direction else {}
    with session_ctx() as db:
        rows = db.execute(
            text(f"""
                SELECT o.*, l.address, l.owner_name, l.status AS lead_status
                FROM   outreach_log o
                JOIN   leads l ON l.id = o.lead_id
                {where}
                ORDER  BY o.sent_at DESC
                LIMIT  300
            """),
            params,
        ).fetchall()
    return templates.TemplateResponse(
        "outreach.html",
        _ctx(request, {
            "messages":        [dict(r._mapping) for r in rows],
            "filter_direction": direction,
        }),
    )


# ── Manual SMS sender ─────────────────────────────────────────────────────────

@app.get("/sms/send", response_class=HTMLResponse)
def sms_send_get(request: Request):
    if not is_authenticated(request):
        return _login_redirect()
    with session_ctx() as db:
        leads = db.execute(
            text("""
                SELECT id, address, city, phone, status
                FROM   leads
                WHERE  phone IS NOT NULL
                  AND  status NOT IN ('dnc', 'dead', 'closed')
                ORDER  BY updated_at DESC
                LIMIT  500
            """)
        ).fetchall()
    return templates.TemplateResponse(
        "manual_sms.html",
        _ctx(request, {
            "leads":          [dict(r._mapping) for r in leads],
            "calling_hours":  _is_calling_hours(),
        }),
    )


@app.post("/sms/send")
async def sms_send_post(
    request:  Request,
    lead_id:  int = Form(...),
    message:  str = Form(...),
):
    if not is_authenticated(request):
        return _login_redirect()

    with session_ctx() as db:
        lead = db.execute(
            text("SELECT phone, status FROM leads WHERE id = :id"), {"id": lead_id}
        ).fetchone()
        if not lead or not lead[0]:
            flash(request, "Lead not found or has no phone", "error")
            return RedirectResponse("/sms/send", status_code=302)
        if lead[1] == "dnc":
            flash(request, "Cannot send to DNC lead", "error")
            return RedirectResponse("/sms/send", status_code=302)

    phone    = lead[0]
    full_msg = message if OPT_OUT in message else f"{message} {OPT_OUT}"
    sid      = _send_twilio(phone, full_msg)

    if sid:
        with session_ctx() as db:
            db.execute(
                text("""
                    INSERT INTO outreach_log
                        (lead_id, message, channel, direction, status,
                         twilio_sid, from_number, to_number)
                    VALUES
                        (:lid, :msg, 'sms', 'outbound', 'sent', :sid, :from_n, :to_n)
                """),
                {"lid": lead_id, "msg": full_msg, "sid": sid,
                 "from_n": TWILIO_FROM_NUMBER, "to_n": phone},
            )
        flash(request, f"SMS sent to {phone}", "success")
    else:
        flash(request, "Send failed — Twilio not configured or error", "error")

    return RedirectResponse("/sms/send", status_code=302)


# ── KPI Dashboard ─────────────────────────────────────────────────────────────

@app.get("/kpi", response_class=HTMLResponse)
def kpi_view(request: Request):
    if not is_authenticated(request):
        return _login_redirect()
    with session_ctx() as db:
        status_counts = {
            r[0]: r[1] for r in db.execute(
                text("SELECT status, COUNT(*) FROM leads GROUP BY status")
            ).fetchall()
        }
        leads_this_week = db.execute(
            text("SELECT COUNT(*) FROM leads WHERE created_at >= NOW() - INTERVAL '7 days'")
        ).scalar()
        leads_last_week = db.execute(
            text("""
                SELECT COUNT(*) FROM leads
                WHERE created_at >= NOW() - INTERVAL '14 days'
                  AND created_at <  NOW() - INTERVAL '7 days'
            """)
        ).scalar()
        deals_active = db.execute(
            text("SELECT COUNT(*) FROM deals WHERE status NOT IN ('closed','dead')")
        ).scalar()
        revenue_closed = db.execute(
            text("SELECT COALESCE(SUM(assignment_fee), 0) FROM deals WHERE status = 'closed'")
        ).scalar()
        avg_deal = db.execute(
            text("SELECT COALESCE(AVG(assignment_fee), 0) FROM deals WHERE status = 'closed'")
        ).scalar()
        sms_sent_week = db.execute(
            text("""
                SELECT COUNT(*) FROM outreach_log
                WHERE direction = 'outbound'
                  AND sent_at >= NOW() - INTERVAL '7 days'
            """)
        ).scalar()
        inbound_week = db.execute(
            text("""
                SELECT COUNT(*) FROM outreach_log
                WHERE direction = 'inbound'
                  AND sent_at >= NOW() - INTERVAL '7 days'
            """)
        ).scalar()
        hot_leads = db.execute(
            text("""
                SELECT COUNT(*) FROM lead_scores
                WHERE classification = 'HOT'
                  AND classified_at >= NOW() - INTERVAL '30 days'
            """)
        ).scalar()

    total_leads = sum(status_counts.values())
    reply_rate  = round((inbound_week / sms_sent_week * 100), 1) if sms_sent_week else 0

    return templates.TemplateResponse(
        "kpi.html",
        _ctx(request, {
            "status_counts":   status_counts,
            "total_leads":     total_leads,
            "leads_this_week": leads_this_week,
            "leads_last_week": leads_last_week,
            "deals_active":    deals_active,
            "revenue_closed":  float(revenue_closed),
            "avg_deal":        float(avg_deal),
            "sms_sent_week":   sms_sent_week,
            "inbound_week":    inbound_week,
            "reply_rate":      reply_rate,
            "hot_leads":       hot_leads,
        }),
    )


# ── Twilio helper ─────────────────────────────────────────────────────────────

def _send_twilio(to: str, message: str) -> str | None:
    if not _twilio:
        log.warning("Twilio not configured — TWILIO_ACCOUNT_SID missing")
        return None
    try:
        msg = _twilio.messages.create(
            body=message, from_=TWILIO_FROM_NUMBER, to=to
        )
        return msg.sid
    except Exception as exc:
        log.error("Twilio error: %s", exc)
        return None
