from django.conf import settings

import tiktoken
from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
from llama_index.llms.azure_openai import AzureOpenAI

from otto.models import Cost, User


class OttoLLM:
    """
    Wrapper around LlamaIndex to assist with cost tracking and to more easily swap LLMs.
    "model" must match the name of the deployment in Azure.
    """

    _deployment_to_model_mapping = {
        "gpt-4o": "gpt-4o",
        "gpt-4": "gpt-4-1106-preview",
        "gpt-35": "gpt-35-turbo-0125",
    }
    _deployment_to_max_input_tokens_mapping = {
        "gpt-4o": 128000,
        "gpt-4": 128000,
        "gpt-35": 16385,
    }

    def __init__(self, deployment: str = "gpt-4o", temperature: float = 0.1):
        if deployment not in self._deployment_to_model_mapping:
            raise ValueError(f"Invalid deployment: {deployment}")
        self.deployment = deployment
        self.model = self._deployment_to_model_mapping[deployment]
        self.temperature = temperature
        self._token_counter = TokenCountingHandler(
            tokenizer=tiktoken.encoding_for_model(self.model).encode
        )
        self.llm = self._get_llm()
        self.max_input_tokens = self._deployment_to_max_input_tokens_mapping[deployment]

    def _get_llm(self) -> AzureOpenAI:
        return AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_version=settings.AZURE_OPENAI_VERSION,
            api_key=settings.AZURE_OPENAI_KEY,
            deployment_name=self.deployment,
            model=self.model,
            temperature=self.temperature,
            callback_manager=CallbackManager([self._token_counter]),
        )

    async def chat_stream(self, chat_history: list):
        response_stream = await self.llm.astream_chat(chat_history)
        async for chunk in response_stream:
            yield chunk.message.content

    def create_costs(self, user: User, feature: str):
        return (
            Cost.objects.new(
                user=user,
                cost_type=f"{self.deployment}-in",
                feature=feature,
                count=self.input_token_count,
            ),
            Cost.objects.new(
                user=user,
                cost_type=f"{self.deployment}-out",
                feature=feature,
                count=self.output_token_count,
            ),
        )

    @property
    def input_token_count(self):
        return self._token_counter.prompt_llm_token_count

    @property
    def output_token_count(self):
        return self._token_counter.completion_llm_token_count
