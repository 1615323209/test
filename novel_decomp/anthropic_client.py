"""Async Anthropic SDK wrapper with retry, tool-use, and caching."""

import json
import asyncio
from typing import Optional, Any
from pathlib import Path

import anthropic
from anthropic import AsyncAnthropic

from novel_decomp.cache.disk_cache import DiskCache
from novel_decomp.config import (
    ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, DEFAULT_MODEL, get_price,
)


class AnthropicClient:
    """Wraps the Anthropic async SDK with retry logic and disk caching."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        cache: Optional[DiskCache] = None,
        max_retries: int = 3,
    ):
        self.api_key = api_key or ANTHROPIC_API_KEY
        self.model = model or DEFAULT_MODEL
        self.base_url = base_url or ANTHROPIC_BASE_URL
        self.client = AsyncAnthropic(api_key=self.api_key, base_url=self.base_url)
        self.cache = cache
        self.max_retries = max_retries

        # Token tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.call_count = 0

    async def analyze(
        self,
        system_prompt: str,
        user_message: str,
        *,
        model: str = "",
        max_tokens: int = 8192,
        temperature: float = 0.3,
        layer: int = 0,
        batch_id: int = 0,
    ) -> anthropic.types.Message:
        """Basic message call with caching and retries."""
        model = model or self.model

        # Check cache
        cache_key = ""
        if self.cache:
            cache_key = self.cache._make_key(layer, batch_id, model, system_prompt, user_message)
            cached = self.cache.get(cache_key)
            if cached:
                # Return a proxy-ish object; we need the raw text for structured parse
                # We store the JSON-serialized message
                return self._reconstruct_message(cached)

        # API call with retries
        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = await self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                )
                # Track usage
                usage = response.usage
                self.total_input_tokens += usage.input_tokens
                self.total_output_tokens += usage.output_tokens
                self.call_count += 1
                self._update_cost(usage.input_tokens, usage.output_tokens, model)

                # Cache response
                if self.cache and cache_key:
                    cache_data = {
                        "id": response.id,
                        "model": response.model,
                        "content": [{"type": b.type, "text": b.text} for b in response.content],
                        "stop_reason": response.stop_reason,
                        "usage": {
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,
                        },
                    }
                    self.cache.set(cache_key, json.dumps(cache_data, ensure_ascii=False))

                return response

            except anthropic.APIStatusError as e:
                # 4xx client errors: don't retry (bad request, auth, etc.)
                if e.status_code < 500:
                    raise
                # 5xx server errors: retry
                last_error = e
                wait = 2 ** attempt
                print(f"  ⚠ Server error {e.status_code} (attempt {attempt+1}/{self.max_retries}), "
                      f"waiting {wait:.1f}s...")
                await asyncio.sleep(wait)

            except Exception as e:
                # Network errors, timeout, SDK internal errors: retry
                last_error = e
                wait = 2 ** attempt * 1.5
                print(f"  ⚠ {type(e).__name__}: {e} (attempt {attempt+1}/{self.max_retries}), "
                      f"waiting {wait:.1f}s...")
                await asyncio.sleep(wait)

        raise RuntimeError(f"API call failed after {self.max_retries} retries: {last_error}")

    async def analyze_with_tool(
        self,
        system_prompt: str,
        user_message: str,
        tool_schema: dict,
        *,
        model: str = "",
        max_tokens: int = 32768,
        temperature: float = 0.3,
        layer: int = 0,
        batch_id: int = 0,
    ) -> dict:
        """Call the API with a forced tool-use for guaranteed structured output.

        Returns the parsed tool input as a dict.
        """
        model = model or self.model

        # Check cache
        cache_key = ""
        if self.cache:
            cache_key = self.cache._make_key(layer, batch_id, model, system_prompt, user_message)
            cached = self.cache.get(cache_key)
            if cached:
                try:
                    data = json.loads(cached)
                    return self._extract_tool_input(data)
                except (json.JSONDecodeError, KeyError):
                    pass  # Cache miss, fall through

        tool_name = tool_schema.get("name", "output_schema")

        for attempt in range(self.max_retries):
            try:
                response = await self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                    tools=[tool_schema],
                    tool_choice={"type": "tool", "name": tool_name},
                )

                usage = response.usage
                self.total_input_tokens += usage.input_tokens
                self.total_output_tokens += usage.output_tokens
                self.call_count += 1
                self._update_cost(usage.input_tokens, usage.output_tokens, model)

                # Cache response
                if self.cache and cache_key:
                    cache_data = {
                        "id": response.id,
                        "model": response.model,
                        "content": [],
                        "stop_reason": response.stop_reason,
                        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
                    }
                    for block in response.content:
                        if block.type == "tool_use":
                            cache_data["content"].append({
                                "type": "tool_use",
                                "name": block.name,
                                "input": block.input,
                            })
                    self.cache.set(cache_key, json.dumps(cache_data, ensure_ascii=False))

                return self._extract_tool_input_from_response(response)

            except anthropic.APIStatusError as e:
                # 4xx client errors: don't retry (bad request, auth, etc.)
                if e.status_code < 500:
                    raise
                # 5xx server errors: retry
                wait = 2 ** attempt
                print(f"  ⚠ Server error {e.status_code} (attempt {attempt+1}/{self.max_retries}), "
                      f"waiting {wait:.1f}s...")
                await asyncio.sleep(wait)

            except Exception as e:
                # Network errors, timeout, SDK internal errors: retry
                wait = 2 ** attempt * 1.5
                print(f"  ⚠ {type(e).__name__}: {e} (attempt {attempt+1}/{self.max_retries}), "
                      f"waiting {wait:.1f}s...")
                await asyncio.sleep(wait)

        raise RuntimeError(f"Tool-use API call failed after {self.max_retries} retries")

    def _reconstruct_message(self, cached: str) -> anthropic.types.Message:
        """Reconstruct an anthropic.Message from cached JSON."""
        data = json.loads(cached)
        # Create a simple namespace to mimic the response
        class Usage:
            input_tokens = data["usage"]["input_tokens"]
            output_tokens = data["usage"]["output_tokens"]

        class ContentBlock:
            def __init__(self, type_val, text=""):
                self.type = type_val
                self.text = text

        msg = object.__new__(anthropic.types.Message)
        msg.id = data.get("id", "cached")
        msg.model = data.get("model", self.model)
        msg.content = [
            ContentBlock(b["type"], b.get("text", "")) for b in data.get("content", [])
        ]
        msg.stop_reason = data.get("stop_reason", "end_turn")
        msg.usage = Usage()
        return msg

    def _extract_tool_input(self, cached_data: dict) -> dict:
        """Extract tool input from cached response data."""
        for block in cached_data.get("content", []):
            if block.get("type") == "tool_use" and "input" in block:
                return block["input"]
        return {}

    def _extract_tool_input_from_response(self, response: anthropic.types.Message) -> dict:
        """Extract structured tool input from an API response."""
        for block in response.content:
            if block.type == "tool_use":
                return block.input if isinstance(block.input, dict) else {}
        # Fallback: try text content for JSON
        for block in response.content:
            if block.type == "text":
                try:
                    return json.loads(block.text)
                except json.JSONDecodeError:
                    pass
        return {}

    def _update_cost(self, input_tokens: int, output_tokens: int, model: str):
        """Accumulate estimated cost based on model pricing."""
        input_price, output_price = get_price(model)
        self.total_cost += (
            input_tokens * input_price / 1_000_000
            + output_tokens * output_price / 1_000_000
        )

    @property
    def usage_summary(self) -> dict:
        return {
            "calls": self.call_count,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "estimated_cost_usd": round(self.total_cost, 4),
        }
