"""LLM backends + a tiny factory.

DeepSeek is intentionally deferred for v0 (its hosted API is text-only and cannot
see frames). The factory leaves an obvious seam to add it — and a self-hosted
DeepSeek-VL2 — later without touching callers.
"""

from __future__ import annotations

import os
from typing import Optional

from .base import Content, ImagePart, LLMClient, LLMConfig

__all__ = ["Content", "ImagePart", "LLMClient", "LLMConfig", "build_llm"]

# Sensible defaults per backend.
_DEFAULT_MODEL = {
    "claude": "claude-haiku-4-5-20251001",
    "ollama": "gemma3:4b",
}


def build_llm(
    backend: str,
    model: Optional[str] = None,
    *,
    system: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.0,
    params: Optional[dict] = None,
) -> LLMClient:
    """Construct an :class:`LLMClient` for ``backend`` ("claude" | "ollama").

    ``params`` is the runtime-param dict from the UI. Only the keys a backend
    supports are read; unknown keys are ignored. ``num_predict`` is accepted as
    an Ollama-flavored alias for ``max_tokens``.
    """
    backend = backend.lower()
    params = params or {}

    def _f(key, default=None):  # float-ish getter, None if absent/blank
        v = params.get(key, default)
        return None if v in (None, "") else float(v)

    def _i(key, default=None):  # int-ish getter
        v = params.get(key, default)
        return None if v in (None, "") else int(v)

    temperature = _f("temperature", temperature)
    max_tokens = _i("max_tokens", None) or _i("num_predict", None) or max_tokens
    top_p = _f("top_p")
    top_k = _i("top_k")
    seed = _i("seed")

    if backend == "claude":
        from .claude_client import ClaudeClient

        cfg = LLMConfig(
            provider="claude",
            model=model or _DEFAULT_MODEL["claude"],
            api_key_env="ANTHROPIC_API_KEY",
            host="https://api.anthropic.com",
            system=system, max_tokens=max_tokens, temperature=temperature,
            top_p=top_p, top_k=top_k,
        )
        return ClaudeClient(cfg)

    if backend == "ollama":
        from .ollama_client import OllamaClient

        cfg = LLMConfig(
            provider="ollama",
            model=model or _DEFAULT_MODEL["ollama"],
            host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            system=system, max_tokens=max_tokens, temperature=temperature,
            top_p=top_p, top_k=top_k, seed=seed,
        )
        return OllamaClient(cfg)

    raise ValueError(
        f"Unknown backend {backend!r}. v0 supports: claude, ollama. "
        "(DeepSeek deferred — hosted API is text-only.)"
    )
