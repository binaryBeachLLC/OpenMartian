"""OpenMartian v0 command line.

Three decoupled commands, mirroring the build order in PART 9.8:

  capture   record episode(s) from the RTSP webcam and SAVE them to disk
  list      show saved episodes (so you can pick one to troubleshoot)
  process   feed ONE saved episode to a model -> recipe verdict -> dispatch

Capture and processing are intentionally separate: you record episodes now and
manually choose which one to interpret later, exactly as requested for v0.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Best-effort .env load (no hard dependency).
try:  # pragma: no cover - convenience only
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

from .capture import capture_episode, default_rtsp_url
from .dispatch import ConsoleSink, Dispatcher, DiscordSink
from .llm import build_llm
from .pipeline import process_episode
from .recipe import DEFAULT_RECIPE_TEXT, SYSTEM_PROMPT, Recipe
from .schema import EpisodeConfig, list_episodes, load_episode, save_episode

DEFAULT_EPISODES_DIR = "episodes"


# --------------------------------------------------------------------------- #
# capture                                                                     #
# --------------------------------------------------------------------------- #


def cmd_capture(args: argparse.Namespace) -> int:
    config = EpisodeConfig(
        fps=args.fps,
        frames_per_episode=args.frames,
        frame_res=args.res,
        jpeg_q=args.jpeg_q,
    )
    url = args.url or default_rtsp_url()
    out_dir = Path(args.dir)

    print(
        f"Capturing {args.count} episode(s) from {url}\n"
        f"  {config.frames_per_episode} frames @ {config.fps} fps "
        f"({config.window_s:.1f}s window), res={config.frame_res}, q={config.jpeg_q}"
    )

    saved = []
    for i in range(args.count):
        print(f"\n[{i + 1}/{args.count}] capturing ...", flush=True)
        try:
            episode = capture_episode(url, config, ffmpeg_timeout=args.timeout)
        except RuntimeError as e:
            print(f"  ! capture failed: {e}", file=sys.stderr)
            return 1
        ep_dir = save_episode(episode, out_dir)
        saved.append(ep_dir)
        print(f"  saved {len(episode.frames)} frames -> {ep_dir}")

    print(f"\nDone. {len(saved)} episode(s) under {out_dir}/")
    return 0


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


def cmd_list(args: argparse.Namespace) -> int:
    eps = list_episodes(args.dir)
    if not eps:
        print(f"No episodes under {args.dir}/ (run `capture` first).")
        return 0
    print(f"{len(eps)} episode(s) under {args.dir}/:\n")
    for p in eps:
        ep = load_episode(p)
        res = ep.config.frame_res if ep.config else "?"
        print(f"  {ep.episode_id}   {len(ep.frames):>2} frames  res={res}  {p}")
    return 0


# --------------------------------------------------------------------------- #
# process                                                                     #
# --------------------------------------------------------------------------- #


def _resolve_episode_path(args: argparse.Namespace) -> Path | None:
    if args.episode:
        p = Path(args.episode)
        return p if (p / "meta.json").exists() else None
    eps = list_episodes(args.dir)
    if not eps:
        return None
    return eps[-1]  # --latest default: newest episode id sorts last


def cmd_process(args: argparse.Namespace) -> int:
    ep_path = _resolve_episode_path(args)
    if ep_path is None:
        print(
            "No episode to process. Pass --episode <dir>, or run `capture` first "
            "and use --latest.",
            file=sys.stderr,
        )
        return 1

    episode = load_episode(ep_path)
    recipe = Recipe(text=args.recipe)

    llm = build_llm(
        args.backend,
        model=args.model,
        system=SYSTEM_PROMPT,
        temperature=args.temperature,
    )

    # Sinks are keyed by ACTION name. "notify" -> console (or discord if given).
    if args.discord_webhook:
        sinks = {"notify": DiscordSink(args.discord_webhook)}
    else:
        sinks = {"notify": ConsoleSink(desktop_notify=args.desktop_notify)}
    dispatcher = Dispatcher(
        sinks, min_confidence=args.min_confidence, dry_run=not args.live
    )

    print(
        f"Episode : {episode.episode_id} ({len(episode.frames)} frames)\n"
        f"Backend : {llm.provider} / {llm.model}\n"
        f"Recipe  : {recipe.text!r}\n"
        f"Mode    : {'LIVE' if args.live else 'dry-run'}  "
        f"(min_confidence={args.min_confidence})\n"
    )

    try:
        decision = process_episode(episode, llm, recipe, dispatcher)
    except RuntimeError as e:
        print(f"! model/dispatch error: {e}", file=sys.stderr)
        return 1

    print(
        f"\nVerdict : match={decision.match}  confidence={decision.confidence:.2f}  "
        f"action={decision.action!r}"
    )
    if decision.reason:
        print(f"Reason  : {decision.reason}")
    return 0


# --------------------------------------------------------------------------- #
# parser                                                                      #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="openmartian",
        description="OpenMartian v0: webcam episodes -> recipe-conditioned VLM -> dispatch.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # capture
    c = sub.add_parser("capture", help="record episode(s) from the RTSP webcam to disk")
    c.add_argument("--url", default=None, help="RTSP URL (default: $OM_RTSP_URL or the webcam alias)")
    c.add_argument("--dir", default=DEFAULT_EPISODES_DIR, help="output episodes dir")
    c.add_argument("--fps", type=float, default=1.0, help="frames captured per second")
    c.add_argument("--frames", type=int, default=5, help="frames per episode")
    c.add_argument("--res", default="320x240", help="frame resolution WxH (cost lever)")
    c.add_argument("--jpeg-q", type=int, default=5, dest="jpeg_q", help="ffmpeg -q:v (2 best .. 31 worst)")
    c.add_argument("--count", type=int, default=1, help="number of episodes to capture")
    c.add_argument("--timeout", type=float, default=30.0, help="ffmpeg timeout seconds")
    c.set_defaults(func=cmd_capture)

    # list
    l = sub.add_parser("list", help="list saved episodes")
    l.add_argument("--dir", default=DEFAULT_EPISODES_DIR, help="episodes dir")
    l.set_defaults(func=cmd_list)

    # process
    pr = sub.add_parser("process", help="run one saved episode through a model")
    pr.add_argument("--episode", default=None, help="path to an episode dir (omit + --latest for newest)")
    pr.add_argument("--latest", action="store_true", help="use the newest saved episode")
    pr.add_argument("--dir", default=DEFAULT_EPISODES_DIR, help="episodes dir (for --latest)")
    pr.add_argument("--backend", default="ollama", choices=["ollama", "claude"], help="LLM backend")
    pr.add_argument("--model", default=None, help="override model (e.g. llama3.2-vision:11b)")
    pr.add_argument("--recipe", default=DEFAULT_RECIPE_TEXT, help="the recipe question")
    pr.add_argument("--temperature", type=float, default=0.0)
    pr.add_argument("--min-confidence", type=float, default=0.6, dest="min_confidence")
    pr.add_argument("--live", action="store_true", help="actually dispatch (default: dry-run)")
    pr.add_argument("--desktop-notify", action="store_true", dest="desktop_notify", help="also fire notify-send")
    pr.add_argument("--discord-webhook", default=None, dest="discord_webhook", help="dispatch to a Discord webhook")
    pr.set_defaults(func=cmd_process)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
