"""Prometheus text exposition and the in-process HTTP request registry.

The request counter and duration histogram live in this process only. Each API process keeps its own
values, so when several processes run, Prometheus must scrape each one. Gauges that describe stored state
are not kept here: the metrics route reads them at scrape time.
"""

from __future__ import annotations

import bisect
import re
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import NamedTuple

DURATION_BUCKETS: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
UNMATCHED_ROUTE = "unmatched"
OTHER_METHOD = "OTHER"
# Any method token can be sent. Only these get their own label value, so arbitrary methods cannot add series.
KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

REQUESTS_TOTAL = "jarvis_http_requests_total"
REQUEST_DURATION = "jarvis_http_request_duration_seconds"

Labels = tuple[tuple[str, str], ...]

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")
_NUMBER_RE = re.compile(r"[0-9]+")


class Sample(NamedTuple):
    """One exposition line. ``name`` carries a suffix (``_bucket``, ``_sum``) for histogram samples."""

    name: str
    labels: Labels
    value: float


def escape_label_value(value: str) -> str:
    """Escapes a label value as the exposition format requires: backslash, double quote and newline."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_value(value: float) -> str:
    if value == float("inf"):
        return "+Inf"
    if value == float("-inf"):
        return "-Inf"
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def format_sample(sample: Sample) -> str:
    if not sample.labels:
        return f"{sample.name} {_format_value(sample.value)}"
    inner = ",".join(f'{key}="{escape_label_value(value)}"' for key, value in sample.labels)
    return f"{sample.name}{{{inner}}} {_format_value(sample.value)}"


def render_family(name: str, kind: str, help_text: str, samples: Iterable[Sample]) -> list[str]:
    """HELP and TYPE lines for one metric family, followed by its samples."""
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
    lines.extend(format_sample(sample) for sample in samples)
    return lines


def route_label(path: str, template: str | None) -> str:
    """Route label for one request.

    ``template`` is the matched route's path (for example ``/api/v1/tasks/{task_id}``). Every parameter and
    every UUID or number segment becomes ``{id}``. The query string is never part of the label. A request
    that matched no route, or whose path does not line up with its template, is ``unmatched``. Raw client
    paths are never labels: a free-form segment such as a provider id would otherwise add a series per value.
    """
    if template is None:
        return UNMATCHED_ROUTE
    raw_segments = path.split("?", 1)[0].split("#", 1)[0].split("/")
    template_segments = template.split("/")
    if len(raw_segments) != len(template_segments):
        return UNMATCHED_ROUTE
    labelled = [
        "{id}" if segment.startswith("{") or _is_identifier(raw) else segment
        for raw, segment in zip(raw_segments, template_segments)
    ]
    return "/".join(labelled)


def _is_identifier(segment: str) -> bool:
    return _UUID_RE.fullmatch(segment) is not None or _NUMBER_RE.fullmatch(segment) is not None


@dataclass
class _Histogram:
    bucket_counts: list[int]
    total: int = 0
    sum_seconds: float = 0.0


class RequestMetrics:
    """Request counter and duration histogram for this process. Thread-safe."""

    def __init__(self, buckets: Sequence[float] = DURATION_BUCKETS) -> None:
        self._buckets = tuple(buckets)
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, str], int] = {}
        self._durations: dict[tuple[str, str], _Histogram] = {}

    def observe(self, method: str, route: str, status: int, duration_seconds: float) -> None:
        duration = max(0.0, float(duration_seconds))
        method_label = method if method in KNOWN_METHODS else OTHER_METHOD
        with self._lock:
            key = (method_label, route, str(status))
            self._requests[key] = self._requests.get(key, 0) + 1
            histogram = self._durations.get((method_label, route))
            if histogram is None:
                histogram = _Histogram(bucket_counts=[0] * len(self._buckets))
                self._durations[(method_label, route)] = histogram
            # Bucket i counts durations <= buckets[i]. Anything above the last bound only reaches +Inf.
            index = bisect.bisect_left(self._buckets, duration)
            if index < len(self._buckets):
                histogram.bucket_counts[index] += 1
            histogram.total += 1
            histogram.sum_seconds += duration

    def render(self) -> list[str]:
        with self._lock:
            requests = sorted(self._requests.items(), key=lambda item: item[0])
            durations = sorted(
                (
                    (key, _Histogram(list(hist.bucket_counts), hist.total, hist.sum_seconds))
                    for key, hist in self._durations.items()
                ),
                key=lambda item: item[0],
            )

        request_samples = [
            Sample(REQUESTS_TOTAL, (("method", method), ("route", route), ("status", status)), float(count))
            for (method, route, status), count in requests
        ]
        duration_samples: list[Sample] = []
        for (method, route), hist in durations:
            base: Labels = (("method", method), ("route", route))
            cumulative = 0
            for bound, count in zip(self._buckets, hist.bucket_counts):
                cumulative += count
                duration_samples.append(
                    Sample(f"{REQUEST_DURATION}_bucket", (*base, ("le", repr(bound))), float(cumulative))
                )
            duration_samples.append(Sample(f"{REQUEST_DURATION}_bucket", (*base, ("le", "+Inf")), float(hist.total)))
            duration_samples.append(Sample(f"{REQUEST_DURATION}_sum", base, hist.sum_seconds))
            duration_samples.append(Sample(f"{REQUEST_DURATION}_count", base, float(hist.total)))

        return [
            *render_family(
                REQUESTS_TOTAL,
                "counter",
                "Completed /api/v1 requests in this process, by method, route and status.",
                request_samples,
            ),
            *render_family(
                REQUEST_DURATION,
                "histogram",
                "Seconds from request start to response headers, by method and route.",
                duration_samples,
            ),
        ]


REQUEST_METRICS = RequestMetrics()
