"""
Skill instructions loader tool for chat_next.

Provides the `load_skill_instructions` tool that lets the model
read the full body of an available skill on demand, implementing
the progressive disclosure pattern.
"""

from django.db.models import F
from django.utils.translation import get_language

from asgiref.sync import sync_to_async
from structlog import get_logger

from chat_next._tools.base import TOOL_REGISTRY, OttoTool, ToolContext
from chat_next._utils.context_hints import sanitize_runtime_context_hints
from chat_next.models import TOOL_CATEGORY_SKILLS
from chat_next.prompts import get_effective_available_skills

logger = get_logger(__name__)


async def _load_skill_instructions(arguments: dict, context: ToolContext) -> dict:
    """Load full instructions for an available skill."""
    raw_skill_id = arguments.get("skill_id")
    try:
        skill_id = int(raw_skill_id)
    except (TypeError, ValueError):
        return {
            "success": False,
            "error": (
                f"Skill id '{raw_skill_id}' is invalid. "
                "Use the numeric skill id from the Available Skills list."
            ),
        }

    chat = context.chat

    if not chat:
        return {"success": False, "error": "No chat context available."}

    # Allow loading skills available in this chat for this turn, including
    # one-turn context-hinted skills that were not persistently enabled.
    skill = await sync_to_async(
        lambda: next(
            (
                skill
                for skill in get_effective_available_skills(
                    chat.settings,
                    chat=chat,
                    user=context.user or chat.user,
                )
                if skill.id == skill_id
            ),
            None,
        )
    )()

    if not skill:
        return {
            "success": False,
            "error": f"Skill id '{skill_id}' is not available in this chat. "
            "Check the available skills list in the system prompt.",
        }

    lang = get_language()
    body = skill.body_fr if lang == "fr" and skill.body_fr else skill.body_en

    result = {"instructions": body}

    # Include context hints so model knows which libraries/docs to use
    safe_context_hints = await sync_to_async(sanitize_runtime_context_hints)(
        skill.context_hints or []
    )
    if safe_context_hints:
        result["context_hints"] = safe_context_hints
        result["context_hints_note"] = (
            "Use existing tools to access these hinted resources."
        )

    # Include required tools info
    if skill.required_tools:
        result["required_tools"] = skill.required_tools

    # Track actual skill use when instructions are loaded for a turn.
    await sync_to_async(
        lambda: (
            type(skill)
            .objects.filter(id=skill.id)
            .update(load_count=F("load_count") + 1)
        )
    )()

    return {"success": True, "result": result}


TOOL_REGISTRY.register(
    OttoTool(
        name="load_skill_instructions",
        description=(
            "Load the full instructions for an available skill before other tool calls."
            # "Returns the skill body plus any hinted resources or required tool categories."
        ),
        parameters={
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "integer",
                    "description": (
                        "The numeric skill id from the Available Skills list."
                    ),
                },
            },
            "required": ["skill_id"],
            "additionalProperties": False,
        },
        execute=_load_skill_instructions,
        category=TOOL_CATEGORY_SKILLS,
        requires_user=True,
        requires_chat=True,
        strict=True,
        requires_approval=False,
        allow_auto_approve=True,
    )
)
