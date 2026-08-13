# CAD baseline scenarios (golden set)

These five scenarios are the measurable bar for commercial readiness.
They are written so a human can run them by hand with `--dry-run` first,
then with confirmation, and later so an automated runner can score them.

All scenarios assume:
- The target CAD / viewer window is already open and visible
- The agent is started with `--window "exact title"` (and require-focus once implemented)
- Per-step confirmation is on (no `--auto` unless explicitly testing the high-risk gate)
- Prefer `--desktop-ax` when the platform supports it

## 1. Zoom to fit / zoom extents

**Task**: "Zoom the current drawing to fit the whole model in the view"

**Expected behaviour**:
- Locate the Zoom Extents / Fit / Zoom All control (toolbar, ribbon, or shortcut)
- Activate it once
- Confirm the view changed (whole model visible)
- `done`

**Pass criteria**: single clear zoom action; no accidental pan or orbit that loses the model.

## 2. Read a dimension / measurement

**Task**: "Read the length of the highlighted dimension (or the selected edge) and report the value"

**Expected behaviour**:
- Identify the dimension text or the measurement readout
- Report the numeric value in the `done` summary (or via `ask_user` if ambiguous)
- No modification of the drawing

**Pass criteria**: correct value reported; zero mutating actions.

## 3. Export / print to PDF

**Task**: "Export the current drawing to a PDF on the Desktop named secdogie-export.pdf"

**Expected behaviour**:
- Open the export / print-to-PDF dialog
- Set filename and location
- Confirm the high-risk save / export step
- `done` with path confirmation

**Pass criteria**: file appears; high-risk confirmation was required; trace records the export.

## 4. Toggle a layer / visibility

**Task**: "Turn off the layer named 'Dimensions' (or the currently selected layer)"

**Expected behaviour**:
- Open layer manager or use layer control
- Toggle visibility of the named layer
- Confirm the view update
- `done`

**Pass criteria**: only the intended layer changes; no accidental freeze / lock / delete.

## 5. Save a copy (never overwrite original)

**Task**: "Save a copy of the current file as 'secdogie-copy.dwg' (or equivalent) next to the original"

**Expected behaviour**:
- Use Save As / Save Copy, never plain Ctrl+S on the original
- High-risk confirmation required for the write
- `done` with the new path

**Pass criteria**: original untouched; new file exists; forced confirmation fired.

---

## How to run (manual)

```sh
# 1. Preview only
secdogie-agent "Zoom the current drawing to fit" --window "Your CAD Title" --dry-run --desktop-ax

# 2. Real run with confirmation + audit trail
secdogie-agent "Zoom the current drawing to fit" --window "Your CAD Title" --desktop-ax --trace cad-run.jsonl
```

After require-focus lands, the same command with a wrong or missing window title will exit 7 instead of acting on the wrong app.
