"""Micro-benchmarks. Run: PYTHONPATH=src python bench/run.py

Standard library timeit, best of five repeats. Numbers are per operation on
one CPU core; compare runs on the same machine, not across machines.
"""

from __future__ import annotations

import platform
import sys
import timeit
from collections.abc import Callable
from datetime import UTC, datetime, time

from callgate.config import Settings
from callgate.gate import Gate
from callgate.hours import CallingPolicy
from callgate.phone import normalize
from callgate.ratelimit import TokenBucket
from callgate.store import Store
from callgate.twilio import compute_signature


def bench(name: str, fn: Callable[[], object], number: int, repeat: int = 5) -> None:
    best = min(timeit.repeat(fn, number=number, repeat=repeat)) / number
    print(f"| {name} | {best * 1e6:,.2f} | {1 / best:,.0f} |")


def header() -> None:
    print(f"Python {platform.python_version()} on {platform.machine()} ({sys.platform})\n")
    print("| case | µs/op | ops/s |")
    print("|---|---:|---:|")


NOON = datetime(2026, 3, 18, 15, 0, tzinfo=UTC)
PURPOSE = "appointment_reminder"


def main() -> None:
    header()
    settings = Settings(audit_key=b"k" * 32, outbound_enabled=True)
    store = Store(":memory:")
    for i in range(1000):
        store.add_consent(
            f"+1555555{i:04d}",
            PURPOSE,
            "America/New_York",
            "web_form",
            f"form:{i}",
            "v2",
            at=NOON.timestamp() - 86_400,
        )
    gate = Gate(settings, store, TokenBucket(1e9, 1e9))
    bench(
        "gate preview (consent + DNC + hours + caps)",
        lambda: gate.preview("+15555550142", PURPOSE, NOON),
        5000,
    )
    nums = iter(range(10**9))
    bench(
        "gate check, records attempt + audit row",
        lambda: gate.check(f"+1555555{next(nums) % 1000:04d}", PURPOSE, NOON),
        1000,
        repeat=1,
    )
    params = {f"Field{i}": f"value-{i}" for i in range(12)}
    bench(
        "Twilio signature (HMAC-SHA1, 12 params)",
        lambda: compute_signature("bench-token", "https://calls.example.com/status", params),
        50000,
    )
    bench("phone normalize", lambda: normalize("(555) 555-0142"), 50000)
    policy = CallingPolicy(start=time(8), end=time(21))
    bench("calling window check", lambda: policy.blocked(NOON, "America/Los_Angeles"), 50000)
    bucket = TokenBucket(1e9, 1e9)
    bench("token bucket take", bucket.try_take, 200000)


if __name__ == "__main__":
    main()
