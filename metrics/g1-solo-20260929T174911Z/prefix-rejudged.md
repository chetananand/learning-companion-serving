# G1 prefix test: re-judged

The saved run marked 4 of 2000 answers as bad, because each one has the word "NaN". The answers quote "NaN" from our own notes. The fixed rule (`is_garbage` in `tools/g1_tests.py`) marks 0 of them as bad. All 2000 answers had HTTP 200. The prefix hit ratio is 0.845 (the rule: 0.6 or more). Result: PASS.
