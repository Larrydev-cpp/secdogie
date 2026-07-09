# secdogie-scene3d

Understand a 3D scene with a **team of models**: several workers each analyze
**one view** of the scene, then an **aggregator** fuses their observations into
a single consolidated 3D understanding — object inventory, spatial layout, and
the points where the views disagree.

```
 view: front  ─▶ worker ─┐
 view: top    ─▶ worker ─┤
 view: left45 ─▶ worker ─┼─▶ aggregator ─▶ one consolidated 3D scene (JSON)
 view: right  ─▶ worker ─┤
 ...          ─▶ worker ─┘
```

## Why multiple views (not multiple looks at one image)

A single 2D frame is missing depth — no model recovers exact 3D from it
reliably. The leverage comes from giving each worker a **genuinely different
view** of the *same* scene (front / top / left-45 / …). The aggregator then
triangulates: an object seen from two angles gets a real depth estimate; an
occlusion in one view is resolved by another.

> Nine models on nine copies of **one** image is nine times the cost for the
> same blind spot. Feed different angles, or split the job by aspect (one
> worker for object inventory, one for occlusion, one for relative position).
> If your target is a modeling app (Blender/CAD) with a scripting API, reading
> its scene graph is more reliable than any vision ensemble — see the note in
> the repo root.

## Concurrency and the key pool

Workers run **in parallel**. Pass `--api-key` several times to build a pool:
each worker is assigned a key round-robin, so nine concurrent views go out over
nine keys instead of stacking on one and tripping its rate limit. The
aggregator uses the first key.

## Install

**Linux/macOS:**
```sh
cd scene3d
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../agent      # shared config + JSON parsing live here
pip install -e .
pip install anthropic        # and/or: pip install 'secdogie-scene3d[openai]'
```

**Windows (PowerShell):**
```powershell
cd scene3d
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ..\agent
pip install -e .
pip install anthropic        # and/or: pip install "secdogie-scene3d[openai]"
```
(cmd: `.venv\Scripts\activate`. See `agent/README.md`'s Install section for
the PowerShell execution-policy note if `Activate.ps1` is blocked.)

### Or: a single-file executable (no Python needed)

```sh
./packaging/build.sh          # Linux/macOS -- produces packaging/dist/secdogie-scene3d
./packaging/dist/secdogie-scene3d --help
```

**Windows (PowerShell):**
```powershell
packaging\build.ps1          # produces packaging\dist\secdogie-scene3d.exe
.\packaging\dist\secdogie-scene3d.exe --help
```
(cmd.exe can't run `.ps1` files directly: `powershell -ExecutionPolicy Bypass -File packaging\build.ps1`.)

Bundles both the Anthropic and OpenAI adapters by default; edit
`packaging/build.sh` (or `packaging\build.ps1` on Windows) to ship a smaller,
single-provider binary.

## Run

```sh
# label=path per view; give different angles of the SAME scene
secdogie-scene3d front=front.png top=top.png left=left45.png right=right.png

# spread the workers across a pool of keys (round-robin), one aggregator
secdogie-scene3d v1=a.png v2=b.png ... \
  --api-key sk-1 --api-key sk-2 --api-key sk-3 --api-key sk-4 \
  --api-key sk-5 --api-key sk-6 --api-key sk-7 --api-key sk-8 --api-key sk-9

# a cheaper/faster model for the views, a stronger one to aggregate
secdogie-scene3d front=front.png top=top.png --model gpt-5.5 --aggregator-model claude-sonnet-5
```

It prints JSON: the consolidated `scene` plus each view's `observation` (and
any per-view `error`). Provider/model/key resolution is shared with
`secdogie-agent` (`--model`, `--provider`, `--config`, env vars).

## High-resolution tiled perception (`secdogie-perceive`)

Same "team of models" idea, but for a **different decomposition**: instead of
different *views* of a 3D scene, split **one high-resolution 2D screenshot** into
a grid of **tiles** and read each at native resolution.

```
 2160×1440 frame
   ├─ tile (0,0) ─▶ worker ─┐
   ├─ tile (1,0) ─▶ worker ─┤   translate each tile's boxes
   ├─ tile (2,0) ─▶ worker ─┼─▶ back to full-image coords ─▶ one element map (JSON)
   ├─ ...        ─▶ worker ─┤   + merge the overlaps
   └─ tile (2,2) ─▶ worker ─┘
```

**Why.** A vision model downscales a big screenshot before it reasons (the agent
caps the long edge at ~1568px). On a 2160×1440 screen that shrinks small
controls — a timeline clip, a toolbar icon in a video editor — below what the
model can read, so it mis-locates them and the agent mis-clicks. Cutting the
frame into a 3×3 grid makes each tile ~720×480, sent at **native** resolution,
so fine detail survives. The nine tiles are nine genuinely-parallel requests, so
the key pool pays off here just like the multi-view case.

The "combine" is **geometric**, not another model call: each worker reports boxes
in its own tile's pixels, those are translated to full-image coordinates, and
boxes that two overlapping tiles both reported are merged (by IoU). A slight
overlap between tiles keeps an element that straddles a seam whole in at least
one tile.

```sh
# read a big screenshot; spread the 9 tiles over a key pool
secdogie-perceive screen.png \
  --api-key sk-1 --api-key sk-2 --api-key sk-3
# tune the grid / overlap
secdogie-perceive screen.png --cols 4 --rows 3 --overlap 0.15
```

It prints JSON: `screen` (width/height), and `elements` — each with a `label`,
`type`, full-image `box` and `center`, `confidence`, and which `tiles` saw it.
That element map is exactly what an agent needs to click a small control by
position instead of guessing pixels on a downscaled frame.

## Layout

```
secdogie_scene3d/
  model.py       SceneModel interface + Anthropic/OpenAI adapters + key-pool builder
  views.py       Viewpoint (label + image + camera hint) and loaders
  analyze.py     stage 1: workers analyze each view concurrently (round-robin over the pool)
  aggregate.py   stage 2: the aggregator fuses per-view observations (text-only)
  pipeline.py    analyze_scene: workers -> aggregator
  cli.py         argument parsing + JSON output (secdogie-scene3d)
  tiles.py       pure geometry: plan a tile grid, translate tile->global boxes, merge overlaps
  perceive.py    tiled perception: crop tiles, analyze each concurrently, stitch one element map
  perceive_cli.py  argument parsing + JSON output (secdogie-perceive)
tests/           fake-model tests (no API key or network needed)
```

## As a library

```python
from secdogie_scene3d.model import build_model_pool, make_scene_model
from secdogie_scene3d.views import load_viewpoint
from secdogie_scene3d.pipeline import analyze_scene

pool = build_model_pool("anthropic", "claude-sonnet-5", ["sk-1", "sk-2", "sk-3"])
views = [load_viewpoint("front.png", "front"), load_viewpoint("top.png", "top")]
result = analyze_scene(pool, pool[0], views)
print(result.data)              # consolidated scene
for obs in result.observations: # per-view detail
    print(obs.label, obs.data or obs.error)
```

And the tiled high-resolution reader:

```python
from secdogie_scene3d.model import build_model_pool
from secdogie_scene3d.perceive import perceive_screen

pool = build_model_pool("anthropic", "claude-sonnet-5", ["sk-1", "sk-2", "sk-3"])
perc = perceive_screen(open("screen.png", "rb").read(), pool, cols=3, rows=3)
for e in perc.elements:         # full-image coordinates, ready to click
    print(e.label, e.center, e.confidence)
```
