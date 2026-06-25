"""Provider-agnostic, VLM-capable LLM abstraction (PART 9.2).

The app talks to ONE interface: ordered text + images in, a schema-validated
dict out. Provider differences (image encoding, structured-output enforcement)
hide inside each concrete client. The same ``generate(...)`` seam works for a
cloud model (Claude) and a local model (Ollama) — swap the impl, not the caller.
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class LLMConfig:
    provider: str = "ollama"  # "claude" | "ollama"
    model: str = "gemma3:4b"
    max_tokens: int = 1024
    temperature: float = 0.0  # near-deterministic for v0
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    seed: Optional[int] = None
    system: str = ""
    api_key_env: Optional[str] = None  # env var holding the API key (cloud only)
    host: str = ""  # base URL (Ollama host or cloud API root)
    timeout: float = 180.0


@dataclass
class ImagePart:
    """One image in a prompt. ``data`` is the raw bytes (e.g. JPEG)."""

    data: bytes
    media_type: str = "image/jpeg"

    def b64(self) -> str:
        """Base64-encoded payload (no data: URI prefix)."""
        return base64.b64encode(self.data).decode("ascii")


# A prompt is an ordered list of text and image parts.
Content = Union[str, ImagePart]


class LLMClient(ABC):
    """The app's single interface to ANY VLM."""

    def __init__(self, config: LLMConfig):
        self.config = config
        # Token usage from the most recent generate() call, normalized to
        # {"input_tokens", "output_tokens", "total_tokens"}. Empty until set.
        self.last_usage: dict = {}

    @property
    def model(self) -> str:
        return self.config.model

    @property
    def provider(self) -> str:
        return self.config.provider

    @abstractmethod
    def generate(
        self,
        content: list[Content],
        *,
        schema: dict,
        schema_name: str = "decision",
    ) -> dict:
        """Return a dict matching ``schema``. Provider-specific enforcement inside.

        ``content`` is the ordered user prompt (text + images). The system prompt
        is taken from ``self.config.system``.
        """
        ...

    # -- small shared helpers concrete clients may use --------------------- #

    @staticmethod
    def split_content(content: list[Content]) -> tuple[list[str], list[ImagePart]]:
        """Partition a prompt into its text fragments and its images, in order."""
        texts = [c for c in content if isinstance(c, str)]
        images = [c for c in content if isinstance(c, ImagePart)]
        return texts, images
