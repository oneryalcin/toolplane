# Persistence follow-up: results

## Hypotheses

| id | point | 95% CI | verdict |
|---|---|---|---|
| P1 peak context, tp - direct (turns 1-5) | -16745.5000 | [-17531.0000, -16330.0000] | HOLDS |
| P2 session cost, tp - direct (turns 1-5) | -0.0783 | [-0.0820, -0.0734] | HOLDS |
| P3 reuse cost, tp - direct (turns 2-5; equivalence +/-10%) | +0.0086 | [+0.0077, +0.0102] | REVERSED |
| P4 reuse cost, tp - nosession (turns 2-5) | -0.0051 | [-0.0062, -0.0034] | HOLDS |
| P5 session cost, tp - nosession (turns 1-5) | -0.0084 | [-0.0119, -0.0032] | HOLDS |
| S1 single, tp - direct $/pass | -0.0026 | [-0.0133, +0.0030] | UNRESOLVED |
| S2 chain_prose, tp - direct $/pass | +0.0030 | [+0.0013, +0.0049] | HOLDS |

## Sessions per arm (reported, not tested)

| arm | passed 1-5 / sessions | errors | median cost 1-5 | median peak ctx | reset verified | compaction events | reuse mechanism (turns 2-5) |
|---|---|---|---|---|---|---|---|
| direct | 8/8 | 0 | 0.1948 | 44207.5000 | 8/8 | 0 | {'retained': 32} |
| toolplane | 8/8 | 0 | 0.1165 | 27462.0000 | 6/8 | 0 | {'retained': 32} |
| toolplane_nosession | 8/8 | 0 | 0.1249 | 28356.5000 | 0/8 | 0 | {'refetch': 32} |
