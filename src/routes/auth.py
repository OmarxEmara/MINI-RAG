from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks, status
from sqlalchemy import text
from helpers.config import get_settings
from utils.security import hash_password, verify_password, make_access_token
from utils.deps import require_super_admin, get_current_user
from routes.schemes.auth import LoginBody, CreateOrgBody, CreateAdminBody, CreateUserBody
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
from email.message import EmailMessage
import secrets, hmac, hashlib, smtplib

auth = APIRouter(prefix="/auth", tags=["auth"])
#ensure model validation for all routes to get the error early on startup of the endpoint 
# --------------------------------------------------------------------------------------
# Helpers (invite token + email)
# --------------------------------------------------------------------------------------

def _invite_secret():
    # Prefer dedicated secret; fallback to JWT secret if not provided
    s = get_settings()
    return (s.INVITE_TOKEN_HMAC_SECRET or s.JWT_SECRET).encode()

def _generate_token() -> str:
    return secrets.token_urlsafe(32)

def _hash_token(raw: str) -> str:
    return hmac.new(_invite_secret(), msg=raw.encode(), digestmod=hashlib.sha256).hexdigest()

def _invite_expires_at() -> datetime:
    return datetime.utcnow() + timedelta(hours=get_settings().INVITE_TOKEN_TTL_HOURS if hasattr(get_settings(), "INVITE_TOKEN_TTL_HOURS") else get_settings().ACCESS_TTL_MIN)

def _build_invite_link(raw_token: str) -> str:
    s = get_settings()
    base = (getattr(s, "FRONTEND_BASE_URL", "") or getattr(s, "BACKEND_BASE_URL", "") or "").strip()
    if not base:
        # backend path as a last resort (works from Swagger/cURL; copy/paste the token)
        return f"/auth/password/setup/verify?token={raw_token}"
    return f"{base}/auth/password/setup/verify?token={raw_token}"

def _send_email_invite(to_email: str, link: str, subject: str = "Set your password"):
    s = get_settings()
    # If SMTP not configured, log to stdout for dev
    if not getattr(s, "SMTP_HOST", None) or not getattr(s, "SMTP_USER", None) or not getattr(s, "SMTP_PASS", None):
        print(f"[MAIL-DEV] To={to_email}\nSubject={subject}\nLink: {link}")
        return
    msg = EmailMessage()
    msg["From"] = getattr(s, "EMAIL_FROM", s.SMTP_USER)
    msg["To"] = to_email
    msg["Subject"] = subject
    txt = f"Welcome! Use the link below to create your password (expires soon):\n{link}"
    html = f"""
    <div style="font-family:Arial,sans-serif">
      <h2>Welcome!</h2>
      <p>Click the button below to set your password.</p>
      <p><a href="{link}" style="background:#1a73e8;color:#fff;padding:10px 16px;text-decoration:none;border-radius:6px">Create password</a></p>
      <p>If the button doesn't work, copy this URL:<br>{link}</p>
    </div>
    """
    msg.set_content(txt)
    msg.add_alternative(html, subtype="html")
    port = int(getattr(s, "SMTP_PORT", 465))
    if str(port) == "465":
        with smtplib.SMTP_SSL(s.SMTP_HOST, port) as client:
            client.login(s.SMTP_USER, s.SMTP_PASS)
            client.send_message(msg)
    else:
        with smtplib.SMTP(s.SMTP_HOST, port) as client:
            client.starttls()
            client.login(s.SMTP_USER, s.SMTP_PASS)
            client.send_message(msg)

async def _revoke_open_invites(session, user_id: int, purpose: str = "SET_PASSWORD"):
    await session.execute(
        text("""
        UPDATE user_invites
           SET expires_at = NOW()
         WHERE user_id = :uid
           AND purpose = :p
           AND used_at IS NULL
           AND expires_at > NOW()
        """),
        {"uid": user_id, "p": purpose}
    )

# --------------------------------------------------------------------------------------
# Existing endpoints (unchanged)
# --------------------------------------------------------------------------------------

