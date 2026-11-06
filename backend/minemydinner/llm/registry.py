from collections.abc import Callable

from langchain.chat_models import init_chat_model
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from pydantic import SecretStr

from minemydinner.constants import EMBEDDING_DIMENSION
from minemydinner.llm.config import ConfigError, ModelConfig
from minemydinner.settings import get_settings

ChatBuilder = Callable[[ModelConfig, int], BaseChatModel]
EmbeddingBuilder = Callable[[ModelConfig, int], Embeddings]

# One retry at most, so a failing provider reaches the fallback model quickly.
MAX_RETRIES = 1

KEY_VARIABLES = {
    "google_genai": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

_chat_builders: dict[str, ChatBuilder] = {}
_embedding_builders: dict[str, EmbeddingBuilder] = {}


def register_provider(name: str, builder: ChatBuilder) -> None:
    _chat_builders[name] = builder


def register_embedding_provider(name: str, builder: EmbeddingBuilder) -> None:
    _embedding_builders[name] = builder


def build_chat_model(model: ModelConfig, timeout: int) -> BaseChatModel:
    builder = _chat_builders.get(model.provider)
    if builder is None:
        raise ConfigError(f"No chat builder is registered for provider {model.provider}")
    return builder(model, timeout)


def build_embedding_model(model: ModelConfig, timeout: int) -> Embeddings:
    builder = _embedding_builders.get(model.provider)
    if builder is None:
        raise ConfigError(f"No embedding builder is registered for provider {model.provider}")
    return builder(model, timeout)


def _api_key(provider: str) -> SecretStr:
    variable = KEY_VARIABLES[provider]
    key: SecretStr | None = getattr(get_settings(), variable.lower())
    if key is None or not key.get_secret_value():
        raise ConfigError(f"{variable} is not set")
    return key


def _default_chat_builder(model: ModelConfig, timeout: int) -> BaseChatModel:
    return init_chat_model(
        model.model,
        model_provider=model.provider,
        api_key=_api_key(model.provider),
        timeout=timeout,
        max_retries=MAX_RETRIES,
        **model.options,
    )


def _gemini_embedding_builder(model: ModelConfig, timeout: int) -> Embeddings:
    return GoogleGenerativeAIEmbeddings(
        model=model.model,
        api_key=_api_key(model.provider),
        output_dimensionality=EMBEDDING_DIMENSION,
        request_options={"timeout": timeout},
    )


for _provider in KEY_VARIABLES:
    register_provider(_provider, _default_chat_builder)
register_embedding_provider("google_genai", _gemini_embedding_builder)
