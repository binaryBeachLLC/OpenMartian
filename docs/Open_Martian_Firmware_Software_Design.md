# Open Martian — Firmware & Software Design

*Living design doc. Started June 24, 2026. Companion to the Competitive & Technical Landscape Analysis.*

## Hardware baseline (this session's assumptions)

- **Two eyes**, each = 1 camera + 1 radar + 1 PIR + 1 mic, driven by **its own ESP32** (two MCUs per device).
- **All heavy processing runs on a host computer.** The ESP32s are **triggered sensor heads**, not AI cameras. Data moves eye → host over WiFi.
- Consequence that shapes everything below: every on-device limit (one camera interface, no video codec, no on-device VLM) stops mattering. The firmware's only jobs are **sense cheaply → decide whether to wake the expensive path → capture → ship bytes → sleep.** All intelligence lives on the host.

> **Two-clock caveat (decide early):** the two ESP32s have independent clocks. Anything that correlates the eyes ("both saw it," which-eye-first, rough disparity) needs a shared time reference — an **ESP-NOW link** between the MCUs or a **GPIO sync line**. This constrains capture and host-side fusion, so it's a Stage-0 decision.

---

## PART 1 — OPERATING MODES

A **mode** is a curated bundle of pipeline knobs (Part 3) shipped as a one-tap preset, with the individual knobs still exposed for advanced users. The firmware boots into a mode; the host honors it. Modes are the product's primary UX surface for "how do you want to use this device right now."

Design truth to keep visible: **Sentry is the mode that makes the mobility pillar real. Stream is the one that quietly contradicts it.** Both are legitimate — just be explicit that Stream is a mains/short-session mode, never the always-on-roaming default.

### Mode comparison

| Mode | One-liner | Sleep strategy | Trigger logic | Capture | Transport | Host behavior | Power target |
|------|-----------|----------------|---------------|---------|-----------|---------------|--------------|
| **Sentry** | Always-watching, battery-first | Deep sleep between events | Radar **AND** PIR (mic optional assist) | 1 keyframe + short audio clip | MQTT push | CV filter → VLM only on confirmed events | **Weeks** on an 18650 |
| **Watch** | Responsive, plugged-in-ish | Light/modem sleep, warm WiFi | Radar **OR** PIR, lower threshold | Burst (3–5 frames) + audio | MQTT or HTTP, kept-warm socket | Continuous CV on bursts, on-demand VLM | Hours on battery / indefinite on mains |
| **Stream** | Live eyes for active interaction | None — radio always on | Host-driven / continuous | Continuous MJPEG or WebRTC | Streaming | Continuous CV, VLM on demand | Mains/PoE or short session |
| **Patrol** | Scheduled check-ins | Deep sleep, timer wake | Time-based (every N sec/min) | 1 keyframe per cycle | MQTT push | CV + optional VLM per cycle | Days–weeks (cadence-dependent) |
| **Tripwire** | Presence sensing, camera dark | Deep/light sleep | Radar/PIR zone logic only | **None** unless a recipe escalates | Tiny event ping | Sensor-event logic; camera only on escalation | **Longest** — months |

### Per-mode detail

**Sentry — the flagship mobile mode.**
The device sleeps in µA-land and wakes only when *both* presence sensors agree, which kills the dominant false-wake sources (HVAC drafts, sun-warmed surfaces for PIR; small reflections for radar). On a confirmed wake it grabs one good keyframe per eye plus a 1–3 s audio clip, pushes over MQTT, then drops back to sleep. The host runs cheap CV first and only escalates survivors to the VLM. This is the "weeks on a single 18650" promise, and it's also the *correct* design for an agent use case that needs a good keyframe on event, not 30 fps.
*Tradeoff it accepts:* AND-gating can miss a fast/edge event; no pre-roll (deep sleep loses the RAM ring buffer), so "what led up to this" isn't available here.

**Watch — responsiveness over runtime.**
WiFi socket stays warm (light/modem sleep) so there's no 1–3 s reconnect penalty on each event, and triggers fire on *either* sensor with a more sensitive threshold. Capture is a short burst so the VLM has motion/multiple angles to reason over. This is the mode for "I'm around and want it snappy" — a desk session, a plugged-in shelf placement, a robot head between active tasks.
*Tradeoff:* warm radio + looser triggers = hours not weeks on battery.

**Stream — live, for interaction or debugging.**
Continuous MJPEG (simple) or WebRTC (lower latency, NAT-friendly) with the host doing continuous CV and firing the VLM only on demand or on a host-side trigger. This is the robot-head / live-assistant / "show me right now" mode and the natural debugging view.
*Tradeoff:* contradicts mobility — treat as mains/PoE or explicitly time-boxed.

**Patrol — scheduled, not reactive.**
Ignores the sensors and wakes on a timer (every N seconds to N minutes), captures one frame per eye, processes, sleeps. Think periodic driveway check, plant/pet monitoring, slow time-lapse with reasoning. Power scales directly with cadence, so it spans days to weeks.
*Tradeoff:* blind between cycles — a fast event in the gap is simply missed.

**Tripwire — presence only, camera stays dark.**
Pure radar/PIR zone logic; the camera and WiFi image path never spin up unless a *recipe* explicitly escalates (e.g. "if presence persists > 30 s, then capture"). Lowest power of all — this is the months-on-a-battery mode and the privacy-forward default (no images unless a rule demands one).
*Tradeoff:* you get presence/occupancy events, not scene understanding, until escalation.

### Mode mechanics to design

- **Who owns the mode?** Proposal: mode lives in firmware (sets the local power/trigger behavior) but is **settable from the host** so the user changes it in the UI, not by reflashing. Host pushes a config blob on (re)connect.
- **Per-eye vs per-device.** Can the two eyes run *different* modes (e.g. one Tripwire wide-FOV, one Sentry on the door)? Powerful, but complicates fusion and the mental model. Flag as an open question.
- **Mode transitions.** Can a recipe *promote* a mode? ("On confirmed person at night, bump Sentry → Watch for 2 min.") This is where modes stop being static presets and become a state machine — likely a v2 feature, but the schema should leave room.
- **Scheduling.** Sentry by day, Tripwire overnight, Patrol on weekends — a simple per-mode schedule covers a lot of real use without per-recipe complexity.

---

## PART 2 — THE PIPELINE (cost-escalating cascade)

The organizing principle: **each stage costs more than the last and gates the next.** Power, bandwidth, and LLM-cents are spent only after something cheaper has justified the spend. Modes are just different entry points and thresholds along this same cascade.

1. **Ambient sensing** *(on-ESP32, always on)* — PIR + radar presence + mic envelope. µW to low mW.
2. **Wake fusion** *(on-ESP32)* — is this trigger worth waking the camera/WiFi path? (This is the trigger-logic knob.)
3. **Capture** *(on-ESP32)* — one keyframe, a burst, or a short clip + audio per the mode.
4. **Transport** — WiFi (MQTT / HTTP / stream) to the host.
5. **Cheap host CV** — object/motion detection (YOLO-class), audio classification. The "free" filter that rejects most events before the VLM.
6. **Host fusion** — merge the two eyes + sensor metadata into one **normalized event** (Part 4).
7. **VLM reasoning** *(expensive)* — natural-language "what's actually happening," only on events that survived step 5.
8. **Recipe evaluation** — does this event match a "when X seen, do Y" predicate?
9. **Agent sink** — dispatch to MCP / webhook / Home Assistant / Discord / email / SMS.

