"""chunked / take / drop 테스트."""

from __future__ import annotations

import pytest

from etl_plugins.utils.chunk import chunked, drop, take

# ---------- chunked ----------


def test_chunked_basic() -> None:
    assert list(chunked(range(7), 3)) == [[0, 1, 2], [3, 4, 5], [6]]


def test_chunked_exact_multiple() -> None:
    assert list(chunked(range(6), 3)) == [[0, 1, 2], [3, 4, 5]]


def test_chunked_size_larger_than_input() -> None:
    assert list(chunked([1, 2], 10)) == [[1, 2]]


def test_chunked_empty_input() -> None:
    assert list(chunked([], 3)) == []


def test_chunked_invalid_size() -> None:
    for bad in [0, -1, -100]:
        with pytest.raises(ValueError, match="positive"):
            list(chunked([1, 2, 3], bad))


def test_chunked_works_with_generator() -> None:
    def gen() -> object:
        yield from range(5)

    assert list(chunked(gen(), 2)) == [[0, 1], [2, 3], [4]]


def test_chunked_is_lazy() -> None:
    # 무한 시퀀스에서도 동작 (앞 몇 개만 가져옴)
    import itertools

    it = chunked(itertools.count(), 3)
    assert next(it) == [0, 1, 2]
    assert next(it) == [3, 4, 5]


# ---------- take ----------


def test_take_basic() -> None:
    assert take(range(10), 3) == [0, 1, 2]


def test_take_more_than_available() -> None:
    assert take([1, 2], 10) == [1, 2]


def test_take_zero() -> None:
    assert take([1, 2, 3], 0) == []


def test_take_negative_raises() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        take([1, 2], -1)


# ---------- drop ----------


def test_drop_basic() -> None:
    assert list(drop(range(5), 2)) == [2, 3, 4]


def test_drop_all() -> None:
    assert list(drop([1, 2, 3], 10)) == []


def test_drop_zero() -> None:
    assert list(drop([1, 2, 3], 0)) == [1, 2, 3]


def test_drop_negative_raises() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        list(drop([1], -1))
