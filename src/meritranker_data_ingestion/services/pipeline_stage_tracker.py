"""Pipeline stage timing and status recorder (batch share-log support)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass
class PipelineStageEvent:
    stage: str
    status: str
    duration_ms: int
    key_result: str = ""
    warning_or_error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class PipelineStageRecorder:
    """Records per-stage duration and outcome for share logs and JSONL events."""

    def __init__(self) -> None:
        self.events: list[PipelineStageEvent] = []

    def run(
        self,
        stage: str,
        fn: Callable[[], T],
        *,
        key_result_fn: Callable[[T], str] | None = None,
        metrics_fn: Callable[[T], dict[str, Any]] | None = None,
    ) -> T:
        start = time.perf_counter()
        try:
            result = fn()
            duration_ms = int((time.perf_counter() - start) * 1000)
            key_result = key_result_fn(result) if key_result_fn else ""
            metrics = metrics_fn(result) if metrics_fn else {}
            self.events.append(
                PipelineStageEvent(
                    stage=stage,
                    status="succeeded",
                    duration_ms=duration_ms,
                    key_result=key_result,
                    metrics=metrics,
                ),
            )
            return result
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            self.events.append(
                PipelineStageEvent(
                    stage=stage,
                    status="failed",
                    duration_ms=duration_ms,
                    warning_or_error=str(exc),
                    errors=[str(exc)],
                ),
            )
            raise

    def skip(self, stage: str, *, reason: str = "skipped") -> None:
        self.events.append(
            PipelineStageEvent(
                stage=stage,
                status="skipped",
                duration_ms=0,
                key_result=reason,
            ),
        )
