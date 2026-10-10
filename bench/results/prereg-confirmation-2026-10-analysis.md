# Pre-registered confirmation matrix: results

## Hypotheses

| id | statistic | n (arm/direct) | point | 95% CI | verdict |
|---|---|---|---|---|---|
| H1a | single: tp - direct $/pass | 8/8 | +0.0005 | [+0.0003, +0.0008] | HOLDS |
| H1b | N=5: tp - direct $/pass | 8/8 | +0.0006 | [+0.0002, +0.0011] | HOLDS |
| H2a | N=100: tp - direct $/pass | 8/8 | -0.0059 | [-0.0065, -0.0053] | HOLDS |
| H2b | $/record slope over N=5..100: tp - direct |  | -0.0001 | [-0.0001, -0.0001] | HOLDS |
| H2c | direct $/record slope over N=5..100 |  | +0.0001 | [+0.0001, +0.0001] | HOLDS |
| H2 info | tp $/record slope (reported, not tested) |  | +0.0000 | [+0.0000, +0.0000] |  |
| H3 b=0 | fetch-one 0 B: tp - direct | 8/8 | -0.0012 | [-0.0015, -0.0009] | HOLDS |
| H3 b=2000 | fetch-one 2000 B: tp - direct | 8/8 | -inf | [-inf, -inf] | HOLDS |
| H3 b=20000 | fetch-one 20000 B: tp - direct | 4/4 | -0.3919 | [-0.4025, -0.3815] | HOLDS |
| H3 growth | gap at 20 KB minus gap at 0 |  | -0.3907 | [-0.4015, -0.3800] | HOLDS |
| H4 b=0 | bulk 0 B: tp - direct | 8/8 | +0.0011 | [+0.0007, +0.0015] | HOLDS |
| H4 b=2000 | bulk 2000 B: tp - direct | 8/8 | +0.0003 | [+0.0001, +0.0006] | HOLDS |
| H4 b=20000 | bulk 20000 B: tp - direct | 4/4 | +0.0022 | [+0.0000, +0.0045] | HOLDS |
| H5 chain | chain: tp - direct | 8/8 | +0.0007 | [+0.0006, +0.0009] | HOLDS |
| H5 chain_prose | chain_prose: tp - direct | 8/8 | +0.0008 | [+0.0005, +0.0011] | HOLDS |
| H6a | latency: tp - direct median wall (s) |  | -1.4500 | [-3.6000, +1.3500] | UNRESOLVED |
| H6b | latency: tp - direct $/pass (not costlier) | 8/8 | -0.0006 | [-0.0009, -0.0003] | HOLDS |
| H7a | refunds: tp_cli - direct | 8/8 | +0.0008 | [-0.0000, +0.0016] | UNRESOLVED |
| H7b | refunds: tp (shell) - direct | 8/8 | +0.0223 | [+0.0063, +inf] | HOLDS |
| H8 | direct $/pass at M=15 minus M=1 (equivalence +/-10%) |  | +0.0002 | [+0.0000, +0.0003] | REVERSED |
| O3 | fetch-one 20 KB, no built-ins: tp - direct | — | — | — | DROPPED (amendment A1) |
| S1 | Sonnet 5.5 single: tp - direct | 2/2 | +0.0043 | [+0.0021, +0.0065] | HOLDS |
| S2 | Sonnet 5.5 chain_prose: tp - direct | 2/2 | +0.0065 | [+0.0052, +0.0078] | HOLDS |

H5-walk (hop-by-hop runs / runs; HOLDS at >= 7/8 of runs per arm):

- chain direct: 8/8 HOLDS
- chain toolplane: 8/8 HOLDS
- chain_prose direct: 8/8 HOLDS
- chain_prose toolplane: 8/8 HOLDS

Timeout costs imputed at the cell max (runs per cell): {"claude-haiku-5-5/loop/M1/B2000/fetch-one/default/direct": 3}

## Cells

