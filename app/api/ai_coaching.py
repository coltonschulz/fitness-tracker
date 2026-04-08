"""AI coaching endpoint — suggests today's workout based on training history."""

import json
import logging
import os
from datetime import datetime, timezone

from anthropic import Anthropic
from fastapi import APIRouter, HTTPException, Request, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from database.models import AIReview
from schemas import AICoachingRequest, AICoachingResponse
from deps import get_db, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

SYSTEM_PROMPT = (
    "You are an elite strength and conditioning coach for a personal "
    "fitness tracker app.\n\n"
    "Your job: given the user's recent training history, suggest what "
    "they should train TODAY.\n\n"
    "Rules:\n"
    "- Prioritize muscle groups that have NOT been trained in the last "
    "48-72 hours.\n"
    "- If a focus_area is specified, build the session around that "
    "muscle group.\n"
    "- Recommend 4-6 exercises with specific sets, reps, and weight "
    "(in lbs).\n"
    "- Base weight recommendations on the user's recent weights and "
    "PRs. For exercises they haven't done, suggest conservative "
    "starting weights.\n"
    "- Keep the summary to 2-3 sentences explaining your reasoning.\n"
    "- Always use exercise names that match the user's history when "
    "possible.\n\n"
    "EQUIPMENT CONSTRAINTS — these are hard rules, not suggestions:\n"
    "- Planet Fitness / commercial gym: cables, dumbbells, machines, "
    "Smith machine. NO free barbells (Planet Fitness does not have "
    "them). Do not suggest barbell bench press, barbell squat, "
    "deadlift, etc.\n"
    "- Home: bodyweight only unless the user specifies equipment "
    "in their notes. Do NOT suggest cable machines, barbells, or "
    "gym machines.\n"
    "- Hotel gym: dumbbells and a bench at most. No cables, no "
    "barbells, no machines unless user says otherwise.\n"
    "- If a revision is requested, adjust the previous plan to meet "
    "the new constraints while keeping as much of the original "
    "structure as possible.\n"
    "- When revising, ALWAYS populate the revision_notes field to "
    "respond to the user's comments or questions. Explain what "
    "you changed and why, or answer their question about exercise "
    "selection, form, progression, etc. Be conversational and "
    "helpful — the user expects a dialogue, not just a silent "
    "plan swap."
)

SUGGEST_TOOL = {
    "name": "suggest_workout",
    "description": "Suggest a complete workout session for today.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": (
                    "2-3 sentence explanation of why this workout "
                    "was chosen."
                ),
            },
            "revision_notes": {
                "type": "string",
                "description": (
                    "When revising a previous plan, use this field "
                    "to respond conversationally to the user's "
                    "comments or questions. Explain what you "
                    "changed and why, or answer their question "
                    "about the exercises. Leave empty on initial "
                    "suggestions."
                ),
            },
            "exercises": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "sets": {"type": "integer"},
                        "reps": {"type": "integer"},
                        "weight_lbs": {"type": "number"},
                        "notes": {"type": "string"},
                    },
                    "required": ["name", "sets", "reps"],
                },
            },
        },
        "required": ["summary", "exercises"],
    },
}


def _get_recent_history(db: Session, user_id: int) -> list[dict]:
    """Last 14 days of training, aggregated per date + exercise."""
    rows = db.execute(
        text("""
            SELECT
                w.workout_date,
                e.exercise_name,
                COALESCE(ed.muscle_group, 'Other') AS muscle_group,
                COUNT(e.id) AS set_count,
                MAX(e.weight_lbs) AS max_weight,
                ROUND(AVG(e.rpe)::numeric, 1) AS avg_rpe
            FROM exercises e
            JOIN workouts w ON e.workout_id = w.id
            LEFT JOIN exercise_definitions ed
                ON e.exercise_name = ed.name
            WHERE e.user_id = :uid
              AND w.workout_date >= CURRENT_DATE - INTERVAL '14 days'
            GROUP BY w.workout_date, e.exercise_name, ed.muscle_group
            ORDER BY w.workout_date DESC, e.exercise_name
        """),
        {"uid": user_id},
    ).fetchall()
    return [
        {
            "date": str(r[0]),
            "exercise": r[1],
            "muscle_group": r[2],
            "sets": r[3],
            "max_weight": float(r[4]) if r[4] else None,
            "avg_rpe": float(r[5]) if r[5] else None,
        }
        for r in rows
    ]


def _get_volume_by_muscle(db: Session, user_id: int) -> list[dict]:
    """Sets per muscle group over the last 30 days."""
    rows = db.execute(
        text("""
            SELECT
                COALESCE(ed.muscle_group, 'Other') AS muscle_group,
                COUNT(e.id) AS set_count
            FROM exercises e
            JOIN workouts w ON e.workout_id = w.id
            LEFT JOIN exercise_definitions ed
                ON e.exercise_name = ed.name
            WHERE e.user_id = :uid
              AND w.workout_date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY COALESCE(ed.muscle_group, 'Other')
            ORDER BY set_count DESC
        """),
        {"uid": user_id},
    ).fetchall()
    return [{"muscle_group": r[0], "sets": r[1]} for r in rows]


