"""2000-query fuzz harness for the SQL lineage extractor (Phase X final).

Drives :func:`etl_plugins.runtime.sql_lineage.extract_sql_lineage` against
a generated corpus spanning 22 SQL shapes (simple → CTE → JOIN → window →
combined). Each generated query carries its expected lineage so the test
compares against ground truth, not a regex.

Failure modes are reported as a structured stats table (per-shape pass /
fail counts) instead of a single ``assert False`` — that way when one
generator regresses we see exactly which family. The test then asserts
zero failures overall, so CI still hard-fails.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import pytest

from etl_plugins.runtime.sql_lineage import extract_sql_lineage
from tests.unit.runtime.sql_corpus_generator import (
    GENERATORS,
    generate_corpus,
)


def _matches(
    actual: dict[str, list[tuple[str, str]]] | None,
    expected: dict[str, set[tuple[str, str]]],
    *,
    containment_only: bool,
) -> tuple[bool, str]:
    """Compare actual lineage against ``expected``.

    ``containment_only=True`` means each expected upstream must be a
    *subset* of the actual upstream set — used for shapes (correlated
    subquery, window) where sqlglot legitimately picks up extra
    join-/order-key columns we didn't bother encoding in the expected
    map. ``False`` requires exact equality.

    Returns ``(passed, diagnostic)``.
    """
    if actual is None:
        return False, "lineage returned None (parser opted out)"
    actual_sets = {k: set(v) for k, v in actual.items()}
    if set(actual_sets.keys()) != set(expected.keys()):
        return False, (
            f"output columns differ: expected={sorted(expected)} actual={sorted(actual_sets)}"
        )
    for col, want in expected.items():
        got = actual_sets[col]
        if containment_only:
            if not want.issubset(got):
                return False, (
                    f"column {col!r}: expected ⊆ actual failed. want={want!s} got={got!s}"
                )
        else:
            if got != want:
                return False, (f"column {col!r}: want={want!s} got={got!s}")
    return True, ""


# Shapes where the walker legitimately picks up extra upstream columns
# (join keys, ORDER BY columns inside CASE/window, etc.). For these we
# only assert that every column in our expected set is present.
_CONTAINMENT_SHAPES = {
    "correlated_subquery",
    "row_number",
    "case_when",
    "combined_real_world",
    "window_partition",
    "left_join_coalesce",
}


def test_fuzz_2000_queries_lineage_matches_ground_truth() -> None:
    """Run the generator-driven corpus and assert 100% pass rate.

    Two compensating mechanisms keep the test honest:
    * **Per-shape stats** — surfaced via ``pytest -s`` so a partial regression
      is visible even when the assertion fires on the first failure.
    * **Containment vs equality** — split per shape (see ``_CONTAINMENT_SHAPES``).
      Exact equality everywhere would force the generator to encode every
      edge sqlglot happens to add (join keys, etc.), which is brittle.
    """
    total = 2000
    failures: list[str] = []
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0})
    shape_counter: Counter[str] = Counter()

    for gq in generate_corpus(total=total, seed=42):
        shape_counter[gq.shape] += 1
        containment = gq.shape in _CONTAINMENT_SHAPES
        actual = extract_sql_lineage(gq.sql)
        ok, diag = _matches(actual, gq.expected, containment_only=containment)
        if ok:
            stats[gq.shape]["pass"] += 1
        else:
            stats[gq.shape]["fail"] += 1
            if len(failures) < 10:
                failures.append(f"[{gq.shape}] {diag}\nSQL:\n{gq.sql.strip()}\n")

    # Print compact stats (visible under ``pytest -s``).
    print("\n=== SQL lineage fuzz stats (2000 queries) ===")
    for shape, _ in GENERATORS:
        c = stats[shape]
        n = c["pass"] + c["fail"]
        if n == 0:
            continue
        pct = 100.0 * c["pass"] / n
        print(f"  {shape:<25} {c['pass']:>4}/{n:<4} ({pct:5.1f}%)")
    overall_pass = sum(s["pass"] for s in stats.values())
    overall_fail = sum(s["fail"] for s in stats.values())
    print(f"  {'TOTAL':<25} {overall_pass:>4}/{overall_pass + overall_fail:<4}")

    if failures:
        first = "\n".join(failures[:5])
        pytest.fail(f"{overall_fail} failures of {total}. First 5:\n{first}")