| model | task | M | B | G | built-ins | arm | ok | $/pass | median wall s | median reqs | abstained |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-haiku-5-5 | chain | 1 | 0 | fetch-one | default | direct | 8/8 | 0.0060 | 11.9 | 7.0 | 0 |
| claude-haiku-5-5 | chain | 1 | 0 | fetch-one | default | toolplane | 8/8 | 0.0067 | 13.3 | 7.0 | 0 |
| claude-haiku-5-5 | chain_prose | 1 | 0 | fetch-one | default | direct | 8/8 | 0.0065 | 13.8 | 8.0 | 0 |
| claude-haiku-5-5 | chain_prose | 1 | 0 | fetch-one | default | toolplane | 8/8 | 0.0073 | 16.0 | 8.0 | 0 |
| claude-haiku-5-5 | loop | 1 | 0 | bulk | default | direct | 8/8 | 0.0048 | 7.8 | 3.0 | 0 |
| claude-haiku-5-5 | loop | 1 | 0 | bulk | default | toolplane | 8/8 | 0.0060 | 10.8 | 5.5 | 0 |
| claude-haiku-5-5 | loop | 1 | 0 | fetch-one | default | direct | 8/8 | 0.0066 | 13.8 | 4.0 | 0 |
| claude-haiku-5-5 | loop | 1 | 0 | fetch-one | default | toolplane | 8/8 | 0.0054 | 9.8 | 4.0 | 0 |
| claude-haiku-5-5 | loop | 1 | 2000 | bulk | default | direct | 8/8 | 0.0062 | 13.1 | 5.0 | 0 |
| claude-haiku-5-5 | loop | 1 | 2000 | bulk | default | toolplane | 8/8 | 0.0065 | 11.3 | 5.0 | 0 |
| claude-haiku-5-5 | loop | 1 | 2000 | fetch-one | default | direct | 0/8 | inf | 748.4 | 5.0 | 0 |
| claude-haiku-5-5 | loop | 1 | 2000 | fetch-one | default | toolplane | 8/8 | 0.0060 | 11.9 | 4.0 | 0 |
| claude-haiku-5-5 | loop | 1 | 20000 | bulk | default | direct | 4/4 | 0.0070 | 14.6 | 6.0 | 0 |
| claude-haiku-5-5 | loop | 1 | 20000 | bulk | default | toolplane | 4/4 | 0.0092 | 12.9 | 6.5 | 0 |
| claude-haiku-5-5 | loop | 1 | 20000 | fetch-one | default | direct | 4/4 | 0.3998 | 18.4 | 4.5 | 0 |
| claude-haiku-5-5 | loop | 1 | 20000 | fetch-one | default | toolplane | 4/4 | 0.0079 | 12.6 | 5.0 | 0 |
| claude-haiku-5-5 | loop | 15 | 0 | fetch-one | default | direct | 8/8 | 0.0068 | 15.3 | 4.0 | 0 |
| claude-haiku-5-5 | loop | 15 | 0 | fetch-one | default | toolplane | 8/8 | 0.0062 | 12.2 | 5.0 | 0 |
| claude-haiku-5-5 | loop100 | 1 | 0 | fetch-one | default | direct | 8/8 | 0.0116 | 28.5 | 5.0 | 0 |
| claude-haiku-5-5 | loop100 | 1 | 0 | fetch-one | default | toolplane | 8/8 | 0.0058 | 10.5 | 4.0 | 0 |
| claude-haiku-5-5 | loop20 | 1 | 0 | fetch-one | default | direct | 8/8 | 0.0057 | 10.4 | 4.0 | 0 |
| claude-haiku-5-5 | loop20 | 1 | 0 | fetch-one | default | toolplane | 8/8 | 0.0053 | 10.1 | 4.0 | 0 |
| claude-haiku-5-5 | loop5 | 1 | 0 | fetch-one | default | direct | 8/8 | 0.0047 | 6.8 | 4.0 | 0 |
| claude-haiku-5-5 | loop5 | 1 | 0 | fetch-one | default | toolplane | 8/8 | 0.0053 | 12.2 | 3.5 | 0 |
| claude-haiku-5-5 | loop_lat100 | 1 | 0 | fetch-one | default | direct | 8/8 | 0.0065 | 13.3 | 4.0 | 0 |
| claude-haiku-5-5 | loop_lat100 | 1 | 0 | fetch-one | default | toolplane | 8/8 | 0.0059 | 11.8 | 4.0 | 0 |
| claude-haiku-5-5 | refunds | 1 | 0 | fetch-one | default | direct | 8/8 | 0.0064 | 14.1 | 5.5 | 0 |
| claude-haiku-5-5 | refunds | 1 | 0 | fetch-one | default | toolplane | 2/8 | 0.0287 | 18.9 | 6.5 | 0 |
| claude-haiku-5-5 | refunds | 1 | 0 | fetch-one | restricted | toolplane_cli | 8/8 | 0.0072 | 19.2 | 8.0 | 0 |
| claude-haiku-5-5 | single | 1 | 0 | fetch-one | default | direct | 8/8 | 0.0041 | 5.2 | 3.0 | 0 |
| claude-haiku-5-5 | single | 1 | 0 | fetch-one | default | toolplane | 8/8 | 0.0045 | 6.3 | 3.0 | 0 |
| claude-sonnet-5-5 | chain_prose | 1 | 0 | fetch-one | default | direct | 2/2 | 0.0913 | 13.2 | 7.0 | 0 |
| claude-sonnet-5-5 | chain_prose | 1 | 0 | fetch-one | default | toolplane | 2/2 | 0.0979 | 15.1 | 7.5 | 0 |
| claude-sonnet-5-5 | single | 1 | 0 | fetch-one | default | direct | 2/2 | 0.0689 | 5.5 | 2.5 | 0 |
| claude-sonnet-5-5 | single | 1 | 0 | fetch-one | default | toolplane | 2/2 | 0.0732 | 6.4 | 3.0 | 0 |

