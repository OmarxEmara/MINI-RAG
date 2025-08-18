# routes/schemes/org.py
from pydantic import BaseModel
from typing import Dict, Any, Optional

class CreateProjectRequest(BaseModel):
    project_name: str

class UpdateOrgMetadataRequest(BaseModel):
    metadata: Dict[str, Any]

# routes/schemes/data.py - Add this if not already present
class ProcessRequest(BaseModel):
    chunk_size: int = 1000
    overlap_size: int = 200
    do_reset: int = 0  # 0 = false, 1 = true
    file_id: Optional[str] = None