Pillar mapping: **mobility** ≈ stages 1–3 done right; **agent-neutrality + no-code** live in stages 6–9; **multi-camera** is the fusion at stage 6.

---

## PART 3 — THE KNOBS (and what each trades)

Nearly every knob moves along one of four axes: **power ↔ responsiveness**, **bandwidth ↔ fidelity**, **cost/latency ↔ intelligence**, **false-positives ↔ false-negatives.** Modes are presets over these; advanced users get the raw dials.

### Edge / firmware knobs

| Knob | Range | Trades |
|------|-------|--------|
| **Trigger fusion** | PIR-only · radar-only · AND · OR · mic-assisted | Highest-leverage power knob. AND cuts false wakes but can miss fast events; radar alone catches *stationary* presence PIR misses. |
| **Sleep depth** | deep (µA, +reconnect) · light/modem (mA, warm socket) | There's a **crossover event-rate** above which staying connected beats reconnecting. Don't default to deepest sleep blindly. |
| **Capture payload** | single frame · burst · clip+audio | Richer VLM input vs power + bandwidth. |
| **Resolution + JPEG quality** | QVGA→UXGA, q-factor | What the VLM has to work with vs bytes on air. VGA/SVGA stills are the practical sweet spot. |
| **Pre-roll buffer** | off · N seconds | Context *before* the trigger — but **incompatible with deep sleep** (loses the RAM ring buffer). Wanting "what led up" sets your sleep floor. |
| **Debounce / cooldown** | ms–minutes | Event spam vs missing rapid repeats. |

### Transport knobs

- **Protocol** — MQTT (QoS, pub/sub, great for duty-cycled push) · HTTP POST (simplest event mode) · MJPEG/WebRTC (streaming). Likely MQTT as the default event bus.
- **Local-only vs cloud relay** — privacy/latency vs reaching a roaming device from outside the LAN. A self-hostable broker keeps the "open" promise; an optional relay enables remote use.

### Host knobs

- **CV confidence threshold** — how much gets filtered before the VLM (cost vs recall).
- **VLM cadence / rate limit** — the SenseCraft "15-min gap" pattern; coverage vs spend.
- **Local VLM (Ollama / Qwen-VL / LLaVA) vs cloud** — cost/privacy vs quality/latency. Make this a **per-recipe override**, not just a global setting.
- **Predicate resolution** — known classes (person, package) settle in cheap CV; natural-language conditions ("someone in a DHL uniform") escalate to the VLM. **Recipe complexity is itself a cost knob.**

---

## PART 4 — FUSION & THE EVENT WINDOW

### What "fusion" means here

Sensor fusion = combining multiple sensors so the result is more complete or reliable than any one alone. The key distinction is **where** you combine:

- **Late / decision-level fusion** — each sensor produces its own verdict, then you combine the *conclusions* ("PIR: motion" + "camera: person" + "radar: approaching"). Easy, modular, survives a sensor failing — but each sensor is already collapsed to a verdict, raw data discarded.
- **Early / raw-level fusion** — combine the raw signals *before* interpreting, so one model sees everything. Maximum information retained, harder to align. (Self-driving stacks fuse camera+radar+lidar into one representation a single network reads.)

Our sensors are **complementary, not redundant** — camera says *what* (semantics, color, logos), radar says *how fast / how far* and works in the dark, mic says *what kind of sound*, PIR is a cheap motion flag. So fusion here is **composition** (assembling facets of one situation), not **voting/averaging**. The classic estimation math (Kalman, Bayesian/Dempster-Shafer) is for the *redundant* case — fusing noisy estimates of the same value; we'd only touch Kalman-ish tracking if we follow a target across the radar stream. Mostly we compose.

### What each sensor hands you

| Sensor | Native output | Gives | Does **not** give |
|---|---|---|---|
| **PIR** | Digital HIGH/LOW | Motion present, sub-second. | Direction, distance, count, identity. Blind to *stationary* warm bodies. |
| **Radar** (mmWave) | Stream, tens of Hz | Presence, range, velocity (Doppler), approach/recede; multi-antenna adds **angle (DoA)** + stationary-breathing presence. | Appearance, identity. |
| **Mic** | PCM waveform | Loudness (cheap wake), sound class (speech, glass-break, bark), keywords; an **array** adds DoA. | Bearing from a *single* mic. Identity. |
| **Camera** | JPEG frames | The semantic jackpot: object, pose, clothing, **color**, faces, logos. | Anything in the dark (sans IR). Power/bandwidth-hungry. |

Two flags: **DoA needs an array / multiple antennas** — a single mic + single-antenna radar give no bearing, though "which eye fired first" is a crude left/right. And **only the camera gives appearance**, which is exactly what open-vocabulary recipes lean on.

### The event is a *window*, not a snapshot

A single synchronized instant fails, because the modalities live on different timescales: audio is meaningless at one sample, radar velocity needs several frames to exist, a lone frame can miss the action. So the unit of perception is an **episode** — a bounded window (~2–5 s) a cheap trigger opens, during which each sensor contributes its *natural* representation:

- **Camera** → a short burst, or one keyframe picked at peak activity (e.g. when radar says the target is closest) — fired at the *most informative* moment, not blindly simultaneous. Pre-roll adds the run-up if a RAM ring buffer is available (rules out deepest sleep).
- **Mic** → a clip spanning the window.
- **Radar** → the short time-series over the window (the track: range / velocity / angle).
- **PIR** → just edge timestamps — metadata, "fired at t=0."

**One trigger, one window, each sensor drops in what it's good for; the bundle is the event.**

### The central tension — and the resolution

**Recipes are open-vocabulary natural language, so the set of possibly-relevant features is unbounded.** Any fixed pre-processing schema is a *lossy projection* and may not preserve what some future recipe asks for. Reduce a frame to `{person, 0.88}` and the red shirt is gone forever — and you couldn't have known to keep it, because "red shirt" only exists in the recipe.

The fix is to separate two jobs that *look* like one step:

1. **Gating (triage)** — "is this worth the expensive model?" **Allowed to be lossy**; its only job is *something is probably here*. Wrong gate = missed event, so tune for recall. Cheap CV / PIR+radar agreement is fine and crude.
2. **Interpretation (answering the recipe)** — must run on the **raw media** with the **recipe in context**, nothing pre-discarded.

The mistake is putting lossy CV *between the camera and the model as a content filter*. Instead: **cheap CV only gates whether the multimodal model runs at all. When it runs, hand it the raw frame + the recipe's actual question.** CV labels ride along as *hints*, never as a *replacement* for the pixels.