## Token classes (medians; the cost model's terms)

| model | task | B | G | built-ins | arm | output | uncached input | cache read | peak context |
|---|---|---|---|---|---|---|---|---|---|
| claude-haiku-5-5 | chain | 0 | fetch-one | default | direct | 1206 | 17943 | 175878 | 28489 |
| claude-haiku-5-5 | chain | 0 | fetch-one | default | toolplane | 1576 | 20132 | 185456 | 30678 |
| claude-haiku-5-5 | chain_prose | 0 | fetch-one | default | direct | 1522 | 18414 | 204946 | 28958 |
| claude-haiku-5-5 | chain_prose | 0 | fetch-one | default | toolplane | 1834 | 20828 | 217890 | 31371 |
| claude-haiku-5-5 | loop | 0 | bulk | default | direct | 1060 | 17648 | 64480 | 28202 |
| claude-haiku-5-5 | loop | 0 | bulk | default | toolplane | 1234 | 20050 | 140847 | 30598 |
| claude-haiku-5-5 | loop | 0 | fetch-one | default | direct | 2946 | 20658 | 92065 | 31210 |
| claude-haiku-5-5 | loop | 0 | fetch-one | default | toolplane | 1082 | 20000 | 96456 | 30554 |
| claude-haiku-5-5 | loop | 2000 | bulk | default | direct | 1403 | 20574 | 122164 | 31124 |
| claude-haiku-5-5 | loop | 2000 | bulk | default | toolplane | 1146 | 23272 | 128506 | 33822 |
| claude-haiku-5-5 | loop | 2000 | fetch-one | default | direct | 95346 | 100054 | 140205 | 69204 |
| claude-haiku-5-5 | loop | 2000 | fetch-one | default | toolplane | 1350 | 21456 | 98016 | 32009 |
| claude-haiku-5-5 | loop | 20000 | bulk | default | direct | 1740 | 20987 | 152722 | 31535 |
| claude-haiku-5-5 | loop | 20000 | bulk | default | toolplane | 1497 | 33062 | 188413 | 43610 |
| claude-haiku-5-5 | loop | 20000 | fetch-one | default | direct | 3456 | 396422 | 295239 | 406974 |
| claude-haiku-5-5 | loop | 20000 | fetch-one | default | toolplane | 1496 | 27082 | 127742 | 37632 |
| claude-haiku-5-5 | loop100 | 0 | fetch-one | default | direct | 8178 | 31108 | 132809 | 41658 |
| claude-haiku-5-5 | loop100 | 0 | fetch-one | default | toolplane | 1127 | 19656 | 97674 | 30206 |
| claude-haiku-5-5 | loop20 | 0 | fetch-one | default | direct | 1840 | 19359 | 92022 | 29911 |
| claude-haiku-5-5 | loop20 | 0 | fetch-one | default | toolplane | 1128 | 19470 | 95814 | 30024 |
| claude-haiku-5-5 | loop5 | 0 | fetch-one | default | direct | 616 | 17428 | 91954 | 27980 |
| claude-haiku-5-5 | loop5 | 0 | fetch-one | default | toolplane | 1042 | 19082 | 81054 | 29634 |
| claude-haiku-5-5 | loop_lat100 | 0 | fetch-one | default | direct | 2828 | 20664 | 92103 | 31216 |
| claude-haiku-5-5 | loop_lat100 | 0 | fetch-one | default | toolplane | 1322 | 20464 | 96608 | 31016 |
| claude-haiku-5-5 | refunds | 0 | fetch-one | default | direct | 1998 | 20512 | 139217 | 31060 |
| claude-haiku-5-5 | refunds | 0 | fetch-one | default | toolplane | 2197 | 20758 | 170872 | 31303 |
| claude-haiku-5-5 | refunds | 0 | fetch-one | restricted | toolplane_cli | 2378 | 21028 | 178280 | 27890 |
| claude-haiku-5-5 | single | 0 | fetch-one | default | direct | 203 | 16643 | 64419 | 27197 |
| claude-haiku-5-5 | single | 0 | fetch-one | default | toolplane | 318 | 18034 | 65996 | 28588 |
| claude-sonnet-5-5 | chain_prose | 0 | fetch-one | default | direct | 674 | 16894 | 170527 | 27440 |
| claude-sonnet-5-5 | chain_prose | 0 | fetch-one | default | toolplane | 764 | 17862 | 187931 | 28407 |
| claude-sonnet-5-5 | single | 0 | fetch-one | default | direct | 115 | 15700 | 49694 | 26256 |
| claude-sonnet-5-5 | single | 0 | fetch-one | default | toolplane | 148 | 16349 | 63532 | 26903 |
