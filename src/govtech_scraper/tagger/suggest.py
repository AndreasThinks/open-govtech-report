"""Step 1: LLM-based tag suggestion using OpenRouter."""

import json
import logging
from typing import Optional

import aiohttp

from .schemas import TaggingResult, TagSuggestion
from .prompts import (
    TAG_SUGGESTION_SYSTEM,
    TAG_SUGGESTION_WITH_VOCABULARY,
    TAG_SUGGESTION_USER,
)

logger = logging.getLogger(__name__)


class TagSuggester:
    """Suggests tags for repositories using an LLM via OpenRouter."""

    def __init__(
        self,
        api_key: str,
        model: str = "qwen/qwen3-32b",
        base_url: str = "https://openrouter.ai/api/v1",
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def suggest(
        self,
        context: str,
        existing_tags: Optional[list[str]] = None,
    ) -> TaggingResult:
        """Get tag suggestions from the LLM.
        
        Args:
            context: Formatted repository context from context.py
            existing_tags: Optional list of existing tags to hint at vocabulary
            
        Returns:
            TaggingResult with suggested tags, category, and tech stack
        """
        if existing_tags:
            system_prompt = TAG_SUGGESTION_WITH_VOCABULARY.format(
                existing_tags=", ".join(existing_tags)
            )
        else:
            system_prompt = TAG_SUGGESTION_SYSTEM

        user_prompt = TAG_SUGGESTION_USER.format(context=context)

        # Use structured output (JSON mode) with response_format
        response_schema = TaggingResult.model_json_schema()

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "tagging_result",
                            "strict": True,
                            "schema": response_schema,
                        },
                    },
                    "temperature": 0,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"OpenRouter API error: {resp.status} {body}")
                data = await resp.json()

        content = data["choices"][0]["message"]["content"]
        
        try:
            parsed = json.loads(content)
            result = TaggingResult.model_validate(parsed)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to parse structured output, attempting recovery: {e}")
            # Fallback: try to extract JSON from the response
            result = self._fallback_parse(content)

        return result

    def _fallback_parse(self, content: str) -> TaggingResult:
        """Attempt to parse LLM output that didn't conform to schema."""
        # Try to find JSON in the response
        import re
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            return TaggingResult.model_validate(parsed)
        
        # Last resort: return empty result
        logger.error(f"Could not parse LLM output: {content[:200]}")
        return TaggingResult(tags=[], primary_category="unknown", tech_stack=[])
