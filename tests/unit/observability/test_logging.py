"""Secret masking + structlog 설정 테스트."""

from __future__ import annotations

import logging
from typing import Any

import pytest
import structlog

from etl_plugins.observability.logging import (
    DEFAULT_SENSITIVE_KEYWORDS,
    MASK_VALUE,
    configure_logging,
    make_secret_masker,
    mask_sensitive_values,
)

# ---------- masker / processor 단위 동작 ----------


def _apply(processor: Any, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor를 단독으로 호출."""
    return dict(processor(None, "info", event_dict))


def test_mask_password_key() -> None:
    assert _apply(mask_sensitive_values, {"password": "leaked", "x": 1}) == {
        "password": MASK_VALUE,
        "x": 1,
    }


def test_mask_is_case_insensitive() -> None:
    assert _apply(mask_sensitive_values, {"PaSsWoRd": "x"})["PaSsWoRd"] == MASK_VALUE


def test_mask_matches_substring() -> None:
    out = _apply(
        mask_sensitive_values,
        {"db_password": "x", "api_key": "y", "user_token": "z"},
    )
    assert out["db_password"] == MASK_VALUE
    assert out["api_key"] == MASK_VALUE
    assert out["user_token"] == MASK_VALUE


def test_mask_passes_safe_keys() -> None:
    out = _apply(mask_sensitive_values, {"username": "u", "count": 3})
    assert out == {"username": "u", "count": 3}


def test_mask_recurses_into_dict() -> None:
    out = _apply(
        mask_sensitive_values,
        {"db": {"host": "x", "password": "p"}},
    )
    assert out["db"] == {"host": "x", "password": MASK_VALUE}


def test_mask_recurses_into_list() -> None:
    out = _apply(
        mask_sensitive_values,
        {"items": [{"password": "p"}, {"name": "n"}]},
    )
    assert out["items"] == [{"password": MASK_VALUE}, {"name": "n"}]


def test_default_keyword_coverage() -> None:
    # 명세에 명시된 주요 키워드가 빠지지 않았는지 확인
    must_have = {"password", "secret", "token", "credential", "authorization"}
    assert must_have.issubset(set(DEFAULT_SENSITIVE_KEYWORDS))


def test_custom_keyword_masker() -> None:
    proc = make_secret_masker(["pin"])
    out = _apply(proc, {"pin": "1234", "password": "ok-not-masked"})
    assert out["pin"] == MASK_VALUE
    # default 키워드를 쓰지 않으므로 "password"는 통과
    assert out["password"] == "ok-not-masked"


# ---------- configure_logging ----------


@pytest.fixture(autouse=True)
def _reset_structlog() -> Any:
    # 테스트 간 structlog 글로벌 상태 격리
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()


def test_configure_logging_idempotent() -> None:
    configure_logging(level="DEBUG", json=True)
    configure_logging(level="DEBUG", json=True)


def test_configure_logging_dev_console_mode() -> None:
    configure_logging(level="INFO", json=False)
    # log 호출이 raise 하지 않으면 OK
    structlog.get_logger().info("hello", x=1)


def test_configured_logger_redacts_secrets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", json=True)
    structlog.get_logger().info("login", username="u", password="leaked")
    captured = capsys.readouterr()
    output = captured.err + captured.out
    assert "leaked" not in output
    assert MASK_VALUE in output


def test_configure_logging_respects_level() -> None:
    configure_logging(level="WARNING", json=True)
    log = structlog.get_logger()
    # DEBUG/INFO는 필터링되어 출력 안됨 - 호출 자체는 raise 하지 않음
    log.debug("debug-event")
    log.info("info-event")
    log.warning("warn-event")
