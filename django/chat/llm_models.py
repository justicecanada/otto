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
    UNKNOWN = "Unknown"


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
        ModelProvider.UNKNOWN, description="The provider of the model."
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
        model_id="gpt-4.1",
        deployment_name="gpt-4.1",
        description_en="GPT-4.1 (speed & quality, 1x cost)",
        description_fr="GPT-4.1 (vitesse et qualité, coût 1x)",
        max_tokens_in=1047576,
        max_tokens_out=32768,
        help_text_en="The highest quality model available. Use for complex tasks requiring deep reasoning, analysis, or creativity, such as legal analysis or generating detailed reports.",
        help_text_fr="Le modèle de la plus haute qualité disponible. À utiliser pour des tâches complexes nécessitant un raisonnement approfondi, une analyse ou de la créativité, telles que l'analyse juridique ou la génération de rapports détaillés.",
        group_en="General / long input",
        group_fr="Général / entrée longue",
    ),
    LLM(
        model_id="gpt-4.1-mini",
        deployment_name="gpt-4.1-mini",
        description_en="GPT-4.1-mini (best value, 0.2x cost)",
        description_fr="GPT-4.1-mini (qualité-prix, coût 0.2x)",
        max_tokens_in=1047576,
        max_tokens_out=32768,
        help_text_en="A good balance of performance and cost. Suitable for a wide range of general tasks like drafting emails, summarizing documents, and answering questions.",
        help_text_fr="Un bon équilibre entre performance et coût. Convient à une large gamme de tâches générales telles que la rédaction d'e-mails, le résumé de documents et la réponse à des questions.",
        group_en="General / long input",
        group_fr="Général / entrée longue",
    ),
    LLM(
        model_id="gpt-4.1-nano",
        deployment_name="gpt-4.1-nano",
        description_en="GPT-4.1-nano (most efficient, < 0.1x cost)",
        description_fr="GPT-4.1-nano (le plus efficace, coût < 0.1x)",
        max_tokens_in=1047576,
        max_tokens_out=32768,
        help_text_en="The most efficient model available. Recommended only when other models are too slow or expensive. Use for simple queries or basic text generation tasks.",
        help_text_fr="Le modèle le plus efficace disponible. Recommandé uniquement lorsque les autres modèles sont trop lents ou coûteux. À utiliser pour des requêtes simples ou des tâches de génération de texte de base.",
        group_en="General / long input",
        group_fr="Général / entrée longue",
    ),
    LLM(
        model_id="o4-mini",
        deployment_name="o4-mini",
        description_en="o4-mini (good reasoning, 1x cost)",
        description_fr="o4-mini (bon raisonnement, coût 1x)",
        max_tokens_in=200000,
        max_tokens_out=100000,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.",
        help_text_en="A specialized model with enhanced reasoning capabilities. Ideal for tasks that involve logical deduction, step-by-step problem solving, or structured data extraction.",
        help_text_fr="Un modèle spécialisé avec des capacités de raisonnement améliorées. Idéal pour les tâches impliquant une déduction logique, une résolution de problèmes étape par étape ou une extraction de données structurées.",
        group_en="Reasoning / long output",
        group_fr="Raisonnement / sortie longue",
        reasoning=True,
    ),
    LLM(
        model_id="o3",
        deployment_name="o3",
        description_en="o3 (special purpose, 2x cost)",
        description_fr="o3 (but spécial, coût 2x)",
        max_tokens_in=200000,
        max_tokens_out=100000,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.",
        help_text_en="A specialized model with enhanced reasoning capabilities. Ideal for tasks that involve logical deduction, step-by-step problem solving, or structured data extraction.",
        help_text_fr="Un modèle spécialisé avec des capacités de raisonnement améliorées. Idéal pour les tâches impliquant une déduction logique, une résolution de problèmes étape par étape ou une extraction de données structurées.",
        group_en="Reasoning / long output",
        group_fr="Raisonnement / sortie longue",
        reasoning=True,
    ),
    LLM(
        model_id="gpt-4o",
        deployment_name="gpt-4o",
        description_en="GPT-4o (deprecated by gpt-4.1)",
        description_fr="GPT-4o (obsolète, remplacé par gpt-4.1)",
        max_tokens_in=128000,
        max_tokens_out=16384,
        is_active=True,
        help_text_en="This model is deprecated and will be removed soon.",
        help_text_fr="Ce modèle est obsolète et sera bientôt supprimé.",
        deprecated_by="gpt-4.1",
        group_en="Legacy / outdated models",
        group_fr="Modèles obsolètes",
    ),
    LLM(
        model_id="gpt-4o-mini",
        deployment_name="gpt-4o-mini",
        description_en="GPT-4o-mini (deprecated by gpt-4.1-mini)",
        description_fr="GPT-4o-mini (obsolète, remplacé par gpt-4.1-mini)",
        max_tokens_in=128000,
        max_tokens_out=16384,
        is_active=True,
        help_text_en="This model is deprecated and will be removed soon.",
        help_text_fr="Ce modèle est obsolète et sera bientôt supprimé.",
        deprecated_by="gpt-4.1-mini",
        group_en="Legacy / outdated models",
        group_fr="Modèles obsolètes",
    ),
    LLM(
        model_id="o3-mini",
        deployment_name="o3-mini",
        description_en="o3-mini (deprecated by o4-mini)",
        description_fr="o3-mini (obsolète, remplacé par o4-mini)",
        max_tokens_in=200000,
        max_tokens_out=100000,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.",
        help_text_en="This model is deprecated and will be removed soon.",
        help_text_fr="Ce modèle est obsolète et sera bientôt supprimé.",
        deprecated_by="o4-mini",
        reasoning=True,
        group_en="Legacy / outdated models",
        group_fr="Modèles obsolètes",
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
        group_en="Sandbox only (testing)",
        group_fr="Sandbox uniquement (testing)",
    ),
    LLM(
        model_id="gemini-2.5-flash",
        deployment_name="gemini-2.5-flash",
        provider=ModelProvider.GOOGLE,
        description_en="Gemini 2.5 Flash (Google, best value, long context)",
        description_fr="Gemini 2.5 Flash (Google, meilleur rapport qualité-prix, contexte long)",
        max_tokens_in=1048576,
        max_tokens_out=65536,
        group_en="Sandbox only (testing)",
        group_fr="Sandbox uniquement (testing)",
    ),
    LLM(
        model_id="gemini-2.5-pro",
        deployment_name="gemini-2.5-pro",
        provider=ModelProvider.GOOGLE,
        description_en="Gemini 2.5 Pro (Google, best overall)",
        description_fr="Gemini 2.5 Pro (Google, meilleur global)",
        max_tokens_in=1048576,
        max_tokens_out=65536,
        group_en="Sandbox only (testing)",
        group_fr="Sandbox uniquement (testing)",
    ),
    LLM(
        model_id="claude-sonnet-4",
        deployment_name="claude-sonnet-4",
        provider=ModelProvider.ANTHROPIC,
        description_en="Claude Sonnet 4 (Anthropic, kind of expensive)",
        description_fr="Claude Sonnet 4 (Anthropic, assez cher)",
        max_tokens_in=200000,
        max_tokens_out=64000,
        group_en="Sandbox only (testing)",
        group_fr="Sandbox uniquement (testing)",
    ),
    LLM(
        model_id="claude-opus-4",
        deployment_name="claude-opus-4",
        provider=ModelProvider.ANTHROPIC,
        description_en="Claude Opus 4 (Anthropic, insanely expensive)",
        description_fr="Claude Opus 4 (Anthropic, extrêmement cher)",
        max_tokens_in=200000,
        max_tokens_out=32000,
        group_en="Sandbox only (testing)",
        group_fr="Sandbox uniquement (testing)",
    ),
    LLM(
        model_id="groq-llama-4-scout",
        deployment_name="groq-llama-4-scout",
        description_en="Llama 4 Scout (Groq, fast open-source RAG)",
        description_fr="Llama 4 Scout (Groq, RAG open-source rapide)",
        max_tokens_in=131072,
        max_tokens_out=8192,
        group_en="Sandbox only (testing)",
        group_fr="Sandbox uniquement (testing)",
    ),
    LLM(
        model_id="groq-llama-4-maverick",
        deployment_name="groq-llama-4-maverick",
        description_en="Llama 4 Maverick (Groq, fast open-source chat)",
        description_fr="Llama 4 Maverick (Groq, chat open-source rapide)",
        max_tokens_in=131072,
        max_tokens_out=8192,
        group_en="Sandbox only (testing)",
        group_fr="Sandbox uniquement (testing)",
    ),
    LLM(
        model_id="groq-QwQ-32B",
        deployment_name="groq-QwQ-32B",
        description_en="QwQ-32B (Groq, reasoning, long context)",
        description_fr="QwQ-32B (Groq, raisonnement, contexte long)",
        max_tokens_in=131072,
        max_tokens_out=131072,
        group_en="Sandbox only (testing)",
        group_fr="Sandbox uniquement (testing)",
    ),
    LLM(
        model_id="fireworks-Qwen3-235B",
        deployment_name="fireworks-Qwen3-235B",
        description_en="Qwen3 235B (Fireworks, best open source)",
        description_fr="Qwen3 235B (Fireworks, meilleur open source)",
        max_tokens_in=128000,
        max_tokens_out=128000,
        group_en="Sandbox only (testing)",
        group_fr="Sandbox uniquement (testing)",
    ),
    LLM(
        model_id="fireworks-Qwen3-30B",
        deployment_name="fireworks-Qwen3-30B",
        description_en="Qwen3 30B-A3B (Fireworks, best small model)",
        description_fr="Qwen3 30B-A3B (Fireworks, meilleur petit modèle)",
        max_tokens_in=39000,
        max_tokens_out=39000,
        group_en="Sandbox only (testing)",
        group_fr="Sandbox uniquement (testing)",
    ),
    LLM(
        model_id="cerebras-llama-4-scout",
        deployment_name="cerebras-llama-4-scout",
        description_en="Llama 4 Scout (Cerebras, instant, short context)",
        description_fr="Llama 4 Scout (Cerebras, instantané, contexte court)",
        max_tokens_in=32000,
        max_tokens_out=32000,
        group_en="Sandbox only (testing)",
        group_fr="Sandbox uniquement (testing)",
    ),
    LLM(
        model_id="cerebras-qwen-3-32b",
        deployment_name="cerebras-qwen-3-32b",
        description_en="Qwen3 32B (Cerebras, instant, strong reasoning)",
        description_fr="Qwen3 32B (Cerebras, instantané, raisonnement fort)",
        max_tokens_in=32768,
        max_tokens_out=32768,
        group_en="Sandbox only (testing)",
        group_fr="Sandbox uniquement (testing)",
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
