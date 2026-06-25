"""OpenMartian v0 host pipeline.

webcam (RTSP) -> episodes on disk -> recipe-conditioned LLM verdict -> dispatch.

See docs/Open_Martian_Firmware_Software_Design.md (PART 8.9 + PART 9) for the
v0 scope and the two non-negotiable seams preserved here:
  1. the Episode object is what the model consumes;
  2. the model call is recipe-conditioned (ask the recipe's own question).
"""

from __future__ import annotations

__version__ = "0.0.0"
