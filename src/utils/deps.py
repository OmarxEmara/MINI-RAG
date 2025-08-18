from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
import jwt  # Changed from jose import
from helpers.config import get_settings
from utils.security import decode_token
from sqlalchemy import text

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

async def get_current_user(request: Request, token: str = Depends(oauth2_scheme)):
    settings = get_settings()
    try:
        payload = decode_token(token, settings.JWT_SECRET, settings.JWT_ALG)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, jwt.DecodeError):  # Updated exception handling
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    request.state.jwt = payload
    return payload

def require_super_admin(user=Depends(get_current_user)):
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin only")
    return user

def require_org_role(required_roles: set[str]):
    async def checker(request: Request, user=Depends(get_current_user)):
        org_id = request.path_params.get("org_id")
        if org_id is None:
            raise HTTPException(status_code=400, detail="org_id required in path")
        if user.get("is_super_admin"):
            return user
        for m in user.get("orgs", []):
            if int(m["org_id"]) == int(org_id) and m["role"] in required_roles:
                return user
        raise HTTPException(status_code=403, detail="Insufficient role")
    return checker

async def require_access_to_project(request: Request, user=Depends(get_current_user)):
    # Use when routes only have {project_id}
    project_id = request.path_params.get("project_id")
    if project_id is None:
        raise HTTPException(status_code=400, detail="project_id required in path")
    async with request.app.db_client() as session:
        row = (await session.execute(
            text("SELECT project_org_id FROM projects WHERE project_id=:pid"),
            {"pid": int(project_id)}
        )).first()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    org_id = row[0]
    if user.get("is_super_admin"):
        return {"org_id": org_id}
    if not any(int(m["org_id"]) == int(org_id) for m in user.get("orgs", [])):
        raise HTTPException(status_code=403, detail="No access to this project/org")
    return {"org_id": org_id}