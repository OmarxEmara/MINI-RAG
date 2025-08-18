from typing import Dict, List, Optional
from fastapi import HTTPException, Depends
from utils.deps import get_current_user

class OrgAccessControl:
    """Utility class for organization-level access control"""
    
    @staticmethod
    def get_user_org_ids(user: Dict) -> List[int]:
        """Extract organization IDs from user token"""
        return [int(org["org_id"]) for org in user.get("orgs", [])]
    
    @staticmethod
    def get_user_admin_org_ids(user: Dict) -> List[int]:
        """Get organization IDs where user has ADMIN role"""
        return [
            int(org["org_id"]) 
            for org in user.get("orgs", []) 
            if org.get("role") == "ADMIN"
        ]
    
    @staticmethod
    def can_access_project(user: Dict, project_org_id: int) -> bool:
        """Check if user can access a project based on organization"""
        if user.get("is_super_admin"):
            return True
        
        user_org_ids = OrgAccessControl.get_user_org_ids(user)
        return project_org_id in user_org_ids
    
    @staticmethod
    def can_admin_project(user: Dict, project_org_id: int) -> bool:
        """Check if user has admin access to a project's organization"""
        if user.get("is_super_admin"):
            return True
        
        admin_org_ids = OrgAccessControl.get_user_admin_org_ids(user)
        return project_org_id in admin_org_ids
    
    @staticmethod
    def validate_project_access(user: Dict, project_org_id: int, require_admin: bool = False):
        """Validate access and raise HTTP exception if denied"""
        if require_admin:
            if not OrgAccessControl.can_admin_project(user, project_org_id):
                raise HTTPException(
                    status_code=403, 
                    detail="Admin access to this organization required"
                )
        else:
            if not OrgAccessControl.can_access_project(user, project_org_id):
                raise HTTPException(
                    status_code=403, 
                    detail="Access to this organization denied"
                )

# Dependency functions for FastAPI routes
async def require_project_access(user: Dict = Depends(get_current_user)):
    """Dependency to ensure user has access to project's organization"""
    return user

async def require_project_admin(user: Dict = Depends(get_current_user)):
    """Dependency to ensure user has admin access to project's organization"""
    return user

# Helper function to get project organization ID
async def get_project_org_id(db_client, project_id: int) -> Optional[int]:
    """Retrieve the organization ID for a given project"""
    from sqlalchemy import text
    
    async with db_client() as session:
        result = await session.execute(
            text("SELECT project_org_id FROM projects WHERE project_id = :pid"),
            {"pid": project_id}
        )
        row = result.first()
        return row.project_org_id if row else None