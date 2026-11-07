# secdogie-policy

Signed, declarative **policy bundles**. They are how an operator "injects"
Socratic gate rules, a decision-model configuration, ingestion limits or an
allowlist change into a mesh (P1 in
[`docs/PLAN-STUDIO-INGEST.zh.md`](../docs/PLAN-STUDIO-INGEST.zh.md) §5).

Three rules hold for every bundle:

1. **Data, never code.** A bundle is canonical JSON with a closed schema per
   kind. Every node interprets it with its own fixed code, so the mesh can never
   carry someone else's program.
2. **Tighten only.** A number may be stricter than the built-in floor
   ([`floor.py`](secdogie_policy/floor.py)), never looser (`loosens`). A
   "switch it on" field accepts only `true`. Lists only add. Gate 2, HITL for
   high-risk actions, the capability allowlist and the forbidden list are in no
   schema at all, so no bundle can name them (`unknown-field`).
3. **Signers fixed per kind.**

   | Kind | Signed by |
   | --- | --- |
   | `gate-rules`, `ingest` | one operator DID |
   | `decision-model`, `allowlist` | `threshold` distinct masters (the k-of-n `MasterSet` that signs revocations) |

## Shape

```json
{
  "type": "secdogie/policy-bundle/v1",
  "kind": "gate-rules",
  "mesh": "home-lab",
  "version": 2,
  "supersedes": "<bundle_id of version 1>",
  "scope": {"nodes": [], "roles": ["serve"]},
  "not_before": 1767225600,
  "body": {"instruction": {"max_len": 400}, "plan": {"require_intent": true}},
  "bundle_id": "<sha256 of the canonical bundle without bundle_id and sigs>",
  "sigs": [{"signer": "did:key:...", "sig": "<base64 Ed25519>"}]
}
```

- Integers only: no floats anywhere, so Python and TypeScript cannot disagree
  about how a number is encoded.
- Lists of strings are sorted by code point and duplicate-free, so a bundle has
  one canonical form.
- Phrases are matched as substrings of normalized text: NFKC, ASCII-only lower
  case, and single spaces with none at the ends ([`text.py`](secdogie_policy/text.py)).
  v1 has no regular expressions.
- Versions form a linear chain per (kind, mesh, scope). `check_successor` checks
  that a bundle is the next version and names its predecessor. Rolling back means
  publishing the old content as the next version, and an empty `gate-rules` body
  means "back to the built-ins".

## Use

```python
from secdogie_identity import Allowlist, MasterSet
from secdogie_policy import create_bundle, sign_bundle, verify_bundle

b = create_bundle("gate-rules", mesh="home-lab", version=1,
                  body={"instruction": {"max_len": 400}})   # raises on any error
b = sign_bundle(operator_identity, b)
v = verify_bundle(b, operators=Allowlist({operator_identity.did}), masters=None)
v.ok, v.errors, v.signers
```

`verify_bundle` returns every error, each `"<code>:<path>"`, sorted. A missing
trust set authorizes nothing, and `ALLOW_ANY` is refused outright.

## Cross-language

[`symbiont/src/policy/bundle.ts`](../symbiont/src/policy/bundle.ts) is the
browser runtime's copy of the validator. `fixtures/vectors/policy.json` holds
more than 80 cases built and signed by this package. For every case the
TypeScript side must return the same `ok`, the same error list and the same
signers. It must also produce the same normal form for every phrase, the same
chain errors and the same floor. Regenerate with
`python fixtures/vectors/generate.py`.

## Tests

```sh
pip install -e ../identity -e . pytest
pytest tests -q
```

`test_floor.py` fails if a floor value drifts from the constant it mirrors in
`citadel/` or `symbiont/src/sandbox/policy.ts`.

## Not yet (later slices)

- **P2.** Distribution and activation: bundles as journal events, and the active
  digest in heartbeats.
- **P3.** Gate 1 and the plan gate loading `gate-rules`.
- **P4.** The decision-model rollout: shadow, canary, full.
- **P5.** Applying `allowlist` bundles.
