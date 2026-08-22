# CAD use: known limitations & compatibility

secdogie drives real UI (screenshot → vision model → click/type). It is **not**
a native CAD plugin. Treat it as an operator assistant on machines you own.

## Recommended flags

```sh
# View / measure only (no type, save, delete, open)
secdogie-agent "Zoom the drawing to fit" \
  --window "Exact CAD window title" \
  --desktop-ax --read-only --gui

# Light edit with confirmation + audit trail (GUI auto-writes a trace file)
secdogie-agent "Export current drawing to Desktop as secdogie-export.pdf" \
  --window "Exact CAD window title" \
  --desktop-ax --gui
```

- `--window` pins focus (exact, then prefix, then substring; case-insensitive);
  **require-focus is auto-on** with `--window` (exit 7 if focus fails).
- `--desktop-ax` makes the accessibility tree the primary targeting path;
  a pixel-diff still verifies every mutating click (see [`ATLAS.md`](ATLAS.md)).
- `--read-only` blocks type / keys / drag / open / elevated; clicks and scroll stay allowed.
- High-risk keys (Ctrl+S, Delete, Alt+F4, Ctrl+W) and `open` still force confirmation under `--auto`.

## Known limitations

| Topic | Reality |
|-------|---------|
| **Focus** | Wayland often refuses programmatic focus. Prefer X11 or Windows. `--window` matches exact, then prefix, then substring (case-insensitive). |
| **DPI / scaling** | Windows display scaling is handled when the process opts into DPI awareness at startup. Mixed-DPI multi-monitor can still offset clicks. |
| **Overlay dialogs** | Modal CAD dialogs, licensing popups, or auto-save prompts can steal the frame the model sees. Dismiss them first or include them in the task text. |
| **Custom ribbons** | Heavily customized toolbars reduce accessibility-tree hit rate; `--desktop-ax` helps when the control exposes a name. |
| **3D orbit / space mouse** | Continuous 3D navigation is a poor fit for a ~1 Hz vision loop. Prefer discrete zoom extents / named views. |
| **Large assemblies** | Slow redraw → raise `--action-pause` (e.g. 0.8–1.2) so verification screenshots are not stale. |
| **File writes** | Never rely on plain Ctrl+S for “safe” automation. Prefer **Save As / copy** tasks; high-risk gate still prompts. |
| **No audit claim** | Hash-chained `--trace` is operator evidence, not a certified audit product. |

## Compatibility matrix (practical)

Statuses are **operator-reported / expected**, not certified. “Works” means: window
can be focused, core view commands are reachable by vision or AX, and
`--read-only` viewing is usable.

| App | Platform | View / zoom | Read dimension | Export PDF | Notes |
|-----|----------|-------------|----------------|------------|-------|
| **AutoCAD** | Windows | Good | Fair | Good | Exact window title; ribbon names help with `--desktop-ax`. |
| **DraftSight** | Windows | Good | Fair | Good | Similar to AutoCAD UI patterns. |
| **SolidWorks** | Windows | Fair | Fair | Fair | Heavy UI; prefer named commands / shortcuts in the task text. |
| **FreeCAD** | Win/Linux | Good | Fair | Fair | Open source; AX quality varies by desktop. |
| **LibreCAD** | Win/Linux | Good | Fair | Fair | Lighter UI, good for dry-run practice. |
| **PDF viewers** (drawing PDFs) | All | Good | Fair | n/a | Useful for measure-from-PDF workflows without editing CAD. |

Update this table as you validate more apps internally.

## Golden scenarios

See [SCENARIOS.md](SCENARIOS.md) for the five baseline tasks (zoom, read
dimension, export PDF, layer toggle, save-a-copy).
