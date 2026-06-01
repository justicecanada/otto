"""LLM model definitions and configuration."""

from enum import Enum
from typing import Dict, List, Optional

from django.utils.translation import get_language

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_REASONING_EFFORTS = ("minimal", "low", "medium", "high")
NONE_BASED_REASONING_EFFORTS = ("none", "low", "medium", "high")
EXTENDED_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")

FLAGSHIP_GROUP_EN = "GPT-5 (reasoning, long output)"
FLAGSHIP_GROUP_FR = "GPT-5 (raisonnement, sortie longue)"
CURRENT_GPT5_GROUP_EN = FLAGSHIP_GROUP_EN
CURRENT_GPT5_GROUP_FR = FLAGSHIP_GROUP_FR
COST_OPTIMIZED_GPT5_GROUP_EN = FLAGSHIP_GROUP_EN
COST_OPTIMIZED_GPT5_GROUP_FR = FLAGSHIP_GROUP_FR
LEGACY_GROUP_EN = FLAGSHIP_GROUP_EN
LEGACY_GROUP_FR = FLAGSHIP_GROUP_FR
GPT4_GROUP_EN = "GPT-4 (non-reasoning, long input)"
GPT4_GROUP_FR = "GPT-4 (sans raisonnement, entrée longue)"

GROUP_SORT_ORDER = {
    FLAGSHIP_GROUP_EN.lower(): 0,
    FLAGSHIP_GROUP_FR.lower(): 0,
    GPT4_GROUP_EN.lower(): 1,
    GPT4_GROUP_FR.lower(): 1,
    "open source": 2,
    "source ouverte": 2,
}


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
    system_prompt_suffix: str = Field(
        "",
        description="A string to append to the system prompt for specific model requirements.",
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
    reasoning_efforts: list[str] = Field(
        default_factory=list,
        description="Ordered list of supported reasoning effort values for this model.",
    )
    vision: bool = Field(
        False, description="Whether the model supports vision/image inputs."
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
        model_id="gpt-5.4",
        deployment_name="gpt-5.4",
        description_en="GPT-5.4 (best quality, long input, 2x cost)",
        description_fr="GPT-5.4 (meilleure qualité, contexte long, coût 2x)",
        max_tokens_in=922000,
        max_tokens_out=128000,
        help_text_en="The flagship GPT-5 model. Best for the hardest tasks, and the only GPT-5 option here with a 1.05M total context window and 128K output.",
        help_text_fr="Le modèle phare GPT-5. Idéal pour les tâches les plus difficiles, et le seul GPT-5 ici avec une fenêtre de contexte totale de 1,05 M et une sortie de 128 K.",
        group_en=FLAGSHIP_GROUP_EN,
        group_fr=FLAGSHIP_GROUP_FR,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.\n",
        reasoning=True,
        reasoning_efforts=list(EXTENDED_REASONING_EFFORTS),
        vision=True,
    ),
    LLM(
        model_id="gpt-5.4-mini",
        deployment_name="gpt-5.4-mini",
        description_en="GPT-5.4-mini (best value, 0.5x cost)",
        description_fr="GPT-5.4-mini (meilleur rapport qualité/prix, coût 0.5x)",
        max_tokens_in=272000,
        max_tokens_out=128000,
        help_text_en="A smaller GPT-5.4 variant with the newest reasoning controls, including xhigh. Good for day-to-day drafting, summarization, and analysis when you want newer GPT-5 behavior at lower cost.",
        help_text_fr="Une variante plus légère de GPT-5.4 avec les contrôles de raisonnement les plus récents, y compris xhigh. Convient à la rédaction, au résumé et à l'analyse au quotidien lorsque vous voulez le comportement GPT-5 le plus récent à moindre coût.",
        group_en=CURRENT_GPT5_GROUP_EN,
        group_fr=CURRENT_GPT5_GROUP_FR,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.\n",
        reasoning=True,
        reasoning_efforts=list(EXTENDED_REASONING_EFFORTS),
        vision=True,
    ),
    LLM(
        model_id="gpt-5.4-nano",
        deployment_name="gpt-5.4-nano",
        description_en="GPT-5.4-nano (high throughput, 0.1x cost)",
        description_fr="GPT-5.4-nano (haut débit, coût 0.1x)",
        max_tokens_in=272000,
        max_tokens_out=128000,
        help_text_en="A high-throughput GPT-5.4 variant for simple transforms, classification, and extraction when you want the newest GPT-5.4 behavior at very low cost.",
        help_text_fr="Une variante GPT-5.4 à haut débit pour les transformations simples, la classification et l'extraction lorsque vous voulez le comportement GPT-5.4 le plus récent à très faible coût.",
        group_en=COST_OPTIMIZED_GPT5_GROUP_EN,
        group_fr=COST_OPTIMIZED_GPT5_GROUP_FR,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.\n",
        reasoning=True,
        reasoning_efforts=list(EXTENDED_REASONING_EFFORTS),
        vision=True,
    ),
    LLM(
        model_id="gpt-5.2",
        deployment_name="gpt-5.2",
        description_en="GPT-5.2 (better quality, 1.4x cost)",
        description_fr="GPT-5.2 (meilleure qualité, coût 1.4x)",
        max_tokens_in=272000,
        max_tokens_out=128000,
        help_text_en="A high-quality GPT-5 model with the newer none/low/medium/high/xhigh reasoning scale. Good when you want stronger quality than GPT-5.1 but do not need GPT-5.4's longer context.",
        help_text_fr="Un modèle GPT-5 de haute qualité avec la nouvelle échelle de raisonnement none/low/medium/high/xhigh. Utile lorsque vous voulez une meilleure qualité que GPT-5.1 sans avoir besoin du contexte plus long de GPT-5.4.",
        group_en=CURRENT_GPT5_GROUP_EN,
        group_fr=CURRENT_GPT5_GROUP_FR,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.\n",
        reasoning=True,
        reasoning_efforts=list(EXTENDED_REASONING_EFFORTS),
        vision=True,
    ),
    LLM(
        model_id="gpt-5.1",
        deployment_name="gpt-5.1",
        description_en="GPT-5.1 (recommended default, 1x cost)",
        description_fr="GPT-5.1 (par défaut recommandé, coût 1x)",
        max_tokens_in=272000,
        max_tokens_out=128000,
        help_text_en="The default GPT-5 choice. A reliable balance of quality, cost, and speed for most chats, with support for none/low/medium/high reasoning effort.",
        help_text_fr="Le choix GPT-5 par défaut. Un équilibre fiable entre qualité, coût et vitesse pour la plupart des conversations, avec prise en charge des niveaux de raisonnement none/low/medium/high.",
        group_en=CURRENT_GPT5_GROUP_EN,
        group_fr=CURRENT_GPT5_GROUP_FR,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.\n",
        # system_prompt_suffix="\n\n- Use Markdown **only where semantically correct** (e.g., `inline code`, ```code fences```, lists, tables).\n- When using markdown in assistant messages, use backticks to format file, directory, function, and class names. Use \( and \) for inline math, \[ and \] for block math.",
        reasoning=True,
        reasoning_efforts=list(NONE_BASED_REASONING_EFFORTS),
        vision=True,
    ),
    LLM(
        model_id="gpt-5",
        deployment_name="gpt-5",
        description_en="GPT-5 (deprecated by gpt-5.1, 1x cost)",
        description_fr="GPT-5 (obsolète, remplacé par gpt-5.1)",
        max_tokens_in=272000,
        max_tokens_out=128000,
        help_text_en="This model is deprecated and will be removed soon.",
        help_text_fr="Ce modèle est obsolète et sera bientôt supprimé.",
        group_en=LEGACY_GROUP_EN,
        group_fr=LEGACY_GROUP_FR,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.\n",
        # system_prompt_suffix="\n\n- Use Markdown **only where semantically correct** (e.g., `inline code`, ```code fences```, lists, tables).\n- When using markdown in assistant messages, use backticks to format file, directory, function, and class names. Use \( and \) for inline math, \[ and \] for block math.",
        reasoning=True,
        reasoning_efforts=list(DEFAULT_REASONING_EFFORTS),
        vision=True,
        deprecated_by="gpt-5.1",
    ),
    LLM(
        model_id="gpt-5-mini",
        deployment_name="gpt-5-mini",
        description_en="GPT-5-mini (low cost, 0.2x cost)",
        description_fr="GPT-5-mini (faible coût, coût 0.2x)",
        max_tokens_in=272000,
        max_tokens_out=128000,
        help_text_en="A lower-cost GPT-5 option for drafting, summarization, and general text tasks. Uses the older minimal/low/medium/high reasoning scale.",
        help_text_fr="Une option GPT-5 à moindre coût pour la rédaction, le résumé et les tâches textuelles générales. Utilise l'ancienne échelle de raisonnement minimal/low/medium/high.",
        group_en=COST_OPTIMIZED_GPT5_GROUP_EN,
        group_fr=COST_OPTIMIZED_GPT5_GROUP_FR,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.\n",
        # system_prompt_suffix="\n\n- Use Markdown **only where semantically correct** (e.g., `inline code`, ```code fences```, lists, tables).\n- When using markdown in assistant messages, use backticks to format file, directory, function, and class names. Use \( and \) for inline math, \[ and \] for block math.",
        reasoning=True,
        reasoning_efforts=list(DEFAULT_REASONING_EFFORTS),
        vision=True,
    ),
    LLM(
        model_id="gpt-5-nano",
        deployment_name="gpt-5-nano",
        description_en="GPT-5-nano (lowest cost, < 0.1x cost)",
        description_fr="GPT-5-nano (coût le plus bas, coût < 0.1x)",
        max_tokens_in=272000,
        max_tokens_out=128000,
        help_text_en="The most efficient model available. Recommended only when other models are too slow or expensive. Use for simple queries or basic text generation tasks.",
        help_text_fr="Le modèle le plus efficace disponible. Recommandé uniquement lorsque les autres modèles sont trop lents ou coûteux. À utiliser pour des requêtes simples ou des tâches de génération de texte de base.",
        group_en=COST_OPTIMIZED_GPT5_GROUP_EN,
        group_fr=COST_OPTIMIZED_GPT5_GROUP_FR,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.\n",
        # system_prompt_suffix="\n\n- Use Markdown **only where semantically correct** (e.g., `inline code`, ```code fences```, lists, tables).\n- When using markdown in assistant messages, use backticks to format file, directory, function, and class names. Use \( and \) for inline math, \[ and \] for block math.",
        reasoning=True,
        reasoning_efforts=list(DEFAULT_REASONING_EFFORTS),
        vision=True,
    ),
    LLM(
        model_id="gpt-4.1",
        deployment_name="gpt-4.1",
        description_en="GPT-4.1 (speed & quality, 1.2x cost)",
        description_fr="GPT-4.1 (vitesse et qualité, coût 1.2x)",
        max_tokens_in=1014808,
        max_tokens_out=32768,
        help_text_en="The highest quality model available. Use for complex tasks requiring deep reasoning, analysis, or creativity, such as legal analysis or generating detailed reports.",
        help_text_fr="Le modèle de la plus haute qualité disponible. À utiliser pour des tâches complexes nécessitant un raisonnement approfondi, une analyse ou de la créativité, telles que l'analyse juridique ou la génération de rapports détaillés.",
        group_en=GPT4_GROUP_EN,
        group_fr=GPT4_GROUP_FR,
        vision=True,
    ),
    LLM(
        model_id="gpt-4.1-mini",
        deployment_name="gpt-4.1-mini",
        description_en="GPT-4.1-mini (good value, 0.3x cost)",
        description_fr="GPT-4.1-mini (qualité-prix, coût 0.3x)",
        max_tokens_in=1014808,
        max_tokens_out=32768,
        help_text_en="A good balance of performance and cost. Suitable for a wide range of general tasks like drafting emails, summarizing documents, and answering questions.",
        help_text_fr="Un bon équilibre entre performance et coût. Convient à une large gamme de tâches générales telles que la rédaction d'e-mails, le résumé de documents et la réponse à des questions.",
        group_en=GPT4_GROUP_EN,
        group_fr=GPT4_GROUP_FR,
        vision=True,
    ),
    LLM(
        model_id="gpt-4.1-nano",
        deployment_name="gpt-4.1-nano",
        description_en="GPT-4.1-nano (most efficient, < 0.1x cost)",
        description_fr="GPT-4.1-nano (le plus efficace, coût < 0.1x)",
        max_tokens_in=1014808,
        max_tokens_out=32768,
        help_text_en="The most efficient model available. Recommended only when other models are too slow or expensive. Use for simple queries or basic text generation tasks.",
        help_text_fr="Le modèle le plus efficace disponible. Recommandé uniquement lorsque les autres modèles sont trop lents ou coûteux. À utiliser pour des requêtes simples ou des tâches de génération de texte de base.",
        group_en=GPT4_GROUP_EN,
        group_fr=GPT4_GROUP_FR,
        vision=True,
    ),
    LLM(
        model_id="gpt-4o",
        deployment_name="gpt-4o",
        description_en="GPT-4o (deprecated by gpt-4.1)",
        description_fr="GPT-4o (obsolète, remplacé par gpt-4.1)",
        max_tokens_in=111616,
        max_tokens_out=16384,
        help_text_en="This model is deprecated and will be removed soon.",
        help_text_fr="Ce modèle est obsolète et sera bientôt supprimé.",
        deprecated_by="gpt-4.1",
        group_en=GPT4_GROUP_EN,
        group_fr=GPT4_GROUP_FR,
        vision=True,
        is_active=False,
    ),
    LLM(
        model_id="gpt-4o-mini",
        deployment_name="gpt-4o-mini",
        description_en="GPT-4o-mini (deprecated by gpt-4.1-mini)",
        description_fr="GPT-4o-mini (obsolète, remplacé par gpt-4.1-mini)",
        max_tokens_in=111616,
        max_tokens_out=16384,
        help_text_en="This model is deprecated and will be removed soon.",
        help_text_fr="Ce modèle est obsolète et sera bientôt supprimé.",
        deprecated_by="gpt-4.1-mini",
        group_en=GPT4_GROUP_EN,
        group_fr=GPT4_GROUP_FR,
        vision=True,
        is_active=False,
    ),
    LLM(
        model_id="o4-mini",
        deployment_name="o4-mini",
        description_en="o4-mini (deprecated by gpt-5-mini)",
        description_fr="o4-mini (obsolète, remplacé par gpt-5-mini)",
        max_tokens_in=100000,
        max_tokens_out=100000,
        system_prompt_prefix="Formatting re-enabled.\nUse markdown formatting (headings, LaTeX math, tables, code blocks, etc. *as needed*) for your final response.",
        help_text_en="A specialized model with enhanced reasoning capabilities. Ideal for tasks that involve logical deduction, step-by-step problem solving, or structured data extraction.",
        help_text_fr="Un modèle spécialisé avec des capacités de raisonnement améliorées. Idéal pour les tâches impliquant une déduction logique, une résolution de problèmes étape par étape ou une extraction de données structurées.",
        deprecated_by="gpt-5-mini",
        group_en=LEGACY_GROUP_EN,
        group_fr=LEGACY_GROUP_FR,
        reasoning=True,
        reasoning_efforts=list(DEFAULT_REASONING_EFFORTS),
        vision=True,
        is_active=False,
    ),
    LLM(
        model_id="cohere-command-a",
        deployment_name="cohere-command-a",
        provider=ModelProvider.COHERE,
        description_en="Cohere Command A (Canadian, 2x cost)",
        description_fr="Cohere Command A (Canadien, coût 2x)",
        max_tokens_in=256000,
        max_tokens_out=256000,
        supports_chat_history=True,
        is_active=True,
        help_text_en="Cohere's language model. Suitable for general chat and Q&A tasks.",
        help_text_fr="Modèle de langage de Cohere. Convient aux tâches de chat général et de Q&A.",
        group_en="Open source",
        group_fr="Source ouverte",
    ),
    LLM(
        model_id="gpt-oss-120b",
        deployment_name="gpt-oss-120b",
        provider=ModelProvider.AZURE_OPENAI,
        description_en="GPT-OSS-120B (fastest, 0.1x cost)",
        description_fr="GPT-OSS-120B (plus rapide, coût 0.1x)",
        max_tokens_in=131072,
        max_tokens_out=131072,
        supports_chat_history=True,
        is_active=True,
        reasoning=False,
        help_text_en="Open-source language model with 120B parameters. Suitable for general chat and content generation tasks.",
        help_text_fr="Modèle de langage open-source avec 120B paramètres. Convient aux tâches de chat général et de génération de contenu.",
        group_en="Open source",
        group_fr="Source ouverte",
    ),
]

