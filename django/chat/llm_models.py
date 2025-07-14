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


# The canonical list of all models available in the system.
# This is the new source of truth.
ALL_MODELS: List[LLM] = [
    LLM(
        model_id="gpt-4.1-mini",
        deployment_name="gpt-4.1-mini",
        description_en="GPT-4.1-mini (best value, 3x cost)",
        description_fr="GPT-4.1-mini (meilleur rapport qualité/prix, coût 3x)",
        max_tokens_in=1047576,
        max_tokens_out=32768,
        help_text_en="A good balance of performance and cost. Suitable for a wide range of general tasks like drafting emails, summarizing documents, and answering questions.",
        help_text_fr="Un bon équilibre entre performance et coût. Convient à une large gamme de tâches générales telles que la rédaction d'e-mails, le résumé de documents et la réponse à des questions.",
    ),
    LLM(
        model_id="gpt-4.1",
        deployment_name="gpt-4.1",
        description_en="GPT-4.1 (best quality, 12x cost)",
        description_fr="GPT-4.1 (meilleure qualité, coût 12x)",
        max_tokens_in=1047576,
        max_tokens_out=32768,
        help_text_en="The highest quality model available. Use for complex tasks requiring deep reasoning, analysis, or creativity, such as legal analysis or generating detailed reports.",
        help_text_fr="Le modèle de la plus haute qualité disponible. À utiliser pour des tâches complexes nécessitant un raisonnement approfondi, une analyse ou de la créativité, telles que l'analyse juridique ou la génération de rapports détaillés.",
    ),
    LLM(
        model_id="o4-mini",
        deployment_name="o4-mini",
        description_en="o4-mini (reasoning, >7x cost)",
        description_fr="o4-mini (ajoute le raisonnement, coût >7x)",
        max_tokens_in=200000,
        max_tokens_out=100000,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.",
        help_text_en="A specialized model with enhanced reasoning capabilities. Ideal for tasks that involve logical deduction, step-by-step problem solving, or structured data extraction.",
        help_text_fr="Un modèle spécialisé avec des capacités de raisonnement améliorées. Idéal pour les tâches impliquant une déduction logique, une résolution de problèmes étape par étape ou une extraction de données structurées.",
    ),
    LLM(
        model_id="o3",
        deployment_name="o3",
        description_en="o3 (reasoning, >12x cost)",
        description_fr="o4-mini (ajoute le raisonnement, coût >12x)",
        max_tokens_in=200000,
        max_tokens_out=100000,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.",
        help_text_en="A specialized model with enhanced reasoning capabilities. Ideal for tasks that involve logical deduction, step-by-step problem solving, or structured data extraction.",
        help_text_fr="Un modèle spécialisé avec des capacités de raisonnement améliorées. Idéal pour les tâches impliquant une déduction logique, une résolution de problèmes étape par étape ou une extraction de données structurées.",
    ),
    LLM(
        model_id="gpt-4.1-nano",
        deployment_name="gpt-4.1-nano",
        description_en="GPT-4.1-nano (most efficient, 0.5x cost)",
        description_fr="GPT-4.1-nano (le plus efficace, coût 0.5x)",
        max_tokens_in=1047576,
        max_tokens_out=32768,
        help_text_en="The most efficient model available. Recommended only when other models are too slow or expensive. Use for simple queries or basic text generation tasks.",
        help_text_fr="Le modèle le plus efficace disponible. Recommandé uniquement lorsque les autres modèles sont trop lents ou coûteux. À utiliser pour des requêtes simples ou des tâches de génération de texte de base.",
    ),
    LLM(
        model_id="o3-mini",
        deployment_name="o3-mini",
        description_en="o3-mini (legacy reasoning, >7x cost)",
        description_fr="o3-mini (modèle hérité de raisonnement, coût >7x)",
        max_tokens_in=200000,
        max_tokens_out=100000,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.",
        help_text_en="This model is deprecated and will be removed soon.",
        help_text_fr="Ce modèle est obsolète et sera bientôt supprimé.",
        deprecated_by="o4-mini",
    ),
    LLM(
        model_id="gpt-4o-mini",
        deployment_name="gpt-4o-mini",
        description_en="GPT-4o-mini (legacy model, 1x cost)",
        description_fr="GPT-4o-mini (modèle hérité, coût 1x)",
        max_tokens_in=128000,
        max_tokens_out=16384,
        is_active=True,
        help_text_en="This model is deprecated and will be removed soon.",
        help_text_fr="Ce modèle est obsolète et sera bientôt supprimé.",
        deprecated_by="gpt-4.1-mini",
    ),
    LLM(
        model_id="gpt-4o",
        deployment_name="gpt-4o",
        description_en="GPT-4o (legacy model, 15x cost)",
        description_fr="GPT-4o (modèle hérité, coût 15x)",
        max_tokens_in=128000,
        max_tokens_out=16384,
        is_active=True,
        help_text_en="This model is deprecated and will be removed soon.",
        help_text_fr="Ce modèle est obsolète et sera bientôt supprimé.",
        deprecated_by="gpt-4.1",
    ),
    LLM(
        model_id="command-a",
        deployment_name="command-a",
        provider=ModelProvider.COHERE,
        description_en="Command-A (Cohere, Canadian)",
        description_fr="Command-A (Cohere, Canadian)",
        max_tokens_in=4096,
        max_tokens_out=1024,
        supports_chat_history=False,
        is_active=False,  # Assuming this might not be for general chat
        help_text_en="Not available in Pilot",
        help_text_fr="Non disponible dans Pilot",
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
        if model.is_active and not model.deprecated_by
    ]


# Default models for various modes
DEFAULT_CHAT_MODEL_ID = "gpt-4.1-mini"
DEFAULT_QA_MODEL_ID = "gpt-4.1-mini"
DEFAULT_SUMMARIZE_MODEL_ID = "gpt-4.1-mini"
DEFAULT_TRANSLATE_MODEL_ID = "gpt-4.1-mini"
DEFAULT_LAWS_MODEL_ID = "gpt-4.1"


def get_model(model_id: str) -> LLM:
    """
    Retrieves a model by its ID. If the model is deprecated, it returns
    the model that replaces it. If the model_id is not found, it returns
    the default chat model.
    """
    model = MODELS_BY_ID.get(model_id)
    if model and model.deprecated_by:
        # Recursively find the current model
        return get_model(model.deprecated_by)
    if model:
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
