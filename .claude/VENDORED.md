# Vendored Claude Code skills

Three third-party design skills are installed for this repository as project
skills. Claude Code loads `.claude/skills/*` and `.claude/agents/*` when a
session opens the repository in the CLI, the desktop app or a cloud session.
They are copied in, not linked: the version below is the one that was
reviewed, and it changes only when someone deliberately updates it.

| Skill | Upstream | Commit | Licence |
| --- | --- | --- | --- |
| `ui-ux-pro-max` (v2.15.0) | https://github.com/nextlevelbuilder/ui-ux-pro-max-skill, `.claude/skills/ui-ux-pro-max` | `b7d5353e0130feb8c5574ef1251f8a1db1873ccb` (2026-11-05) | MIT (`skills/ui-ux-pro-max/LICENSE`) |
| `web-design-guidelines` (Vercel, v1.0.0) | https://github.com/vercel-labs/agent-skills, `skills/web-design-guidelines` (MIT per its README), with the rules from https://github.com/vercel-labs/web-interface-guidelines `command.md` | skill `1a2c79d4e6c4a2283dc2c614b6538cbe1ed5ca62` (2026-10-27); rules `434b7f91364665f2f733b310ec54809bf8f37937` (2026-10-05) | MIT (`skills/web-design-guidelines/LICENSE`, from the rules repository) |
| `impeccable` (v4.8.0, engine 0.1.18) | https://github.com/pbakaus/impeccable, `plugin/skills/impeccable` and `plugin/agents/*` | `687bc381bc9091bf252c0a05303640561014a425` (2026-11-06) | Apache 2.0 (`skills/impeccable/LICENSE`, `NOTICE.md`) |

## What each one does

- **ui-ux-pro-max** is a local design knowledge base: styles, palettes, font
  pairings, UX guidelines, charts and stack notes, stored as CSV files and
  searched by `scripts/search.py`. The scripts use only the Python standard
  library. They make no network calls and start no other programs. They write
  files only with `--persist` (`design-system/<project>/MASTER.md` under
  `--output-dir`).
- **impeccable** is a design-direction skill with 24 commands (`/impeccable
  critique`, `audit`, `polish`, `layout`, `typeset` and others) and four
  subagents (`.claude/agents/impeccable-*.md`).
  - **Its engine.** Its launcher `scripts/impeccable` runs a native engine
    binary. On first use it downloads that binary from the project's own GitHub
    releases (`pbakaus/impeccable`, tag `engine-v0.1.18`) into
    `~/.impeccable/bin/` and checks it against the SHA-256 digests in
    `scripts/engine-checksums.txt`.
  - **Without the engine.** Without network access the skill falls back to
    reading `PRODUCT.md` / `DESIGN.md` directly.

- **web-design-guidelines** reviews UI code against Vercel's Web Interface
  Guidelines (accessibility, focus, forms, motion, typography, content,
  performance, theming, copy) and reports `file:line` findings.

Taste skill (https://github.com/Leonxlnx/taste-skill, v2.5, MIT) was **not**
vendored. It is 40 MB, mostly website imagery and effects, and it targets
company and marketing websites. Its principles were read from upstream commit
`9b4bf937a1f69348126959c15d2f99a73e65699b` as a reference for the Studio design
pass (`studio/DESIGN.md`).

## Local changes

- **ui-ux-pro-max.**
  - `SKILL.md`: the script path `${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max/scripts/search.py`
    (written for a plugin install) now reads `.claude/skills/ui-ux-pro-max/scripts/search.py`,
    run from the repository root.
  - `scripts/tests/` was left out.
- **impeccable.**
  - **Hooks.** Its automatic hooks are **not** installed. Upstream, they run the
    engine on session start, after every Edit/Write, and on Stop. Turn them on
    deliberately with `/impeccable hooks on` if you want the design detector to
    run on every UI edit.
  - **Other skills.** The other six skills in the ui-ux-pro-max repository
    (`ui-styling`, `design`, `design-system`, `brand`, `slides`, `banner-design`)
    were not installed.
- **web-design-guidelines.** Upstream's skill fetches `command.md` from the
  `main` branch before every review and follows it as instructions. Here
  `SKILL.md` reads a pinned copy, `guidelines.md`, instead. The rules are the
  reviewed ones and work offline. To refresh them, read the new `command.md`
  and copy it over.
- **Lint.** `.claude/` is excluded from this repository's ruff lint
  (`ruff.toml`): it is upstream code, kept as upstream wrote it.

## Update or remove

To update, clone the upstream at the new commit, read the diff (above all any
script the skill tells Claude to run, and any hook), copy the same paths over,
and update the table. To remove a skill, delete its directory under
`.claude/skills/`. For impeccable, also delete `.claude/agents/impeccable-*.md`.
