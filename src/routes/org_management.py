# routes/org_management.py
from fastapi import APIRouter, status, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text
from utils.deps import get_current_user, require_super_admin
from utils.org_access import OrgAccessControl
from routes.schemes.org import CreateProjectRequest, UpdateOrgMetadataRequest
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging

logger = logging.getLogger('uvicorn.error')

org_router = APIRouter(
    prefix="/api/v1/organizations",
    tags=["organizations"],
)

class ProjectResponse(BaseModel):
    project_id: int
    project_name: str
    project_org_id: int
    created_at: Optional[str] = None

class OrganizationResponse(BaseModel):
    org_id: int
    org_uuid: str
    name: str
    metadata: Dict[str, Any]
    created_at: str

class UserResponse(BaseModel):
    user_id: int
    user_uuid: str
    email: str
    role: str
    is_active: bool

@org_router.get("/")
async def list_organizations(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """List organizations - Super Admin sees all, others see only their orgs"""
    
    async with request.app.db_client() as session:
        if user.get("is_super_admin"):
            # Super admin sees all organizations
            result = await session.execute(
                text("""
                    SELECT org_id, org_uuid, name, metadata, created_at 
                    FROM organizations 
                    ORDER BY name
                """)
            )
        else:
            # Regular users see only their organizations
            user_org_ids = OrgAccessControl.get_user_org_ids(user)
            if not user_org_ids:
                return JSONResponse(content={"organizations": []})
            
            placeholders = ",".join([f":org_{i}" for i in range(len(user_org_ids))])
            params = {f"org_{i}": org_id for i, org_id in enumerate(user_org_ids)}
            
            result = await session.execute(
                text(f"""
                    SELECT org_id, org_uuid, name, metadata, created_at 
                    FROM organizations 
                    WHERE org_id IN ({placeholders})
                    ORDER BY name
                """),
                params
            )
        
        organizations = [
            OrganizationResponse(
                org_id=row.org_id,
                org_uuid=str(row.org_uuid),
                name=row.name,
                metadata=row.metadata or {},
                created_at=row.created_at.isoformat()
            )
            for row in result.all()
        ]
    
    return JSONResponse(content={"organizations": [org.dict() for org in organizations]})


@org_router.get("/{org_id}/projects")
async def list_organization_projects(
    request: Request,
    org_id: int,
    user: dict = Depends(get_current_user)
):
    """List projects in an organization - requires access to the organization"""
    
    OrgAccessControl.validate_project_access(user, org_id, require_admin=False)
    
    async with request.app.db_client() as session:
        result = await session.execute(
            text("""
                SELECT project_id, project_name, project_org_id, created_at
                FROM projects 
                WHERE project_org_id = :org_id
                ORDER BY project_name
            """),
            {"org_id": org_id}
        )
        
        projects = [
            ProjectResponse(
                project_id=row.project_id,
                project_name=row.project_name,
                project_org_id=row.project_org_id,
                created_at=row.created_at.isoformat() if row.created_at else None
            )
            for row in result.all()
        ]
    
    return JSONResponse(content={"projects": [proj.dict() for proj in projects]})


@org_router.post("/{org_id}/projects")
async def create_organization_project(
    request: Request,
    org_id: int,
    project_request: CreateProjectRequest,
    user: dict = Depends(get_current_user)
):
    """Create a new project in an organization - requires admin access"""
    
    OrgAccessControl.validate_project_access(user, org_id, require_admin=True)
    
    async with request.app.db_client() as session:
        # Verify organization exists
        org_check = await session.execute(
            text("SELECT org_id FROM organizations WHERE org_id = :org_id"),
            {"org_id": org_id}
        )
        if not org_check.first():
            raise HTTPException(status_code=404, detail="Organization not found")
        
        # Create the project
        result = await session.execute(
            text("""
                INSERT INTO projects (project_name, project_org_id, created_at)
                VALUES (:name, :org_id, NOW())
                RETURNING project_id, project_name, project_org_id, created_at
            """),
            {"name": project_request.project_name, "org_id": org_id}
        )
        
        row = result.first()
        await session.commit()
        
        project = ProjectResponse(
            project_id=row.project_id,
            project_name=row.project_name,
            project_org_id=row.project_org_id,
            created_at=row.created_at.isoformat()
        )
    
    return JSONResponse(content={"project": project.dict()})


@org_router.get("/{org_id}/users")
async def list_organization_users(
    request: Request,
    org_id: int,
    user: dict = Depends(get_current_user)
):
    """List users in an organization - requires admin access"""
    
    OrgAccessControl.validate_project_access(user, org_id, require_admin=True)
    
    async with request.app.db_client() as session:
        result = await session.execute(
            text("""
                SELECT u.user_id, u.user_uuid, u.email, u.is_active, um.role
                FROM users u
                JOIN user_memberships um ON u.user_id = um.user_id
                WHERE um.org_id = :org_id
                ORDER BY u.email
            """),
            {"org_id": org_id}
        )
        
        users = [
            UserResponse(
                user_id=row.user_id,
                user_uuid=str(row.user_uuid),
                email=row.email,
                role=row.role,
                is_active=row.is_active
            )
            for row in result.all()
        ]
    
    return JSONResponse(content={"users": [user_data.dict() for user_data in users]})


@org_router.put("/{org_id}/metadata")
async def update_organization_metadata(
    request: Request,
    org_id: int,
    metadata_request: UpdateOrgMetadataRequest,
    user: dict = Depends(get_current_user)
):
    """Update organization metadata - Super Admin or Admin access required"""
    
    # Super admins can update any org, admins can only update their own
    if not user.get("is_super_admin"):
        OrgAccessControl.validate_project_access(user, org_id, require_admin=True)
    
    async with request.app.db_client() as session:
        result = await session.execute(
            text("""
                UPDATE organizations 
                SET metadata = :metadata
                WHERE org_id = :org_id
                RETURNING org_id, name, metadata
            """),
            {"org_id": org_id, "metadata": metadata_request.metadata}
        )
        
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Organization not found")
        
        await session.commit()
    
    return JSONResponse(content={
        "message": "Organization metadata updated successfully",
        "org_id": row.org_id,
        "name": row.name,
        "metadata": row.metadata
    })


@org_router.delete("/{org_id}/users/{user_id}")
async def remove_user_from_organization(
    request: Request,
    org_id: int,
    user_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Remove user from organization - requires admin access"""
    
    OrgAccessControl.validate_project_access(current_user, org_id, require_admin=True)
    
    # Prevent removing yourself
    if current_user.get("uid") == user_id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself from organization")
    
    async with request.app.db_client() as session:
        # Check if user exists in the organization
        check_result = await session.execute(
            text("""
                SELECT user_id FROM user_memberships 
                WHERE user_id = :user_id AND org_id = :org_id
            """),
            {"user_id": user_id, "org_id": org_id}
        )
        
        if not check_result.first():
            raise HTTPException(status_code=404, detail="User not found in organization")
        
        # Remove the user
        await session.execute(
            text("""
                DELETE FROM user_memberships 
                WHERE user_id = :user_id AND org_id = :org_id
            """),
            {"user_id": user_id, "org_id": org_id}
        )
        
        await session.commit()
    
    return JSONResponse(content={"message": "User removed from organization successfully"})


@org_router.get("/{org_id}/stats")
async def get_organization_stats(
    request: Request,
    org_id: int,
    user: dict = Depends(get_current_user)
):
    """Get organization statistics - requires access to organization"""
    
    OrgAccessControl.validate_project_access(user, org_id, require_admin=False)
    
    async with request.app.db_client() as session:
        # Get project count
        project_result = await session.execute(
            text("SELECT COUNT(*) as project_count FROM projects WHERE project_org_id = :org_id"),
            {"org_id": org_id}
        )
        project_count = project_result.scalar()
        
        # Get user count
        user_result = await session.execute(
            text("SELECT COUNT(*) as user_count FROM user_memberships WHERE org_id = :org_id"),
            {"org_id": org_id}
        )
        user_count = user_result.scalar()
        
        # Get total assets across all projects
        asset_result = await session.execute(
            text("""
                SELECT COUNT(*) as asset_count 
                FROM assets a
                JOIN projects p ON a.asset_project_id = p.project_id
                WHERE p.project_org_id = :org_id
            """),
            {"org_id": org_id}
        )
        asset_count = asset_result.scalar()
        
        # Get total chunks across all projects
        chunk_result = await session.execute(
            text("""
                SELECT COUNT(*) as chunk_count 
                FROM chunks c
                WHERE c.chunk_project_id IN (
                    SELECT project_id FROM projects WHERE project_org_id = :org_id
                )
            """),
            {"org_id": org_id}
        )
        chunk_count = chunk_result.scalar()
    
    return JSONResponse(content={
        "org_id": org_id,
        "stats": {
            "project_count": project_count,
            "user_count": user_count,
            "asset_count": asset_count,
            "chunk_count": chunk_count
        }
    })