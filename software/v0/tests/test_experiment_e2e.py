"""End-to-end smoke test for the experiment platform.

Exercises the whole graph the design doc calls for — *upload video -> derive
episodes -> label -> experiment -> run -> results matrix* — against a synthetic
video so it needs no real footage and no network. The build-order discipline from
the doc ("smallest independently-testable slice first: derive one episode set from
one video -> run one arm -> log one Run -> see one cell of the matrix") is exactly
what these tests walk.

The PAID Claude path is covered WITHOUT spending: ``test_claude_request_is_well_formed``
monkeypatches the HTTP transport and asserts the request the ``ClaudeClient`` would
send is correct (model, forced tool-call schema, image blocks), and the full run
uses the no-cost ``FakeLLMClient``. No costing call is ever sent to the API.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Make the package importable when run directly (pytest rootdir is software/v0).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openmartian.experiment import (  # noqa: E402
    Dataset,
    Experiment,
    ExperimentStore,
    FakeLLMClient,
    Label,
    ModelPin,
    Orchestrator,
    SamplingSpec,
)

FFMPEG = "/usr/bin/ffmpeg"


def _make_test_video(path: Path, seconds: int = 6) -> None:
    """Synthesize a short test video (ffmpeg testsrc) so the suite needs no footage."""
    cmd = [
        FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=10",
        "-pix_fmt", "yuv420p", str(path), "-y",
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)


@pytest.fixture()
def store(tmp_path) -> ExperimentStore:
    return ExperimentStore(data_root=tmp_path / "data")


@pytest.fixture()
def video(store: ExperimentStore) -> str:
    vpath = store.videos_dir / "testclip.mp4"
    _make_test_video(vpath)
    return "testclip.mp4"


# --------------------------------------------------------------------------- #
# 1. The headless end-to-end slice (no cost, no network).                     #
# --------------------------------------------------------------------------- #


def test_full_graph_no_cost(store, video):
    orch = Orchestrator(store=store)

    # upload (the video is already in the store) -> Dataset
    ds = Dataset.new(name="smoke", video_paths=[video], notes="synthetic testsrc clip")
    # label: condition holds in the first 3s of the clip (presence shape)
    ds.labels.append(Label.new(video=video, t_start_ms=0, t_end_ms=3000,
                               value=True, shape="presence", label="pattern visible"))
    store.save_dataset(ds)

    # derive: fps=1, 2 frames/episode (2s window), non-overlapping -> 3 episodes
    spec = SamplingSpec(fps=1.0, frames_per_episode=2, frame_res="160x120")
    der = orch.derive(ds, spec)
    assert len(der.episode_ids) == 3, der.episode_ids

    # each episode dir was written by the SAME save_episode the CLI uses
    for eid in der.episode_ids:
        ep_dir = Path(der.episode_dir) / eid
        assert (ep_dir / "meta.json").exists()
        meta = json.loads((ep_dir / "meta.json").read_text())
        assert meta["source_video"] == video
        assert "source_t_start_ms" in meta and "source_window_ms" in meta
        assert len(meta["frames"]) == 2

    # experiment on the implemented "direct" arm, two recipes
    exp = Experiment.new(
        derivation_id=der.id,
        arm="direct",
        objective_model=ModelPin(provider="ollama", model_id="gemma3:4b"),
        recipes=["Is the test pattern visible?", "Are color bars present?"],
        name="smoke-exp",
    )
    store.save_experiment(exp)

    # run with the no-cost fake client (answers match=True deterministically)
    run = orch.run_experiment(exp, no_cost=True)
    assert run.status == "done"
    assert len(run.results) == 6  # 3 episodes x 2 recipes

    # pin + env auto-captured (PART F)
    assert run.pin.provider == "ollama"
    assert run.env.pipeline_git_commit  # we are in a git repo
    assert run.env.captured_at

    # ground truth attached by time-overlap: window [0,2000) overlaps label [0,3000)
    first_ep = der.episode_ids[0]
    first_trials = [t for t in run.results if t.episode_id == first_ep]
    assert all(t.ground_truth is True for t in first_trials)
    assert all(t.correct is True for t in first_trials)  # fake says match=True

    # the last episode window [4000,6000) does NOT overlap the label -> gt False
    last_ep = der.episode_ids[-1]
    last_trials = [t for t in run.results if t.episode_id == last_ep]
    assert all(t.ground_truth is False for t in last_trials)
    assert all(t.correct is False for t in last_trials)  # match=True vs gt False

    # cost axis is populated for the (ollama) local arm via the wall-clock proxy
    assert all(t.cost_usd >= 0 for t in run.results)

    # the matrix: one (presence, direct, spec) cell aggregating all 6 trials
    matrix = orch.matrix()
    assert matrix["shapes"] == ["presence"]
    assert matrix["arms"] == ["direct"]
    cell = matrix["cells"][0]
    assert cell["n"] == 6
    assert cell["scored"] == 6
    # 4 correct (first 2 episodes overlap) of 6 -> accuracy 4/6
    assert cell["correct"] == 4
    assert abs(cell["accuracy"] - 4 / 6) < 1e-9


def test_unimplemented_arm_is_an_honest_seam(store, video):
    orch = Orchestrator(store=store)
    ds = store.save_dataset(Dataset.new(name="d", video_paths=[video]))
    der = orch.derive(ds, SamplingSpec(fps=1.0, frames_per_episode=2, frame_res="160x120"))
    exp = store.save_experiment(Experiment.new(derivation_id=der.id, arm="caption_rag"))
    with pytest.raises(NotImplementedError):
        orch.run_experiment(exp, no_cost=True)


# --------------------------------------------------------------------------- #
# 2. The PAID Claude path — request construction validated, NEVER billed.     #
# --------------------------------------------------------------------------- #


def test_claude_request_is_well_formed(monkeypatch):
    """Assert the Claude arm builds a correct request without sending a paid call.

    We monkeypatch ``requests.post`` to capture the outgoing body and return a
    canned ``tool_use`` response, so the forced-tool-call structured-output path is
    exercised end to end with zero spend. This is the "include the paid model APIs,
    but don't send any costing tests out to them" requirement.
    """
    from openmartian.llm import build_llm
    from openmartian.llm.base import ImagePart
    from openmartian.recipe import SYSTEM_PROMPT
    from openmartian.schema import decision_schema

    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "content": [{"type": "tool_use", "name": "decision",
                             "input": {"match": True, "confidence": 0.9,
                                       "action": "notify", "evidence": []}}],
                "usage": {"input_tokens": 1234, "output_tokens": 56},
            }

        @property
        def text(self):
            return ""

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        return _Resp()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKE-not-a-real-key")
    monkeypatch.setattr("openmartian.llm.claude_client.requests.post", _fake_post)

    llm = build_llm("claude", model="claude-haiku-4-5-20251001", system=SYSTEM_PROMPT)
    content = ["Frame 0:", ImagePart(data=b"\xff\xd8\xff\xd9", media_type="image/jpeg"),
               "Question about this episode: Is there a man standing?"]
    out = llm.generate(content, schema=decision_schema(["notify"]), schema_name="decision")

    # the structured-output contract held
    assert out["match"] is True
    assert llm.last_usage["input_tokens"] == 1234

    # the request was aimed at the real endpoint with the right shape...
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    body = captured["body"]
    assert body["model"] == "claude-haiku-4-5-20251001"
    # forced tool call pins the schema as structured output
    assert body["tool_choice"] == {"type": "tool", "name": "decision"}
    assert body["tools"][0]["input_schema"]["properties"]["match"]["type"] == "boolean"
    # the image rode through as a base64 image block (vision wired)
    blocks = body["messages"][0]["content"]
    assert any(b.get("type") == "image" for b in blocks)
    assert body["system"] == SYSTEM_PROMPT
    # ...and the api key header was set from the env (never a real key in tests)
    assert captured["headers"]["x-api-key"] == "sk-ant-FAKE-not-a-real-key"


def test_claude_cost_model_and_batch_discount():
    """The matrix cost axis: real Anthropic prices, Batch API halves it (PART E.2)."""
    from openmartian.experiment.tracking import claude_cost_usd

    # Haiku 4.5: $1/Mtok in, $5/Mtok out
    full = claude_cost_usd("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    assert abs(full - (1.0 + 5.0)) < 1e-9
    batched = claude_cost_usd("claude-haiku-4-5-20251001", 1_000_000, 1_000_000, batch=True)
    assert abs(batched - full / 2) < 1e-9


# --------------------------------------------------------------------------- #
# 3. Pinning auto-capture (PART F) — never raises, captures what's present.    #
# --------------------------------------------------------------------------- #


def test_pin_capture_is_best_effort():
    from openmartian.experiment.pin import capture_model_pin, capture_run_environment

    # claude pin: no Ollama scrape, sampling params carried
    pin = capture_model_pin("claude", "claude-haiku-4-5-20251001", temperature=0.0, max_tokens=512)
    assert pin.provider == "claude"
    assert pin.max_tokens == 512

    env = capture_run_environment("claude")
    assert env.captured_at
    assert env.os  # platform always known
    # endpoint defaults to the API root for the claude path
    assert env.endpoint == "https://api.anthropic.com"


if __name__ == "__main__":  # allow `python tests/test_experiment_e2e.py`
    raise SystemExit(pytest.main([__file__, "-v"]))
