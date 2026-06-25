# OpenMartian — software

Umbrella for all OpenMartian software. Each stage / component is a self-contained
subdirectory so they can evolve independently.

| Dir | What |
|---|---|
| [`v0/`](v0/) | **The v0 host pipeline + dev console** — webcam (RTSP) → episodes on disk → recipe-conditioned VLM (Ollama / Claude) → deterministic dispatch, plus a FastAPI + React dev console. See [`v0/README.md`](v0/README.md). |
| _(future)_ | firmware (ESP32 sensor heads), later host stages (Part 7 cascade, Part 8 attention), etc. |

Quick start:

```bash
make up        # from repo root → forwards to software/v0 (serve console, VPN-accessible)
make help      # list targets
```
