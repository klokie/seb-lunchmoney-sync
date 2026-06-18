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
    # 1Password lookup (personal account — op defaults to the Werlabs work
    # account, so this is pinned explicitly).
    op_account: str = _env("OP_ACCOUNT", "56RHUXXFBNAPJKCJUZ22YN36NE")
    op_vault: str = _env("OP_VAULT", "Personal")
    eb_item: str = _env("EB_ITEM", "Enable Banking — klokie-lunchmoney-sync")

    # Enable Banking
    eb_base_url: str = _env("EB_BASE_URL", "https://api.enablebanking.com")
    eb_redirect_url: str = _env("EB_REDIRECT_URL", "https://localhost:8080/callback")
    eb_aspsp_name: str = _env("EB_ASPSP_NAME", "SEB")
    eb_aspsp_country: str = _env("EB_ASPSP_COUNTRY", "SE")
    eb_psu_type: str = _env("EB_PSU_TYPE", "business")
    eb_application_id_override: str | None = _env("EB_APPLICATION_ID")
    eb_private_key_path: str | None = _env("EB_PRIVATE_KEY_PATH")

    # Lunch Money
    lm_token_override: str | None = _env("LUNCHMONEY_API_TOKEN")
    lm_op_item: str = _env("LM_OP_ITEM", "3vrk4of6otddew56uuo5icpofa")
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
