"""Explicit total solver budget; optional caller-supplied monotonic deadline."""
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class SolveBudget:
    seconds: float
    clock: Callable[[], float] | None = None
    spent: float = 0.0
    started: float = field(init=False, default=0.0)

    def __post_init__(self):
        if self.clock is not None:
            self.started = self.clock()

    def remaining(self) -> float:
        elapsed = self.clock() - self.started if self.clock is not None else self.spent
        return max(0.0, self.seconds - max(elapsed, self.spent))

    def consume(self, wall_time: float | None):
        self.spent += max(0.0, wall_time or 0.0)