@auth.post("/login")
async def login(body: LoginBody, request: Request):
    settings = get_settings()
    async with request.app.db_client() as session:
        row = (await session.execute(
            text("SELECT user_id, user_uuid, password_hash, is_super_admin, is_active FROM users WHERE email=:e"),
            {"e": body.email}
        )).first()
        if not row or not row.is_active or not row.password_hash or not verify_password(body.password, row.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        memberships = (await session.execute(
            text("SELECT org_id, role FROM user_memberships WHERE user_id=:uid"),
            {"uid": row.user_id}
        )).all()
        orgs = [{"org_id": m.org_id, "role": m.role} for m in memberships]

        token = make_access_token(
            {"sub": str(row.user_uuid), "uid": row.user_id, "is_super_admin": row.is_super_admin, "orgs": orgs},
            settings.JWT_SECRET, settings.JWT_ALG, settings.ACCESS_TTL_MIN
        )
        return {"access_token": token, "token_type": "bearer"}

@auth.post("/orgs", dependencies=[Depends(require_super_admin)])
async def create_org(body: CreateOrgBody, request: Request):
    async with request.app.db_client() as session:
        res = await session.execute(
            text("INSERT INTO organizations (name) VALUES (:n) RETURNING org_id, org_uuid"),
            {"n": body.name}
        )
        row = res.first()
        await session.commit()
        return {"org_id": row.org_id, "org_uuid": str(row.org_uuid)}
#sql alchemy better than sql(security reasons)
# --------------------------------------------------------------------------------------
# Modified: create_admin → invite flow (no password set here)
# --------------------------------------------------------------------------------------

@auth.post("/admins", dependencies=[Depends(require_super_admin)])
async def create_admin(body: CreateAdminBody, request: Request, bg: BackgroundTasks):
    async with request.app.db_client() as session:
        # Create user without password; inactive until setup
        u = (await session.execute(
            text("""
                INSERT INTO users (email, is_super_admin, is_active)
                VALUES (:e, FALSE, FALSE)
                RETURNING user_id, email
            """),
            {"e": body.email}
        )).first()

        await session.execute(
            text("INSERT INTO user_memberships (user_id, org_id, role) VALUES (:uid, :org, 'ADMIN')"),
            {"uid": u.user_id, "org": body.org_id}
        )

        # Create one-time invite
        raw = _generate_token()
        th = _hash_token(raw)
        await _revoke_open_invites(session, u.user_id, "SET_PASSWORD")
        await session.execute(
            text("""
                INSERT INTO user_invites (user_id, token_hash, purpose, expires_at, created_by_user_id)
                VALUES (:uid, :th, 'SET_PASSWORD', :exp, NULL)
            """),
            {"uid": u.user_id, "th": th, "exp": _invite_expires_at()}
        )
        await session.commit()

    link = _build_invite_link(raw)
    bg.add_task(_send_email_invite, u.email, link)
    return {"user_id": u.user_id, "invited": True}

# --------------------------------------------------------------------------------------
# Modified: create_user → invite flow (no password set here)
# --------------------------------------------------------------------------------------

@auth.post("/users")
async def create_user(body: CreateUserBody, request: Request, bg: BackgroundTasks, user=Depends(get_current_user)):
    # super admin can create anywhere; admin can create only in their org
    if not user.get("is_super_admin"):
        if not any(int(m["org_id"]) == int(body.org_id) and m["role"] == "ADMIN" for m in user.get("orgs", [])):
            raise HTTPException(status_code=403, detail="Admin of the target org required")

    async with request.app.db_client() as session:
        # Create user without password; inactive until setup
        u = (await session.execute(
            text("""
                INSERT INTO users (email, is_super_admin, is_active)
                VALUES (:e, FALSE, FALSE)
                RETURNING user_id, email
            """),
            {"e": body.email}
        )).first()

        await session.execute(
            text("INSERT INTO user_memberships (user_id, org_id, role) VALUES (:uid, :org, 'USER')"),
            {"uid": u.user_id, "org": body.org_id}
        )

        # Create one-time invite
        raw = _generate_token()
        th = _hash_token(raw)
        await _revoke_open_invites(session, u.user_id, "SET_PASSWORD")
        await session.execute(
            text("""
                INSERT INTO user_invites (user_id, token_hash, purpose, expires_at, created_by_user_id)
                VALUES (:uid, :th, 'SET_PASSWORD', :exp, :cby)
            """),
            {"uid": u.user_id, "th": th, "exp": _invite_expires_at(), "cby": user.get("uid")}
        )
        await session.commit()

    link = _build_invite_link(raw)
    bg.add_task(_send_email_invite, u.email, link)
    return {"user_id": u.user_id, "invited": True}

# --------------------------------------------------------------------------------------
# New: verify + setup + resend endpoints
# --------------------------------------------------------------------------------------

class SetPasswordBody(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=256)

@auth.get("/password/setup/verify")
async def verify_setup_token(token: str, request: Request):
    th = _hash_token(token)
    async with request.app.db_client() as session:
        inv = (await session.execute(
            text("""
                SELECT invite_id, user_id, expires_at, used_at
                  FROM user_invites
                 WHERE token_hash=:th AND purpose='SET_PASSWORD'
            """),
            {"th": th}
        )).first()
        if not inv or inv.used_at is not None or inv.expires_at < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Invalid or expired token")
    return {"ok": True, "expires_at": inv.expires_at.isoformat()}

@auth.post("/password/setup")
async def setup_password(body: SetPasswordBody, request: Request):
    th = _hash_token(body.token)
    async with request.app.db_client() as session:
        inv = (await session.execute(
            text("""
                SELECT invite_id, user_id, expires_at, used_at
                  FROM user_invites
                 WHERE token_hash=:th AND purpose='SET_PASSWORD'
            """),
            {"th": th}
        )).first()
        if not inv or inv.used_at is not None or inv.expires_at < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Invalid or expired token")

        # Set password + activate user
        pwd_hash = hash_password(body.new_password)
        await session.execute(
            text("UPDATE users SET password_hash=:ph, is_active=TRUE WHERE user_id=:uid"),
            {"ph": pwd_hash, "uid": inv.user_id}
        )
        await session.execute(
            text("UPDATE user_invites SET used_at=NOW() WHERE invite_id=:iid"),
            {"iid": inv.invite_id}
        )
        await session.commit()

    return {"message": "Password set successfully"}

@auth.post("/password/setup/resend/{user_id}", dependencies=[Depends(require_super_admin)])
async def resend_invite(user_id: int, request: Request, bg: BackgroundTasks):
    async with request.app.db_client() as session:
        user_row = (await session.execute(
            text("SELECT email FROM users WHERE user_id=:uid"),
            {"uid": user_id}
        )).first()
        if not user_row:
            raise HTTPException(status_code=404, detail="User not found")

        raw = _generate_token()
        th = _hash_token(raw)
        await _revoke_open_invites(session, user_id, "SET_PASSWORD")
        await session.execute(
            text("""
                INSERT INTO user_invites (user_id, token_hash, purpose, expires_at, created_by_user_id)
                VALUES (:uid, :th, 'SET_PASSWORD', :exp, NULL)
            """),
            {"uid": user_id, "th": th, "exp": _invite_expires_at()}
        )
        await session.commit()

    link = _build_invite_link(raw)
    bg.add_task(_send_email_invite, user_row.email, link)
    return {"message": "Invite re-sent"}