This kills the red-shirt bug: don't ask the VLM "describe this image" and string-match the caption (which may never mention shirts). Ask the recipe's question directly — *"Is there a person wearing a red shirt? yes/no + bbox."* The recipe **drives the query**, so you look for the thing the rule cares about against data you never threw away. This is **recipe-conditioned perception.**

### How the non-visual sensors actually get fused

Off-the-shelf multimodal models don't read radar time-series or PIR edges. So **render them into structured text and put that in the prompt** alongside the raw image + audio:

> `radar: target approached 3m → 0.5m over 2s at 1.2 m/s; audio: speech detected; PIR: left eye fired first`

**That textual context is the fusion.** The event object is the carrier; the model is the fuser. Net architecture: **late fusion for the cheap gate** (AND/OR of presence sensors) + **model-level early fusion for interpretation** (raw image + audio + sensor-context-as-text + the recipe's question → one multimodal call). That split is what lets you stay cheap *and* never pre-discard the red shirt.

Practical consequences:
- The event object **carries raw media (or URIs), not just labels.** Labels are a hint/index layer on top of the bytes.
- **Two kinds of VLM call:** an optional generic caption (for a browsable timeline / history search) *and* targeted recipe queries (the actual matcher). Caption is for humans; targeted query is for matching.
- **Cost scales with active recipes** (each asks its question), but batch them ("answer all of these about this image") in one call, and the gate keeps any of it from running on ambient nothing.

---

## PART 5 — THE NORMALIZED EVENT OBJECT (keystone)

Define **one** event object at the host and make everything downstream speak it. This single abstraction is what makes the device genuinely agent-neutral (any sink) and no-code (any predicate → any sink) without special-casing every integration — and it's how you out-open the Watcher, which funnels toward SenseCraft.

```jsonc
{
  "event_id": "uuid",
  "device_id": "om-001",
  "timestamp": "2026-06-24T18:30:00.123Z",   // host-stamped; see two-clock caveat
  "mode": "sentry",
  "window": { "start": "...", "end": "...", "duration_s": 3.0 },  // it's an episode, not an instant
  "trigger": {
    "source": ["radar", "pir"],              // which sensors opened the window
    "eye": "left",                            // or "both"
    "confidence": 0.92
  },
  "media": {                                  // RAW carrier — never pre-discarded
    "frames": [ { "eye": "left", "uri": "...", "res": "640x480", "jpeg_q": 12, "t_offset_ms": 1200 } ],
    "audio_clip": { "uri": "...", "duration_s": 3.0 }
  },
  "sensor_track": {                           // non-visual streams, kept raw + summarized to text
    "radar": { "range_m": [3.0, 0.5], "velocity_ms": 1.2, "trend": "approach" },
    "pir": { "edges_ms": [0] },
    "context_text": "radar: target 3m→0.5m over 2s at 1.2 m/s; audio: speech; PIR: left first"
  },
  "cv": {                                     // GATE/HINT layer — lossy, never a content filter
    "labels": [ { "class": "person", "conf": 0.88, "bbox": [..] } ],
    "audio_tags": [ { "tag": "speech", "conf": 0.7 } ]
  },
  "vlm": {                                    // present only if escalated past the gate
    "caption": "A person in a delivery uniform sets a box by the door.",  // optional, for human timeline
    "model": "qwen2.5-vl:7b",
    "recipe_answers": [                       // targeted, recipe-driven queries — the matcher
      { "recipe_id": "r-42", "question": "person wearing a red shirt?", "answer": false },
      { "recipe_id": "r-09", "question": "package present?", "answer": true, "bbox": [..] }
    ]
  },
  "fusion": {                                 // cross-eye correlation
    "both_eyes_agree": true,
    "first_eye": "left"
  }
}
```

- **Recipes** are predicates over this object; **agent sinks** are pluggable consumers of it.
- **Progressive enrichment:** created at trigger, filled in as it climbs the cascade (`cv` at the gate, `vlm` only if it survives). A cheap recipe can match on `cv`/`sensor_track` and never touch the VLM.
- **`media` and `sensor_track` hold the raw signal.** `cv` is a *hint/gate* layer on top — it must never stand in for the pixels when a recipe escalates. `vlm.recipe_answers` are produced by asking the recipe's own question against the raw media (recipe-conditioned perception), which is what preserves the red shirt.

---

## PART 6 — RECIPE ENGINE & AGENT SINKS (table-stakes pillars)

Necessary, but not where you win — incumbents and the Watcher already have versions of this. Build it solid, don't lead the marketing with it.

- **Recipe shape:** `WHEN <predicate over event object> [WITHIN <window>] THEN <action via sink>`.
  - **Cheap predicates resolve at the gate** (`cv.labels contains person`, `sensor_track.radar.trend == approach`) — no VLM.
  - **Rich predicates become a targeted VLM query** against raw media (`"person wearing a red shirt?"` → `vlm.recipe_answers`), never a string-match on a generic caption.
  - Compound, à la Watcher: "dog AND playing AND 0 people," "person in DHL outfit."
- **Sinks (pluggable):** MCP server/client · generic webhook · Home Assistant · Node-RED · Discord · email · SMS · local script. Each sink just consumes the normalized event — adding one is a plugin, not a core change.
- **Coupling to watch:** triggering on the edge but reasoning on the host means a recipe can want data the firmware already slept through. **Capture policy and recipe design are not independent** — e.g. a "package theft" recipe needs pre-roll, which rules out deepest sleep for that device/mode. The recipe builder should surface this ("this rule requires Watch mode or pre-roll enabled").

---

## PART 7 — PROTOTYPE SCAFFOLD (camera-only host slice)

> **Where this sits in the build ladder:** this is the **v1** slice (full gate→interpret→match cascade). The *actual first thing to build* is **v0** — radically simpler, defined in **§8.9** — one model watching the webcam, pinging on a recipe match, no cascade. Build v0 first to prove the recipe works at all; grow into this Part-7 scaffold; grow that into the Part-8 target architecture. Same `Event` seam throughout.

**Goal of this prototype:** validate the *thesis*, not the hardware. The hardware questions (power, camera interface) are answered in the landscape analysis; what's unproven is the **host pipeline** — gate → interpret → match against an open-vocabulary natural-language recipe. That runs entirely on a laptop + webcam. If the red-shirt problem isn't solved here, no BOM fixes it.

**The seam that makes this throwaway-proof:** the `Event` object is the contract. The webcam is just a *mock event source* emitting the same object the real ESP32 sensors will later emit over MQTT. Build the whole receiving side against the schema now; when hardware arrives, **swap the source, not the pipeline.**

**v0 scope trim (deliberate):** one webcam = one eye, camera-only. `sensor_track`, the second eye, and cross-eye `fusion` are stubbed/empty. Prove the gate→interpret→match→sink spine first; add a second source and sensor-context-as-text after. Walk before stereo.

**What v0 must empirically answer:** (1) does recipe-conditioned querying beat caption-matching? (run both, compare on the same frames); (2) real VLM latency + cost per event; (3) gate recall vs cost; (4) where windows actually start/end on messy continuous motion; (5) which VLM tier is "good enough" (small local model may nail "red shirt," flunk "FedEx vs UPS logo").

> This section is an **implementation handoff**: schema + interfaces + stubs only, no logic. Hand to the implementation agent. Python assumed (Ollama/CV/VLM ecosystem); all method bodies are `...` or raise `NotImplementedError`.

### 7.1 — Schema as dataclasses (mirrors Part 5)

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Iterator, Union

# ---- raw media (the carrier — never pre-discarded) ----
@dataclass
class Frame:
    eye: str                          # "left" | "right"
    res: str                          # "640x480"
    t_offset_ms: int                  # offset within the window
    jpeg_q: int = 12
    uri: Optional[str] = None         # where bytes live (file/obj store)
    data: Optional[bytes] = None      # OR carried in-memory (prototype convenience)

@dataclass
class AudioClip:
    duration_s: float
    uri: Optional[str] = None
    data: Optional[bytes] = None

@dataclass
class Media:
    frames: list[Frame] = field(default_factory=list)
    audio_clip: Optional[AudioClip] = None

# ---- non-visual sensors: raw streams + the text summary that IS the fusion ----
@dataclass
class SensorTrack:
    radar: Optional[dict] = None          # {"range_m":[3.0,0.5],"velocity_ms":1.2,"trend":"approach"}
    pir: Optional[dict] = None            # {"edges_ms":[0]}
    context_text: str = ""                # "radar: 3m→0.5m at 1.2 m/s; audio: speech; PIR: left first"

# ---- gate/hint layer (LOSSY — never substitutes for pixels) ----
@dataclass
class Label:
    cls: str
    conf: float
    bbox: Optional[list[float]] = None

@dataclass
class CVResult:
    labels: list[Label] = field(default_factory=list)
    audio_tags: list[Label] = field(default_factory=list)

# ---- interpretation layer (recipe-conditioned, runs on raw media) ----
@dataclass
class RecipeAnswer:
    recipe_id: str
    question: str                     # the recipe's OWN question, asked of the VLM
    answer: Union[bool, str]
    bbox: Optional[list[float]] = None

@dataclass
class VLMResult:
    model: str
    caption: Optional[str] = None             # optional, for human timeline / history search
    recipe_answers: list[RecipeAnswer] = field(default_factory=list)  # the actual matcher

@dataclass
class Fusion:
    both_eyes_agree: bool = False
    first_eye: Optional[str] = None

# ---- the event = the seam between source and pipeline ----
@dataclass
class Window:
    start: datetime
    end: datetime
    duration_s: float

@dataclass
class Trigger:
    source: list[str]                 # ["radar","pir"] — ["motion"] in webcam v0
    eye: str = "left"
    confidence: float = 1.0

@dataclass
class Event:
    event_id: str
    device_id: str
    timestamp: datetime
    mode: str
    window: Window
    trigger: Trigger
    media: Media
    sensor_track: Optional[SensorTrack] = None   # empty in camera-only v0
    cv: Optional[CVResult] = None                # filled by Gate
    vlm: Optional[VLMResult] = None              # filled by Interpreter, only if escalated
    fusion: Optional[Fusion] = None              # filled when >1 eye
```

### 7.2 — Event sources (webcam now, MQTT later, file always)

```python
class EventSource(ABC):
    """Emits Event objects with raw media populated. Pipeline is source-agnostic."""
    @abstractmethod
    def events(self) -> Iterator[Event]:
        """Yield Events as windows open. Blocks/streams for live sources."""
        ...

class WebcamSource(EventSource):
    """v0 mock source: open a window on motion-delta (or timer), grab a burst."""
    def __init__(self, device_index: int = 0, window_s: float = 3.0): ...
    def events(self) -> Iterator[Event]:
        raise NotImplementedError  # capture loop → motion trigger → burst → Event

class FileClipSource(EventSource):
    """Replays recorded clips as Events. The regression backbone (see 7.6).
    Most valuable source: deterministic, lets every pipeline change be re-tested."""
    def __init__(self, fixtures_dir: str): ...
    def events(self) -> Iterator[Event]:
        raise NotImplementedError

class MqttSensorSource(EventSource):
    """FUTURE: real ESP32 eyes publish events here. Same Event out — pipeline unchanged."""
    def __init__(self, broker_url: str, topics: list[str]): ...
    def events(self) -> Iterator[Event]:
        raise NotImplementedError
```

### 7.3 — Gate (cheap, lossy triage — tune for recall)

```python
@dataclass
class GateDecision:
    escalate: bool
    reason: str
    hints: Optional[CVResult] = None   # rides along as HINTS, never replaces pixels

class Gate(ABC):
    @abstractmethod
    def evaluate(self, event: Event) -> GateDecision:
        """Decide if this event is worth the expensive model. Allowed to be lossy."""
        ...

class MotionDeltaGate(Gate):
    """Dumbest viable gate: frame changed enough → escalate. Good v0 baseline."""
    def __init__(self, threshold: float = 0.15): ...
    def evaluate(self, event: Event) -> GateDecision:
        raise NotImplementedError

class YoloPersonGate(Gate):
    """Cheap local detector. Escalate on person (or configured classes). Emits hints."""
    def __init__(self, model_path: str, classes: list[str], min_conf: float = 0.5): ...
    def evaluate(self, event: Event) -> GateDecision:
        raise NotImplementedError
```

### 7.4 — VLM backends (BOTH behind one interface) + Interpreter

```python
@dataclass
class Answer:
    question: str
    answer: Union[bool, str]
    bbox: Optional[list[float]] = None

class VLMBackend(ABC):
    """One interface, swappable impls. Recipe-conditioned: takes QUESTIONS, not 'describe'."""
    model: str
    @abstractmethod
    def answer(self, frames: list[Frame], questions: list[str],
               context_text: str = "") -> list[Answer]:
        """Ask the recipe's own questions against raw frames (+ sensor context text).
        This is where the red shirt is preserved — we look for what the rule asked."""
        ...
    @abstractmethod
    def caption(self, frames: list[Frame], context_text: str = "") -> str:
        """Optional generic caption for the human timeline / history search."""
        ...

class OllamaBackend(VLMBackend):
    """Local: Qwen-VL / LLaVA via Ollama. Cheap, private, maybe weaker on logos."""
    def __init__(self, model: str = "qwen2.5-vl:7b", host: str = "http://localhost:11434"): ...
    def answer(self, frames, questions, context_text=""): raise NotImplementedError
    def caption(self, frames, context_text=""): raise NotImplementedError

class CloudBackend(VLMBackend):
    """Cloud: Claude / OpenAI vision. Stronger, costs per call, leaves the LAN."""
    def __init__(self, provider: str, model: str, api_key_env: str): ...
    def answer(self, frames, questions, context_text=""): raise NotImplementedError
    def caption(self, frames, context_text=""): raise NotImplementedError

class Interpreter:
    """Wraps a backend. Batches all active recipe questions into one call per event."""
    def __init__(self, backend: VLMBackend, want_caption: bool = False): ...
    def interpret(self, event: Event, questions: list["QuestionSpec"]) -> VLMResult:
        raise NotImplementedError  # → fills event.vlm.recipe_answers (+caption if wanted)
```

### 7.5 — Recipes, predicates, matcher, sinks

```python
@dataclass
class QuestionSpec:
    recipe_id: str
    question: str                     # natural-language question handed to the VLM

class Predicate(ABC):
    """Compiled from a recipe's natural language. Knows its own cost tier."""
    @abstractmethod
    def cost_tier(self) -> str:
        """'gate'  → resolvable from cv/sensor_track, no VLM.
           'vlm'   → needs a targeted question against raw media."""
        ...
    @abstractmethod
    def questions(self) -> list[str]:
        """For vlm-tier predicates: the question(s) to ask. Empty for gate-tier."""
        ...
    @abstractmethod
    def evaluate(self, event: Event) -> bool:
        """True if this event satisfies the predicate (reads cv OR vlm.recipe_answers)."""
        ...

@dataclass
class Recipe:
    id: str
    text: str                         # user-facing NL: "alert me when someone in a red shirt enters"
    predicate: Predicate
    sinks: list[str]                  # sink names to dispatch to
    window_s: Optional[float] = None  # optional WITHIN constraint

@dataclass
class Match:
    recipe: Recipe
    event: Event

class RecipeEngine:
    def __init__(self, recipes: list[Recipe]): ...
    def questions_for(self, event: Event) -> list[QuestionSpec]:
        """Collect targeted questions from all vlm-tier recipes active for this event."""
        raise NotImplementedError
    def match(self, event: Event) -> list[Match]:
        """Evaluate every recipe predicate (gate-tier + vlm-tier) → list of matches."""
        raise NotImplementedError

class Sink(ABC):
    name: str
    @abstractmethod
    def dispatch(self, event: Event, match: Match) -> None: ...

class ConsoleSink(Sink):
    name = "console"
    def dispatch(self, event, match): raise NotImplementedError

class WebhookSink(Sink):
    name = "webhook"
    def __init__(self, url: str): ...
    def dispatch(self, event, match): raise NotImplementedError
# FUTURE sinks: MqttSink, HomeAssistantSink, DiscordSink, EmailSink — same interface.
```

### 7.6 — Orchestrator (the run loop = the cascade)

```python
class Orchestrator:
    """Wires source → gate → interpret → match → sinks. The whole pipeline, one place."""
    def __init__(self, source: EventSource, gate: Gate,
                 interpreter: Interpreter, engine: RecipeEngine,
                 sinks: dict[str, Sink]): ...

    def run(self) -> None:
        # PSEUDOCODE — the contract the implementation must honor:
        # for event in self.source.events():
        #     decision = self.gate.evaluate(event)        # cheap triage
        #     if not decision.escalate:
        #         continue                                 # most events die here
        #     event.cv = decision.hints                    # hints, not a filter
        #     questions = self.engine.questions_for(event) # what do rich recipes need to know?
        #     if questions:
        #         event.vlm = self.interpreter.interpret(event, questions)  # raw media + questions
        #     for match in self.engine.match(event):       # cheap + rich predicates
        #         for sink_name in match.recipe.sinks:
        #             self.sinks[sink_name].dispatch(event, match)
        raise NotImplementedError
```

### 7.7 — Regression fixtures (build this early; it outlives everything)

```
fixtures/
  manifest.yaml
  clips/
    red_shirt_enters.mp4
    blue_shirt_enters.mp4
    empty_hallway.mp4
    fedex_at_door.mp4
```

```yaml
# manifest.yaml — ground truth. Every pipeline change re-runs against this.
recipes:
  - id: r-red
    text: "alert when a person wearing a red shirt enters"
  - id: r-fedex
    text: "alert when a delivery driver arrives"
expected:
  red_shirt_enters.mp4:   { r-red: true,  r-fedex: false }
  blue_shirt_enters.mp4:  { r-red: false, r-fedex: false }
  empty_hallway.mp4:      { r-red: false, r-fedex: false }
  fedex_at_door.mp4:      { r-red: false, r-fedex: true  }
```

A `FileClipSource(fixtures/clips)` + a test runner that diffs actual matches vs `expected` gives a **measurable** answer to "did this change improve or break recipe accuracy," and lets you A/B Ollama-vs-Cloud and caption-match-vs-targeted-query on identical inputs. This corpus is the most durable artifact in the prototype.

---

## PART 8 — RECIPE-DRIVEN CONFIGURATION & ATTENTION CONTROL (TARGET ARCHITECTURE)

> **Status: saved target, NOT v0.** This part exists so the complexity we reasoned through doesn't escape. None of it is built first. The simplification ladder in §8.9 says what to actually build now and how each piece expands into the vision below. Read §8.9 if you just want to start.
>
> **Core thesis of this part:** the recipe is not a predicate checked at the end — it is a **specification compiled into configuration for everything upstream** (sensors, firmware duty cycle, fusion, model tiers). "The recipe drives everything" is the organizing idea; the layered models, the anticipation/loitering problem, and recipe-dependent firmware params are all the same realization.

### 8.1 — The mental model: peripheral vs foveal attention

Two tiers, by analogy to biological attention:

- **Peripheral tier** — always-on, cheap, low-res awareness (PIR + radar + mic envelope, and optionally a cheap model). Job: *situational awareness and attention control* — "is something developing that could become recipe-relevant?" It never answers the recipe; it decides when/where to point the expensive tier.
- **Foveal tier** — expensive, pointed, high-res (camera on + flagship VLM answering the recipe's actual question). Runs only when the peripheral tier arms it.

**Duty cycle = attention allocation**: how eagerly and how long you point the fovea. The layered-model idea = the peripheral tier got smart. The anticipation event = the peripheral tier holding the fovea open expecting a terminal event. Recipe-dependent params = the recipe compiled into both tiers' policy.

### 8.2 — The peripheral tier is a CONTROLLER, not a gate (+ two clocks)

The loiterer case is the proof: nothing is escalating, but the right behavior isn't "ignore" or "fully engage" — it's "maintain a low-energy watch, spend nothing until the state changes, and *throttle the capture rate down* because it's the same person who's been there 30 s." Only something that **holds state** can do that. So the peripheral tier has **three jobs**:

1. **Escalate** — point the fovea (binary, the old "gate").
2. **Modulate** — set the sensing rate/duty for the *current situation* (the new, important one: "same person, chill to once per 30 s").
3. **Remember** — hold the timeline that makes "same person, still here, no change" knowable. (3) enables (2).

**This runs on TWO CLOCKS — and their difference is the architecture, not a defect:**

- **Reflex clock** (on-device, fast, dumb): sensors fire on their *currently-configured* policy. No model in this loop. Milliseconds. PIR/radar trip → grab episodes at the current rate.
- **Control clock** (peripheral model, slow, smart): wakes on a *batch of episodes*, assesses the *situation*, and **rewrites the policy the reflex loop runs next.** Seconds to tens of seconds.

The peripheral model reacts to **situations** (slow-evolving), not **events** (fast). This dissolves the earlier "edge reconfiguration latency" worry: the control loop is *supposed* to be slow relative to reflexes. You're changing standing orders, not reacting in milliseconds.

**The one hard constraint this imposes — the safe-default floor:** between control-loop ticks the reflex loop runs *stale orders*. So the recipe compiler must set not just a target rate but a **non-suppressible floor**: even when "chilled" to 30 s, the on-device logic still fully wakes on a *new/different* signature vs. the tracked one. **The model sets policy; the device keeps reflexes the policy can't suppress.** Reflexes are the safety net for the control loop's slowness — they're what keeps "chill out" from making you miss the actual door-crossing.

### 8.3 — The fovea↔peripheral interface & recipe-state memory (the closing of the loop)

The missing piece that makes the loop *close*: a **fovea-confirmed recipe/situation state log** — a memory of *recipe progress* that the fovea writes and the peripheral reads, so the peripheral knows what to do next.

Examples that show why it's needed:
- Recipe = "notify on the **first** person through the door." Peripheral sees a candidate → arms fovea → fovea **confirms** → writes `state: SATISFIED` → tells peripheral "task done, stand down / stop turning the camera on, await further instruction." Without this, the system keeps spending forever on an already-answered recipe.
- Recipe = "**count** people through the door" / "wait for **several**." Now the recipe carries running memory (`count: 3`, maybe saved keyframes per crossing) and is *never* terminal — the fovea increments state and tells the peripheral to keep watching.

So a recipe instance owns a **RecipeState** the fovea updates and the peripheral consults:

```python
@dataclass
class RecipeState:
    recipe_id: str
    status: str                 # "watching" | "armed" | "satisfied" | "counting" | "stood_down"
    progress: dict              # e.g. {"count": 3} ; recipe-specific running memory
    evidence: list[str]         # uris of saved keyframes/clips that confirmed progress
    last_confirmed: datetime
    next_directive: str         # peripheral's standing order: "stand_down" | "keep_watching" | ...
```

This interface is also where "is it the same person" lives (peripheral writes a tracked-subject identity; fovea confirms/refines). Whether `evidence` is a saved image, a structured doc, or both is an open question (§OQ). The key principle: **the fovea's confirmation flows back down to change the peripheral's behavior** — that backward edge is what was missing, and it's what makes recipes terminate, count, or persist correctly.

### 8.4 — The SituationAssessment contract (intelligence is a swappable knob)

Keep the peripheral tier's **job description fixed, its intelligence swappable.** Define exactly what it must output and let any model fill it:

```python
@dataclass
class SituationAssessment:
    tracked_subjects: list[dict]        # identities + how long present
    state: str                          # "stable" | "evolving" | "new"
    recommended_policy_delta: dict      # e.g. {"capture_rate_s": 30, "camera": "keep_warm"}
    escalate: bool                      # point the fovea now?
    rationale: str                      # WHY — surfaced to pilot/agent
```

"How dumb can the model be?" becomes empirical: swap a big model for a small one, run the fixtures, watch where assessments degrade. **It's likely recipe-dependent** ("same person stayed in frame" = tiny model; "is this person casing the house vs. waiting for a rideshare" = big model), so a future **classifier picks the intelligence tier per recipe.** Door kept open; not built now. *(Per your direction: use smart models in every slot at the gate; the contract is what lets you downgrade later without touching architecture.)*

### 8.5 — Recipe as a first-class typed config object (compiler + override + live cost)

The recipe is **first-class**. A **recipe compiler** turns natural language into a typed config bundle that parameterizes sensors, firmware duty cycle, fusion, model tiers, and the attention state machine — each field carrying a **recommendation + rationale + live cost estimate.**

- **Compiler recommends; pilot overrides.** Every field is overridable. Any override **re-renders predicted battery life + predicted performance live.**
- **Agent-first philosophy:** the operator is assumed to be a capable agent, so traditional UI-usability constraints relax. The config is a **fully-readable, fully-writable structured object** with rationale on every field — an agent reads the recommendation *and why*, and can rewrite any field with its own justification. A human UI renders a friendly subset; the agent gets the whole surface. **One schema, two consumers** — same pattern as the event object and the source interface.
- **The cost/battery estimator is therefore a FIRST-CLASS component, not a nicety.** If an override silently lies about predicted battery, the "pilot sees the consequences" promise collapses. Conveniently it's pure host-side logic — buildable and later validated against measured numbers on the laptop slice.

### 8.6 — The arbiter (multiple recipes, one set of sensors)

You'll run **multiple recipes at once on one device.** Recipe A wants ultra-low-power tripwire; Recipe B wants pre-roll + warm camera. The hardware can't hold both postures, so an **arbiter** reconciles all active recipes into one device config. Default rule: **max/union of requirements, clamped to the power budget** — the most demanding active recipe sets the floor.

Emergent consequence to surface to the pilot: adding an innocent-looking recipe can **silently collapse battery life** because it raised the floor for everything. The product must say so ("this recipe drops runtime to ~6 h"). Keep arbiter logic dead simple (max-of-requirements) at first.

### 8.7 — What this does to "modes"

**A mode becomes a policy + budget envelope over the attention state machine, not a fixed schedule.** It defines how eagerly you arm, how long you dwell, how deep you idle, and a **power ceiling the arbiter can't exceed.** Recipes operate *within* the envelope. **Resolution (decided): hybrid — the mode sets the budget; recipes bid within it.** (Not "mode constrains recipes" alone = too rigid; not "recipes drive modes" alone = unpredictable battery.)

Existing five modes re-read cleanly as envelopes (Sentry = conservative arm + deep idle + short dwell; Stream = permanently foveal; Tripwire = arms but never foveal unless a recipe escalates; etc.). The anticipation work unlocks **new envelopes**:

- **Stakeout / Vigil** — eager to arm and *stays* foveal through long dwell windows; built for loiter→entry "catch the moment." High power, for when an event is *expected imminent*.
- **Adaptive / Auto** — the cheap model picks posture dynamically from observed patterns + time of day. Self-tuning duty cycle; most "smart," least predictable.

### 8.8 — Honest risk list for "recipe drives everything"

- NL → firmware params is **hard and silently failure-prone** (mis-compiled recipe tanks battery or drops events with no obvious cause). → Start **rule-based** (recipe → explicitly *declared* requirements), not learned.
- **Predictability suffers** — users won't know why battery died. → The live cost estimator + per-field rationale are the mitigation.
- **Arbitration produces emergent behavior** — recipe interactions surprise you. → Keep arbiter trivial (max-of-requirements) at first; surface the result.
- **Cheap-tier false negatives are recipe-specific and hard to debug.** → Keep the peripheral bar deliberately low (over-escalate); precision is the fovea's job.

### 8.9 — THE SIMPLIFICATION LADDER (what to actually build, and the expansion seams)

The whole point: lock the target above, then collapse it with explicit assumptions so v0 is buildable this week and grows without rework. **Each assumption names the seam it later expands along.**

| Concern | **v0 — build now** | Simplifying assumption | Expands into (target) |
|---|---|---|---|
| Attention | **One model watches the webcam continuously** | No peripheral/foveal split; no duty cycle (laptop on mains, camera always on) | §8.1–8.2 two-tier peripheral/foveal + duty cycle |
| Trigger | Continuous, or trivial motion-delta to save API calls | No reflex/control clocks; no firmware | §8.2 two-clock reflex+control loops |
| Models | **Single (smart) model** answers the recipe directly | No cascade, no cheap tier | §8.3–8.4 layered models + SituationAssessment |
| Recipe state | Fire-and-notify; stateless | No counting, no "task done" memory | §8.3 RecipeState + fovea→peripheral backward edge |
| Config | Recipe text + hardcoded params | No compiler, no overrides, no cost model | §8.5 recipe compiler + live cost + agent overrides |
| Multi-recipe | **One recipe at a time** | No arbiter | §8.6 arbiter (max-of-requirements) |
| Modes | Ignore modes entirely for v0 | — | §8.7 modes-as-envelopes |
| Sensors | **Camera only** | No radar/PIR/mic, no fusion | Part 4 fusion + sensor-context-as-text |
| Output | **Ping me** (console/desktop notify/webhook-to-self) | One sink | Part 6 pluggable sinks |

**v0 in one sentence:** webcam → (continuous or motion-gated) → one smart model answers *the single recipe's question* → if yes, ping me. That validates the only thing that truly must work first: **does recipe-conditioned querying actually detect what the recipe asked for, and not what it didn't?** Everything else in Part 8 is optimization layered onto a loop that already works.

**The non-negotiable seam to preserve even in v0:** keep the `Event` object (Part 5) as the thing the model consumes, and keep the model call *recipe-conditioned* (ask the recipe's own question against raw frames — never describe-then-string-match). Hold those two invariants and every Part-8 layer bolts on without rework.

---

## PART 9 — v0 BUILD: LLM ABSTRACTION, EPISODES & DECISION/DISPATCH

> The concrete day-one build of §8.9's v0. Python. Stubs + contracts, same handoff style as Part 7. First wiring: **Claude API, Haiku 4.5, vision, forced-tool-call structured output.** *(Pricing/IDs current mid-2026: Haiku 4.5 = `claude-haiku-4-5-20251001`, $1/MTok in, $5/MTok out, vision-capable. Re-verify at claude.com/pricing.)*

### 9.1 — The load-bearing principle: decide ≠ act

**The LLM returns a structured verdict; a deterministic Dispatcher executes the side effect.** The LLM never touches Discord. This is not a shortcut — it's the **Event→Sink seam (Part 6) at minimum scale**: the verdict is a partial `Event` fill, the Dispatcher is the sink layer. Grow it, don't replace it.

Payoffs: deterministic + unit-testable (same verdict → same action; test Dispatcher with no LLM, test LLM with no real pings); no accidental spam during prototyping (dry-run mode); and "tools the LLM may invoke" become a **closed enum in the output schema** (clamped per-recipe by `allowed_actions`), with the host owning the action→executor registry — tool-calling expressiveness without giving the model the trigger.

### 9.2 — The LLM abstraction (provider-agnostic, VLM-capable)

Two layers. The app talks to **one** interface: text + images in, schema-validated dict out. Provider differences (image encoding, schema enforcement) hide inside the impl.

```python
@dataclass
class LLMConfig:
    provider: str = "claude"             # "claude" | "ollama" | ...
    model: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 1024
    temperature: float = 0.0             # near-deterministic for v0
    system: str = ""
    api_key_env: str = "ANTHROPIC_API_KEY"

@dataclass
class ImagePart:
    data: bytes
    media_type: str = "image/jpeg"

Content = Union[str, ImagePart]          # a prompt = ordered list of these

class LLMClient(ABC):
    """App's single interface to ANY VLM. The whole point of the abstraction."""
    def __init__(self, config: LLMConfig): ...
    @abstractmethod
    def generate(self, content: list[Content], *, schema: dict,
                 schema_name: str = "decision") -> dict:
        """Return a dict GUARANTEED to match `schema`. Provider-specific inside."""
        ...

class ClaudeClient(LLMClient):
    """v0 default. Uses the PORTABLE forced-tool-call pattern so the same approach
       works on local models later (Ollama tool/grammar-constrained output):
         tools=[{"name": schema_name, "input_schema": schema}]
         tool_choice={"type":"tool","name": schema_name}
         → read the tool_use block's .input  (never executed; just structured JSON)
       Claude's native structured outputs (output_config.format) can swap in behind
       this same method later as a Claude-only optimization."""
    def generate(self, content, *, schema, schema_name="decision"):
        raise NotImplementedError
        # build messages: user content = [image blocks (base64) ..., text]
        # call client.messages.create(...); return the forced tool_use.input dict

class OllamaClient(LLMClient):           # FUTURE — identical interface, local VLM
    def generate(self, content, *, schema, schema_name="decision"):
        raise NotImplementedError
```

### 9.3 — Episodes (capture knobs; doubles as the fixture recorder)

The `Episode` is v0's stand-in for the `Event` — convertible to it later. Both capture knobs are parameters.

```python
@dataclass
class EpisodeConfig:
    fps: float = 1.0                     # frames captured per second
    frames_per_episode: int = 5          # episode span = frames_per_episode / fps seconds
    frame_res: str = "320x240"           # DOMINANT cost lever (tokens ∝ W×H)
    jpeg_q: int = 12

@dataclass
class Episode:
    episode_id: str
    started: datetime
    frames: list[Frame]                  # reuse Part 5 Frame; carries raw bytes in v0
    # .to_event() -> Event   (grow-path into the Part 5 schema)

class EpisodeSource(ABC):
    @abstractmethod
    def episodes(self) -> Iterator[Episode]: ...

class WebcamEpisodeSource(EpisodeSource):
    def __init__(self, cfg: EpisodeConfig, device_index: int = 0): ...
    def episodes(self) -> Iterator[Episode]: raise NotImplementedError

class FolderEpisodeSource(EpisodeSource):
    """Replays saved episodes. Manual-select sending AND §7.7 regression fixtures
       are the SAME mechanism — saved episodes feed the live loop and the A/B harness."""
    def __init__(self, folder: str): ...
    def episodes(self) -> Iterator[Episode]: raise NotImplementedError
```

### 9.4 — Recipe, Decision schema, Dispatcher, Discord sink

```python
@dataclass
class Recipe:
    text: str                            # NL: "notify me when a person in a red shirt appears"
    allowed_actions: list[str] = field(default_factory=lambda: ["notify"])
    # allowed_actions clamps the schema's action enum — the recipe bounds what the LLM may request

def decision_schema(allowed_actions: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "match":      {"type": "boolean", "description": "Did the recipe condition occur?"},
            "confidence": {"type": "number"},
            "reason":     {"type": "string",  "description": "Brief justification."},
            "action":     {"type": "string",  "enum": ["none", *allowed_actions]},
            "message":    {"type": "string",  "description": "Text to send if action != none."},
        },
        "required": ["match", "confidence", "action"],
    }