def _get_prs(db: Session, user_id: int) -> list[dict]:
    """All-time PR (max weight) and most recent weight per exercise."""
    rows = db.execute(
        text("""
            WITH latest AS (
                SELECT DISTINCT ON (e.exercise_name)
                    e.exercise_name,
                    e.weight_lbs AS recent_weight
                FROM exercises e
                JOIN workouts w ON e.workout_id = w.id
                WHERE e.user_id = :uid
                  AND e.weight_lbs IS NOT NULL
                ORDER BY e.exercise_name,
                         w.workout_date DESC,
                         e.set_number DESC
            ),
            prs AS (
                SELECT exercise_name, MAX(weight_lbs) AS pr_weight
                FROM exercises
                WHERE user_id = :uid AND weight_lbs IS NOT NULL
                GROUP BY exercise_name
            )
            SELECT
                l.exercise_name,
                l.recent_weight,
                p.pr_weight
            FROM latest l
            JOIN prs p ON l.exercise_name = p.exercise_name
            ORDER BY l.exercise_name
        """),
        {"uid": user_id},
    ).fetchall()
    return [
        {
            "exercise": r[0],
            "recent_weight": float(r[1]) if r[1] else None,
            "pr_weight": float(r[2]) if r[2] else None,
        }
        for r in rows
    ]


def _build_user_message(
    history: list[dict],
    volume: list[dict],
    prs: list[dict],
    focus_area: str | None,
    location: str | None,
    context: str | None,
    previous_suggestion: str | None,
) -> str:
    """Assemble the user message with all training context."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts = [f"Today's date: {today}\n"]

    # Location / equipment — listed first so it frames everything else
    parts.append("## Location & Equipment")
    if location:
        parts.append(f"Training location: {location}")
    else:
        parts.append("Training location: Planet Fitness (default)")
    parts.append("")

    # Recent history
    parts.append("## Recent Training (last 14 days)")
    if history:
        for row in history:
            weight_str = f"{row['max_weight']} lbs" if row['max_weight'] else "BW"
            rpe_str = f", RPE {row['avg_rpe']}" if row['avg_rpe'] else ""
            parts.append(
                f"- {row['date']} | {row['exercise']} "
                f"({row['muscle_group']}): "
                f"{row['sets']} sets @ {weight_str}{rpe_str}"
            )
    else:
        parts.append("No workouts logged in the last 14 days.")
    parts.append("")

    # Volume
    parts.append("## Volume by Muscle Group (last 30 days)")
    if volume:
        for row in volume:
            parts.append(f"- {row['muscle_group']}: {row['sets']} sets")
    else:
        parts.append("No data available.")
    parts.append("")

    # PRs
    parts.append("## Personal Records")
    if prs:
        for row in prs:
            parts.append(
                f"- {row['exercise']}: "
                f"PR {row['pr_weight']} lbs, "
                f"recent {row['recent_weight']} lbs"
            )
    else:
        parts.append("No PRs recorded yet.")
    parts.append("")

    # Revision context — include previous plan if this is a refinement
    if previous_suggestion:
        parts.append("## Previous Suggestion (revise this)")
        parts.append(previous_suggestion)
        parts.append("")

    # Request
    parts.append("## Request")
    if focus_area:
        parts.append(f"Focus area: {focus_area}")
    else:
        parts.append(
            "No specific focus — suggest the best session for today."
        )
    if context:
        parts.append(f"Notes / constraints: {context}")
    if previous_suggestion:
        parts.append(
            "This is a REVISION REQUEST. Adjust the previous suggestion "
            "based on the notes above. Keep exercises that still work; "
            "replace only what violates the new constraints."
        )

    return "\n".join(parts)


@router.post("/ai-coaching", response_model=AICoachingResponse)
async def request_ai_coaching(
    request_data: AICoachingRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Suggest today's workout based on the user's training history."""
    user = get_current_user(request, db)

    # --- Rate limit ---
    daily_limit = int(os.environ.get("AI_DAILY_LIMIT", "10"))
    today_count = (
        db.query(func.count(AIReview.id))
        .filter(
            AIReview.user_id == user.id,
            AIReview.status == "completed",
            func.date(AIReview.requested_at) == func.current_date(),
        )
        .scalar()
    )
    if today_count >= daily_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Daily AI coaching limit reached ({daily_limit}).",
        )

    # --- API key ---
    api_key = os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="AI coaching is temporarily unavailable.",
        )

    # --- Gather training data ---
    history = _get_recent_history(db, user.id)
    volume = _get_volume_by_muscle(db, user.id)
    prs = _get_prs(db, user.id)

    user_message = _build_user_message(
        history, volume, prs,
        request_data.focus_area,
        request_data.location,
        request_data.context,
        request_data.previous_suggestion,
    )

    # --- Persist pending review ---
    review = AIReview(
        user_id=user.id,
        workout_id=None,
        status="pending",
        prompt=user_message,
        requested_at=datetime.now(timezone.utc),
    )
    db.add(review)
    db.flush()

    # --- Call Claude ---
    try:
        client = Anthropic(api_key=api_key)
        message = client.messages.create(
            model=os.environ.get(
                "AI_COACH_MODEL", "claude-haiku-4-5-20251001"
            ),
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            tools=[SUGGEST_TOOL],
            tool_choice={"type": "tool", "name": "suggest_workout"},
        )

        # Extract the tool_use block.
        tool_result = None
        for block in message.content:
            if block.type == "tool_use":
                tool_result = block.input
                break

        if not tool_result:
            raise ValueError("No tool_use block in response")

        summary = tool_result.get("summary", "")
        exercises = tool_result.get("exercises", [])
        revision_notes = tool_result.get("revision_notes")

        review.status = "completed"
        review.response = json.dumps(tool_result)
        review.completed_at = datetime.now(timezone.utc)
        review.tokens_used = message.usage.output_tokens
        db.commit()

        return AICoachingResponse(
            review_id=review.id,
            summary=summary,
            exercises=exercises,
            revision_notes=revision_notes,
            tokens_used=message.usage.output_tokens,
        )

    except Exception as e:
        logger.exception("AI coaching request failed")
        review.status = "failed"
        review.error_message = str(e)
        review.retry_count = (review.retry_count or 0) + 1
        db.commit()
        raise HTTPException(
            status_code=500,
            detail="AI coaching request failed. Please try again later.",
        )
