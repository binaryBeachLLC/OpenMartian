# Hardware / BOM

`BOM.csv` is the working bill of materials. Opens in Sheets/Excel; tracked in git so changes are diffable.

**Before starting KiCad/FreeCAD, read [PRE-BUILD.md](PRE-BUILD.md)** — PCB architecture decision, XIAO pin budget, compatibility fixes, and the risks to breadboard-validate first.

**Planning the build?** See [ROADMAP.md](ROADMAP.md) for the phased plan (breadboard → carrier PCB → product PCB → pilot) and [PHASE-0.md](PHASE-0.md) for the buy-to-learn shopping list, test-gear list, and the bring-up/validation procedure to run when parts arrive.

## Columns

| Column | Meaning |
|---|---|
| Ref | Line ID |
| Subsystem | MCU, Cameras, Motion, Power-*, Mech-*, Indicators, Wiring, PCB |
| Component | What it is |
| Tier | `Proto` (breadboard), `Product` (shipped/PCB), `Both` |
| Qty | Per device |
| Manufacturer / MPN | Maker + manufacturer part number |
| Supplier | Where to buy — **Hawaii-aware** (see below) |
| ProductURL / Datasheet / CAD | Links (`TBD` = still to fill, `N/A` = none / self-made) |
| UnitPriceUSD / ExtPriceUSD | Unit and qty×unit (USD, ~2026-06) |
| WeightG | Grams; `~` = approximate, `TBD-measure` = unpublished |
| Status | `Locked` / `Open` / `Candidate` — doubles as decision tracker |
| AltSource | Backup part / second supplier |
| Notes | Specs, caveats, sourcing flags |

## Sourcing strategy (ships to Hawaii, cheap + quick)

- **DigiKey** for ICs/passives (WROOM module, BQ24074, regulator IC, AO3401A MOSFET, EG1218 switch, holder) — reliably ships HI; consolidate into **one order** to save shipping.
- **Amazon** (Prime reaches HI) for commodity/mechanical: XIAO, Arducam (resold), PIR, goosenecks, heat-set inserts, wire.
- **Avoid China-direct when speed matters** (Seeed/Arducam direct are cheaper but slow to HI) — use the Amazon/distributor reseller of the same MPN.
- ⚠️ **18650 lithium cell:** air-transport hazmat rules often block/surcharge shipping to HI. **Verify the retailer ships cells to Hawaii before ordering, or buy the cell locally.**

## Key open specs (gate downstream design)

1. **Gooseneck inner bore** — only the PATIKIL Armored Loop (8mm) publishes a verified bore. The threaded 6" options (3/8"-16, M10) don't list it. **Buy one and measure** before committing — bore gates the eyeball CAD and wiring.
2. **Gooseneck-to-eyeball joint** — heat-set inserts top out ~M6/M8; a 3/8"-16 or M10 gooseneck end needs a **captive nut / tapped boss**, not a heat-set insert.
3. **Component weights** — Arducam Mega, XIAO, WROOM, and the holder don't publish per-unit weight; marked `TBD-measure`.
4. **Mega board dims** — 33×33×17 mm; size the eyeball shell to this.
