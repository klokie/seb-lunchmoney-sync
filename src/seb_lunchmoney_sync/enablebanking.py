"""Minimal Enable Banking API client.

Auth model (PSD2 AISP): the application signs a short-lived RS256 JWT with its
private key (kid = application_id) and uses it as a Bearer token. Account access
needs the PSU's BankID consent, captured once via the redirect flow, which
yields a `session_id` valid for the consent window (~90 days).

Docs: https://enablebanking.com/docs/api/reference/
NOTE: endpoint/field shapes are per the public docs; verify against a live call
the first time (especially the SEB corporate/business consent).
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Any

import httpx
import jwt

from .config import config
from . import secrets


def _now() -> int:
    return int(time.time())


def _make_jwt(application_id: str, private_key: str) -> str:
    headers = {"typ": "JWT", "alg": "RS256", "kid": application_id}
    payload = {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": _now(),
        "exp": _now() + 3600,
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers=headers)


class EnableBanking:
    def __init__(self) -> None:
        self._app_id = secrets.enablebanking_application_id()
        self._key = secrets.enablebanking_private_key()
        self._client = httpx.Client(base_url=config.eb_base_url, timeout=30)

    def _auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {_make_jwt(self._app_id, self._key)}"}

    def _request(self, method: str, path: str, **kw: Any) -> dict:
        """Enable Banking rate-limits (429), which matters for a job that runs
        several times a day or asks for a wide date range. Back off and retry
        rather than dying, and fail with something readable if it persists."""
        delay = 2.0
        for attempt in range(4):
            r = self._client.request(method, path, headers=self._auth_header(), **kw)
            if r.status_code != 429:
                r.raise_for_status()
                return r.json() if r.content else {}

            # Two very different 429s. ASPSP_RATE_LIMIT_EXCEEDED is the bank's
            # own PSD2 quota — regulation caps unattended access at 4 requests
            # per account per day — so the window is hours, not seconds, and
            # retrying only burns time (and possibly quota). Fail immediately
            # and say so. Anything else is short-term throttling worth a retry.
            if (r.json() if r.content else {}).get("error") == "ASPSP_RATE_LIMIT_EXCEEDED":
                raise RuntimeError(
                    "The bank (not Enable Banking) refused: daily PSD2 quota "
                    "exhausted. Unattended access is capped at ~4 requests per "
                    "account per day and resets on the bank's own clock — "
                    "retrying now will not help. Reduce the schedule, or count "
                    "manual runs against the same budget."
                )

            if attempt == 3:
                raise RuntimeError(
                    "Enable Banking is rate-limiting (429) and did not recover "
                    "after 4 attempts. Narrow --lookback-days, or run the job "
                    "less often."
                )
            retry_after = r.headers.get("Retry-After")
            wait = float(retry_after) if (retry_after or "").isdigit() else delay
            time.sleep(wait)
            delay *= 3
        raise AssertionError("unreachable")

    def application(self) -> dict:
        """Read back the registered application. Requires only a valid JWT, so
        it doubles as a cert/keypair health check (see `seb-sync check`)."""
        return self._request("GET", "/application")

    # --- consent / auth flow ---

    def start_authorization(
        self, valid_days: int = 90, psu_type: str | None = None
    ) -> dict:
        """Begin consent. Returns {url, authorization_id} — open `url` in a
        browser, authorize with BankID, get redirected to the callback."""
        valid_until = (
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=valid_days)
        ).isoformat()
        body = {
            "access": {"valid_until": valid_until},
            "aspsp": {"name": config.eb_aspsp_name, "country": config.eb_aspsp_country},
            "state": str(uuid.uuid4()),
            "redirect_url": config.eb_redirect_url,
            "psu_type": psu_type or config.eb_psu_type,
        }
        return self._request("POST", "/auth", json=body)

    def create_session(self, code: str) -> dict:
        """Exchange the redirect `code` for a session (accounts + session_id)."""
        return self._request("POST", "/sessions", json={"code": code})

    def get_session(self, session_id: str) -> dict:
        return self._request("GET", f"/sessions/{session_id}")

    # --- data ---

    def transactions(
        self, account_uid: str, date_from: str | None = None
    ) -> list[dict]:
        """All transactions for an account, following continuation keys."""
        params: dict[str, Any] = {}
        if date_from:
            params["date_from"] = date_from
        out: list[dict] = []
        while True:
            data = self._request(
                "GET", f"/accounts/{account_uid}/transactions", params=params
            )
            out.extend(data.get("transactions", []))
            cont = data.get("continuation_key")
            if not cont:
                break
            params["continuation_key"] = cont
        return out
