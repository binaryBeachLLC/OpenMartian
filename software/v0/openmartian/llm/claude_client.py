"""Claude (Anthropic) backend for the VLM abstraction (PART 9.2).

Talks to the Anthropic Messages API directly over HTTP with ``requests`` — no
``anthropic`` SDK dependency. Defaults to Haiku 4.5 (fast, cheap, vision-capable)
and guarantees a schema-shaped dict by using the PORTABLE forced-tool-call
pattern: a single tool whose ``input_schema`` is the caller's schema, pinned via
``tool_choice``, so the model must emit exactly one ``tool_use`` block matching
the schema. Claude's native structured outputs could swap in behind this same
``generate`` method later without changing any caller.
"""

from __future__ import annotations

import json
import os

import requests

from .base import Content, ImagePart, LLMClient


class ClaudeClient(LLMClient):
    """Anthropic Messages API backend.

    Uses Haiku 4.5 by default (vision-capable). The forced-tool-call pattern
    (a single tool pinned via ``tool_choice``) makes the response a
    schema-guaranteed dict. Claude's native structured outputs are a drop-in
    replacement behind ``generate`` if we ever want to switch.
    """

    def generate(
        self,
        content: list[Content],
        *,
        schema: dict,
        schema_name: str = "decision",
    ) -> dict:
        """Return a dict matching ``schema`` via a forced tool call."""
        api_key = os.environ.get(self.config.api_key_env or "ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export your Anthropic API key, "
                "e.g. `export ANTHROPIC_API_KEY=sk-ant-...`, before calling ClaudeClient."
            )

        # Build the user content blocks in order, preserving text/image interleaving.
        blocks: list[dict] = []
        for part in content:
            if isinstance(part, ImagePart):
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": part.media_type,
                            "data": part.b64(),
                        },
                    }
                )
            else:
                blocks.append({"type": "text", "text": part})

        body: dict = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "tools": [
                {
                    "name": schema_name,
                    "description": "Return the structured decision.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": schema_name},
            "messages": [{"role": "user", "content": blocks}],
        }
        if self.config.system:
            body["system"] = self.config.system
        if self.config.top_p is not None:
            body["top_p"] = self.config.top_p
        if self.config.top_k is not None:
            body["top_k"] = self.config.top_k

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        response = requests.post(
            f"{self.config.host}/v1/messages",
            headers=headers,
            json=body,
            timeout=self.config.timeout,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Anthropic API request failed with status {response.status_code}: "
                f"{response.text[:500]}"
            )

        data = response.json()
        usage = data.get("usage", {}) or {}
        in_tok = int(usage.get("input_tokens", 0) or 0)
        out_tok = int(usage.get("output_tokens", 0) or 0)
        self.last_usage = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
        }
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                return block["input"]

        raise RuntimeError(
            "No tool_use block in Anthropic response (likely a refusal or a "
            f"different stop reason): {json.dumps(data)[:500]}"
        )
