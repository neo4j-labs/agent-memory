"""Factory for creating LLM clients."""

from neo4j_agent_memory.config.settings import LLMConfig
from neo4j_agent_memory.core.exceptions import ExtractionError


def create_llm(config: LLMConfig):
    """Create a LiteLLM client based on configuration.
    Args:
        config: LLM configuration
    Returns:
        LiteLLM instance
    Raises:
        ExtractionError: If LiteLLM is not installed
    """
    try:
        from neo4j_agent_memory.llm.litellm import LiteLLM
    except ImportError as e:
        raise ExtractionError(
            "LiteLLM is not installed. Install it with: pip install 'neo4j-agent-memory[litellm]'"
        ) from e

    return LiteLLM(
        model=config.model,
        api_key=config.api_key.get_secret_value() if config.api_key else None,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
