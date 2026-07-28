# GRAINLINE — Free Edition

**Stop guessing at sheet layouts. Nest your parts, measure your yield, and know
exactly how much material you're throwing away.**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-452%20passing-brightgreen.svg)](#testing)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](#)

GRAINLINE is a nesting and material-yield engine for sheet-goods job shops —
sign shops, laser and waterjet cutters, upholstery workrooms, and small sheet
metal fabricators.

It runs **entirely on your machine**. Your DXF files never leave the building.

---

## The problem

Material is 35–55% of cost of goods sold in a sheet-goods shop. Most shops lay
parts out by hand, dragging shapes around in Illustrator or their CAM software
until the sheet "looks full". Measured yield from manual nesting runs 60–72%.

Real nesting software exists, but it ships bundled inside $15,000–$40,000 CAM
seats. Between "free rectangular cut-list calculator" and "enterprise CAM" there
has been essentially nothing.

GRAINLINE Free is the honest middle: a real nesting engine, a real yield report,
and a number you can take to your material supplier.

---

## What the Free edition does

| | |
|---|---|
| **Import** | DXF (R12–R2018) and SVG. Arcs, splines and ellipses flattened to a tolerance you set. |
| **Repairs your files** | Rebuilds closed outlines from exploded `LINE`/`ARC` soup — the state most production DXFs are actually in. |
| **Nests** | Rectangular guillotine packing with 90° rotation. Edge-to-edge cuts only, which is exactly what a panel saw, shear or slitter can physically do. |
| **Respects your machine** | Kerf, part spacing, sheet margin, mill-edge trim, grain direction, material matching. |
| **Reports honestly** | Yield measured against **whole sheets consumed**, not against the area your parts happen to span. |
| **Exports** | DXF (layered `SHEET` / `USABLE` / `PARTS`), SVG at 1:1 mm scale, JSON, and a standalone HTML report. |
| **Two interfaces** | A scriptable CLI and a local web UI with drag-and-drop. |

### What it deliberately does *not* do

Guillotine nesting works on **bounding boxes**. Two L-brackets that would
interlock perfectly are, to this engine, two rectangles that cannot overlap.