@dataclass
class Decision:                          # parsed from the schema-guaranteed dict
    match: bool
    confidence: float
    action: str
    reason: str = ""
    message: str = ""

class Sink(ABC):
    name: str
    @abstractmethod
    def send(self, message: str) -> None: ...

class DiscordSink(Sink):
    name = "discord"
    def __init__(self, webhook_url: str): ...
    def send(self, message: str) -> None:
        raise NotImplementedError        # POST {"content": message} to the webhook

class Dispatcher:
    """The deterministic half. LLM output in → side effect out. No model here.
       Testable in isolation; supports dry_run to print instead of send."""
    def __init__(self, sinks: dict[str, Sink], min_confidence: float = 0.6,
                 dry_run: bool = False): ...
    def handle(self, decision: Decision, episode: Episode) -> None:
        raise NotImplementedError
        # gate: decision.match and decision.action in sinks and confidence >= min_confidence
        # then sinks[decision.action_target].send(decision.message)   (or print if dry_run)
```

### 9.5 — Prompt construction = where recipe-conditioning lives

The one invariant from §8.9: **ask the recipe's own question against raw frames** — never describe-then-string-match.

```python
def build_prompt(episode: Episode, recipe: Recipe) -> list[Content]:
    """system (cacheable): 'You judge whether a condition occurred across these frames;
       return the decision schema.'  user: [frame images...] + recipe.text as the question."""
    raise NotImplementedError
