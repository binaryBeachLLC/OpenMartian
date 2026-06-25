# Phase 0 — Buy-to-learn + bring-up procedure

Everything you need to order *now*, why each item earns its place, the test gear to validate it,
and the step-by-step procedure to run the moment it all arrives. See [ROADMAP.md](ROADMAP.md) for
where this sits, and [PRE-BUILD.md](PRE-BUILD.md) for the risk rationale.

**Order principle:** buy only what answers a Phase 1 question. Nothing here is for the final product —
it's for *learning whether the design holds*.

---

## Shopping list

### Group A — the risk-test core (Phase 1 rides on these)

| Ref | Part | Qty | ~$ | Why now |
|---|---|---|---|---|
| 3 | Arducam Mega 5MP SPI | **2** | 69.98 | The dual-SPI test *is* the project's make-or-break. Need two to prove shared-bus capture. |
| 1 | XIAO ESP32S3 | 1 (+1 spare) | 7.12 | Breadboard host. A $7 spare is cheap insurance against a fried board mid-test. |
| 15 | Gooseneck (Awclub B0F1XQBB45) | **1** | 5.69 | Only need one to *measure* bore + thread (build qty is 2). Only ~4 in stock — grab it. |
| 4 | AM312 PIR (3-pack) | 1 | 3.00 | Validate RTC-capable wake-from-deep-sleep. |

### Group B — power / validation glue (run the rig on battery)

| Ref | Part | Qty | ~$ | Note |
|---|---|---|---|---|
| 7 | TP4056 USB-C charger | 1 | 1.25 | Charge the cell; XIAO's onboard charger is only 50 mA. |
| 9 | Pololu S7V8F3 buck-boost 3.3V | 1 | 9.95 | Breadboard-friendly module; product IC (TPS63021) is Phase 4. |
| 5 | 18650 cell (NCR18650B) | 1 | 13.99 | ⚠️ **Hawaii air-hazmat** — verify the retailer ships cells to HI, or buy local. Put on its own order so it can't block the rest. |
| 6 | 18650 holder — **wire-lead / thru-hole** | 1 | ~3 | **Not** the Keystone 1042 (SMT — useless on a breadboard). Any wire-lead holder. |
| 12 | EG1218 slide switch | 1 | 0.72 | True hard-off. |
| 13 | LED — grab a **WS2812B** too | few | ~1 | WS2812B = all status states on one GPIO; worth validating now. |
| 14 | Tactile button | 1 | 0.35 | Config / pairing input. |
| 11 | TPS22918 load switch | 1 | 0.65 | ⚠️ SOT-23-6 — **needs a breakout to breadboard**, or defer. The dual-SPI test doesn't need gating; validate the load switch later or buy a SOT-23-6 breakout. |

### Group C — consumable kits (amortize across the whole project)

| Ref | Part | ~$ |
|---|---|---|
| 21 | Silicone wire 26AWG 6-color | 15 |
| 22 | JST-SH 1.0mm pre-crimped set (camera side) | 13 |
| 23 | JST-PH 2.0mm kit + 0.1in headers | 18 |
| 25 | R/C 0603/0805 assortment | 15 |

### Group D — not in the BOM, but needed day one

- **Solderless breadboard (full+ size)** + **jumper kit** + **dupont leads** (M-M / M-F), ~$12 — the Mega's
  fine-pitch cable and the XIAO headers both need to reach breadboard rows.
- **Header pins for the XIAO** (often included — confirm). Must be soldered to breadboard it.
- **A real USB-C *data* cable** (not charge-only) for flashing the XIAO — classic time-waster.

**Order total ≈ $190.** Split per the sourcing strategy:
- **DigiKey** (one box, ships HI reliably): holder, slide switch, load switch, LED/button, passives.
- **Amazon** (Prime-quick to HI): XIAO, 2× Mega, PIR, gooseneck, wire, JST kits, breadboard/jumpers.
- **18650 cell**: separate, Hawaii-verified.

---

## Test equipment to validate Phase 0

Cheap gear mostly does the job. Two tools matter more than an expensive scope.

### The two that actually de-risk this
1. **Digital calipers** (~$15) — **the single most important Phase 0 tool.** Every blocking unknown is a
   measurement: camera connector pitch (~1 mm), gooseneck inner bore, Mega body, thread. Don't start CAD without them.
2. **Cheap USB logic analyzer** (8-ch "Saleae clone" ~$10 + free PulseView/sigrok) — **the hero for the
   dual-SPI test.** It's how you *see* whether the deselected Mega tri-states MISO and whether both cameras
   share the bus. Caveat: 24 MHz clones want ≥4× the signal, so they're marginal at 8 MHz SPI — **lower SCLK
   during bring-up** (you may need to anyway for cable SI), then push speed back up.

### Cheap stuff you likely have — fine for this
- **Multimeter** — continuity, rail voltages, charge current. Fine for everything except µA sleep current.
- **Temp-controlled soldering iron** — XIAO headers now; heat-set inserts later.
- **Adjustable, current-limited bench PSU** — power the rig safely *and* sweep 2.7→4.2 V into the buck-boost
  to confirm it holds 3.3 V across the full 18650 discharge range.
- **USB inline power meter** (~$15) — read TP4056 charge V/A and rough system draw.
- Helping hands / vise, flux, kapton tape — nice-to-have.

### Where cheap gear struggles — and the workaround
- **Deep-sleep current (µA).** Runtime math lives or dies here, and cheap multimeters are bad at it (poor µA
  resolution, burden voltage, range-switch dropouts that reset the MCU). Cheapest → best:
  - **1 Ω shunt + scope/meter across it** — crude ballpark.
  - **Nordic Power Profiler Kit II (~$100)** — the right tool for sleep-current / battery-life; the one
    upgrade genuinely worth considering for this project.
