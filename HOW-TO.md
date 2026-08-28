# GRAINLINE Free — How-To

A working guide for the person actually cutting the sheet.

**Contents**
1. [Your first nest](#1-your-first-nest)
2. [The job file, setting by setting](#2-the-job-file-setting-by-setting)
3. [Getting your CAD files to import](#3-getting-your-cad-files-to-import)
4. [Kerf, spacing and margin](#4-kerf-spacing-and-margin)
5. [Grain direction](#5-grain-direction)
6. [Several materials in one job](#6-several-materials-in-one-job)
7. [Reading the report](#7-reading-the-report)
8. [Troubleshooting](#8-troubleshooting)
9. [Using GRAINLINE from a script](#9-using-grainline-from-a-script)
10. [Command reference](#10-command-reference)

---

## 1. Your first nest

```bash
grainline init gate-job
cd gate-job
```

That writes a commented `job.toml` and an empty `parts/` folder.

Copy your DXF or SVG files into `parts/`, then **check one imports before you
build a whole job around it**:

```bash
grainline inspect parts/bracket.dxf
```

```
parts/bracket.dxf
  unit mm (x1 to mm)   47 entities read
  1 closed ring(s)   0 unclosed run(s)   1 shape(s) built
  layers: 0, CUT, DIMENSIONS

    #   Size (mm)   Area (mm2)   Holes   Box fill   Points
    1   160.00 x 100.00      6900       0        43%       6
```

Read that carefully:

- **`1 shape(s) built`** — good. Zero means nothing imported.
- **`0 unclosed run(s)`** — good. Anything above zero means part of your outline
  didn't close, and that geometry was excluded.
- **`unit mm`** — confirm this matches reality. A part imported as inches when
  it was drawn in mm will be 25.4× too big and will fail to fit anything.
- **`Box fill 43%`** — this part uses 43% of its bounding box. Under guillotine
  nesting, the other 57% is nested as if it were solid material.

Now edit `job.toml` for your sheet and machine, and run:

```bash
grainline nest job.toml --out nest-output -f svg -f dxf -f html
```

Open `nest-output/sheet-001.svg` to see the layout, or `report.html` for the
numbers. `sheet-001.dxf` goes to your machine.

---

## 2. The job file, setting by setting

```toml
name = "gate-panels-oct"          # appears on reports and exported filenames

[config]
kerf = 0.2                        # mm the tool destroys
part_spacing = 2.0                # mm gap you want between parts
sheet_margin = 5.0                # mm clear border inside the usable sheet
allow_rotation = true             # try 90/180/270 as well as 0
rotations = [0, 90, 180, 270]     # angles to consider
strategy = "guillotine"           # Free edition has one engine
sort_key = "area"                 # area | longest_side | perimeter | height
material_thickness = 3.0          # mm; used to size micro-joints (Premium)
# allow_part_in_hole = false      # Premium only - see the note below
# micro_joint_width = 0.0         # Premium only; 0 derives it from thickness

[import]
chord_tolerance = 0.05            # mm; how finely arcs are flattened
weld_tolerance = 0.001            # mm; endpoint welding for exploded outlines
simplify_tolerance = 0.0          # mm; 0 keeps every vertex
min_area = 1.0                    # mm2; ignore marks smaller than this
min_hole_area = 0.0               # mm2; ignore holes smaller than this
exclude_layers = ["DIMENSIONS", "NOTES", "DEFPOINTS"]
# include_layers = ["CUT"]        # whitelist instead, if you prefer
# unit = "in"                     # force units, ignoring the file header

[[stock]]
id = "alu-3mm-2440x1220"
width = 2440
height = 1220
cost = 180.0                      # per whole sheet
material = "aluminium"
trim_margin = 0.0                 # unusable mill edge, per side
# quantity = 5                    # omit for unlimited

[[parts]]
id = "bracket"
file = "parts/bracket.dxf"
quantity = 12
material = "aluminium"
grain = "free"                    # free | fixed | bidirectional
allow_mirror = false              # may the part be flipped over?
priority = 0                      # higher goes on the sheet first
select = "largest"                # largest | all
```

### Settings worth understanding

**`sort_key`** — the order parts are placed in. `area` (default) places the
biggest parts while the sheet is still empty. Try `longest_side` if your job is
mostly long strips; it often does better.

**`priority`** — higher numbers get placed first, ahead of size. Use it when a
job may not fit and you know which customer is waiting.

**`select`** — what to do when one file contains several separate shapes.
`largest` takes only the biggest, which is right when your drawing includes a
border or title block. `all` nests every shape, naming them `bracket-1`,
`bracket-2` and so on — right when the file is a whole kit.

**`quantity` on stock** — leave it out if you can always buy more. Set it when
you have a fixed number on hand; GRAINLINE will stop and tell you exactly how
many parts it couldn't place.

**Stock order is preference order.** List offcuts first and they get consumed
before you open a fresh sheet.

### Settings this edition accepts but does not act on

`allow_part_in_hole` and `micro_joint_width` are accepted by the job schema so
that the same file works on every tier, but **the guillotine engine ignores
them**. Nesting a part inside another part's bore requires the irregular kernel,
which is a Premium capability.

They are validated rather than rejected on purpose: a shop running Free and Pro
seats side by side should be able to share one job file, and a schema that threw
an error here would force them to maintain two.

`material_thickness` is honoured everywhere, but in this edition it only affects
reporting.

---

## 3. Getting your CAD files to import

### The single most common problem

Your DXF looks like a clean part outline on screen, but it is actually 400
separate `LINE` and `ARC` entities. There is no closed polygon in the file at
all.

GRAINLINE handles this. It welds endpoints together and walks the segments until
they close, backtracking when a construction line leads it astray. This is why
`inspect` reports `closed ring(s)` separately from `entities read`.

If some runs won't close, raise the welding tolerance:

```bash
grainline inspect parts/messy.dxf --weld-tolerance 0.05
```

CAD exports written with few decimal places may need `0.05` or even `0.1`. Go up
gradually — too large and separate features start merging.

### Keep dimensions off your cut

Dimensions, hatching and notes will be nested as parts if they form closed
loops. Exclude their layers:

```toml
[import]
exclude_layers = ["DIMENSIONS", "NOTES", "DEFPOINTS", "TITLE"]
```

Or whitelist just your cut layer, which is more robust:

```toml
[import]
include_layers = ["CUT"]
```

### Units

GRAINLINE reads the DXF `$INSUNITS` header. Many exporters leave it unset, in
which case you'll see:

```
warning: DXF declares no usable unit ($INSUNITS=0); assuming mm
```

If that's wrong, force it:

```toml
[import]
unit = "in"
```

SVG is always interpreted at 96 dpi, which is what Illustrator and Inkscape
emit. An SVG whose root says `width="100mm"` imports as exactly 100 mm.

### Blocks

Block references (`INSERT`) are expanded automatically, including nested ones.
A file made entirely of block references imports correctly.

### Arcs and splines

`chord_tolerance` controls how finely curves are flattened. The default of
0.05 mm is finer than any sheet cutter can hold. Lower it only if you have a
specific reason; it increases vertex count and slows nesting.

If a part has hundreds of vertices and nests slowly, thin it out:

```toml
[import]
simplify_tolerance = 0.02   # mm of permitted deviation
```

The guarantee is on *deviation*: no point of the simplified outline strays
further than the tolerance from the original.

---

## 4. Kerf, spacing and margin

These three are constantly confused. They are different things.

```
   ┌─────────────────────────────────────────┐  ← sheet edge
   │ ← trim_margin (unusable mill edge)      │
   │  ┌───────────────────────────────────┐  │
   │  │ ← sheet_margin (clear border)     │  │
   │  │   ┌──────┐        ┌──────┐        │  │
   │  │   │ part │←gap → │ part │        │  │
   │  │   └──────┘        └──────┘        │  │
   │  └───────────────────────────────────┘  │
   └─────────────────────────────────────────┘
```

- **`kerf`** — what the tool destroys. Fibre laser ~0.15 mm, plasma 1.5–3 mm,
  table saw 3.2 mm, router = your bit diameter.
- **`part_spacing`** — the gap you *want*, for heat, for handling, for tabs.
- **The gap actually used is `max(kerf, part_spacing)`.** If you set spacing
  below kerf, the tool would eat into the neighbour, so GRAINLINE uses the
  larger.
- **`sheet_margin`** — clear border inside the usable area. Keeps parts off
  clamps and away from the edge of the bed.
- **`trim_margin`** (on stock) — the mill edge that is out of square or damaged.
  Excluded from nesting *and* from any offcut.

**Yield is always measured against the full sheet you paid for**, including trim
and margin. A 2440×1220 sheet counts as 2440×1220 whether or not you can use the
outer 20 mm. Any other convention would flatter the software at your expense.

---

## 5. Grain direction

Brushed aluminium, wood veneer, printed vinyl and directional fabric all have a
visible direction. Nesting a part 90° off ruins it even though the geometry fits.

```toml
grain = "free"            # rotate freely. Plain acrylic, mild steel, MDF.
grain = "fixed"           # no rotation at all. Exactly as drawn.
grain = "bidirectional"   # 0 or 180 only. Correct for most veneer and brushed metal.
```

Grain **outranks yield**. GRAINLINE will use more sheets rather than rotate a
`fixed` part.

`allow_mirror` is separate: it controls whether a part may be flipped over. Safe
on plain stock with no face, wrong on anything laminated, printed or one-sided.

---

## 6. Several materials in one job

Give parts and stock matching `material` keys:

```toml
[[stock]]
id = "alu-3mm"
width = 2440
height = 1220
material = "aluminium"

[[stock]]
id = "acrylic-5mm"
width = 2000
height = 1000
material = "acrylic"

[[parts]]
id = "bracket"
file = "parts/bracket.dxf"
quantity = 8
material = "aluminium"

[[parts]]
id = "window"
file = "parts/window.dxf"
quantity = 4
material = "acrylic"
```

Parts only nest onto stock whose material matches. An empty material on either
side is a wildcard, so a simple single-material job needs no configuration at
all.

---

## 7. Reading the report

```
Nest: gate-panels-oct

  Sheet  Stock                 Parts   Yield
      1  alu-3mm-2440x1220        14   78.2%
      2  alu-3mm-2440x1220         6   31.4%

  Overall yield  61.3%   sheets 2   placed 20
  Material cost  360.00   wasted value 139.32
```

**Overall yield** is placed part area ÷ total area of sheets consumed. This is
the number that matters and the one to track month over month.

**A low yield on the last sheet is normal.** Sheet 2 above is 31% because it
only holds the six parts that didn't fit on sheet 1. That's the tail of the job,
not a failure.

**`wasted value`** is what the material you're throwing away cost you.

### Unplaced parts

```
  UNPLACED PARTS
    bracket: 3
```

Three copies could not be placed. Causes, in order of likelihood:

1. **`quantity` on your stock ran out.** Raise it or remove it.
2. **The part doesn't fit any sheet even alone.** Check units first — a part
   imported as inches instead of mm is 25.4× too big.
3. **Grain constraint blocks the only orientation that fits.**

`grainline nest` exits with code **2** when anything is unplaced, so a script
can catch it.

### The material analysis line

```
  Parts fill only 43% of their bounding boxes. Irregular nesting could
  recover roughly 0.4 sheet(s) on this job. Estimated value: 72.00.
```

Or, honestly, the opposite:

```
  Parts are close to rectangular (index 0.98); guillotine nesting is already
  near-optimal for this job.
```

If you see the second message, the Free edition is doing everything the Premium
engine could. Don't upgrade.

---

## 8. Troubleshooting

### "no cuttable geometry found"

Run `grainline inspect` on the file. Check in order:

1. Is your cut geometry on a layer you excluded?
2. Are the outlines actually closed? Look at `unclosed run(s)`.
3. Is `min_area` filtering everything out because the units are wrong?

### "N segment run(s) could not be closed into a loop"

Raise `weld_tolerance` — try `0.01`, then `0.05`, then `0.1`. If it still fails,
open the file in your CAD tool and check for genuine gaps in the outline.

### Parts import at the wrong size

Units. Set `unit` explicitly under `[import]`. Verify with `grainline inspect`:
a part you know is 160 mm wide must report `160.00`.

### Nesting is slow

- Set `simplify_tolerance = 0.02` to thin dense flattened curves.
- Set `min_hole_area` to drop sub-millimetre holes that are flattening noise.
- Reduce `rotations` to `[0, 90]` if 180/270 are equivalent for your parts.

### Yield looks worse than my manual layout

Two likely causes:

1. **Your parts interlock.** Guillotine nesting works on bounding boxes and
   cannot do this. Check the irregularity index in the report.
2. **Your spacing is too generous.** Every millimetre of gap costs yield.
   Compare against what your machine actually needs.

### The nest overlaps parts

It doesn't. This is verified by property-based tests against randomly generated
geometry on every release. If you believe you've found a case, please send the
job file — that would be a serious bug and we want it.

What you may be seeing is `kerf` being wider than you set `part_spacing`, so
parts sit closer than you expected. The reserved gap is `max(kerf, spacing)`.

---

## 9. Using GRAINLINE from a script

```bash
grainline nest job.toml --out out --json -f json
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Every part placed |
| `1` | The command failed — bad file, missing geometry, bad settings |
| `2` | The nest ran, but some parts could not be placed |

```bash
#!/usr/bin/env bash
set -euo pipefail

grainline nest job.toml --out out -f dxf -f json --quiet
status=$?

if [ $status -eq 2 ]; then
    echo "WARNING: job is short. See out/report.json" >&2
elif [ $status -ne 0 ]; then
    echo "ERROR: nesting failed" >&2
    exit 1
fi

yield=$(python -c "import json;print(json.load(open('out/report.json'))['summary']['yield_percent'])")
echo "Yield: ${yield}%"
```

### As a library

```python
from grainline.core.config import load_job
from grainline.core.nesting import nest
from grainline.core.report import build_report

job = load_job("job.toml")
result = nest(job)

print(f"{result.yield_percent:.1f}% over {result.sheets_used} sheet(s)")
for part_id, count in result.unplaced.items():
    print(f"  unplaced: {part_id} x{count}")

report = build_report(result, job)   # JSON-serialisable
```

---

## 10. Command reference

```
grainline init [DIR] [--force]
    Write a commented example job file and a parts/ folder.

grainline inspect FILE
    Import a DXF or SVG and report what was found.
      --chord-tolerance FLOAT   arc flattening accuracy, mm (default 0.05)
      --weld-tolerance FLOAT    endpoint welding distance, mm (default 0.001)
      --min-area FLOAT          discard shapes below this, mm2 (default 1.0)
      --unit TEXT               override the file's units
      --exclude-layer TEXT      layer to ignore (repeatable)

grainline nest JOBFILE
    Nest a job and export the sheets.
      -o, --out DIR             output directory (default nest-output)
      -f, --format TEXT         svg | dxf | json | html (repeatable)
      -s, --strategy TEXT       override the job's strategy
      --spacing FLOAT           override part spacing, mm
      --kerf FLOAT              override kerf, mm
      --json                    print the machine-readable report to stdout
      -q, --quiet               suppress the banner

grainline strategies
    List nesting strategies and whether your licence covers them.

grainline serve [--host HOST] [--port PORT] [--allow-network]
    Start the local web interface (default 127.0.0.1:8711).
    Non-loopback binds require --allow-network.

grainline license show | set TOKEN | path
    Inspect or install a licence key.

grainline version
    Print the version and active edition.
```

---

Questions, bug reports, or a job file that nests badly:
**manijose1919@gmail.com**
