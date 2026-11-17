"""Bounded Tesseract retry policy; coverage heuristics never imply accuracy."""
from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .ocr_result import OCRRichResult, text_quality


@dataclass(frozen=True)
class AdaptiveOCRPolicy:
    profiles: tuple[tuple[int, int], ...]
    maximum_attempts: int
    total_timeout_seconds: float
    minimum_characters: int
    minimum_confidence: float
    maximum_garbled_ratio: float

    @classmethod
    def from_config(cls, raw: Any) -> AdaptiveOCRPolicy | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("ocr.adaptive_retry 必须是对象")
        allowed = {"enabled", "profiles", "maximum_attempts", "total_timeout_seconds",
                   "minimum_characters", "minimum_confidence", "maximum_garbled_ratio"}
        if set(raw) - allowed or type(raw.get("enabled", False)) is not bool:
            raise ValueError("ocr.adaptive_retry 存在未知项或 enabled 非布尔值")
        attempts = raw.get("maximum_attempts", 3)
        chars = raw.get("minimum_characters", 40)
        if type(attempts) is not int or not 1 <= attempts <= 4:
            raise ValueError("adaptive_retry.maximum_attempts 必须在1到4之间")
        if type(chars) is not int or not 0 <= chars <= 100000:
            raise ValueError("adaptive_retry.minimum_characters 必须在0到100000之间")
        numbers = [raw.get("total_timeout_seconds", 60), raw.get("minimum_confidence", .8),
                   raw.get("maximum_garbled_ratio", .03)]
        for number, low, high in zip(numbers, [0, 0, 0], [120, 1, 1], strict=True):
            if type(number) not in (int, float) or not math.isfinite(number) or not low <= number <= high:
                raise ValueError("adaptive_retry 时间或质量触发参数无效")
        if not numbers[0]:
            raise ValueError("adaptive_retry 时间预算必须大于0")
        profiles = raw.get("profiles", [{"image_scale": 3, "page_segmentation_mode": 6},
                                        {"image_scale": 2, "page_segmentation_mode": 11}])
        if not isinstance(profiles, list) or not 1 <= len(profiles) <= 3:
            raise ValueError("adaptive_retry.profiles 必须含1到3个重试策略")
        values = []
        for profile in profiles:
            if not isinstance(profile, dict) or set(profile) != {"image_scale", "page_segmentation_mode"}:
                raise ValueError("adaptive_retry 重试策略必须指定倍率与分段模式")
            scale, psm = profile["image_scale"], profile["page_segmentation_mode"]
            if type(scale) is not int or not 1 <= scale <= 4 or type(psm) is not int or psm not in {1,3,4,5,6,7,8,9,10,11,12,13}:
                raise ValueError("adaptive_retry 重试倍率或分段模式无效")
            if (scale, psm) not in values:
                values.append((scale, psm))
        return cls(tuple(values), attempts, float(numbers[0]), chars, float(numbers[1]), float(numbers[2])) if raw.get("enabled", False) else None

    def needs_retry(self, result: OCRRichResult) -> bool:
        chars, garbled = text_quality(result.text)
        score = result.confidence
        return (chars < self.minimum_characters or garbled > self.maximum_garbled_ratio
                or score is None or type(score) not in (int, float) or not math.isfinite(score)
                or not self.minimum_confidence <= score <= 1)


def recognize_adaptive(png_bytes: bytes, recognize_once: Callable[..., OCRRichResult],
                       primary: tuple[int, int], policy: AdaptiveOCRPolicy) -> OCRRichResult:
    deadline = time.monotonic() + policy.total_timeout_seconds
    all_profiles = tuple(dict.fromkeys((primary, *policy.profiles)))
    profiles = all_profiles[:policy.maximum_attempts]
    attempts: list[dict[str, Any]] = []
    candidates: list[tuple[int, OCRRichResult]] = []
    stopped = "attempt_budget_exhausted" if len(all_profiles) > len(profiles) else "profiles_exhausted"
    for scale, psm in profiles:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stopped = "time_budget_exhausted"
            break
        index = len(attempts)
        try:
            result = recognize_once(png_bytes, scale=scale, psm=psm, timeout_seconds=remaining)
            if not isinstance(result, OCRRichResult) or not isinstance(result.text, str) or len(result.text) > 1000000:
                raise ValueError("OCR retry output exceeds text contract")
            if result.confidence is not None and (type(result.confidence) not in (int, float) or
                    not math.isfinite(result.confidence) or not 0 <= result.confidence <= 1):
                raise ValueError("OCR retry confidence is invalid")
            chars, garbled = text_quality(result.text)
            attempt = {"image_scale": scale, "page_segmentation_mode": psm, "text": result.text,
                       "confidence": result.confidence, "printable_characters": chars, "garbled_ratio": garbled,
                       "text_sha256": hashlib.sha256(result.text.encode()).hexdigest()}
            attempts.append(attempt)
            if result.text.strip() and (result.confidence is None or
                    type(result.confidence) in (int, float) and math.isfinite(result.confidence) and 0 <= result.confidence <= 1):
                candidates.append((index, result))
            if index == 0 and candidates and not policy.needs_retry(result):
                stopped = "primary_trigger_not_met"
                break
        except Exception as exc:  # noqa: BLE001 - failed attempt must not erase usable earlier output
            attempts.append({"image_scale": scale, "page_segmentation_mode": psm,
                             "error": f"{type(exc).__name__}: {exc}"})
    if not candidates:
        raise RuntimeError("OCR adaptive retry produced no usable text: " + "; ".join(
            item.get("error", "empty output") for item in attempts))

    def rank(candidate: tuple[int, OCRRichResult]) -> tuple[float, int, float]:
        _, result = candidate
        chars, garbled = text_quality(result.text)
        return (-garbled, chars, result.confidence if result.confidence is not None else -1)

    selected, result = max(candidates, key=rank)
    different = len({" ".join(item.text.split()) for _, item in candidates}) > 1
    review = different or policy.needs_retry(result) or stopped == "time_budget_exhausted" or any("error" in item for item in attempts)
    result.metadata["adaptive_retry"] = {
        "attempts": attempts, "selected_attempt": selected, "stop_reason": stopped,
        "review_required": review, "disagreement": different,
        "selection_semantics": "printable_text_coverage_heuristic_not_accuracy",
        "input_sha256": hashlib.sha256(png_bytes).hexdigest(),
        "maximum_attempts": policy.maximum_attempts, "total_timeout_seconds": policy.total_timeout_seconds,
    }
    return result
