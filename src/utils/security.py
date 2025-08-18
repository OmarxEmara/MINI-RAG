from passlib.hash import argon2, bcrypt
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta

# Create a context that supports both bcrypt and argon2
pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    """Hash password using Argon2"""
    return argon2.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash - supports both bcrypt and argon2"""
    try:
        # Try with the context that handles both formats
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        # Fallback: try bcrypt directly for database bcrypt hashes
        if hashed_password.startswith('$2'):  # bcrypt format
            try:
                return bcrypt.verify(plain_password, hashed_password)
            except:
                return False
        return False

def make_access_token(data: dict, secret: str, algorithm: str, expire_minutes: int) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=expire_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, secret, algorithm=algorithm)

def verify_access_token(token: str, secret: str, algorithm: str) -> dict:
    """Verify and decode a JWT token"""
    return jwt.decode(token, secret, algorithms=[algorithm])

def decode_token(token: str, secret: str, algorithm: str = "HS256") -> dict:
    """Decode a JWT token - used by deps.py"""
    return jwt.decode(token, secret, algorithms=[algorithm])