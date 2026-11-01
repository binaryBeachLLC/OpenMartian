# Open Martian — Experiment App & Compute Design

*Companion to the Firmware & Software Design doc (esp. Parts 10–11: memory design-space + benchmark harness). Started June 25, 2026. This doc owns the tooling that turns Part 10 from opinion into data: dataset management, labeling, episode derivation, experiment configuration, results, and the compute that runs it all — reproducibly.*

## Scope & the one-line thesis

A purpose-built full-stack app to **manage datasets, label them, derive episodes (fps / fpe sweeps), define & launch experiments, and view the shape × arm × spec results matrix** — built as a *thin bespoke shell around adopted, self-hosted tools and the shared pipeline code*. The bespoke part is the orchestration + data model that speak our abstractions (`Episode`, `SamplingSpec`, arm, question-shape); everything deceptively-huge-but-solved (annotation UI, experiment tracking, vector store, video decode) is adopted, self-hosted, and wired by API.

---

## PART A — BUILD VS ADOPT (the split that keeps this a weekend, not a tar pit)

**Build (truly bespoke — nothing models *our* experiment):**
- The **orchestration layer + data model** (Dataset → Derivation → Experiment → Run → Results).
- The **results view** for the question-shape × arm × spec matrix (the real deliverable of Part 11).
- Episode **derivation** config UX (ffmpeg under the hood).
- Light **glue/config** that wires the adopted tools to one shared backend.

**Adopt (self-hosted, free, offline-friendly — do NOT build):**
- **Annotation UI → Label Studio** (or CVAT). Frame-accurate scrubbing, interval/temporal labels, custom schema export + API. Weeks of polish already solved. Highest-leverage "don't build."
- **Experiment tracking + results store → MLflow** (self-hosted; fits "laptop overnight"). Log params (arm, fps, fpe, model pin, hardware), metrics (accuracy *by shape*, cost, latency), artifacts. Don't hand-roll the run DB.
- **Vector store (RAG arm) → Chroma / LanceDB / Qdrant.**
- **Video decode / episode derivation → ffmpeg.**
- **Job queue (when async lands) → RQ / Dramatiq / Celery**, or Modal (Part E).

**Stack:** FastAPI (Python) backend + React frontend. Backend is Python **on purpose** — see Part B.

---

## PART B — THE LOAD-BEARING PRINCIPLE: the app invokes the pipeline, never reimplements it

The app, the benchmark harness (firmware-doc Part 11), and v0 all import the **same** modules: `VideoEpisodeSource`, `WebcamEpisodeSource`, `MemoryIndex`, `ObjectiveAnalyst`, `LLMClient`, the judge. The app is **CRUD + job orchestration + visualization *around* those modules** — it never forks pipeline logic.

Why this is non-negotiable: if the app reimplements any pipeline step, the benchmark stops measuring the thing we'll ship, which destroys the benchmark's entire purpose. This is also why the backend is Python: **the orchestrator and the production pipeline are literally the same imports.** One seam (`Episode`, `MemoryIndex`, `LLMClient`) runs in webcam-live, video-offline, and app-orchestrated contexts unchanged.

---

## PART C — DATA MODEL

```
Dataset      raw video file(s) + labels (Label Studio export → our time-keyed schema §11.3)
   │
Derivation   (Dataset × SamplingSpec) → a concrete episode set on disk
   │            fps & fpe are sweep params over FIXED footage (record-high-sample-down)
Experiment   (Derivation × arm × models × judge cadence) → the thing to run
   │
Run          one execution of an Experiment in a pinned environment
   │            carries ModelPin + RunEnvironment metadata (Part F) — the apples-to-apples record
Results      per-shape accuracy + cost + latency, logged to MLflow, rendered as the matrix
```

Each arrow is a **Job** (Part G). Labels attach to the **Dataset** (keyed to source-video time), so every Derivation under it inherits labels by time-overlap — label once, sweep many.

---

## PART D — ADOPTED TOOLS, SELF-HOSTED, WIRED BY API (decided)

**Decision: separate tools, each self-hosted with its own shipped frontend, wired to one shared backend by API — light glue/config, not embedding.** Less to build; slightly more context-switching, accepted.

- **Label Studio** runs as its own service; the app links out / calls its API. Annotators use Label Studio's UI; the app **pulls completed annotations via API** and post-processes them into our `Label` schema (§11.3). Where our shapes don't map cleanly (count-with-crossing-timestamps; anticipate = precursor interval + terminal instant), the post-processor builds them from raw Label Studio regions.
- **MLflow** runs as its own service (tracking server + backing store). The app **writes runs/metrics via the MLflow API** and **reads them back** to render the bespoke shape × arm matrix. MLflow's own UI stays available for deep dives; our matrix view is the one thing we build on top.
- **Shared backend:** the FastAPI app holds the canonical Dataset/Derivation/Experiment records (Postgres or SQLite to start) and **references** Label Studio project IDs and MLflow run IDs. The adopted tools own their domain; our DB owns the experiment graph and the cross-references.

The glue is config + API calls, not iframes. If a tool ever lacks a usable frontend, *then* tie its API into our app's UI — but Label Studio and MLflow both ship good ones, so we don't.

