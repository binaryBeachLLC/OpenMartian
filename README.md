# OpenMartian

<p align="center">
  <img src="openmartian.png" alt="OpenMartian" width="320">
</p>

Open, mobile, agent-neutral multi-sensor perception.

- [`docs/`](docs/) — firmware & software design doc (the living spec).
- [`hardware/`](hardware/) — BOM, build phases, roadmap.
- [`software/`](software/) — **v0 host pipeline**: webcam (RTSP) → episodes on disk → recipe-conditioned VLM (Ollama / Claude) → deterministic dispatch. See [`software/README.md`](software/README.md).
