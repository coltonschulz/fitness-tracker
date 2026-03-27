"""Shared FastAPI dependencies (DB session, current user)."""

import os

from fastapi import HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from database.models import User

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency: yields a SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session) -> User:
    """Resolve the authenticated user, auto-creating on first login."""
    user_email = getattr(request.state, "user_email", "")
    if not user_email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.query(User).filter(User.email == user_email).first()
    if not user:
        name = (
            getattr(request.state, "user_name", "") or user_email.split("@")[0]
        )
        user = User(email=user_email, name=name)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user
