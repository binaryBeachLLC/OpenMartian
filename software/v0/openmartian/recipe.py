"""Recipe + recipe-conditioned prompt construction (PART 9.5).

The one invariant from the design doc: **ask the recipe's own question against
the raw frames** — never describe-then-string-match. ``build_prompt`` puts the
frames in the prompt and asks the recipe's literal question, so the model looks
for exactly what the rule cares about against data nothing pre-discarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .llm.base import Content, ImagePart
from .schema import Episode

# The initial v0 recipe.
DEFAULT_RECIPE_TEXT = "Is there a man standing in the frame?"

SYSTEM_PROMPT = (
    "You are a perception judge for a sensor device. You are shown a short "
    "episode: an ordered series of still frames captured a fraction of a second "
    "apart from a single fixed camera. Judge ONLY the specific yes/no question "
    "asked about what is visible across these frames — do not give a general "
    "description. Return your verdict with the provided decision tool/schema: "
    "set match=true only when the condition is clearly satisfied by the pixels, "
    "give a calibrated confidence in [0,1], a one-sentence reason grounded in "
    "what you actually see, and set action to 'notify' when match is true "
    "(otherwise 'none'). CITE YOUR WORK: in 'evidence', list the specific frames "
    "that justify your verdict — reference each by its integer index from the "
    "'Frame N' labels — with a short observation per frame (supports=true for a "
    "frame showing the condition, false for one showing it is absent). Do not "
    "cite a frame you did not actually use."
)


@dataclass
class Recipe:
    text: str = DEFAULT_RECIPE_TEXT
    # allowed_actions clamps the decision schema's action enum — the recipe
    # bounds what the LLM may request.
    allowed_actions: list[str] = field(default_factory=lambda: ["notify"])


def build_prompt(episode: Episode, recipe: Recipe) -> list[Content]:
    """Build the recipe-conditioned user prompt: frames in order + the question."""
    parts: list[Content] = []
    n = len(episode.frames)
    parts.append(
        f"This is one episode ({n} frame{'s' if n != 1 else ''}, chronological order)."
    )
    for i, fr in enumerate(episode.frames):
        parts.append(f"Frame {i} (t+{fr.t_offset_ms}ms):")
        parts.append(ImagePart(data=fr.jpeg_bytes(), media_type="image/jpeg"))
    parts.append(f"Question about this episode: {recipe.text}")
    return parts
