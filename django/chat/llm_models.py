from enum import Enum
from typing import Dict, List, Optional

from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from pydantic import BaseModel, ConfigDict, Field


class ModelProvider(str, Enum):
    AZURE_OPENAI = "Azure OpenAI"
    GOOGLE = "Google"
    ANTHROPIC = "Anthropic"
    COHERE = "Cohere"


class LLM(BaseModel):
    """
    Defines the configuration for a single Large Language Model.
    """

    model_config = ConfigDict(protected_namespaces=(), arbitrary_types_allowed=True)

    model_id: str = Field(
        ...,
        description="The unique identifier for the model used internally.",
    )
    deployment_name: str = Field(
        ...,
        description="The name of the model deployment, e.g., in Azure.",
    )
    provider: ModelProvider = Field(
        ModelProvider.AZURE_OPENAI, description="The provider of the model."
    )
    description_en: str = Field(
        "", description="The English description for the model."
    )
    description_fr: str = Field("", description="The French description for the model.")
    help_text_en: str = Field("", description="The English help text for the model.")
    help_text_fr: str = Field("", description="The French help text for the model.")
    is_active: bool = Field(
        True, description="Whether the model is currently active and available for use."
    )
    deprecated_by: Optional[str] = Field(
        None,
        description="The model_id of the model that replaces this one, if any.",
    )
    supports_chat_history: bool = Field(
        True, description="Whether the model supports a conversational chat history."
    )
    system_prompt_prefix: str = Field(
        "",
        description="A string to prepend to the system prompt for specific model requirements.",
    )
    max_tokens_in: int = Field(
        ..., description="The maximum number of input tokens the model supports."
    )
    max_tokens_out: int = Field(
        ..., description="The maximum number of output tokens the model can generate."
    )
    group_en: str = Field(
        "General",
        description="The group this model belongs to, used for categorization in the UI.",
    )
    group_fr: str = Field(
        "Général",
        description="The group this model belongs to, used for categorization in the UI (French).",
    )
    reasoning: bool = Field(
        False, description="Whether the model is a reasoning model."
    )

    @property
    def description(self) -> str:
        if get_language() == "fr" and self.description_fr:
            return self.description_fr
        return self.description_en

    @property
    def help_text(self) -> str:
        if get_language() == "fr" and self.help_text_fr:
            return self.help_text_fr
        return self.help_text_en

    @property
    def group(self) -> str:
        if get_language() == "fr" and self.group_fr:
            return self.group_fr
        return self.group_en


# The canonical list of all models available in the system.
# This is the new source of truth.
ALL_MODELS: List[LLM] = [
    LLM(
        model_id="gpt-oss-20b",
        deployment_name="gpt-oss-20b",
        description_en="GPT-OSS-20B",
        description_fr="GPT-OSS-20B",
        max_tokens_in=128000,
        max_tokens_out=32768,
        group_en="Local / on-prem",
        group_fr="Local / sur site",
    ),
]

# A dictionary for quick lookups by model_id
MODELS_BY_ID: Dict[str, LLM] = {model.model_id: model for model in ALL_MODELS}


def get_chat_model_choices() -> List[tuple[str, str]]:
    """
    Returns a list of tuples (model_id, description) for all active chat models.
    This is used to populate dropdowns in forms.
    """
    return [
        (model.model_id, model.description)
        for model in ALL_MODELS
        if model.is_active
        # and not model.deprecated_by
    ]


def get_grouped_chat_model_choices() -> list[tuple[str, list[tuple[str, dict]]]]:
    """
    Returns a list of tuples (group, [(model_id, {label: description, is_reasoning: bool})])
    for all active chat models.
    This is used to populate grouped dropdowns in forms.
    """
    from collections import defaultdict

    groups = defaultdict(list)
    for model in ALL_MODELS:
        if (
            model.is_active
            # and not model.deprecated_by
        ):
            groups[model.group].append(
                (
                    model.model_id,
                    {"label": model.description, "is-reasoning": model.reasoning},
                )
            )

    # Return as a list of (group, choices) tuples, sorted by group name
    # Sort groups so that any group containing "Legacy" or "obsolète" comes last
    def group_sort_key(item):
        group_name = item[0].lower()
        if (
            "legacy" in group_name
            or "obsolète" in group_name
            or "outdated" in group_name
        ):
            return (1, group_name)
        return (0, group_name)

    return sorted(groups.items(), key=group_sort_key)


# Default models for various modes
DEFAULT_CHAT_MODEL_ID = "gpt-oss-20b"
DEFAULT_QA_MODEL_ID = "gpt-oss-20b"
DEFAULT_SUMMARIZE_MODEL_ID = "gpt-oss-20b"
DEFAULT_TRANSLATE_MODEL_ID = "gpt-oss-20b"
DEFAULT_LAWS_MODEL_ID = "gpt-4.1"


def get_model(model_id: str) -> LLM:
    """
    Retrieves a model by its ID. If the model is deprecated, it returns
    the model that replaces it. If the model_id is not found, it returns
    the default chat model.
    """
    model = MODELS_BY_ID.get(model_id)
    if model and model.deprecated_by and not model.is_active:
        # Recursively find the current model
        return get_model(model.deprecated_by)
    if model and model.is_active:
        return model
    # Fallback to default if model_id is invalid
    return MODELS_BY_ID[DEFAULT_CHAT_MODEL_ID]


def get_updated_model_id(model_id: str) -> tuple[str, bool]:
    """
    Given a model_id, returns the current valid model_id for it and a boolean
    indicating if it was changed (e.g., due to deprecation or invalid ID).
    """
    if not model_id or model_id not in MODELS_BY_ID:
        # Model doesn't exist, so it's definitely updated to default.
        return DEFAULT_CHAT_MODEL_ID, True

    new_model = get_model(model_id)  # get_model handles deprecation chain
    return new_model.model_id, new_model.model_id != model_id
