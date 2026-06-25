# OpenMartian — v0 host pipeline

The first slice of the software described in
[`../../docs/Open_Martian_Firmware_Software_Design.md`](../../docs/Open_Martian_Firmware_Software_Design.md)
(PART 8.9 + PART 9). Lives under `software/v0/` — `software/` is the umbrella for
all OpenMartian software (firmware and later stages live alongside `v0/`).

```
webcam (RTSP) ──capture──▶ episodes on disk ──process──▶ recipe-conditioned VLM ──▶ deterministic dispatch
```

**What v0 proves:** does *recipe-conditioned querying* actually detect what the
recipe asked for ("Is there a man standing in the frame?") and not what it
didn't — across local (Ollama) and cloud (Claude) models, on the same frames.

Capture and processing are **decoupled on purpose**: you record episodes to
local disk now and manually pick which one to feed a model later, so you can
troubleshoot deterministically. Episodes are never loaded continuously.

## The two non-negotiable seams (kept even in v0)

1. **The `Episode` object** is what the model consumes (v0's stand-in for the
   normalized `Event` in PART 5). Swap the *source* later (MQTT eyes), not the pipeline.
2. **The model call is recipe-conditioned** — we ask the recipe's own question
   against the raw frames, never describe-then-string-match. This is what
   preserves the "red shirt" (PART 4).

## Layout

```
software/                 umbrella for ALL OpenMartian software (firmware, ... later)
└─ v0/
   ├─ openmartian/        core pipeline package (pure Python, requests + ffmpeg)
   ├─ webapp/             dev console: FastAPI backend + no-build React UI
   ├─ Makefile            one entry point (make up / serve / capture / ...)
   ├─ episodes/           recorded episodes (git-ignored)
   └─ data/               recipes, run history, app config (git-ignored)
```

**`openmartian/` — the core pipeline**

| File | Role |
|---|---|
| `schema.py` | `Frame` / `Episode` / `EpisodeConfig`, on-disk save/load, `Decision` + decision schema |
| `capture.py` | ffmpeg RTSP → `Episode`; `WebcamEpisodeSource`, `FolderEpisodeSource` |
| `llm/base.py` | provider-agnostic `LLMClient` (text+images in, schema-valid dict out; `last_usage` tokens) |
| `llm/claude_client.py` | Claude/Haiku via Messages API (forced tool-call structured output) |
| `llm/ollama_client.py` | local VLM via Ollama `/api/chat` (`format`=JSON-schema) |
| `recipe.py` | `Recipe` + `build_prompt` (recipe-conditioning lives here) |
| `dispatch.py` | `Sink`s + `Dispatcher` (decide ≠ act; dry-run by default) |
| `pipeline.py` / `cli.py` | `process_episode` loop; `capture` / `list` / `process` CLI |

**`webapp/` — the dev console (modular)**

| File | Role |
|---|---|
| `app.py` | FastAPI app factory: loads `.env`, mounts routers, serves the UI |
| `routers/{webcam,config,episodes,recipes,models,process}.py` | one `APIRouter` per domain |
| `camera.py` | ffmpeg helpers: reachability probe, snapshot, live MJPEG stream |
| `services.py` | model discovery + background **processing jobs** (tokens, progress) |
| `state.py` | JSON persistence + in-memory webcam/recording/job state |
| `static/index.html` | single-file React app (CDN, no npm build) |

## Quick start (Makefile)

System deps (already present here): **ffmpeg**, **ollama**. Everything else runs
through the Makefile — from the repo root or `software/v0/`:

```bash
make install      # pip install -r requirements.txt (fastapi, uvicorn, requests, dotenv)
make up           # ensure ollama is running, then serve the console (VPN-accessible)
make help         # list all targets + print the local + VPN URLs
make check        # verify ffmpeg / ollama / Claude key
```

`make up` (or `make serve`) binds `0.0.0.0:8000`, so the UI + API are reachable
**over the NetBird VPN** at `http://<this-host-netbird-ip>:8000/` (e.g.
`http://100.68.169.237:8000/`). `make url` prints the exact addresses.

Secrets live in `software/v0/.env` (git-ignored): `ANTHROPIC_API_KEY`,
`OM_RTSP_URL`, `OLLAMA_HOST`. The RTSP source defaults to
`rtsp://100.68.17.41:8554/webcam` (your `webcam` alias / NetBird VPN).

