"""The v0 loop (PART 9.6): episode -> recipe-conditioned verdict -> dispatch.

This is the whole spine. ``process_episode`` is the single-episode unit that the
CLI calls for a manually selected, saved episode; it also composes into a live
loop trivially (iterate an EpisodeSource and call it per episode).
"""

from __future__ import annotations

from .dispatch import Dispatcher
from .llm.base import LLMClient
from .recipe import Recipe, build_prompt
from .schema import Decision, Episode, decision_schema


def process_episode(
    episode: Episode,
    llm: LLMClient,
    recipe: Recipe,
    dispatcher: Dispatcher,
) -> Decision:
    """Run one episode through the model and dispatch the deterministic result.

    1. build the recipe-conditioned prompt (raw frames + the recipe's question);
    2. ask the LLM for a schema-guaranteed decision dict;
    3. parse into a ``Decision``;
    4. hand it to the Dispatcher (decide != act).
    """
    schema = decision_schema(recipe.allowed_actions)
    content = build_prompt(episode, recipe)
    raw = llm.generate(content, schema=schema, schema_name="decision")
    decision = Decision.from_dict(raw)
    dispatcher.handle(decision, episode)
    return decision
