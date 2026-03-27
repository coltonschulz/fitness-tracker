from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session

from deps import get_db, get_current_user

router = APIRouter()


@router.get("/auth/me")
async def get_current_user_info(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
    }
