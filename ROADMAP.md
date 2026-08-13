# secdogie commercial readiness roadmap (CAD-first)

**Product goal**: make secdogie reliable enough for daily internal use on
operator-owned machines for CAD viewing and light editing — not an
"experimental" label.

Constraints (non-negotiable):
- Operator-owned / explicitly authorized machines only
- Keep per-step confirmation by default
- Prefer Preview / dry-run first
- No independent security audit claimed
- Primary focus: CAD viewing / editing (games demoted)

## P0 — Baseline safety & measurability (done / in progress)

- [x] High-risk gate for `open` / `run_elevated` (still confirms under `--auto`)
- [x] Hash-chained audit trace (`--trace`)
- [x] Portable config next to the exe + first-run key dialog
- [x] GUI task examples + plan briefing
- [ ] **require-focus** abort (exit 7) when pinned window cannot be confirmed
- [ ] Expand high-risk to common save / delete / close key combos
- [ ] Golden scenarios documented and runnable

## P1 — CAD daily-driver reliability

- [ ] `--read-only` mode (blocks any mutating action: type, key that changes state, open that writes, etc.)
- [ ] Pre-edit confirmation prompt for any action that can modify a document
- [ ] Known-limitations section for CAD apps (focus, DPI, overlay dialogs)
- [ ] Compatibility matrix (AutoCAD / SolidWorks / FreeCAD / DraftSight / …)
- [ ] Menu / example tasks reordered with CAD recommended first
- [ ] Auto-enable `--trace` when running under `--gui` for internal audit

## P2 — Scenario fixtures & runner

- [ ] `fixtures/` with reference screenshots / expected outcomes for the 5 baseline scenarios
- [ ] Scenario runner that can replay or score a run against a golden set
- [ ] CI job that runs dry-run scenarios (no real mouse) and fails on regression

## P3 — Install & ops polish

- [ ] Single-file install path documentation (Windows first)
- [ ] Clear "supported vs experimental" labels in README and menu
- [ ] Versioned release notes that map to roadmap items
- [ ] Optional signed Windows binary path (future)

## Exit criteria for "daily internal use"

A run that:
1. Pins a known CAD window (`--window` + require-focus),
2. Completes one of the golden scenarios with per-step confirmation,
3. Leaves a verifiable `--trace` JSONL,
4. Never acts on the wrong window or silently skips a high-risk action,

is considered production-ready for the operator’s own machines.
