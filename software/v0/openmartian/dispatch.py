"""Deterministic decide-vs-act layer (PART 9.1 / 9.4 of the design doc).

Core principle: **decide != act**. The LLM is a pure decision function — it
inspects an :class:`~openmartian.schema.Episode` and returns a structured
:class:`~openmartian.schema.Decision`. It never touches a sink (Discord,
desktop notification, ...) directly. All side effects flow through the
deterministic :class:`Dispatcher`, which applies a fixed gate (match ->
action -> confidence -> registered sink) before anything is sent.

This separation keeps the dangerous part (sending pings to the outside world)
auditable and out of the model's hands. ``dry_run=True`` is the safe default:
during prototyping the dispatcher logs what it *would* send without firing any
real side effect, so a chatty or miscalibrated model can't spam you.
"""

from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod

import requests

from .schema import Decision, Episode

# --------------------------------------------------------------------------- #
# Sinks — the only things allowed to produce an outward side effect.           #
# --------------------------------------------------------------------------- #


class Sink(ABC):
    """An outbound side-effect target. Subclasses own exactly one way to ``send``."""

    name: str

    @abstractmethod
    def send(self, message: str) -> None:
        """Deliver ``message`` to this sink. Raise on hard delivery failure."""
        ...


class ConsoleSink(Sink):
    """Prints to stdout; optionally also fires a desktop ``notify-send`` toast."""

    name = "console"

    def __init__(self, desktop_notify: bool = False) -> None:
        self.desktop_notify = desktop_notify

    def send(self, message: str) -> None:
        print(f"[notify] {message}")
        if self.desktop_notify and shutil.which("notify-send"):
            # Best-effort: a missing/broken notifier must never break dispatch.
            try:
                subprocess.run(
                    ["notify-send", "OpenMartian", message],
                    check=False,
                    capture_output=True,
                )
            except Exception:
                pass


class DiscordSink(Sink):
    """Stub-quality sink: POSTs to a Discord webhook URL — all it needs is the URL.

    No bot token, no auth dance; paste a channel webhook and it works.
    """

    name = "discord"

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send(self, message: str) -> None:
        resp = requests.post(
            self.webhook_url,
            json={"content": message},
            timeout=10,
        )
        if not (200 <= resp.status_code < 300):
            body = (resp.text or "")[:200]
            raise RuntimeError(
                f"Discord webhook returned {resp.status_code}: {body}"
            )


# --------------------------------------------------------------------------- #
# Dispatcher — the deterministic gate between a Decision and a real side effect.#
# --------------------------------------------------------------------------- #


class Dispatcher:
    """Executes the side effect a :class:`Decision` requests, behind a fixed gate.

    The LLM only *proposes* (via ``decision.action``); this class deterministically
    decides whether to *act*. ``sinks`` maps an action name (e.g. ``"notify"``) to
    the :class:`Sink` that realizes it. ``dry_run=True`` (default) logs intent
    without sending — flip it off only once you trust the recipe.
    """

    def __init__(
        self,
        sinks: dict[str, Sink],
        min_confidence: float = 0.6,
        dry_run: bool = True,
    ) -> None:
        self.sinks = sinks
        self.min_confidence = min_confidence
        self.dry_run = dry_run

    def _log(self, message: str) -> None:
        """Single chokepoint for dispatcher output (swap for real logging later)."""
        print(message)

    def handle(self, decision: Decision, episode: Episode) -> bool:
        """Run the gate and dispatch if it passes. Returns True iff something fired."""
        # a. No match -> nothing to do.
        if not decision.match:
            self._log(
                f"[skip] no match for episode {episode.episode_id} "
                f"(conf={decision.confidence:.2f})"
            )
            return False

        # b. Matched, but the model explicitly requested no action.
        if decision.action == "none":
            self._log(
                f"[skip] match but action=none for episode {episode.episode_id}"
            )
            return False

        # c. Matched, but not confident enough to act on.
        if decision.confidence < self.min_confidence:
            self._log(
                f"[skip] match but confidence {decision.confidence:.2f} "
                f"< {self.min_confidence:.2f}"
            )
            return False

        # d. No sink wired up for the requested action.
        sink = self.sinks.get(decision.action)
        if sink is None:
            self._log(
                f"[skip] no sink registered for action '{decision.action}'"
            )
            return False

        # e. Pick the message: model's text, else a synthesized fallback.
        msg = (
            decision.message
            or f"Recipe matched on episode {episode.episode_id}: {decision.reason}"
        )

        # f. Safe default: log intent, send nothing.
        if self.dry_run:
            self._log(
                f"[dry-run] would dispatch via '{decision.action}': {msg}"
            )
            return True

        # g. Live: actually fire the side effect.
        sink.send(msg)
        self._log(
            f"[dispatched] action='{decision.action}' via {sink.name}"
        )
        return True
