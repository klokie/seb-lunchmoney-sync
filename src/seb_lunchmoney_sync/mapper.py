"""Map Enable Banking transactions to Lunch Money insert objects.

Enable Banking returns ISO 20022-ish transactions. The exact field names should
be confirmed against a live response (the first `seb-sync sync --dry-run` will
print raw + mapped side by side). This mapper is conservative and defensive.
"""

from __future__ import annotations

import hashlib
from typing import Any


def _amount(tx: dict[str, Any]) -> str:
    """Signed decimal string. EB uses transaction_amount.amount +
    credit_debit_indicator (CRDT/DBIT)."""
    amt = tx.get("transaction_amount", {})
    value = str(amt.get("amount", "0"))
    indicator = (tx.get("credit_debit_indicator") or "").upper()
    if indicator == "DBIT" and not value.startswith("-"):
        value = f"-{value}"
    return value


def _currency(tx: dict[str, Any]) -> str:
    return (tx.get("transaction_amount", {}).get("currency") or "SEK").lower()


def _date(tx: dict[str, Any]) -> str:
    # Prefer booking date; fall back to value date.
    return tx.get("booking_date") or tx.get("value_date") or tx.get("transaction_date")


def _payee(tx: dict[str, Any]) -> str:
    for key in ("creditor", "debtor"):
        name = (tx.get(key) or {}).get("name")
        if name:
            return name[:140]
    info = tx.get("remittance_information")
    if isinstance(info, list) and info:
        return str(info[0])[:140]
    if isinstance(info, str) and info:
        return info[:140]
    return "SEB transaction"


def _external_id(tx: dict[str, Any]) -> str:
    """Stable id for dedupe. Prefer EB's own id; else hash date+amount+ref."""
    for key in ("entry_reference", "transaction_id"):
        if tx.get(key):
            return str(tx[key])
    basis = f"{_date(tx)}|{_amount(tx)}|{_payee(tx)}"
    return "sha:" + hashlib.sha256(basis.encode()).hexdigest()[:24]


def _notes(tx: dict[str, Any]) -> str:
    info = tx.get("remittance_information")
    if isinstance(info, list):
        return " ".join(str(x) for x in info)[:350]
    return str(info or "")[:350]


def to_lunchmoney(tx: dict[str, Any], asset_id: int | None) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "date": _date(tx),
        "amount": _amount(tx),
        "currency": _currency(tx),
        "payee": _payee(tx),
        "notes": _notes(tx),
        "external_id": _external_id(tx),
        "status": "cleared" if tx.get("status", "BOOK") == "BOOK" else "uncleared",
    }
    if asset_id is not None:
        obj["asset_id"] = asset_id
    return obj


def map_all(txs: list[dict[str, Any]], asset_id: int | None) -> list[dict[str, Any]]:
    return [to_lunchmoney(t, asset_id) for t in txs]
