"""Command-line entry points.

  seb-sync auth                 # one-time BankID consent → saves session.json
  seb-sync accounts             # list authorized accounts + uids
  seb-sync sync --dry-run       # fetch + map, print proposed inserts (no POST)
  seb-sync sync --account-uid X --asset-id N   # actually insert into Lunch Money
"""

from __future__ import annotations

import json
import os
import webbrowser
from pathlib import Path

import click

from .config import config
from .enablebanking import EnableBanking
from .lunchmoney import LunchMoney
from . import callback_server, mapper


def _save_session(data: dict) -> None:
    p = Path(os.path.expanduser(config.session_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    p.chmod(0o600)


def _load_session() -> dict:
    p = Path(os.path.expanduser(config.session_path))
    if not p.exists():
        raise click.ClickException("No session. Run `seb-sync auth` first.")
    return json.loads(p.read_text())


@click.group()
def cli() -> None:
    """SEB → Lunch Money sync."""


@cli.command()
@click.option(
    "--psu-type",
    type=click.Choice(["personal", "business"]),
    default=None,
    help="Must match the 'usage type' used when linking in the EB control panel.",
)
def auth(psu_type) -> None:
    """One-time BankID consent. Opens the bank's auth page, captures the
    redirect, and stores the session (valid ~90 days)."""
    eb = EnableBanking()
    click.echo(f"psu_type={psu_type or config.eb_psu_type}")
    started = eb.start_authorization(psu_type=psu_type)
    url = started.get("url")
    click.echo(f"Opening BankID consent:\n  {url}\n")
    click.echo("(Your browser will warn about the self-signed localhost cert — proceed.)")
    if url:
        webbrowser.open(url)
    code = callback_server.wait_for_code()
    session = eb.create_session(code)
    _save_session(session)
    click.echo(f"Session saved → {config.session_path}")
    for acc in session.get("accounts", []):
        click.echo(f"  account_uid={acc.get('uid')}  {acc.get('identification_hash','')}")


@cli.command()
def check() -> None:
    """Verify the EB app accepts a JWT signed by our private key.

    Read-only, no consent needed — the fastest way to tell whether the cert
    currently uploaded to the Enable Banking app matches our local keypair.
    `401 Wrong signature` means the app still holds a stale cert: upload
    ~/.config/enablebanking/enablebanking-cert.pem in the EB dashboard.
    """
    import httpx

    eb = EnableBanking()
    try:
        app = eb.application()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text
        click.echo(f"✗ HTTP {exc.response.status_code}: {body}")
        if exc.response.status_code == 401 and "signature" in body.lower():
            raise click.ClickException(
                "The EB app's cert does not match the local private key.\n"
                "Upload ~/.config/enablebanking/enablebanking-cert.pem to the "
                "'klokie-lunchmoney-sync' app at enablebanking.com, then re-run."
            )
        raise click.ClickException("Unexpected auth failure — see body above.")
    click.echo("✓ JWT accepted — cert matches. Ready for `seb-sync auth`.")
    click.echo(json.dumps(app, indent=2))


@cli.command()
def accounts() -> None:
    """List accounts from the saved session."""
    session = _load_session()
    accs = session.get("accounts", [])
    if not accs:
        click.echo("No accounts in session.")
        return
    for acc in accs:
        click.echo(json.dumps(acc, indent=2))


@cli.command(name="lm-assets")
def lm_assets() -> None:
    """List Lunch Money manually-managed assets (to find --asset-id)."""
    lm = LunchMoney()
    data = lm.assets()
    assets = data.get("assets", [])
    if not assets:
        click.echo("No manually-managed assets. Create one in Lunch Money for the SEB account.")
        return
    for a in assets:
        click.echo(
            f"  asset_id={a.get('id')}  "
            f"{a.get('name')!r}  "
            f"[{a.get('type_name')}/{a.get('subtype_name','')}]  "
            f"{a.get('institution_name','')}  "
            f"bal={a.get('balance')} {a.get('currency')}"
        )


@cli.command()
@click.option("--account-uid", help="Enable Banking account uid (default: first in session).")
@click.option("--asset-id", type=int, default=None, help="Lunch Money asset id to attach to.")
@click.option("--date-from", default=None, help="ISO date lower bound (YYYY-MM-DD).")
@click.option("--dry-run/--commit", default=True, help="Print proposed inserts vs POST to Lunch Money.")
@click.option("--limit", type=int, default=10, help="Rows to preview in dry-run.")
@click.option(
    "--include-pending",
    is_flag=True,
    default=False,
    help="Include PDNG transactions. Off by default: pending rows have no "
    "entry_reference, so their external_id is a hash of the value date — when "
    "they later book they get a real reference AND a different date, which "
    "would insert a second copy. Wait for them to book instead.",
)
def sync(account_uid, asset_id, date_from, dry_run, limit, include_pending) -> None:
    """Fetch SEB transactions and (dry-run) preview or insert into Lunch Money."""
    session = _load_session()
    if not account_uid:
        accs = session.get("accounts", [])
        if not accs:
            raise click.ClickException("No account_uid and none in session.")
        account_uid = accs[0].get("uid")
        click.echo(f"Using account_uid={account_uid}")

    eb = EnableBanking()
    raw = eb.transactions(account_uid, date_from=date_from)
    click.echo(f"Fetched {len(raw)} transactions from Enable Banking.")

    if not include_pending:
        pending = [t for t in raw if t.get("status") == "PDNG"]
        if pending:
            raw = [t for t in raw if t.get("status") != "PDNG"]
            click.echo(
                f"Skipping {len(pending)} pending (PDNG) — they re-appear with a "
                f"stable id once booked. Use --include-pending to override."
            )

    mapped = mapper.map_all(raw, asset_id)

    if dry_run:
        click.echo(f"\n--- DRY RUN: first {min(limit, len(mapped))} proposed inserts ---")
        for r, m in list(zip(raw, mapped))[:limit]:
            click.echo(json.dumps({"mapped": m}, ensure_ascii=False))
        click.echo(f"\n{len(mapped)} would be sent. Re-run with --commit to insert.")
        return

    lm = LunchMoney()
    result = lm.insert_transactions(mapped)
    click.echo(f"Inserted: {json.dumps(result)}")


if __name__ == "__main__":
    cli()
