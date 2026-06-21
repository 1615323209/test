"""Configuration management — env vars with sensible defaults.

Supports:
- Anthropic official API (via Anthropic SDK)
- DeepSeek API (via OpenAI SDK → https://api.deepseek.com)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PROMPTS_DIR = PROJECT_ROOT / "prompts"

# ── Provider ──
PROVIDER = os.getenv("NOVEL_DECOMP_PROVIDER", "anthropic").lower()  # "anthropic" or "deepseek"

# ── API ──
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# For Anthropic provider: https://api.anthropic.com
# For DeepSeek provider: https://api.deepseek.com (native OpenAI endpoint)
DEFAULT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "deepseek": "https://api.deepseek.com",
    "zkmj": "https://ai.zkmjnic.tech/v1",
}
ANTHROPIC_BASE_URL = os.getenv(
    "ANTHROPIC_BASE_URL",
    DEFAULT_BASE_URLS.get(PROVIDER, "https://api.anthropic.com"),
)

# ── Models ──
# Provider-specific defaults
_PROVIDER_DEFAULTS = {
    "anthropic": {
        "main": "claude-sonnet-4-6-20250514",
        "cheap": "claude-haiku-4-5-20251001",
    },
    "deepseek": {
        "main": "deepseek-v4-pro",
        "cheap": "deepseek-v4-flash",
    },
    "zkmj": {
        "main": "deepseek-v4-pro",
        "cheap": "deepseek-v4-flash",
    },
}
_defaults = _PROVIDER_DEFAULTS.get(PROVIDER, _PROVIDER_DEFAULTS["anthropic"])

DEFAULT_MODEL = os.getenv("NOVEL_DECOMP_MODEL", _defaults["main"])
CHEAP_MODEL = os.getenv("NOVEL_DECOMP_CHEAP_MODEL", _defaults["cheap"])

# ── Pricing (per 1M tokens) ──
# Format: {model_name_lower_fragment: (input_price, output_price)}
PRICING = {
    # Anthropic
    "sonnet": (3.00, 15.00),
    "haiku": (0.80, 4.00),
    "opus": (15.00, 75.00),
    # DeepSeek
    "deepseek-v4-pro": (0.47, 1.10),
    "deepseek-v4-flash": (0.10, 0.30),
    "deepseek-chat": (0.47, 1.10),
    "deepseek-reasoner": (0.47, 1.10),
}

# ── Pipeline ──
DEFAULT_BATCH_SIZE = 20          # chapters per batch
MAX_BATCH_TOKENS = 60_000        # max input tokens per batch
DEFAULT_CONCURRENCY = 3          # max concurrent API calls
ROLLING_CONTEXT_MAX_TOKENS = 1500  # max tokens for rolling context

# ── Caching ──
CACHE_ENABLED = True
CACHE_DIR = DATA_DIR / "cache"

# ── Output ──
OUTPUT_DIR = DATA_DIR / "output"
PROCESSED_DIR = DATA_DIR / "processed"
CHECKPOINT_DIR = DATA_DIR / "checkpoint"
EXPORT_DIR = DATA_DIR / "export"

# Ensure directories exist
for d in [CACHE_DIR, OUTPUT_DIR, PROCESSED_DIR, CHECKPOINT_DIR, EXPORT_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def get_price(model: str) -> tuple[float, float]:
    """Get (input_price, output_price) per 1M tokens for a model.

    Args:
        model: Model ID string (e.g. 'claude-sonnet-4-6-20250514', 'deepseek-v4-pro').

    Returns:
        (input_price_per_1M, output_price_per_1M)
    """
    model_lower = model.lower()
    # DeepSeek exact model name check first
    for key, prices in PRICING.items():
        if key in model_lower:
            return prices
    # Fallback to Sonnet pricing
    return PRICING["sonnet"]


def get_provider_info() -> dict:
    """Get current provider configuration info.

    Returns:
        Dict with provider, models, base_url, and key status.
    """
    return {
        "provider": PROVIDER,
        "base_url": ANTHROPIC_BASE_URL,
        "main_model": DEFAULT_MODEL,
        "cheap_model": CHEAP_MODEL,
        "api_key_set": bool(ANTHROPIC_API_KEY),
        "pricing": get_price(DEFAULT_MODEL),
    }


def create_client(
    cache=None,
    model: str = "",
    max_retries: int = 3,
):
    """Factory: create the right LLM client based on NOVEL_DECOMP_PROVIDER.

    Args:
        cache: DiskCache instance (optional).
        model: Override default model.
        max_retries: Max API call retries.

    Returns:
        AnthropicClient or DeepSeekClient instance.
    """
    if PROVIDER in ("deepseek", "zkmj"):
        from novel_decomp.deepseek_client import DeepSeekClient
        return DeepSeekClient(
            api_key=ANTHROPIC_API_KEY,
            model=model or DEFAULT_MODEL,
            base_url=ANTHROPIC_BASE_URL,
            cache=cache,
            max_retries=max_retries,
        )
    else:
        from novel_decomp.anthropic_client import AnthropicClient
        return AnthropicClient(
            api_key=ANTHROPIC_API_KEY,
            model=model or DEFAULT_MODEL,
            base_url=ANTHROPIC_BASE_URL,
            cache=cache,
            max_retries=max_retries,
        )
