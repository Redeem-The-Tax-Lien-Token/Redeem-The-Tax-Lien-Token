"""
Session-based authentication for the CRM Dashboard.
Credentials are stored as env vars — no user table needed for a single operator.

Setup:
  1. Generate a password hash:
     python -c "from passlib.context import CryptContext; \\
                print(CryptContext(schemes=['bcrypt']).hash('yourpassword'))"
  2. Set DASHBOARD_USERNAME and DASHBOARD_PASSWORD_HASH in Replit Secrets.
  3. Set SESSION_SECRET_KEY to a long random string (secrets.token_hex(32)).
"""

import os

from fastapi import Request
from fastapi.responses import RedirectResponse
from passlib.context import CryptContext

_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

DASHBOARD_USERNAME     = os.environ.get("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD_HASH = os.environ.get("DASHBOARD_PASSWORD_HASH", "")


def verify_password(plain: str) -> bool:
    if not DASHBOARD_PASSWORD_HASH:
        return False
    return _ctx.verify(plain, DASHBOARD_PASSWORD_HASH)


def is_authenticated(request: Request) -> bool:
    return request.session.get("user") == DASHBOARD_USERNAME


def require_auth(request: Request):
    """Call at the top of every protected route. Redirects to /login if not authed."""
    if not is_authenticated(request):
        raise _LoginRedirect()


class _LoginRedirect(Exception):
    pass


# ── Flash message helpers ──────────────────────────────────────────────────────

def flash(request: Request, message: str, category: str = "success") -> None:
    request.session["_flash"] = {"msg": message, "cat": category}


def pop_flash(request: Request) -> dict | None:
    return request.session.pop("_flash", None)
