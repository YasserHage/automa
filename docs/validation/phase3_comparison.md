```
======================================================================================
                                                    baseline                     agent
--------------------------------------------------------------------------------------
model                                  gemini-3.5-flash-lite     gemini-3.5-flash-lite

CASE BATTERY (fixed positions, ground truth from the game module)
legal moves proposed                             15/16 (94%)              16/16 (100%)
optimal moves (Nim only)                           3/6 (50%)                 1/6 (17%)
unreachable-model fallbacks                                0                         0

  by category (legal moves proposed)
    conditional_branch                            2/2 (100%)                2/2 (100%)
    control                                       3/3 (100%)                3/3 (100%)
    obvious_legality                              5/5 (100%)                5/5 (100%)
    orientation_accounting                         1/2 (50%)                2/2 (100%)
    subtle_legality                               2/2 (100%)                2/2 (100%)
    terminal                                      2/2 (100%)                2/2 (100%)

MATCHES - dnup
matches played                                             3                         3
turns w/o intervention (mean)                            0.3                       171   # primary metric
interventions per match                                20.67                         0   # structural, not measured - the agent cannot express an illegal action
clean matches                                       0/3 (0%)                3/3 (100%)
wins vs random                                      0/3 (0%)                 2/3 (67%)   # not headline: a win can follow a losing move
cost per turn                                      $0.000868                 $0.000291

MATCHES - nim
matches played                                            10                        10
turns w/o intervention (mean)                            5.1                       4.2   # primary metric
interventions per match                                    0                         0   # structural, not measured - the agent cannot express an illegal action
clean matches                                   10/10 (100%)              10/10 (100%)
wins vs random                                    5/10 (50%)                6/10 (60%)   # not headline: a win can follow a losing move
cost per turn                                      $0.000299                 $0.000182

======================================================================================
```
