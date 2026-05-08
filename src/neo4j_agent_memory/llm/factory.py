"""Factory for creating LLM clients."""

from neo4j_agent_memory.config.settings import LLMConfig
from neo4j_agent_memory.llm.litellm import LiteLLM


def create_llm(config: LLMConfig) -> LiteLLM:
    """Create a LiteLLM client based on configuration.
    Args:
        config: LLM configuration
    Returns:
        LiteLLM instance
    """
    return LiteLLM(
        model=config.model,
        api_key=config.api_key.get_secret_value() if config.api_key else None,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
