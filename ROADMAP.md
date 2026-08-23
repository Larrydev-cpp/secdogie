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

## P0 — Baseline safety & measurability (done)

- [x] High-risk gate for `open` / `run_elevated` (still confirms under `--auto`)
- [x] Hash-chained audit trace (`--trace`)
- [x] Portable config next to the exe + first-run key dialog
- [x] GUI task examples + plan briefing
- [x] **require-focus** abort (exit 7) when pinned window cannot be confirmed
- [x] Expand high-risk to common save / delete / close key combos (Ctrl+S, Delete, Alt+F4, Ctrl+W)
- [x] Golden scenarios documented (`docs/SCENARIOS.md`)

## P1 — CAD daily-driver reliability (done)

- [x] `--read-only` mode (blocks type/key/drag/open; clicks & scroll allowed for viewing)
- [x] Pre-edit confirmation prompt for any action that can modify a document
- [x] Known-limitations section for CAD apps (`docs/CAD.md`)
- [x] Compatibility matrix (AutoCAD / SolidWorks / FreeCAD / DraftSight / …) in `docs/CAD.md`
- [x] Menu / example tasks reordered with CAD recommended first
- [x] Auto-enable `--trace` when running under `--gui` for internal audit

## P2 — Scenario fixtures & runner (done)

- [x] `fixtures/` with expected outcomes for the 5 baseline scenarios (screenshots later)
- [x] Scenario runner that can score a `--trace` JSONL against a golden set (dry-run offline)
- [x] CI job that runs dry-run scenarios (no real mouse) and fails on regression

## P3 — Install & ops polish

- [ ] Single-file install path documentation (Windows first)
- [ ] Clear "supported vs experimental" labels in README and menu
- [ ] Versioned release notes that map to roadmap items
- [ ] Optional signed Windows binary path (future)

## P4 — Atlas hybrid loop (done)

Dual-tier perception for CAD daily-driver reliability, with an honest privilege
wall. See [`docs/ATLAS.md`](docs/ATLAS.md).

- [x] UIA / accessibility as primary targeting (`--desktop-ax`); vision is fallback
- [x] Pixel-diff verification on `click_element` (UIA Invoke) as well as pixel clicks
- [x] Read-only process-handle wall (`PROCESS_VM_READ | PROCESS_QUERY_*` only;
      `PROCESS_ALL_ACCESS` / write bits refused, not narrowed)
- [x] TrustedInstaller impersonation and anti-EDR documented as `refused-identity`
- [x] Native C++ twin (`native/atlas/`) with self-contained unit tests in CI
- [x] Cloudflare named-tunnel config + setup scripts (`tunnel/cloudflare/`)
- [x] Restore `loop.py` / `cli.py` / `osfocus.py` after accidental PLACEHOLDER overwrite
- [x] `--window` matching: exact → prefix → substring, prefer visible/non-minimized/larger

## Exit criteria for "daily internal use"

A run that:
1. Pins a known CAD window (`--window` + require-focus),
2. Completes one of the golden scenarios with per-step confirmation,
3. Leaves a verifiable `--trace` JSONL,
4. Never acts on the wrong window or silently skips a high-risk action,

is considered production-ready for the operator’s own machines.
