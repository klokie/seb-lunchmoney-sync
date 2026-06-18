"""Resolve secrets from 1Password via the `op` CLI.

The `op` CLI defaults to the Werlabs (work) account, so every call pins
`--account` to the personal account explicitly. See the vault memory note
"Confirm 1Password vault before storing secrets".
"""

from __future__ import annotations

import os
import subprocess

from .config import config


def _op_read(ref: str) -> str:
    """`op read op://<vault>/<item>/<field>` against the personal account."""
    out = subprocess.run(
        ["op", "read", "--account", config.op_account, ref],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def enablebanking_private_key() -> str:
    """PEM private key — env override path, else 1Password, else on-disk default."""
    if config.eb_private_key_path:
        with open(os.path.expanduser(config.eb_private_key_path)) as fh:
            return fh.read()
    try:
        return _op_read(f"op://{config.op_vault}/{config.eb_item}/credential")
    except subprocess.CalledProcessError:
        # Fall back to the local copy used during setup.
        path = os.path.expanduser("~/.config/enablebanking/enablebanking-private.pem")
        with open(path) as fh:
            return fh.read()


def enablebanking_application_id() -> str:
    if config.eb_application_id_override:
        return config.eb_application_id_override
    return _op_read(f"op://{config.op_vault}/{config.eb_item}/application_id")


def lunchmoney_token() -> str:
    if config.lm_token_override:
        return config.lm_token_override
    return _op_read(f"op://{config.op_vault}/{config.lm_op_item}/{config.lm_op_field}")
