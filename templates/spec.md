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

## Interfaces / protocol changes
- <new fields, actions, function signatures, and their types>

## Out of scope
- <things the workers must not do this phase>