---

## PART E — COMPUTE & THE 1-FOR-1 CONSTRAINT

### E.1 — The two model paths have *different* fidelity stories

The whole compute problem splits cleanly here:

- **Cloud API models (Claude/Haiku) are already remote** → identical by construction (Part E.2).
- **Local models (Ollama) → 1-for-1 is about *pinning*, not *location*** (Part E.3).

### E.2 — API path: identical by construction; use Batch

A Claude call from the laptop and from a cloud VM hit the **same endpoint, same model** — no fidelity question. So for API arms, *where the orchestrator runs is irrelevant to the result* (only throughput / tying up the laptop differ). Pin the **model ID + API date + any beta headers + sampling params**. For sweeps, default to the **Anthropic Batch API** — 50% cheaper, completes <24h — which fits "queue it overnight" perfectly.

### E.3 — Local path: the 1-for-1 guarantee = pin four things + containerize

The trap: "I'll run it on a cloud GPU, same as local." No — Ollama serves **quantized GGUFs**; the same model at Q4_K_M vs Q8 vs fp16 gives **different outputs**, and llama.cpp version drift + cross-hardware float differences add more. The guarantee comes from pinning:

1. **Exact model tag** (e.g. `qwen2.5-vl:7b`) **+ digest/sha** of the blob.
2. **Exact quantization** (Q4_K_M, …).
3. **Ollama + llama.cpp/backend version.**
4. **Sampling params** — `temperature=0`, fixed `seed`, `top_p`, `num_predict`.

**Containerize Ollama + the pinned model into a Docker image; record the image digest (sha256).** "Production runs the same pinned container" is the 1-for-1 promise. *(Decided: everything we run is pinned in Docker.)*

### E.4 — Decouple inference from orchestrator (decided)

Ollama exposes an HTTP endpoint; `OllamaClient` already talks to a **URL via env var (never hardcode `localhost`)**. So the same pinned container runs:
- local overnight → `http://localhost:11434`
- burst → spin the container up on a GPU host → point at its URL

Same artifact, same outputs (modulo tiny float noise). Inference location becomes a config flag, not a rewrite.

### E.5 — Hardware as a held-constant, tracked per run (decided)

Strict bit-exactness across *different* GPUs isn't guaranteed even at temp 0. For accuracy benchmarks it rarely matters, but for airtight reproducibility: **treat the inference environment as a Part-11 held-constant** — run *all* local-model trials of a given study on **one** pinned machine/container — **and record the actual hardware per Run** so any cross-machine comparison is explicit, not hidden. *(Decided: note/track hardware for every test; orchestrator stores it — Part F.)*

### E.6 — Runner options

| Option | When it's right | Watch out for |
|---|---|---|
| **Laptop overnight + job queue** | **Default.** Zero fidelity risk (it *is* the target). Free. | Local-model GPU throughput; API arms barely tax it (just HTTP). |
| **Anthropic Batch API** | All Claude/Haiku sweeps | 50% off, <24h async — ideal overnight. Pin model IDs. |
| **Colab** | Bursty local-model runs you don't want tying up the laptop | Ephemeral (~12h, disconnects), no persistent storage (mount Drive), expose Ollama via tunnel (cloudflared/ngrok). Fiddly. Pin quant to match local. |
| **Modal / RunPod / Lambda** | Grown-up Colab — persistent, scriptable batch GPU | **Modal:** define the pinned Ollama container, per-second billing, spin-up→run→teardown. Best remote 1-for-1 story. |

**Recommendation:** default laptop-overnight + real job queue; route every Claude arm through Batch; containerize Ollama from day one so Modal/RunPod is a config flag, not a rewrite.

---

## PART F — THE PINNING METADATA SCHEMA (the apples-to-apples record)

Every **Run** captures a full `ModelPin` + `RunEnvironment`. **Auto-capture, don't hand-enter:** Ollama's HTTP API exposes model metadata (modelfile, quantization, parameter size, digest) and a version endpoint; Docker exposes the image digest; the host exposes GPU/CPU/OS. The orchestrator scrapes all of it at Run start. *(Confirm exact Ollama API field names against current Ollama docs at build time.)*

```python
@dataclass
class ModelPin:
    provider: str                  # "claude" | "ollama"
    model_id: str                  # "claude-haiku-4-5-20251001" | "qwen2.5-vl:7b"
    # --- ollama-specific (auto-pulled from Ollama API) ---
    model_digest: str | None = None        # sha256 of the model blob
    quantization: str | None = None        # "Q4_K_M", "Q8_0", "fp16", ...
    parameter_size: str | None = None      # "7B", ...
    modelfile_params: dict | None = None   # template + parameter overrides
    # --- api-specific ---
    api_date: str | None = None            # model version/date pin
    beta_headers: list[str] | None = None
    # --- sampling (BOTH; part of reproducibility) ---
    temperature: float = 0.0
    top_p: float | None = None
    seed: int | None = None
    max_tokens: int = 1024

@dataclass
class RunEnvironment:
    # --- software ---
    ollama_version: str | None = None
    backend_version: str | None = None     # llama.cpp / engine build
    docker_image_digest: str | None = None # sha256 — pins the exact container
    pipeline_git_commit: str = ""          # exact app/pipeline code version
    # --- hardware (auto-detected per Run) ---
    gpu_model: str | None = None           # "RTX 4090", "Apple M3 Max", ...
    gpu_vram_gb: float | None = None
    cpu: str | None = None
    ram_gb: float | None = None
    os: str | None = None
    cuda_driver: str | None = None
    # --- api routing ---
    endpoint: str | None = None            # localhost / container URL / api.anthropic.com
    inference_geo: str | None = None       # if US-only routing used (affects cost)
    batch: bool = False                    # Anthropic Batch API used?
    captured_at: datetime | None = None
```

