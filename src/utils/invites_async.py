import secrets, hashlib, hmac
from datetime import datetime, timedelta
from sqlalchemy import text
from helpers.config import get_settings

def generate_token() -> str:
    return secrets.token_urlsafe(32)

def hash_token(raw: str) -> str:
    s = get_settings().INVITE_TOKEN_HMAC_SECRET.encode()
    return hmac.new(s, msg=raw.encode(), digestmod=hashlib.sha256).hexdigest()

def expiry_dt() -> datetime:
    return datetime.utcnow() + timedelta(hours=get_settings().INVITE_TTL_HOURS)

async def revoke_open_invites(session, user_id: int, purpose: str = "SET_PASSWORD"):
    await session.execute(
        text("""
        UPDATE user_invites
           SET expires_at = NOW()
         WHERE user_id=:uid AND purpose=:p
           AND used_at IS NULL
           AND expires_at > NOW()
        """),
        {"uid": user_id, "p": purpose}
    )

async def create_invite(session, user_id: int, token_hash: str, purpose: str, created_by_user_id: int | None):
    row = (await session.execute(
        text("""
        INSERT INTO user_invites (user_id, token_hash, purpose, expires_at, created_by_user_id)
        VALUES (:uid, :th, :p, :exp, :cby)
        RETURNING invite_id, expires_at
        """),
        {"uid": user_id, "th": token_hash, "p": purpose, "exp": expiry_dt(), "cby": created_by_user_id}
    )).first()
    return row

async def find_invite_by_hash(session, token_hash: str, purpose: str = "SET_PASSWORD"):
    return (await session.execute(
        text("""
        SELECT invite_id, user_id, expires_at, used_at
          FROM user_invites
         WHERE token_hash=:th AND purpose=:p
        """),
        {"th": token_hash, "p": purpose}
    )).first()

async def mark_invite_used(session, invite_id: int):
    await session.execute(
        text("UPDATE user_invites SET used_at=NOW() WHERE invite_id=:iid"),
        {"iid": invite_id}
    )
