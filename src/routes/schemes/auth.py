from pydantic import BaseModel, EmailStr

class LoginBody(BaseModel):
    email: EmailStr
    password: str

class CreateOrgBody(BaseModel):
    name: str

class CreateAdminBody(BaseModel):
    org_id: int
    email: EmailStr
    password: str

class CreateUserBody(BaseModel):
    org_id: int
    email: EmailStr
    password: str
