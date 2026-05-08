"""LiteLLM client for extraction."""

import json
from typing import Any

from litellm import acompletion

from neo4j_agent_memory.core.exceptions import ExtractionError


class LiteLLM:
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ):
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def extract(self, prompt: str) -> dict[str, Any]:
        """Extract structured information using the LLM.
        Args:
            prompt: The prompt to send to the LLM
        Returns:
            Parsed JSON response as a dictionary
        Raises:
            ExtractionError: If extraction fails
        """
        try:
            response = await acompletion(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at extracting structured information from text. "
                        "You follow the POLE+O data model (Person, Object, Location, Event, Organization). "
                        "Always respond with valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
                api_key=self.api_key,
            )
            content = response.choices[0].message.content
            if not content:
                return {}

            return json.loads(content)
        except json.JSONDecodeError as e:
            raise ExtractionError(f"Failed to parse LLM response as JSON: {e}") from e
        except Exception as e:
            raise ExtractionError(f"Failed to extract entities: {e}") from e
