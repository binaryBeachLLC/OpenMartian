"""Ollama (local VLM) backend for the LLM abstraction (PART 9.2).

Talks to a local ``ollama serve`` over its HTTP API — no Python SDK required,
just ``requests``. Serves multimodal models such as ``gemma3:4b`` (default),
``llava``, and ``llama3.2-vision:11b``.

Vision requires a *multimodal* model: a text-only model (e.g. plain ``gemma``)
will silently ignore the ``images`` array. Schema guarantees come from Ollama's
structured-output support — we pass the JSON Schema as the ``format`` parameter,
so the model is constrained to emit JSON matching it.
"""

from __future__ import annotations

import json

import requests

from .base import Content, ImagePart, LLMClient


class OllamaClient(LLMClient):
    """:class:`LLMClient` backed by a local Ollama server.

    Serves ``gemma3:4b`` (default), ``llava``, ``llama3.2-vision:11b``, etc.
    Vision needs a multimodal model (text-only models ignore images). Structured
    output is enforced via the ``format`` JSON-schema parameter on ``/api/chat``.
    """

    def generate(
        self,
        content: list[Content],
        *,
        schema: dict,
        schema_name: str = "decision",
    ) -> dict:
        texts, images = self.split_content(content)

        messages: list[dict] = []
        if self.config.system:
            messages.append({"role": "system", "content": self.config.system})
        messages.append(
            {
                "role": "user",
                "content": "\n".join(texts),
                "images": [img.b64() for img in images],
            }
        )

        options = {
            "temperature": self.config.temperature,
            "num_predict": self.config.max_tokens,
        }
        if self.config.top_p is not None:
            options["top_p"] = self.config.top_p
        if self.config.top_k is not None:
            options["top_k"] = self.config.top_k
        if self.config.seed is not None:
            options["seed"] = self.config.seed

        body = {
            "model": self.config.model,
            "stream": False,
            "format": schema,
            "options": options,
            "messages": messages,
        }

        url = f"{self.config.host}/api/chat"
        try:
            resp = requests.post(url, json=body, timeout=self.config.timeout)
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Could not reach Ollama at {self.config.host} — "
                "is `ollama serve` running?"
            )

        if resp.status_code != 200:
            raise RuntimeError(
                f"Ollama returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json()
        in_tok = int(data.get("prompt_eval_count", 0) or 0)
        out_tok = int(data.get("eval_count", 0) or 0)
        self.last_usage = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
        }
        raw = data["message"]["content"]
        try:
            return json.loads(_strip_fences(raw))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Ollama emitted non-JSON content ({exc}): {raw[:500]!r}"
            )


def _strip_fences(text: str) -> str:
    """Defensively strip a leading/trailing markdown ```/```json fence."""
    s = text.strip()
    if s.startswith("```"):
        # Drop the opening fence line (``` or ```json).
        first_newline = s.find("\n")
        s = s[first_newline + 1 :] if first_newline != -1 else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()
