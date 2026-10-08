# Phase <N> spec: <title>

Goal: <one or two sentences: what a user can do at the end of this phase>.

## Tasks and file ownership
| task | model(s) | edits only | notes |
|---|---|---|---|
| tests | <author> | tests/<new>.py, CONTRACTS.md, <config/protocol files> | tests first; the new tests fail until implemented |
| <core> | bake-off: <m1>, <m2>, <m3> | <files> | the winner goes forward |
| <ui> | <m> | <files> | play-tested with screenshot.py |
| <tooling/docs> | <m> | <new files> | single author, one reviewer |

## Behaviour (exact; this is what the tests assert)
- <rule: inputs → outputs / state changes; what is ignored or refused>
- <limits, numbers, units, ordering; say what is left to the implementer>

## Acceptance targets (numbers, measured by a script or test)
- <for example "the idle player loses every level on every seed", "p95 step < 5 ms at 4000 units">

## Gates (pre-registered: fixed before any worker runs)
Commit this section with the spec, before tests are written or any model is run. A gate that is changed after
results are seen is not a gate any more: record the change under Amendments.
- Pass/fail per acceptance target: <target, measurement command, threshold, denominator>
- Bake-off scoring rubric (0–100): <criteria and weights, for example correctness 50, spec compliance 30, clarity 20>
- Winner rule: <for example "highest score; ties go to the model with fewer failing review items">
- Controls: <negative cases or a forced control that must fail, if the target is a "catches X" claim>

## Amendments (dated, post-hoc, with direction)
Empty at the start of a phase. Each entry: date, what changed, why, and whether it makes the gate more or less
strict (say "conservative" or "relaxed"). A relaxed gate needs the user's approval before it is used.
- <YYYY-MM-DD: change, reason, conservative | relaxed>

## Interfaces / protocol changes
- <new fields, actions, function signatures, and their types>

## Out of scope
- <things the workers must not do this phase>
