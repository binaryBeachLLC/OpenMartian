# OpenMartian — Build Roadmap

Big-picture plan to take OpenMartian from a BOM to a shippable product, sequenced so each
phase kills the risks that would otherwise force expensive rework. Companion docs:
[BOM.csv](BOM.csv) · [PRE-BUILD.md](PRE-BUILD.md) · [PHASE-0.md](PHASE-0.md)

## Core principle

**Do the cheap thing that can invalidate the expensive thing first.** The expensive things here
are (a) a PCB spin and (b) finished enclosure CAD. Both are gated by the same unknowns —
*does dual-Mega-on-one-SPI-bus actually work*, and *what are the real measured dimensions*
(camera connector pitch, gooseneck bore, board weights). So: **breadboard before routing copper,
and measure before drawing shells.**

Each phase has an explicit **gate** — don't start the next phase until the gate is met.

---

## Phase 0 — Buy-to-learn  ·  ~$190, 1–2 orders
Order only what answers a Phase 1 question. Full list + justifications in **[PHASE-0.md](PHASE-0.md)**.

- 2× Arducam Mega, 1× XIAO ESP32S3, 1× gooseneck, PIR, + power/validation glue + consumable kits.
- ⚠️ **Hawaii:** put the 18650 cell on its own / verify it ships to HI (air-hazmat) — don't let it block the order.

**Gate:** parts in hand + calipers on the bench.

---

## Phase 1 — De-risk the architecture on a breadboard  ·  the make-or-break phase
Answers everything in `PRE-BUILD.md` → "Top risks." Procedure lives in **[PHASE-0.md](PHASE-0.md)**.

1. **Dual-camera SPI bus** — both Megas on shared SCK/MISO/MOSI + per-cam CS. Capture from each, then
   both. Confirm the deselected Mega tri-states MISO. **If this fails, the one-MCU concept changes** —
   far cheaper to learn now than after a PCB.
2. **Measure** — camera connector pitch/keying, Mega body, gooseneck inner bore + thread, and weigh
   XIAO/Mega/holder (all the `TBD-measure` / "key open specs"). These gate both PCB footprints and eyeball CAD.
3. **Cable length / SI** — run camera signals over ~250–350 mm. Confirm 8 MHz survives or drop SCLK /
   twist GND with the clock.
4. **Power-gate + PIR wake** — prove the camera load switch + RTC-capable PIR wake from deep sleep;
   sanity-check sleep current vs. the 18650 runtime target.

**Gate:** both cameras capture reliably, all open dimensions measured, wake+gate works, sleep current acceptable.

---

## Phase 2 — Integrated breadboard / dev-board-on-protoboard  ·  cheap insurance
Prove the *whole system together* with real Proto-tier parts before committing to a PCB.

- Solder the proven circuit onto protoboard (XIAO + TP4056 + Pololu reg + load switch + connectors).
- **In parallel, start enclosure CAD:** Body **v0** can start now (XIAO/battery/PIR fit, PIR Fresnel window,
  USB-C access, 18650 bay). Eyeball **v0** clamshell once the Mega + bore are measured.

**Gate:** protoboard runs the full firmware loop on battery; v0 shells physically fit the real parts.

---

## Phase 3 — Carrier PCB v1  ·  BOM "Proto" → Path A
Copper is now low-risk because the circuit is proven.

- Route the **XIAO carrier** (Ref 24): XIAO + TP4056 + 2 camera connectors (real measured footprint) +
  PIR + load switch + LED + button. Order 5 from JLCPCB/PCBWay.
- Expect 1–2 issues on a v1 — capture them for v2. Drop the carrier into Body **v1**.

**Gate:** carrier boots, charges, captures, sleeps/wakes — all inside the v1 body.

---

## Phase 4 — Integrated product PCB v2  ·  BOM "Product" → Path B
Only worth doing once Path A proves the architecture end-to-end.

- ESP32-S3-WROOM-1 + BQ24074 power-path + TPS63021 (with the required 1.5 µH ≥5.4 A inductor) +
  native USB-C auto-program + passives at datasheet values.
- This is the "real product" board: GPIO headroom, power-path (runs while charging), single integrated unit.

**Gate:** functionally equal-or-better than the carrier, on the WROOM, self-contained.

---

## Phase 5 — Productize  ·  enclosure final + small pilot

- Enclosure **v2 / DFM**: refined form, real tolerances, heat-set inserts (or captive nut / tapped boss
  for the gooseneck joint), magnetic wall mount. SLA/MJF for small batch; molding only at real volume.
- Build a handful, give to beta users, gather field data (runtime, thermal near steam, mount reliability).
- Decide the manufacturing path (small-batch printed vs. molded) based on demand.

---

## Enclosure version ladder

| Ver | Purpose | Process | Looks like |
|---|---|---|---|
| **v0** | Fit-check measured parts | FDM PETG, ugly is fine | Functional clamshell cradle + battery box. No aesthetics. |
| **v1** | Form + ergonomics, holds carrier PCB | FDM, cleaner | Recognizable "eyeball + body" silhouette; iris LED, PIR window, USB located right. |
| **v2** | Design-for-manufacture, beta units | SLA/MJF (small batch) | Finished surface, real tolerances, hidden fasteners, magnetic mount. The photogenic one. |
| **Prod** | Scale | Molded (only at volume) | v2 form, tuned for draft / wall thickness / parting lines. |

**Rule of thumb:** never aesthetically refine a shell around dimensions you haven't measured or a PCB
you haven't validated. v0 exists precisely so v2 isn't a guess.

---

## Critical path at a glance

```
Phase 0 order ──▶ dual-SPI test ──┬──▶ PASS ──▶ protoboard ──▶ carrier PCB v1 ──▶ product PCB v2 ──▶ pilot
   (measure) ─────────────────────┤                │
                                  └──▶ FAIL ──▶ rethink one-MCU architecture (more MCUs / cam adapter)
   (measure) ──────────────────────────────────────┴──▶ Body v0 CAD (starts in parallel, no blockers)
```

The **dual-SPI test** is the gate everything hinges on. Run it the moment the two Megas land.