## CLI usage

The CLI is the headless path (the console wraps the same package). Run from
`software/v0/` (or `make capture ARGS="..."` / `make process ARGS="..."`):

```bash
# 1) Record episodes from the webcam to ./episodes/ (5 frames @ 1fps, 320x240)
python -m openmartian capture --count 3
python -m openmartian capture --fps 2 --frames 6 --res 640x480   # richer/pricier

# 2) See what you captured
python -m openmartian list

# 3) Feed ONE episode to a model (dry-run by default — no real ping)
python -m openmartian process --latest                                   # gemma3:4b
python -m openmartian process --latest --model llama3.2-vision:11b       # bigger local VLM
python -m openmartian process --episode episodes/<id> --backend claude   # Haiku
python -m openmartian process --latest --recipe "Is a package on the doorstep?"

# 4) Actually dispatch (console notify, or Discord), once you trust it
python -m openmartian process --latest --live
python -m openmartian process --latest --live --discord-webhook https://discord.com/api/webhooks/...
```

## Models

| Backend | Default | Vision | Notes |
|---|---|---|---|
| `ollama` | `gemma3:4b` | ✅ | also `llava`, `llama3.2-vision:11b` (pulled) via `--model` |
| `claude` | `claude-haiku-4-5-20251001` | ✅ | needs `ANTHROPIC_API_KEY`; called over raw HTTP, no SDK |

Caveats:
- **`gemma4` is text-only** — it will silently ignore the frames. Use a
  multimodal model (`gemma3:4b` / `llava` / `llama3.2-vision:11b`) for this recipe.
- **DeepSeek is deferred.** Its hosted API (`api.deepseek.com`) is text-only —
  it cannot see frames, so it can't answer a pixel recipe. DeepSeek-VL2 exists
  only as open weights you'd self-host. The `build_llm` factory has the seam to
  add it (API for text recipes, or a self-hosted VL endpoint) when wanted.

## Cost lever (Claude, vision)

Image tokens ≈ (W × H) / 750 per frame. Resolution is quadratic — `320×240` ≈
~$0.001/episode on Haiku vs `640×480` ≈ ~$0.003. Keep `--res` small; raise it
only when the recipe needs the detail. (Estimates — measure real usage.)

## Web dev console (`webapp/`)

A trusted-network **development UI + API** over the whole pipeline — FastAPI
backend (modular routers) + a no-build React single-page app (React via CDN, no
npm/node toolchain). Start it with `make up`.

- **UI** (`http://<host>:8000/`): webcam control + **live MJPEG feed** with a
  **RECORDING** indicator (lit while episodes are captured); capture/param
  editing; an episode browser with a frame viewer + metadata + batch-select; a
  recipe store; a model picker (vision/availability badges); an **editable
  decision schema**; a process runner with a **live progress bar + per-job token
  counts**; and a normalized-output runs table (tokens, latency, raw output).
- **Processing is async**: `POST /api/process` starts a background **job** and
  returns immediately; poll `GET /api/jobs/{job_id}` for live `completed/total`,
  `current_episode`, accumulated `tokens`, and per-episode results as they stream
  in. (Pass `"wait": true` for a single blocking call.)
- **API for agents:** `http://<host>:8000/docs` (Swagger) + `/openapi.json` —
  a Claude Code agent can drive the whole pipeline over REST
  (`POST /api/episodes/capture`, `POST /api/process`, `GET /api/jobs/{id}`,
  `GET /api/runs`, ...). CORS is wide open (dev only).

React, ReactDOM, and Babel are **vendored locally** under
`webapp/static/vendor/` and served from your own host — the UI needs **no public
internet**, so it works from an internet-restricted PC on the VPN. (Babel is
pinned to v7; v8 breaks in-browser `text/babel` JSX.)

Local state (recipes, run history, app config) is JSON under `v0/data/`;
episodes under `v0/episodes/`. Both are git-ignored.

> Security note: no auth, CORS `*`, `0.0.0.0` bind — intended only for your own
> trusted NetBird network, exactly as requested.

## Grows into

Same `Episode` seam and recipe-conditioned call expand into PART 7 (full
gate→interpret→match cascade) then PART 8 (peripheral/foveal attention) with no
rework — see the simplification ladder in §8.9.