Both ride on every Run and log to MLflow as params, so the results matrix can **filter/group by exact model+quant+hardware** — that's what makes it truly apples-to-apples, and what later feeds the firmware-doc §8.5 cost/perf estimator with real numbers.

---

## PART G — THE JOB / RUNNER SEAM (start sync, async drops in)

**Decided: start synchronous ("run and wait"), but design the `Job` / `Runner` seam from the start** so async is a later drop-in with no rework — same pattern as `EpisodeSource` / `LLMClient`.

```python
@dataclass
class Job:
    job_id: str
    kind: str                      # "derive" | "label_sync" | "run_experiment"
    params: dict                   # e.g. {experiment_id, derivation_id, arm, ...}
    status: str = "pending"        # pending | running | done | failed
    pin: ModelPin | None = None
    env: RunEnvironment | None = None

class Runner(ABC):
    """Executes a Job. Location/async-ness is the only thing that varies."""
    @abstractmethod
    def submit(self, job: Job) -> str: ...      # returns job_id
    @abstractmethod
    def status(self, job_id: str) -> str: ...
    @abstractmethod
    def result(self, job_id: str) -> dict: ...

class LocalRunner(Runner):
    """v1: synchronous in-process (or a local subprocess). 'Run and wait.'"""
    def submit(self, job): raise NotImplementedError
    def status(self, job_id): raise NotImplementedError
    def result(self, job_id): raise NotImplementedError

class QueueRunner(Runner):       # FUTURE — RQ/Dramatiq/Celery; async, same interface
    def submit(self, job): raise NotImplementedError
    ...

class ModalRunner(Runner):       # FUTURE — remote pinned-container GPU; same interface
    def submit(self, job): raise NotImplementedError
    ...
```

The orchestrator calls `Runner` and never knows whether the job ran in-process, on a queue, or on a remote GPU — exactly like the inference-vs-orchestrator decoupling, applied to job execution.

---

## PART H — APP DATA-MODEL STUBS

```python
@dataclass
class Dataset:
    id: str
    name: str
    video_paths: list[str]
    label_studio_project_id: str | None = None   # cross-ref to the adopted labeler
    labels: list["Label"] = field(default_factory=list)  # synced from Label Studio

@dataclass
class Derivation:
    id: str
    dataset_id: str
    spec: "SamplingSpec"           # fps / fpe / res — the sweep point
    episode_dir: str               # derived episodes on disk (ffmpeg output)

@dataclass
class Experiment:
    id: str
    derivation_id: str
    arm: str                       # "pyramid_agnostic" | "pyramid_specific" | "caption_rag" | "vision_rag" | ...
    objective_model: ModelPin      # held-constant across arms in a study
    judge_model: ModelPin
    judge_cadence_s: float
    recipes: list[str]

@dataclass
class Run:
    id: str
    experiment_id: str
    pin: ModelPin                  # the model actually exercised
    env: RunEnvironment            # where/how — the apples-to-apples record
    mlflow_run_id: str | None = None
    results: list["TrialResult"] = field(default_factory=list)
```

---

## OPEN QUESTIONS / NEXT

1. **Backing DB** — SQLite (simplest, single-user, fits laptop) vs Postgres (room to grow). Lean SQLite for v1.
2. **Label Studio schema mapping** — exact post-processor from Label Studio regions → our `count` / `anticipate` shapes. Build alongside the first labeled video.
3. **MLflow granularity** — one MLflow run per (arm × spec) trial, or per Experiment with nested runs? Affects how the matrix queries group.
4. **Ollama API fields** — confirm exact field names for digest/quant/params/version against current Ollama docs at build time (Part F auto-capture).
5. **Cost capture for local models** — API arms have $ per call; local arms cost *time/energy*. Decide a comparable "cost" axis (wall-clock × hardware tier?) so the matrix compares fairly.
6. **Determinism audit** — once pinned, run the *same* trial twice on the same container and confirm identical outputs before trusting any cross-arm delta.

---

*Next session: design the harness/runner concretely — pick the queue lib (or Modal), stand up Label Studio + MLflow locally, define the ffmpeg derivation command set for the fps/fpe sweep, and write the Part-F auto-capture probes. Build order mirrors the firmware doc's §9.8 discipline: smallest independently-testable slice first (derive one episode set from one video → run one arm → log one Run to MLflow → see one cell of the matrix), then widen.*
