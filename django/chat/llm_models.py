from enum import Enum
from typing import Dict, List, Optional

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
    description: str = Field(
        "", description="A short, user-facing description for the model selector UI."
    )
    help_text: str = Field(
        "",
        description="Longer, more detailed help text for a modal or tooltip in the UI.",
    )
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


# The canonical list of all models available in the system.
# This is the new source of truth.
ALL_MODELS: List[LLM] = [
    LLM(
        model_id="gpt-4.1-mini",
        deployment_name="gpt-4.1-mini",
        description=str(_("GPT-4.1-mini (best value, 3x cost)")),
        max_tokens_in=1047576,
        max_tokens_out=32768,
        help_text=str(
            _(
                "A good balance of performance and cost. Suitable for a wide range of general tasks like drafting emails, summarizing documents, and answering questions."
            )
        ),
    ),
    LLM(
        model_id="gpt-4.1",
        deployment_name="gpt-4.1",
        description=str(_("GPT-4.1 (best quality, 12x cost)")),
        max_tokens_in=1047576,
        max_tokens_out=32768,
        help_text=str(
            _(
                "The highest quality model available. Use for complex tasks requiring deep reasoning, analysis, or creativity, such as legal analysis or generating detailed reports."
            )
        ),
    ),
    LLM(
        model_id="o3-mini",
        deployment_name="o3-mini",
        description=str(_("o3-mini (adds reasoning, 7x cost)")),
        max_tokens_in=200000,
        max_tokens_out=100000,
        system_prompt_prefix="Formatting re-enabled.",
        help_text=str(
            _(
                "A specialized model with enhanced reasoning capabilities. Ideal for tasks that involve logical deduction, step-by-step problem solving, or structured data extraction."
            )
        ),
    ),
    LLM(
        model_id="gpt-4o-mini",
        deployment_name="gpt-4o-mini",
        description=str(_("GPT-4o-mini (legacy model, 1x cost)")),
        max_tokens_in=128000,
        max_tokens_out=16384,
        is_active=False,
        deprecated_by="gpt-4.1-mini",
        help_text=str(_("This model is deprecated and will be removed soon.")),
    ),
    LLM(
        model_id="gpt-4o",
        deployment_name="gpt-4o",
        description=str(_("GPT-4o (legacy model, 15x cost)")),
        max_tokens_in=128000,
        max_tokens_out=16384,
        is_active=False,
        deprecated_by="gpt-4.1",
        help_text=str(_("This model is deprecated and will be removed soon.")),
    ),
    LLM(
        model_id="command-a",
        deployment_name="command-a",
        provider=ModelProvider.COHERE,
        description=str(_("Command-A (special purpose)")),
        max_tokens_in=4096,
        max_tokens_out=1024,
        supports_chat_history=False,
        is_active=False,  # Assuming this might not be for general chat
        help_text=str(
            _(
                "A special-purpose model that does not support chat history. Used for specific internal commands."
            )
        ),
    ),
]

# A dictionary for quick lookups by model_id
MODELS_BY_ID: Dict[str, LLM] = {model.model_id: model for model in ALL_MODELS}

# A list of (model_id, description) for active models, for use in UI dropdowns.
CHAT_MODELS = [
    (model.model_id, model.description)
    for model in ALL_MODELS
    if model.is_active and model.supports_chat_history
]


def get_model(model_id: str) -> Optional[LLM]:
    """
    Retrieves a model by its ID. If the model is deprecated, it returns
    the model that replaces it.
    """
    model = MODELS_BY_ID.get(model_id)
    if model and model.deprecated_by:
        return MODELS_BY_ID.get(model.deprecated_by)
    return model
