"""Dedup logic for `sync-all`.

The regression these guard against: an earlier version treated
same-amount/same-payee transactions within a few days as duplicates and
silently dropped them, which loses legitimate repeated purchases. Steady-state
dedup is now `external_id` only; the fuzzy match survives solely as a read-only
migration aid (`find_cross_source`).
"""

from seb_lunchmoney_sync.cli import select_new, find_cross_source

START = "2026-07-01"


def _tx(external_id, date, amount, payee="CAFE"):
    return {"external_id": external_id, "date": date, "amount": amount, "payee": payee}


def test_repeated_legitimate_purchase_is_kept():
    """Two identical purchases a day apart, distinct ids → BOTH imported.

    This is the case the old fuzzy match silently dropped.
    """
    existing: list[dict] = []
    mapped = [
        _tx("eb-1", "2026-07-10", "-40.00"),
        _tx("eb-2", "2026-07-11", "-40.00"),  # same amount+payee, next day
    ]
    fresh = select_new(mapped, existing, START)
    assert [m["external_id"] for m in fresh] == ["eb-1", "eb-2"]


def test_rerun_is_idempotent():
    """A transaction already in Lunch Money (same external_id) is not re-added."""
    existing = [_tx("eb-1", "2026-07-10", "-40.00")]
    mapped = [_tx("eb-1", "2026-07-10", "-40.00")]
    assert select_new(mapped, existing, START) == []


def test_new_transaction_selected():
    existing = [_tx("eb-1", "2026-07-10", "-40.00")]
    mapped = [_tx("eb-1", "2026-07-10", "-40.00"), _tx("eb-9", "2026-07-12", "-99.00")]
    assert [m["external_id"] for m in select_new(mapped, existing, START)] == ["eb-9"]


def test_before_window_ignored():
    existing: list[dict] = []
    mapped = [_tx("eb-old", "2026-06-30", "-5.00"), _tx("eb-new", "2026-07-02", "-5.00")]
    assert [m["external_id"] for m in select_new(mapped, existing, START)] == ["eb-new"]


def test_cross_provider_shift_surfaced_by_reconcile():
    """Same transaction from a prior Lunch Flow sync: different id, and dated a
    day earlier. select_new (external_id only) does NOT recognise it — that's
    the accepted trade for never dropping legitimate repeats — but
    find_cross_source flags it for review during migration.
    """
    existing = [
        # Lunch Flow row: no external_id, dated one day before EB's booking date
        {"external_id": None, "date": "2026-07-09", "amount": "40.00", "payee": "CAFE"},
    ]
    mapped = [_tx("eb-1", "2026-07-10", "-40.00", "CAFE")]

    # external_id-only would re-import it (known migration gap)…
    assert len(select_new(mapped, existing, START)) == 1

    # …but reconcile surfaces the overlap for a human to judge.
    pairs = find_cross_source(mapped, existing, window_days=3)
    assert len(pairs) == 1
    fresh_txn, matches = pairs[0]
    assert fresh_txn["external_id"] == "eb-1"
    assert matches[0]["date"] == "2026-07-09"


def test_reconcile_ignores_exact_id_matches():
    """A row already ours (same external_id) is not a cross-source candidate."""
    existing = [_tx("eb-1", "2026-07-10", "-40.00")]
    mapped = [_tx("eb-1", "2026-07-10", "-40.00")]
    assert find_cross_source(mapped, existing, window_days=3) == []


def test_reconcile_respects_window():
    existing = [
        {"external_id": None, "date": "2026-07-01", "amount": "40.00", "payee": "CAFE"},
    ]
    mapped = [_tx("eb-1", "2026-07-10", "-40.00", "CAFE")]  # 9 days apart
    assert find_cross_source(mapped, existing, window_days=3) == []
