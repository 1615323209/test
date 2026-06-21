"""DeepSeek client via OpenAI SDK with function-calling support.

DeepSeek's primary API follows the OpenAI format. This client uses the
OpenAI SDK to call DeepSeek models with structured function-calling
(equivalent to Anthropic's tool-use) for guaranteed JSON output.

Reference: https://api.deepseek.com
"""

import json
import asyncio
from typing import Optional
from pathlib import Path

from openai import AsyncOpenAI

from novel_decomp.cache.disk_cache import DiskCache
from novel_decomp.config import (
    ANTHROPIC_API_KEY as DEEPSEEK_API_KEY,  # Same key field
    ANTHROPIC_BASE_URL as DEEPSEEK_BASE_URL,
    DEFAULT_MODEL,
    get_price,
)


class DeepSeekClient:
    """OpenAI SDK wrapper for DeepSeek API with function-calling support."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        cache: Optional[DiskCache] = None,
        max_retries: int = 3,
    ):
        self.api_key = api_key or DEEPSEEK_API_KEY

        self.base_url = base_url or "https://api.deepseek.com"

        self.model = model or DEFAULT_MODEL
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )
        self.cache = cache
        self.max_retries = max_retries

        # Token tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.call_count = 0

    # ── Public interface (same as AnthropicClient) ──

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
    ):
        """Basic chat completion — returns an OpenAI response object."""
        model = model or self.model

        # Check cache
        cache_key = ""
        if self.cache:
            cache_key = self.cache._make_key(layer, batch_id, model, system_prompt, user_message)
            cached = self.cache.get(cache_key)
            if cached:
                return self._reconstruct_from_cache(cached)

        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )

                usage = response.usage
                self.total_input_tokens += usage.prompt_tokens
                self.total_output_tokens += usage.completion_tokens
                self.call_count += 1
                self._update_cost(usage.prompt_tokens, usage.completion_tokens, model)

                # Cache
                if self.cache and cache_key:
                    cache_data = {
                        "id": response.id,
                        "model": response.model,
                        "content": response.choices[0].message.content,
                        "usage": {
                            "input_tokens": usage.prompt_tokens,
                            "output_tokens": usage.completion_tokens,
                        },
                    }
                    self.cache.set(cache_key, json.dumps(cache_data, ensure_ascii=False))

                return response

            except Exception as e:
                last_error = e
                err_str = str(e)
                # 不重试认证错误、参数错误、权限错误
                if any(code in err_str for code in ("401", "403", "400")):
                    raise
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
        """Call DeepSeek with function calling for structured output.

        Converts the Anthropic-format tool_schema to OpenAI function format,
        forces the model to call the function, and returns the parsed result.

        Args:
            system_prompt: System instruction.
            user_message: User message with chapter text.
            tool_schema: Anthropic-format tool schema (will be converted).
            model: Model override.
            max_tokens: Max completion tokens.
            temperature: Sampling temperature.
            layer: Pipeline layer for caching.
            batch_id: Batch ID for caching.

        Returns:
            Parsed dict from the function call arguments.
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
                    if "tool_output" in data:
                        return data["tool_output"]
                except (json.JSONDecodeError, KeyError):
                    pass

        # Convert Anthropic tool schema → OpenAI function format
        openai_tools = _anthropic_tool_to_openai(tool_schema)
        tool_name = tool_schema.get("name", "provide_batch_analysis")

        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=openai_tools,
                    tool_choice={
                        "type": "function",
                        "function": {"name": tool_name},
                    },
                    # DeepSeek: 关闭 thinking 模式才能用 tool_choice
                    # 否则报错 "Thinking mode does not support this tool_choice"
                    extra_body={"thinking": {"type": "disabled"}},
                )

                usage = response.usage
                self.total_input_tokens += usage.prompt_tokens
                self.total_output_tokens += usage.completion_tokens
                self.call_count += 1
                self._update_cost(usage.prompt_tokens, usage.completion_tokens, model)

                # Extract function call arguments
                result = _extract_openai_tool_output(response)
                if not result:
                    # Fallback: try to parse content as JSON directly
                    content = response.choices[0].message.content
                    if content:
                        try:
                            result = json.loads(content)
                        except json.JSONDecodeError:
                            pass

                if not result:
                    raise ValueError(
                        f"No function call or parseable JSON in response. "
                        f"Finish reason: {response.choices[0].finish_reason}"
                    )

                # Cache
                if self.cache and cache_key:
                    cache_data = {
                        "id": response.id,
                        "model": response.model,
                        "tool_output": result,
                        "usage": {
                            "input_tokens": usage.prompt_tokens,
                            "output_tokens": usage.completion_tokens,
                        },
                    }
                    self.cache.set(cache_key, json.dumps(cache_data, ensure_ascii=False))

                return result

            except Exception as e:
                last_error = e
                err_str = str(e)
                # 不重试认证错误、参数错误、权限错误
                if any(code in err_str for code in ("401", "403", "400")):
                    raise
                if "ValueError" in type(e).__name__ and "No function call" in err_str:
                    pass  # 模型没返回 function call，重试
                wait = 2 ** attempt * 1.5
                print(f"  ⚠ {type(e).__name__}: {e} (attempt {attempt+1}/{self.max_retries}), "
                      f"waiting {wait:.1f}s...")
                await asyncio.sleep(wait)

        raise RuntimeError(f"Tool-use API call failed after {self.max_retries} retries: {last_error}")

    def _reconstruct_from_cache(self, cached: str):
        """Reconstruct an OpenAI response-like object from cached JSON."""
        data = json.loads(cached)

        class Usage:
            prompt_tokens = data["usage"]["input_tokens"]
            completion_tokens = data["usage"]["output_tokens"]
            total_tokens = prompt_tokens + completion_tokens

        class Message:
            content = data.get("content", "")

        class Choice:
            def __init__(self):
                self.message = Message()

        class FakeResponse:
            def __init__(self):
                self.id = data.get("id", "cached")
                self.model = data.get("model", self.model)
                self.choices = [Choice()]
                self.usage = Usage()

        return FakeResponse()

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


