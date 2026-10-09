# demo-check 2026-09-30 01:41 UTC: judged again with SLO-4 = 60 s

The run used the old SLO-4 (30 s) in `app/demo_check.py`. The spec changed SLO-4 to 60 s the same day
(the owner, option B). The rows below use 60 s. The raw rows are in the JSON file.

| Question | Pass | Rule | Time | Seconds |
|---|---|---|---|---|
| D-01 | yes | ok | ok | 8.84 |
| D-02 | yes | ok | ok | 9.88 |
| D-03 | no | fail | ok | 52.5 |
| D-04 | yes | ok | ok | 53.47 |
| D-05 | yes | ok | ok | 28.06 |
| D-06 | yes | ok | ok | 52.08 |
| D-07 | no | fail | ok | 7.32 |
| D-08 | no | fail | fail | 5.59 |
| D-09 | no | fail | ok | 56.01 |
| D-10 | yes | ok | ok | 31.26 |
| D-11 | yes | ok | ok | 0.19 |
| D-12 | yes | ok | ok | 9.96 |

Result: 8 of 12 pass.
