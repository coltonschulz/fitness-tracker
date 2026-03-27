import logging
import os

from fastapi import APIRouter, Request, HTTPException, Depends
from anthropic import Anthropic
from sqlalchemy.orm import Session

from schemas import AICoachingRequest
from deps import get_db, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/ai-coaching")
async def request_ai_coaching(
    request_data: AICoachingRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Request AI coaching feedback on a workout."""
    get_current_user(request, db)  # Require authentication

    api_key = os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="AI coaching is temporarily unavailable.",
        )

    user_context = request_data.context or "No additional context provided."
    focus = (
        ", ".join(request_data.focus_areas)
        if request_data.focus_areas
        else "General fitness"
    )

    system_prompt = (
        "You are an elite strength and conditioning coach. "
        "Be concise, direct, and actionable."
    )
    user_message = (
        f"I just finished workout ID {request_data.workout_id}. "
        f"Focus areas: {focus}. Context: {user_context}. "
        "Give me a quick assessment and one key takeaway."
    )

    try:
        client = Anthropic(api_key=api_key)
        message = client.messages.create(
            model=os.environ.get("AI_COACH_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        ai_response = message.content[0].text
        return {
            "status": "success",
            "workout_id": request_data.workout_id,
            "ai_advice": ai_response,
        }
    except Exception:
        logger.exception("AI coaching request failed")
        raise HTTPException(
            status_code=500,
            detail="AI coaching request failed. Please try again later.",
        )