def _recover_truncated_json(text: str, error_pos: int) -> dict | None:
    """Recover truncated/corrupted JSON.

    Tries:
    1. Balance brackets/braces from error_pos forward
    2. If that fails, scan backward from error_pos to find the last clean
       structural boundary and retry from there
    """
    # Strategy 1: balance from error_pos
    result = _balance_and_close(text, error_pos)
    if result:
        return result

    # Strategy 2: scan backward to find last valid structural boundary
    # Try progressively shorter prefixes — cut at each }, or ], or "
    boundary_chars = []
    for i, ch in enumerate(text[:error_pos]):
        if ch in '}]"':
            boundary_chars.append(i)

    # Try from latest boundary backwards
    for cut_pos in reversed(boundary_chars[-50:]):  # last 50 boundaries
        # Skip if we're inside a string
        result = _balance_and_close(text, cut_pos + 1)
        if result:
            return result

    return None


def _balance_and_close(text: str, cut_pos: int) -> dict | None:
    """Balance brackets from text[:cut_pos] and close them to form valid JSON."""
    prefix = text[:cut_pos]
    stack = []
    in_string = False
    escape = False
    for ch in prefix:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in '{[':
            stack.append(ch)
        elif ch == '}':
            if stack and stack[-1] == '{':
                stack.pop()
        elif ch == ']':
            if stack and stack[-1] == '[':
                stack.pop()

    closing = ''
    if in_string:
        closing += '"'
    for ch in reversed(stack):
        closing += '}' if ch == '{' else ']'

    # Try candidates: progressively add closing chars
    candidate = prefix.rstrip(',\n\r ')
    for i in range(len(closing) + 1):
        trial = candidate + closing[:i]
        try:
            return json.loads(trial)
        except json.JSONDecodeError:
            continue
    return None


# ── Helper functions ──

def _anthropic_tool_to_openai(anthropic_tool: dict) -> list[dict]:
    """Convert an Anthropic-format tool schema to OpenAI function-calling format.

    Anthropic format:
        {"name": "foo", "input_schema": {"type": "object", "properties": {...}}}

    OpenAI format:
        [{"type": "function", "function": {"name": "foo", "parameters": {...}}}]
    """
    name = anthropic_tool.get("name", "output_schema")
    description = anthropic_tool.get("description", "")
    input_schema = anthropic_tool.get("input_schema", {})

    # The Anthropic input_schema IS a JSON Schema — OpenAI expects the same
    return [{
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": input_schema,
            # NOTE: Do NOT set strict=True — our schema is complex/nested
            # and strict mode requires allOf/anyOf-free schemas with
            # additionalProperties: false at every level, which is too
            # restrictive for our dynamic analysis output structure.
        },
    }]


def _extract_openai_tool_output(response) -> dict | None:
    """Extract function call arguments from an OpenAI chat completion response."""
    choice = response.choices[0]
    message = choice.message
    finish_reason = choice.finish_reason

    # Method 1: Extract from tool_calls (handle any type)
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        for tc in tool_calls:
            # Get function/arguments — don't require tc.type == "function"
            func = getattr(tc, "function", None)
            if func is None:
                continue

            args_str = getattr(func, "arguments", "") or ""
            if not args_str:
                continue

            func_name = getattr(func, "name", "unknown")
            print(f"    [extract] tool_call: {func_name}, args_len={len(args_str)}")

            # Try to parse as JSON
            try:
                return json.loads(args_str)
            except json.JSONDecodeError as e:
                # Truncated JSON? Close all open brackets/braces
                print(f"  ⚠ JSON parse error on function args "
                      f"(len={len(args_str)}, pos={e.pos}): {e}")
                recovered = _recover_truncated_json(args_str, e.pos)
                if recovered:
                    return recovered
                return None

    # Method 2: Extract JSON from message content (fallback)
    content = getattr(message, "content", "") or ""
    if content:
        content = content.strip()
        # Remove markdown code fences
        if content.startswith("```"):
            lines = content.split("\n")
            if len(lines) > 2:
                content = "\n".join(lines[1:-1])
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

    # Debug: print what we got
    print(f"  ⚠ Could not extract tool output. "
          f"finish_reason={finish_reason}, "
          f"has_tool_calls={tool_calls is not None and len(tool_calls) > 0 if tool_calls else False}, "
          f"content_len={len(content)}")
    if tool_calls:
        for i, tc in enumerate(tool_calls):
            func = getattr(tc, "function", None)
            print(f"  ⚠ tool_call[{i}]: id={getattr(tc, 'id', '')}, "
                  f"type={getattr(tc, 'type', '')}, "
                  f"func_name={getattr(func, 'name', '') if func else 'N/A'}, "
                  f"args_len={len(getattr(func, 'arguments', '') or '') if func else 0}")

    return None
