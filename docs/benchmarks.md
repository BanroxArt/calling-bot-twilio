# Benchmarks

Run them yourself:

```
make bench
```

or `PYTHONPATH=src python bench/run.py`. The script uses `timeit` from the
standard library and reports the best of five repeats, so the numbers are a
floor for one CPU core, not a throughput promise. Compare runs on the same
machine only.

Last recorded run, Nov 14, 2026, on a shared cloud VM (Intel Xeon, 1 core in use):

Python 3.13.5 on x86_64 (linux)

| case | µs/op | ops/s |
|---|---:|---:|
| gate preview (consent + DNC + hours + caps) | 16.18 | 61,807 |
| gate check, records attempt + audit row | 36.67 | 27,269 |
| Twilio signature (HMAC-SHA1, 12 params) | 4.01 | 249,219 |
| phone normalize | 0.73 | 1,373,432 |
| calling window check | 0.93 | 1,070,424 |
| token bucket take | 0.25 | 4,078,467 |
