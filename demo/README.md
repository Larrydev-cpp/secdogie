# secdogie-demo

One reproducible, headless run that chains all four pillars into a single flow, to
show the pieces actually fit together — not just that each passes its own tests.

Everything is real: DID identity, signed capability grants, the encrypted node
mesh, journal replication, content-addressed evidence, the Socratic step, the
structured (screenshot-free) agent loop, and signed run records. Only the two
things a headless box lacks are faked, exactly as `fleet`'s end-to-end test fakes
them: the **vision model** (a scripted provider) and the **browser** (a canned
page).

```
secdogie-demo          # run it and print the story + PASS/FAIL per stage
python -m secdogie_demo
```

The flow (all on 127.0.0.1): an operator and three encrypted nodes come up on the
allowlist → the operator signs a minimal capability grant to node A → A learns a
page into content-addressed evidence and a signed knowledge entry → the grant,
knowledge and evidence bytes converge to node C byte-for-byte → a goal runs on A
through the Supervisor (Socratic review/revise, capability enforcement — the
granted click runs, ungranted typing is refused, the real structural loop, a
signed run record) → the run record converges to C and its hash chain verifies.

Install `secdogie-demo[full]` (agent + desktop) so the real structural agent loop
is exercised; without them the loop stage falls back to a stand-in and the run
reports `real structural agent loop exercised: False`.
