"""Configuration, loaded from environment with sensible defaults.

Secrets are NOT stored here. They are resolved at runtime from 1Password
(see secrets.py), unless an explicit env override is provided.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(key: str, default: str | None = None) -> str | None:
    val = os.environ.get(key)
    return val if val not in (None, "") else default


@dataclass(frozen=True)
class Config:
    # 1Password coordinates. Deliberately un-defaulted: they describe one
    # person's vault, and this repo is public. Set them in the environment (see
    # .env.example) — `op` also defaults to whichever account it feels like, so
    # OP_ACCOUNT should be pinned explicitly if you have more than one.
    #
    # Reference items by **ID, not title**: `op read` rejects titles containing
    # characters like an em-dash ("invalid character in secret reference").
    op_account: str = _env("OP_ACCOUNT", "")
    op_vault: str = _env("OP_VAULT", "Personal")
    eb_item: str = _env("EB_ITEM", "")

    # Enable Banking
    eb_base_url: str = _env("EB_BASE_URL", "https://api.enablebanking.com")
    eb_redirect_url: str = _env("EB_REDIRECT_URL", "https://localhost:8080/callback")
    eb_aspsp_name: str = _env("EB_ASPSP_NAME", "SEB")
    eb_aspsp_country: str = _env("EB_ASPSP_COUNTRY", "SE")
    # Must match how the account was linked in the EB control panel (the
    # "usage type" dropdown). Accounts were linked as `personal` on 2026-07-18;
    # asking for a `business` session against them fails. Override per-run with
    # `seb-sync auth --psu-type business` when linking the S2A corporate account.
    eb_psu_type: str = _env("EB_PSU_TYPE", "personal")
    eb_application_id_override: str | None = _env("EB_APPLICATION_ID")
    eb_private_key_path: str | None = _env("EB_PRIVATE_KEY_PATH")

    # Lunch Money
    lm_token_override: str | None = _env("LUNCHMONEY_API_TOKEN")
    lm_op_item: str = _env("LM_OP_ITEM", "")
    lm_op_field: str = _env("LM_OP_FIELD", "credential")
    lm_base_url: str = _env("LM_BASE_URL", "https://dev.lunchmoney.app")

    # Callback listener
    callback_host: str = _env("CALLBACK_HOST", "localhost")
    callback_port: int = int(_env("CALLBACK_PORT", "8080"))

    # Where to remember the active Enable Banking session (90-day consent)
    session_path: str = _env(
        "EB_SESSION_PATH",
        os.path.expanduser("~/.config/enablebanking/session.json"),
    )


config = Config()
