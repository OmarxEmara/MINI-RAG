# routes/user_activity.py
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text
from utils.deps import get_current_user
from utils.org_access import OrgAccessControl
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger('uvicorn.error')

# Helper function to get project organization ID
async def get_project_org_id(db_client, project_id: int) -> Optional[int]:
    """Retrieve the organization ID for a given project"""
    async with db_client() as session:
        result = await session.execute(
            text("SELECT project_org_id FROM projects WHERE project_id = :pid"),
            {"pid": project_id}
        )
        row = result.first()
        return row.project_org_id if row else None

activity_router = APIRouter(
    prefix="/api/v1/users",
    tags=["user_activity"],
)

class ChatHistoryEntry(BaseModel):
    chat_id: int
    project_id: int
    project_name: str
    query: str
    answer: str
    created_at: str

class UserActivityEntry(BaseModel):
    activity_id: int
    activity_type: str  # 'CHAT', 'UPLOAD', 'SEARCH', etc.
    project_id: Optional[int]
    project_name: Optional[str]
    description: str
    created_at: str

class ChatEntryCreate(BaseModel):
    project_id: int
    query: str
    answer: str

@activity_router.get("/me/profile")
async def get_user_profile(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """Get current user's profile information"""
    
    async with request.app.db_client() as session:
        # Get user details
        user_result = await session.execute(
            text("""
                SELECT u.user_id, u.user_uuid, u.email, u.is_super_admin, u.is_active, u.created_at
                FROM users u
                WHERE u.user_id = :user_id
            """),
            {"user_id": user.get("uid")}
        )
        user_row = user_result.first()
        
        if not user_row:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get user's organizations and roles
        org_result = await session.execute(
            text("""
                SELECT o.org_id, o.name as org_name, um.role
                FROM organizations o
                JOIN user_memberships um ON o.org_id = um.org_id
                WHERE um.user_id = :user_id
                ORDER BY o.name
            """),
            {"user_id": user.get("uid")}
        )
        
        organizations = [
            {
                "org_id": row.org_id,
                "org_name": row.org_name,
                "role": row.role
            }
            for row in org_result.all()
        ]
    
    return JSONResponse(content={
        "user": {
            "user_id": user_row.user_id,
            "user_uuid": str(user_row.user_uuid),
            "email": user_row.email,
            "is_super_admin": user_row.is_super_admin,
            "is_active": user_row.is_active,
            "created_at": user_row.created_at.isoformat(),
            "organizations": organizations
        }
    })


@activity_router.get("/me/chat-history")
async def get_user_chat_history(
    request: Request,
    user: dict = Depends(get_current_user),
    project_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0
):
    """Get user's chat history - optionally filtered by project"""
    
    user_org_ids = OrgAccessControl.get_user_org_ids(user)
    if not user_org_ids:
        return JSONResponse(content={"chat_history": []})
    
    async with request.app.db_client() as session:
        # Base query
        query = """
            SELECT ch.chat_id, ch.project_id, p.project_name, ch.query, ch.answer, ch.created_at
            FROM chat_history ch
            JOIN projects p ON ch.project_id = p.project_id
            WHERE ch.user_id = :user_id 
            AND p.project_org_id = ANY(:org_ids)
        """
        
        params = {
            "user_id": user.get("uid"),
            "org_ids": user_org_ids
        }
        
        # Add project filter if specified
        if project_id:
            # Verify user has access to this project
            project_org_id = await get_project_org_id(request.app.db_client, project_id)
            if project_org_id is None:
                raise HTTPException(status_code=404, detail="Project not found")
            if project_org_id not in user_org_ids:
                raise HTTPException(status_code=403, detail="Access to this project denied")
            
            query += " AND ch.project_id = :project_id"
            params["project_id"] = project_id
        
        query += " ORDER BY ch.created_at DESC LIMIT :limit OFFSET :offset"
        params["limit"] = limit
        params["offset"] = offset
        
        result = await session.execute(text(query), params)
        
        chat_entries = [
            ChatHistoryEntry(
                chat_id=row.chat_id,
                project_id=row.project_id,
                project_name=row.project_name,
                query=row.query,
                answer=row.answer,
                created_at=row.created_at.isoformat()
            )
            for row in result.all()
        ]
    
    return JSONResponse(content={
        "chat_history": [entry.dict() for entry in chat_entries],
        "total_shown": len(chat_entries),
        "offset": offset,
        "limit": limit
    })


@activity_router.post("/me/chat-history")
async def save_chat_entry(
    request: Request,
    chat_data: ChatEntryCreate,
    user: dict = Depends(get_current_user)
):
    """Save a chat interaction to history"""
    
    # Get project organization ID directly from database
    async with request.app.db_client() as session:
        project_result = await session.execute(
            text("SELECT project_org_id FROM projects WHERE project_id = :pid"),
            {"pid": chat_data.project_id}
        )
        project_row = project_result.first()
    
    if not project_row:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project_org_id = project_row.project_org_id
    OrgAccessControl.validate_project_access(user, project_org_id, require_admin=False)
    
    async with request.app.db_client() as session:
        result = await session.execute(
            text("""
                INSERT INTO chat_history (user_id, project_id, query, answer, created_at)
                VALUES (:user_id, :project_id, :query, :answer, NOW())
                RETURNING chat_id, created_at
            """),
            {
                "user_id": user.get("uid"),
                "project_id": chat_data.project_id,
                "query": chat_data.query,
                "answer": chat_data.answer
            }
        )
        
        row = result.first()
        await session.commit()
    
    return JSONResponse(content={
        "message": "Chat entry saved successfully",
        "chat_id": row.chat_id,
        "created_at": row.created_at.isoformat()
    })


@activity_router.get("/me/activity")
async def get_user_activity(
    request: Request,
    user: dict = Depends(get_current_user),
    limit: int = 50,
    offset: int = 0
):
    """Get user's activity log"""
    
    user_org_ids = OrgAccessControl.get_user_org_ids(user)
    if not user_org_ids:
        return JSONResponse(content={"activities": []})
    
    async with request.app.db_client() as session:
        result = await session.execute(
            text("""
                SELECT ua.activity_id, ua.activity_type, ua.project_id, 
                       p.project_name, ua.description, ua.created_at
                FROM user_activities ua
                LEFT JOIN projects p ON ua.project_id = p.project_id
                WHERE ua.user_id = :user_id 
                AND (ua.project_id IS NULL OR p.project_org_id = ANY(:org_ids))
                ORDER BY ua.created_at DESC 
                LIMIT :limit OFFSET :offset
            """),
            {
                "user_id": user.get("uid"),
                "org_ids": user_org_ids,
                "limit": limit,
                "offset": offset
            }
        )
        
        activities = [
            UserActivityEntry(
                activity_id=row.activity_id,
                activity_type=row.activity_type,
                project_id=row.project_id,
                project_name=row.project_name,
                description=row.description,
                created_at=row.created_at.isoformat()
            )
            for row in result.all()
        ]
    
    return JSONResponse(content={
        "activities": [activity.dict() for activity in activities],
        "total_shown": len(activities),
        "offset": offset,
        "limit": limit
    })


@activity_router.get("/me/stats")
async def get_user_stats(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """Get user's usage statistics"""
    
    user_org_ids = OrgAccessControl.get_user_org_ids(user)
    if not user_org_ids:
        return JSONResponse(content={"stats": {}})
    
    async with request.app.db_client() as session:
        # Get chat count
        chat_result = await session.execute(
            text("""
                SELECT COUNT(*) as chat_count 
                FROM chat_history ch
                JOIN projects p ON ch.project_id = p.project_id
                WHERE ch.user_id = :user_id 
                AND p.project_org_id = ANY(:org_ids)
            """),
            {"user_id": user.get("uid"), "org_ids": user_org_ids}
        )
        chat_count = chat_result.scalar()
        
        # Get accessible projects count
        project_result = await session.execute(
            text("""
                SELECT COUNT(*) as project_count 
                FROM projects 
                WHERE project_org_id = ANY(:org_ids)
            """),
            {"org_ids": user_org_ids}
        )
        project_count = project_result.scalar()
        
        # Get recent activity count (last 30 days)
        recent_activity_result = await session.execute(
            text("""
                SELECT COUNT(*) as recent_activity_count 
                FROM user_activities ua
                LEFT JOIN projects p ON ua.project_id = p.project_id
                WHERE ua.user_id = :user_id 
                AND ua.created_at >= NOW() - INTERVAL '30 days'
                AND (ua.project_id IS NULL OR p.project_org_id = ANY(:org_ids))
            """),
            {"user_id": user.get("uid"), "org_ids": user_org_ids}
        )
        recent_activity_count = recent_activity_result.scalar()
    
    return JSONResponse(content={
        "stats": {
            "total_chats": chat_count,
            "accessible_projects": project_count,
            "recent_activity_count": recent_activity_count,
            "organizations_count": len(user_org_ids)
        }
    })


# Helper function to log user activities
async def log_user_activity(
    db_client,
    user_id: int,
    activity_type: str,
    description: str,
    project_id: Optional[int] = None
):
    """Helper function to log user activities"""
    try:
        async with db_client() as session:
            await session.execute(
                text("""
                    INSERT INTO user_activities (user_id, activity_type, project_id, description, created_at)
                    VALUES (:user_id, :activity_type, :project_id, :description, NOW())
                """),
                {
                    "user_id": user_id,
                    "activity_type": activity_type,
                    "project_id": project_id,
                    "description": description
                }
            )
            await session.commit()
    except Exception as e:
        logger.error(f"Failed to log user activity: {e}")

# Usage example in your existing endpoints:
# await log_user_activity(
#     request.app.db_client,
#     user.get("uid"),
#     "CHAT",
#     f"Asked question in project {project_id}",
#     project_id
# )