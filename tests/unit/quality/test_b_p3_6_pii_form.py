"""B-P3-6：高熵密钥正则加上下文/词形约束，降低长普通词误报。

- 纯字母长串（英文词）不再被当密钥；
- 大小写混合 + 数字的真实密钥形态仍被识别；
- 重复单字符长串不构成密钥形态。
"""

from __future__ import annotations

from omnicrawler.quality.pii_redaction import detect_pii


def _kinds(text: str) -> list[str]:
    return [finding.kind for finding in detect_pii(text)]


def test_long_plain_english_word_is_not_secret_token() -> None:
    text = "thequickbrownfoxjumpsoverthelazydog"
    assert "secret_token" not in _kinds(text)


def test_mixed_case_digit_token_still_detected() -> None:
    text = "token sk9fA2bC4dE6fH8jK0lM2nO4pQ6rS8tU"
    assert "secret_token" in _kinds(text)


def test_repeated_single_character_is_not_secret_token() -> None:
    text = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert "secret_token" not in _kinds(text)


def test_symbol_loaded_token_still_detected() -> None:
    text = "key aB3dE9fG2hJ7kL1mN4oP6qR8sT0uV2wX4yZ6=="
    assert "secret_token" in _kinds(text)
