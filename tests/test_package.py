"""패키지 메타 / 임포트 sanity test."""

from __future__ import annotations

import etl_plugins


def test_version_is_defined() -> None:
    assert isinstance(etl_plugins.__version__, str)
    assert etl_plugins.__version__.count(".") >= 2  # x.y.z 형태


def test_package_importable() -> None:
    # 모듈이 정상적으로 import 되는지 확인
    assert etl_plugins is not None
