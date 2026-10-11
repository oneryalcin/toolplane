# Store-teaching re-test: results

## Hypotheses

| id | point | 95% CI | verdict |
|---|---|---|---|
| T1 sessions-off reuse turns served by the result store (share; >0.5) | +0.0000 | [+0.0000, +0.0000] | REVERSED |
| T2 reuse cost, tp - nosession (turns 2-5; equivalence +/-10%) | -0.0037 | [-0.0039, -0.0033] | REVERSED (T1 did not hold: not a store comparison) |

## Sessions per arm (reported, not tested)

| arm | passed 1-5 / sessions | errors | median cost 1-5 | median reuse cost 2-5 | median peak ctx | reuse mechanism (turns 2-5) |
|---|---|---|---|---|---|---|
| toolplane | 8/8 | 0 | 0.1185 | 0.0317 | 27857.5000 | {'retained': 32} |
| toolplane_nosession | 8/8 | 0 | 0.1215 | 0.0354 | 27909.5000 | {'refetch': 32} |
