"""Thin Lunch Money API client — just what the sync needs.

Docs: https://lunchmoney.dev/#transactions
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import config
from . import secrets


class LunchMoney:
    def __init__(self) -> None:
        token = secrets.lunchmoney_token()
        self._client = httpx.Client(
            base_url=config.lm_base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )

    def insert_transactions(
        self,
        transactions: list[dict[str, Any]],
        *,
        apply_rules: bool = True,
        check_for_recurring: bool = True,
        debit_as_negative: bool = True,
        skip_duplicates: bool = True,
        chunk_size: int = 200,
    ) -> dict:
        """POST /v1/transactions. `external_id` on each transaction makes
        re-runs idempotent (Lunch Money rejects duplicate external_ids per
        asset).

        Note this only dedupes against rows *this tool* inserted. It will not
        recognise transactions that arrived via another sync (e.g. Lunch Flow /
        GoCardless), so scope the date range before a first run against an
        account that already has history.

        Sent in chunks: a first sync can be thousands of rows, which would
        otherwise be one oversized POST against a 30s timeout.
        """
        results: dict[str, Any] = {"ids": []}
        for i in range(0, len(transactions), chunk_size):
            batch = transactions[i : i + chunk_size]
            body = {
                "transactions": batch,
                "apply_rules": apply_rules,
                "check_for_recurring": check_for_recurring,
                "debit_as_negative": debit_as_negative,
                "skip_duplicates": skip_duplicates,
            }
            r = self._client.post("/v1/transactions", json=body)
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload.get("ids"), list):
                results["ids"].extend(payload["ids"])
            else:  # surface anything unexpected rather than swallowing it
                results.setdefault("other", []).append(payload)
        return results

    def assets(self) -> dict:
        r = self._client.get("/v1/assets")
        r.raise_for_status()
        return r.json()