```

Cache the stable system + recipe block (Haiku cache reads ≈ $0.10/MTok) so only the fresh frames pay full input price.

### 9.6 — The v0 loop (the whole thing)

```python
def run_v0(source: EpisodeSource, llm: LLMClient, recipe: Recipe, dispatcher: Dispatcher):
    schema = decision_schema(recipe.allowed_actions)
    for ep in source.episodes():
        content = build_prompt(ep, recipe)                    # raw frames + recipe question
        raw     = llm.generate(content, schema=schema)        # schema-guaranteed dict
        decision = Decision(**{k: raw.get(k) for k in
                               ("match","confidence","action","reason","message")})
        dispatcher.handle(decision, ep)                       # deterministic side effect
```

### 9.7 — Cost model & levers (Haiku, vision)

Image input ≈ **(W × H) / 750 tokens per frame** (approx; frames are downsized if large). Worked, Haiku $1/$5 per MTok:

| Frame res | ~tok/frame | 5-frame episode in (w/ ~450 tok instr) | ~$/call | Continuous @ 1 ep / 5 s |
|---|---|---|---|---|
| 640×480 | ~410 | ~2,500 | ~$0.003 | ~$2.4/hr |
| 320×240 | ~102 | ~960 | ~$0.001 | ~$0.7/hr |

Levers, in order of impact: **(1) resolution** (quadratic — default 320×240); **(2) gate before sending** (dumb motion-delta suppresses static episodes, ~90% cut); **(3) frames/episode**; **(4) prompt-cache** the stable system+recipe; **(5) Haiku** already chosen. Output is 5× input/token but the decision is tiny (~150 tok), so input dominates here. Numbers are estimates — measure real usage early (one of v0's jobs).

### 9.8 — Build order

1. `LLMClient` + `ClaudeClient` (forced tool-call) → prove schema-guaranteed dict from Haiku on a still image.
2. `WebcamEpisodeSource` + `EpisodeConfig` → prove capture at configurable fps/frames/res; save episodes to a folder.
3. `build_prompt` + `Decision` + `run_v0` with `dry_run=True` → prove recipe-conditioned verdicts on live episodes, printing decisions.
4. `DiscordSink` + flip `dry_run=False` → the ping.
5. `FolderEpisodeSource` + a tiny diff harness over saved episodes → the §7.7 regression corpus.

Each step is independently testable; the `Episode`/`Decision` seams are the same ones that grow into Part 5's `Event` and Part 6's sinks.

---

## OPEN QUESTIONS / NEXT DECISIONS

1. **Eye sync mechanism** — ESP-NOW link vs GPIO sync line? (Stage-0; gates fusion fidelity.)
2. **Default transport** — MQTT event bus assumed; confirm vs HTTP-only for v1 simplicity.
3. **Pre-roll** — is "what led up to the event" a v1 requirement? If yes, it sets the sleep floor and changes the Sentry promise.
4. **Per-eye modes** — allow the two eyes to run different modes, or keep mode per-device for v1?
5. **Mode promotion** — static presets in v1, recipe-driven mode state machine in v2?
6. **Host shape** — single daemon? What's the minimum host spec we target (mini-PC, Pi 5, existing HA box)?
7. **Local vs cloud VLM default** — ship pointing at local Ollama to honor the open/private thesis, with cloud as opt-in?
8. **Window length** — fixed ~2–5 s episode, or trigger-adaptive (close the window when the radar track ends)?
9. **Keyframe selection** — fixed-offset, "when radar says closest," or motion-peak across the burst?
10. **Recipe-query batching & caching** — how many targeted questions per VLM call, and do we cache answers for visually-near-identical frames to cap cost?
11. **DoA** — do any BOM sensors (mic array / multi-antenna radar) actually give bearing, or is "which eye fired first" our only directional signal for v1?
12. **Control-loop cadence** — how often does the peripheral model re-assess? And is it **two triggers** — *event-driven* (reflex layer escalates a batch) **and** *heartbeat* (periodic re-assess of an ongoing situation, e.g. "still the same loiterer, keep chilling") — or one mechanism? Likely recipe-dependent.
13. **RecipeState evidence form** (§8.3) — is fovea→peripheral confirmation memory a saved image, a structured doc, or both? Affects storage + how "same person / count" is represented.
14. **Cost-estimator fidelity** (§8.5) — what accuracy does the predicted-battery model need before override-with-live-feedback is trustworthy? How is it validated against measured draw?
15. *(Resolved)* **Mode vs recipe authority** — **hybrid**: mode sets the budget envelope, recipes bid within it (§8.7). Kept here as a recorded decision, not an open item.

---

*Next steps: (a) **build v0 per §9.8** — `LLMClient`/`ClaudeClient` (forced tool-call, Haiku) → webcam episodes → recipe-conditioned `run_v0` (dry-run) → Discord ping → folder-replay regression harness; (b) preserve the two non-negotiable seams (the `Event`/`Episode` object + recipe-conditioned querying) so v0 grows into Part 7 then Part 8 without rework; (c) Part 8 stays the saved target architecture; expand along the §8.9 seams only after v0 proves the recipe loop. Deferred: firmware state-machine, recipe-compiler spec, arbiter, cost estimator — all have a home above and none block v0.*
