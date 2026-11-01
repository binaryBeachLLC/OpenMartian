# OpenMartian

<p align="center">
  <img src="openmartian.png" alt="OpenMartian" width="320">
</p>

Open, mobile, agent-neutral multi-sensor perception.

- [`docs/`](docs/) — firmware & software design + the experiment app/compute design (the living spec).
- [`hardware/`](hardware/) — BOM, build phases, roadmap.
- [`software/`](software/) — **v0 host pipeline**: webcam (RTSP) → episodes on disk → recipe-conditioned VLM (Ollama / Claude) → deterministic dispatch. See [`software/README.md`](software/README.md).
- **Experiment platform** (built on that pipeline, not a fork of it): upload **raw video** → label by time → derive episode sets → run experiments → read the shape×arm×spec results matrix. `make up`, then open `/experiment`. See [`software/v0/README.md`](software/v0/README.md#experiment-console-the-platform).