That is a real limitation of the method, not a crippled feature. The free
edition tells you exactly how much material that limitation is costing you on
your own parts — see [Is Free enough for you?](#is-free-enough-for-you) below.

---

## Install

```bash
pip install grainline
```

With the local web interface:

```bash
pip install "grainline[web]"
```

Requires Python 3.11 or newer. See [SETUP.md](SETUP.md) for offline and
shop-floor installation, including air-gapped machines.

---

## 60-second start

```bash
# 1. Create an example job
grainline init my-job
cd my-job

# 2. Drop your DXF or SVG files into ./parts/, then check one imports cleanly
grainline inspect parts/bracket.dxf

# 3. Edit job.toml to match your sheet and your machine, then nest
grainline nest job.toml --out nest-output -f svg -f dxf -f html
```

Or run the web interface and drag files in:

```bash
grainline serve
# opens on http://127.0.0.1:8711
```

---

## What a job file looks like

```toml
name = "gate-panels-oct"

[config]
kerf = 0.2            # material your tool destroys, in mm
part_spacing = 2.0    # gap you want between parts, in mm
sheet_margin = 5.0    # clear border inside the sheet, in mm

[import]
exclude_layers = ["DIMENSIONS", "NOTES"]

[[stock]]
id = "alu-3mm-2440x1220"
width = 2440
height = 1220
cost = 180.0
material = "aluminium"

[[parts]]
id = "bracket"
file = "parts/bracket.dxf"
quantity = 12
material = "aluminium"
grain = "free"        # free | fixed | bidirectional
```

Full reference in [HOW-TO.md](HOW-TO.md).

---

## Kerf vs. spacing — the setting everyone gets wrong

These are **different quantities** and GRAINLINE keeps them separate:

- **Kerf** is what the tool *destroys*. A fibre laser is ~0.15 mm. A table saw
  blade is 3.2 mm.
- **Spacing** is the gap you *want* between parts, for heat, handling or
  tab-and-bridge.

The gap actually reserved is `max(kerf, spacing)`. If you set spacing below your
kerf, the blade would eat into the neighbouring part — so GRAINLINE uses the
larger of the two and tells you nothing further about it. Set both honestly.

---

## Is Free enough for you?

Every report includes an **irregularity index**: the area-weighted mean of each
part's true area divided by its bounding-box area.

```
  Parts fill only 43% of their bounding boxes. Irregular nesting could
  recover roughly 0.4 sheet(s) on this job. Estimated value: 72.00.
```

- **Index near 1.00** — your parts are essentially rectangular. Guillotine
  nesting is already near-optimal. **Free is the right tool and you should stop
  here.** The report will tell you so in as many words.
- **Index below 0.80** — a large fraction of every bounding box is air, and that
  air is being nested as if it were material.

That number is computed from *your* parts and you can verify it by hand on a
single drawing. We would rather tell you the truth and lose the upsell than
quote you a saving you can't reproduce.

---

## Upgrading

| | **Free** | **Premium** | **Pro** |
|---|:---:|:---:|:---:|
| DXF / SVG import, loop repair | ✅ | ✅ | ✅ |
| Rectangular guillotine nesting | ✅ | ✅ | ✅ |
| Kerf, spacing, margin, grain, materials | ✅ | ✅ | ✅ |
| DXF / SVG / JSON / HTML export | ✅ | ✅ | ✅ |
| CLI + local web UI | ✅ | ✅ | ✅ |
| **Irregular (no-fit polygon) nesting** | — | ✅ | ✅ |
| Free-angle rotation search | — | ✅ | ✅ |
| Mixed multi-stock optimisation | — | ✅ | ✅ |
| **Part-in-hole nesting + micro-joints** | — | ✅ | ✅ |
| **Remnant ledger** (offcuts tracked and reused) | — | — | ✅ |
| Cut-path sequencing & cycle time | — | — | ✅ |
| **True common-line cutting + bridges** | — | — | ✅ |
| **G-code post-processors** (GRBL, LinuxCNC, Mach3/4, plasma) | — | — | ✅ |
| **Yield analytics & material history** | — | — | ✅ |
| **Learned placement warm-start** | — | — | ✅ |
| Multi-machine scheduling | — | — | ✅ |
| Costed quote export (JSON / CSV) | — | — | ✅ |
| **REST API** for ERP / MRP | — | — | ✅ |

**Premium $79/mo** per seat · **Pro $249/mo** per shop (unlimited seats).
Annual billing available. Licences are verified **offline** — no phone-home, no
account, works on an air-gapped CNC network.

### What the paid tiers add, in one line each

- **Irregular nesting** — nests true outlines instead of bounding boxes, so
  parts interlock. 14–24 points of yield on non-rectangular work.
- **Part-in-hole nesting** — recovers the bore of rings, frames and flanges,
  leaving micro-joints so the nested part cannot drop into the machine bed.
- **Remnant ledger** — every offcut is tracked and fed back into later jobs.
  Compounds: each job makes the next one cheaper.
- **Common-line cutting** — one pass cuts two parts where they share an edge.
  Saves machine time rather than material, which is a second budget line.
- **G-code output** — post straight to the controller, removing the CAM step.
- **Yield analytics** — twelve months of material history, so you can see
  whether your yield is actually improving.
- **Learned warm-start** — trains on *your* completed nests, locally, and gets
  better the more you use it. Nothing is uploaded.

### Measured improvement

Free vs. Premium on the same parts and the same stock:

| Job | Free | Premium | Sheets |
|---|---:|---:|:---:|
| L-brackets | 23.7% | **47.3%** | 2 → 1 |
| Triangular gussets | 28.8% | **43.2%** | 3 → 2 |
| Mixed fabrication kit | 21.3% | **42.6%** | 2 → 1 |
| Rectangular plates | 64.2% | 64.2% | 2 → 2 |

That last row is the important one. On rectangles the two engines tie exactly,
because guillotine nesting is already optimal there. If your parts are
rectangles, **Premium will not help you** and this table is how you can tell.

Details: `manijose1919@gmail.com`

---

## Privacy

- No telemetry. No analytics. No crash reporting.
- No network access of any kind at runtime.
- The web interface binds to `127.0.0.1` by default and warns loudly if you
  bind it anywhere else.
- Uploaded files are parsed in memory and never written to a temporary
  directory.

---

## Testing

```bash
pip install "grainline[dev]"
pytest
```

This distribution ships **257** tests covering the Free core. (The full product,
including the commercial modules, carries 452.)

The suite is property-based where it matters. The nesting engine is verified
with [Hypothesis](https://hypothesis.readthedocs.io/) against randomly generated
part sets, asserting invariants that examples miss:

- no two placed parts ever overlap;
- every part lies inside the usable sheet area;
- required clearance is always respected;
- every requested copy is either placed or explicitly reported as unplaced.

That last one matters: a nester that silently drops three parts and reports
success is how a job ships short.

---

## Documentation

- **[SETUP.md](SETUP.md)** — installation, offline install, shop-floor deployment
- **[HOW-TO.md](HOW-TO.md)** — job files, importing, tuning, troubleshooting

---

## Licence

MIT. See [LICENSE](LICENSE).

The Premium and Pro modules are **not** included in this distribution and are
licensed separately.
