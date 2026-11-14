---
name: web-design-guidelines
description: Review UI code for Web Interface Guidelines compliance. Use when asked to "review my UI", "check accessibility", "audit design", "review UX", or "check my site against best practices".
metadata:
  author: vercel
  version: "1.0.0"
  argument-hint: <file-or-pattern>
---

# Web Interface Guidelines

Review files for compliance with Web Interface Guidelines.

## How It Works

1. Read the guidelines in `guidelines.md` next to this file
2. Read the specified files (or prompt user for files/pattern)
3. Check against all rules in the guidelines
4. Output findings in the terse `file:line` format

## Guidelines Source

The rules and the output format are in `guidelines.md` in this skill's folder
(`.claude/skills/web-design-guidelines/guidelines.md`): Vercel's
`command.md` from https://github.com/vercel-labs/web-interface-guidelines,
pinned at commit `434b7f91364665f2f733b310ec54809bf8f37937` (MIT, `LICENSE`).
This repository reads the pinned copy instead of fetching `main` before every
review, so the instructions are the reviewed ones and work offline. To refresh
them, see `.claude/VENDORED.md`.

## Usage

When a user provides a file or pattern argument:
1. Read `guidelines.md`
2. Read the specified files
3. Apply all rules from the guidelines
4. Output findings using the format specified in the guidelines

If no files specified, ask the user which files to review.
