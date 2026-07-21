"""Resolve secrets from 1Password via the `op` CLI.

The `op` CLI defaults to the Werlabs (work) account, so every call pins
`--account` to the personal account explicitly. See the vault memory note
"Confirm 1Password vault before storing secrets".
"""

from __future__ import annotations

import functools
import os
import subprocess

from .config import config


@functools.lru_cache(maxsize=None)
def _op_read(ref: str) -> str:
    """`op read op://<vault>/<item>/<field>` against the personal account.

    Cached per process: a single `sync` run needs three secrets, and without
    this each one spawns its own `op` (and its own unlock prompt).

    For unattended runs don't rely on this at all — `seb-sync bootstrap` writes
    the values to an env file so scheduled runs never invoke `op`.
    """
    try:
        out = subprocess.run(
            ["op", "read", "--account", config.op_account, ref],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"Need {ref} but the `op` CLI is not installed.\n"
            "For unattended runs, source the env file written by "
            "`seb-sync bootstrap`:\n"
            "  set -a; . ~/.config/enablebanking/env; set +a"
        ) from None
    except subprocess.CalledProcessError as exc:
        # Usually 1Password is locked, or this is a non-interactive shell that
        # cannot prompt — exactly the case scheduled runs hit.
        detail = (exc.stderr or "").strip().splitlines()
        raise RuntimeError(
            f"`op` could not read {ref}"
            + (f" ({detail[-1]})" if detail else "")
            + ".\nIf this is an unattended run, source the env file from "
            "`seb-sync bootstrap` instead:\n"
            "  set -a; . ~/.config/enablebanking/env; set +a"
        ) from None
    return out.stdout.strip()


def enablebanking_private_key() -> str:
    """PEM private key — env override path, else 1Password, else on-disk default."""
    if config.eb_private_key_path:
        with open(os.path.expanduser(config.eb_private_key_path)) as fh:
            return fh.read()
    try:
        return _op_read(f"op://{config.op_vault}/{config.eb_item}/credential")
    except RuntimeError:
        # _op_read normalizes "op missing / locked / non-interactive" to
        # RuntimeError. Fall back to the local copy used during setup.
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
