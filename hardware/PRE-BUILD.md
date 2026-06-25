# Pre-Build Checklist — read before KiCad / FreeCAD

The BOM specs purchasable parts. This doc captures the **decisions, compatibility checks, and risks** to resolve before committing to PCB layout or enclosure CAD. Findings verified 2026-06 against vendor datasheets.

## PCB architecture — decide first

| Path | What it is | Use when |
|---|---|---|
| **A: XIAO carrier** (recommended for proto) | XIAO ESP32S3 module on a simple 2-layer carrier + TP4056 + camera/PIR connectors + LED + button + load switch. XIAO brings its own regulator + USB. | Validate the concept cheaply. Start here. |
| **B: Integrated** (product) | ESP32-S3-WROOM-1 + BQ24074 + TPS63021 + all passives + native USB-C + auto-program. | After A proves the architecture. |

> Even path A **still needs the external TP4056** — the XIAO's onboard charger is only **50 mA** (~70 h for a 3400 mAh cell). Solder the cell to the XIAO BAT pads only if charge speed doesn't matter; otherwise charge through the TP4056.

## XIAO pin budget (11 GPIO — tight but fits)

| Function | Pad(s) | Constraint |
|---|---|---|
| SPI SCK / MISO / MOSI (shared by both cameras) | D8 / D9 / D10 | — |
| Camera A CS, Camera B CS | D6, D7 | OK on D6/D7 (GPIO43/44 — not RTC, fine for CS) |
| PIR wake (1–2) | D0–D5 | **MUST be RTC-capable** (D0–D5, D8–D10); never D6/D7 |
| Camera power-gate | one of D0–D5 | — |
| Status LED (WS2812B = 1 pin for all states) | one free pad | — |
| Button | one free pad | — |

Two PIRs + everything else ≈ 10 of 11 pads. If you run out, drop to one body PIR or move status to the WS2812B (one pin, all colors). Path B (WROOM, ~36 GPIO) removes this constraint.

## 🔴 Top risks — validate on breadboard BEFORE designing the PCB

1. **Two Megas on one SPI bus.** Arducam only documents multi-camera via their 4-cam adapter. Sharing MISO requires each Mega to **tri-state MISO when deselected** — undocumented. This is the core assumption of the one-MCU design. **Breadboard two cameras + capture from both before laying out a PCB.** If it fails, the architecture changes.
2. **Camera connector pitch is unpublished.** Don't draw the PCB camera-connector footprint until you **measure a physical Mega's connector** (fine-pitch keyed wire-to-board, not 2.54 mm).
3. **Cable length.** Stock cable is 300 mm; a 150 mm gooseneck + service loops needs ~250–350 mm. Plan a custom/extension cable, and watch SPI signal integrity at 8 MHz over that length (twist GND with SCK/data, or lower SCLK).

## Compatibility fixes already folded into the BOM
- **Camera load switch** (Ref 11): a bare P-FET can't be GPIO-driven on the 4.2 V rail — use the **TPS22918 load-switch IC**, or P-FET + N-FET + 2 resistors (Ref 28).
- **TPS63021** (Ref 26): requires a specific **1.5 µH, ≥5.4 A** power inductor + input/output caps — easy to forget.
- **BQ24074**: needs ISET/ILIM/TMR programming resistors + a 10 kΩ on TS (no thermistor) — see datasheet typical-app.
- **WROOM-1**: EN 10 kΩ + 1 µF RC, GPIO0 10 kΩ pull-up, 3V3 decoupling. Crystal is **integrated** (none external). Flash via **native USB** (GPIO19/20 → USB-C) — no UART bridge / auto-reset transistors needed.

## FreeCAD readiness (enclosure)
- **Body** — can start now (houses XIAO/battery/PIR; needs a PIR Fresnel window, USB-C access, 18650 bay).
- **Eyeball** — **buy a Mega + gooseneck first and measure.** The Mega has **no screw holes** (only a rear 1/4-20 boss), so design a **clamshell cradle** around the 33×33×17 mm body or a 1/4-20 post. Size the gooseneck socket to the **measured inner bore** (still unverified — the one spec nobody publishes).

## Buy-first list (to unblock CAD + the key risk test)
1. **2× Arducam Mega 5MP** — measure connector pitch + body, breadboard the dual-SPI test.
2. **1× gooseneck** (Awclub B0F1XQBB45) — measure inner bore + thread.
3. **1× XIAO ESP32S3** — breadboard host for the camera + PIR + power-gate validation.

Validate the dual-camera SPI bus with these three, then commit the PCB schematic and the eyeball CAD.