- **Signal integrity on the long camera cable (Phase 1 risk #3).** Seeing 8 MHz edge ringing wants ~100 MHz
  scope bandwidth — most cheap scopes fall short. **Workaround:** the logic analyzer confirms the *bits*
  arrive intact; if they don't, lower SCLK / twist GND with the clock rather than chasing waveforms.
  (If you ever buy a real scope, the used **Rigol DS1054Z** is the classic cheap-but-real pick.)

**Bottom line:** calipers + a $10 logic analyzer + your existing multimeter/iron/PSU validate all of
Phase 0/1 except precise sleep current — defer that or add the PPK2 at runtime-budgeting time.

---

## When it all arrives — bring-up procedure

Work top to bottom. Each step has an explicit **pass/fail** — log results in a notebook or a
`results` section here. Don't move to PCB/CAD until the Phase 1 gate (bottom) is green.

### Step 1 — Measure first (before any power)
Calipers + notebook. These numbers unblock CAD and PCB footprints.

- [ ] **Camera connector** — pitch, pin count, keying/orientation. (Risk: footprint is unpublished — do **not**
  draw the PCB camera connector until measured.)
- [ ] **Mega body** — confirm 33×33×17 mm; note lens barrel height + the rear 1/4-20 boss (no screw holes).
- [ ] **Gooseneck** — **inner bore** (the spec nobody publishes — gates eyeball CAD + wiring) and end thread (3/8-16? M10?).
- [ ] **Weigh** XIAO, Mega, holder, cell (all `TBD-measure`) — feeds the weight budget.

**Pass:** every value above recorded. Update the BOM `WeightG` / notes fields with measured numbers.

### Step 2 — XIAO alive + toolchain
- [ ] Solder headers, seat on breadboard, flash a blink/serial sketch over the **data** USB-C cable.

**Pass:** sketch uploads and serial prints. **Fail:** check cable is data-capable, board in bootloader mode.

### Step 3 — Single camera capture
- [ ] Wire **one** Mega: shared SPI (SCK=D8, MISO=D9, MOSI=D10) + its CS (D6) + 3V3 + GND. Run the Arducam
  Mega example; pull one JPEG over serial. Keep SCLK low for now (e.g. 4–8 MHz).

**Pass:** a valid, viewable JPEG from camera A. **Fail:** recheck CS, SPI mode 0, 3V3 rail, wiring.

### Step 4 — 🔴 The dual-SPI test (the make-or-break)
This is *the* gate. Reference `PRE-BUILD.md` risk #1.

- [ ] Add the **second** Mega: **same** SCK/MISO/MOSI bus, separate CS (cam A = D6, cam B = D7).
- [ ] Put the **logic analyzer** on SCK / MISO / MOSI / CS_A / CS_B.
- [ ] Capture from **A only**, then **B only**, then **both in one session** (A, then B, back to back).
- [ ] On the analyzer, verify when **CS_A is high (A deselected), MISO is released/tri-stated** so B can drive it
  — and vice-versa. Watch for bus contention (both driving MISO at once).

**Pass:** valid JPEGs from *both* cameras on the shared bus, MISO cleanly handed off per CS, no contention. The
one-MCU architecture holds → PCB is unblocked.

**Fail:** the deselected Mega does **not** release MISO (Arducam only documents multi-cam via their 4-cam
adapter). **Stop — do not lay out a PCB.** Architecture options: (a) Arducam multi-cam adapter, (b) second
MCU / second SPI peripheral, (c) MISO isolation (mux / bus switch). Re-evaluate the whole one-MCU premise.

### Step 5 — Cable length / SI
- [ ] Repeat Step 4 with the camera on a **~250–350 mm** cable (gooseneck run + service loop). Push SCLK up.

**Pass:** clean capture at target length + speed. **Fail:** lower SCLK, twist GND with SCK/data, or shorten run;
record the max reliable SCLK at length (feeds the cable spec).

### Step 6 — Power chain
- [ ] **Bench PSU sweep:** feed the buck-boost 2.7→4.2 V, confirm steady 3.3 V across the range (full 18650 discharge).
- [ ] **Charge:** TP4056 from USB-C into the cell via the wire-lead holder; verify charge V/A on the USB meter; confirm the EG1218 hard-off cuts power.

**Pass:** stable 3.3 V over the full Vin sweep; cell charges; hard-off works.

### Step 7 — PIR wake + (optional) power-gate
- [ ] AM312 on an **RTC-capable** pad (D0–D5; never D6/D7). Deep-sleep the XIAO, wave a hand, confirm wake.
- [ ] If you have the load-switch breakout: gate camera 3V3 from a GPIO, confirm cameras power off in sleep.
- [ ] **Sleep current:** measure with whatever you have (shunt/PPK2). Compare to the 18650 runtime target.

**Pass:** reliable PIR wake; (if tested) cameras gate off; sleep current within ballpark of the runtime goal.

---

## Phase 1 exit gate — all must be green before Phase 2/3

- [ ] **Step 4 PASS** — both cameras capture on the shared SPI bus, MISO handed off cleanly.
- [ ] **Step 1 complete** — connector pitch, gooseneck bore + thread, Mega body, weights all measured.
- [ ] **Step 5 PASS** — capture holds at the real cable length (max reliable SCLK recorded).
- [ ] **Step 6/7 PASS** — power chain stable across the discharge range; PIR wake works; sleep current acceptable.

Green → commit the carrier PCB schematic (Phase 3) and the eyeball CAD (Phase 2).
Red on Step 4 → architecture rework before *any* PCB.
