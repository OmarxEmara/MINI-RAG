from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from helpers.config import get_settings
from utils.security import hash_password, verify_password, make_access_token
from utils.deps import require_super_admin, get_current_user
from routes.schemes.auth import LoginBody, CreateOrgBody, CreateAdminBody, CreateUserBody

auth = APIRouter(prefix="/auth", tags=["auth"])

@auth.post("/login")
async def login(body: LoginBody, request: Request):
    settings = get_settings()
    async with request.app.db_client() as session:
        row = (await session.execute(
            text("SELECT user_id, user_uuid, password_hash, is_super_admin, is_active FROM users WHERE email=:e"),
            {"e": body.email}
        )).first()
        if not row or not row.is_active or not verify_password(body.password, row.password_hash):
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

@auth.post("/admins", dependencies=[Depends(require_super_admin)])
async def create_admin(body: CreateAdminBody, request: Request):
    pwd = hash_password(body.password)
    async with request.app.db_client() as session:
        u = (await session.execute(
            text("INSERT INTO users (email, password_hash, is_super_admin) VALUES (:e, :p, FALSE) RETURNING user_id"),
            {"e": body.email, "p": pwd}
        )).first()
        await session.execute(
            text("INSERT INTO user_memberships (user_id, org_id, role) VALUES (:uid, :org, 'ADMIN')"),
            {"uid": u.user_id, "org": body.org_id}
        )
        await session.commit()
        return {"user_id": u.user_id}

@auth.post("/users")
async def create_user(body: CreateUserBody, request: Request, user=Depends(get_current_user)):
    # super admin can create anywhere; admin can create only in their org
    if not user.get("is_super_admin"):
        if not any(int(m["org_id"]) == int(body.org_id) and m["role"] == "ADMIN" for m in user.get("orgs", [])):
            raise HTTPException(status_code=403, detail="Admin of the target org required")
    pwd = hash_password(body.password)
    async with request.app.db_client() as session:
        u = (await session.execute(
            text("INSERT INTO users (email, password_hash, is_super_admin) VALUES (:e, :p, FALSE) RETURNING user_id"),
            {"e": body.email, "p": pwd}
        )).first()
        await session.execute(
            text("INSERT INTO user_memberships (user_id, org_id, role) VALUES (:uid, :org, 'USER')"),
            {"uid": u.user_id, "org": body.org_id}
        )
        await session.commit()
        return {"user_id": u.user_id}
