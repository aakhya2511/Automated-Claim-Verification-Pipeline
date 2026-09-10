"""Time and stage-timing primitives.

Two reasons this exists instead of calling ``datetime.now()`` inline:

* Date-boundary logic (expired promotions, "through September 30") must be
  testable without freezing the system clock, so "now" is injected.
* Latency measurement needs a monotonic source. ``time.perf_counter`` is not
  affected by NTP adjustments, unlike wall-clock time.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...

    def today(self) -> date: ...


class SystemClock:
    """Wall-clock time in UTC.

    All internal date comparisons are UTC-normalized; a claim's local-time
    intent is carried explicitly by ``TimeContext.as_of`` when the caller
    supplies it.
    """

    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        return datetime.now(UTC).date()


@dataclass(frozen=True)
class FixedClock:
    """Deterministic clock for tests, dataset generation, and evaluation."""

    moment: datetime

    def now(self) -> datetime:
        return self.moment

    def today(self) -> date:
        return self.moment.date()


@dataclass
class StageTimer:
    """Accumulates per-stage elapsed milliseconds for one verification.

    Stages are accumulated rather than assigned so a stage entered more than
    once (e.g. a retried rater call) reports total time spent, which is what
    the latency budget actually cares about.
    """

    stages: dict[str, float] = field(default_factory=dict)
    _started: float = field(default_factory=time.perf_counter)

    @contextmanager
    def measure(self, stage: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self.stages[stage] = self.stages.get(stage, 0.0) + elapsed_ms

    def add(self, stage: str, milliseconds: float) -> None:
        self.stages[stage] = self.stages.get(stage, 0.0) + milliseconds

    def get(self, stage: str) -> float:
        return self.stages.get(stage, 0.0)

    @property
    def total_ms(self) -> float:
        """Wall-clock time since the timer was created.

        Deliberately not the sum of stages: it includes orchestration overhead,
        which is exactly what an end-to-end latency SLO must account for.
        """
        return (time.perf_counter() - self._started) * 1000.0