# A dictionary for quick lookups by model_id
MODELS_BY_ID: Dict[str, LLM] = {model.model_id: model for model in ALL_MODELS}


def get_supported_reasoning_efforts(model_id: str | None) -> tuple[str, ...]:
    """Return the supported reasoning efforts for the given model."""
    model = MODELS_BY_ID.get(model_id or "")
    if not model or not model.reasoning:
        return ()
    return tuple(model.reasoning_efforts or DEFAULT_REASONING_EFFORTS)


def normalize_reasoning_effort(model_id: str | None, reasoning_effort: str | None):
    """Normalize a reasoning effort value to something supported by the model."""
    if not reasoning_effort:
        return reasoning_effort

    supported = get_supported_reasoning_efforts(model_id)
    if not supported:
        return reasoning_effort
    if reasoning_effort in supported:
        return reasoning_effort
    if reasoning_effort == "minimal" and "none" in supported:
        return "none"
    if reasoning_effort == "none" and "minimal" in supported:
        return "minimal"
    if reasoning_effort == "xhigh" and "high" in supported:
        return "high"
    if reasoning_effort == "high" and "xhigh" in supported:
        return "high"
    return supported[0]


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
                    {
                        "label": model.description,
                        "is-reasoning": model.reasoning,
                        "supported-reasoning-efforts": ",".join(
                            model.reasoning_efforts
                        ),
                    },
                )
            )

    # Return as a list of (group, choices) tuples, sorted by group name
    def group_sort_key(item):
        group_name = item[0].lower()
        return (GROUP_SORT_ORDER.get(group_name, 99), group_name)

    return sorted(groups.items(), key=group_sort_key)


# Default models for various modes
DEFAULT_CHAT_MODEL_ID = "gpt-5.1"
DEFAULT_QA_MODEL_ID = "gpt-5-mini"
DEFAULT_SUMMARIZE_MODEL_ID = "gpt-5-mini"
DEFAULT_TRANSLATE_MODEL_ID = "gpt-5.4-mini"
DEFAULT_LAWS_MODEL_ID = "gpt-5.1"


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
