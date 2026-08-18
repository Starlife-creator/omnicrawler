"""Business/SLO metrics used by the desktop dashboard and RC reports."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    time_to_first_record_seconds: float
    first_run_success_rate: float
    field_completeness: float
    field_accuracy: float
    human_review_rate: float
    change_false_positive_rate: float
    change_false_negative_rate: float
    recovery_success_rate: float
    longest_no_progress_seconds: float
    seconds_per_thousand_pages: float
    bytes_per_thousand_pages: int
    cost_per_thousand_pages: float
    peak_memory_bytes: int
    template_failure_rate: float
    template_no_edit_reuse_rate: float
    valid_automation_rate: float


def rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def valid_automation(valid_without_manual_fix: int, candidates: int) -> float:
    return rate(valid_without_manual_fix, candidates)

