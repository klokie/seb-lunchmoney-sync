"""Command-line entry points.

  seb-sync auth                 # one-time BankID consent → saves session.json
  seb-sync accounts             # list authorized accounts + uids
  seb-sync sync --dry-run       # fetch + map, print proposed inserts (no POST)
  seb-sync sync --account-uid X --asset-id N   # actually insert into Lunch Money
"""

from __future__ import annotations

import datetime as dt
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
def _cli() -> None:
    """SEB → Lunch Money sync."""


def cli() -> None:
    """Entry point. Turns expected runtime failures (rate limiting, a missing
    `op`, an unusable session) into a single-line error, because these mostly
    surface in an unattended log where a traceback is just noise."""
    try:
        _cli.main(standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code)
    except click.Abort:
        raise SystemExit(130)
    except RuntimeError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)


@_cli.command()
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


@_cli.command()
@click.option(
    "--env-file",
    default="~/.config/enablebanking/env",
    show_default=True,
    help="Where to write the env file for unattended runs.",
)
@click.option("--force", is_flag=True, help="Overwrite an existing env file.")
def bootstrap(env_file, force) -> None:
    """Materialize secrets from 1Password into a 0600 env file, once.

    Unattended runs (cron / LaunchAgent / openclaw) can't answer a 1Password
    unlock prompt, and this plan has no service accounts. So resolve everything
    once, interactively, and let scheduled runs read the env file instead.

    Re-run after rotating the key or the Lunch Money token.
    """
    from . import secrets as _secrets

    path = Path(os.path.expanduser(env_file))
    if path.exists() and not force:
        raise click.ClickException(f"{path} exists. Re-run with --force to replace it.")

    click.echo("Reading from 1Password (expect one unlock prompt)…")
    app_id = _secrets._op_read(
        f"op://{config.op_vault}/{config.eb_item}/application_id"
    )
    lm_token = _secrets._op_read(
        f"op://{config.op_vault}/{config.lm_op_item}/{config.lm_op_field}"
    )

    # The private key is multi-line, so it goes on disk and the env file points
    # at it rather than trying to inline a PEM.
    key_path = Path(os.path.expanduser(
        "~/.config/enablebanking/enablebanking-private.pem"))
    if not key_path.exists():
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(
            _secrets._op_read(f"op://{config.op_vault}/{config.eb_item}/credential")
        )
        key_path.chmod(0o600)
        click.echo(f"Wrote private key → {key_path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Written by `seb-sync bootstrap`. Contains a live Lunch Money token —\n"
        "# keep mode 0600 and out of git. Re-run bootstrap after rotating.\n"
        f"EB_APPLICATION_ID={app_id}\n"
        f"EB_PRIVATE_KEY_PATH={key_path}\n"
        f"LUNCHMONEY_API_TOKEN={lm_token}\n"
    )
    path.chmod(0o600)
    click.echo(f"Wrote {path} (0600)\n")
    click.echo("Unattended runs — source it first, then `op` is never called:\n")
    click.echo(f"  set -a; . {path}; set +a")
    click.echo("  seb-sync sync --account-uid <uid> --asset-id <id> --date-from <d> --commit")


@_cli.command()
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
    click.echo("✓ JWT accepted — cert matches.")

    # Consent expires every ~90 days; a scheduled run would otherwise just start
    # failing one morning with no explanation.
    p = Path(os.path.expanduser(config.session_path))
    if not p.exists():
        click.echo("! No session yet — run `seb-sync auth`.")
    else:
        session = json.loads(p.read_text())
        valid_until = (session.get("access") or {}).get("valid_until")
        if valid_until:
            expires = dt.datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
            days = (expires - dt.datetime.now(dt.timezone.utc)).days
            accounts = len(session.get("accounts", []))
            marker = "✓" if days > 14 else "!"
            click.echo(
                f"{marker} Consent: {accounts} account(s), "
                f"{days} days left (until {expires.date()})."
            )
            if days <= 14:
                click.echo("  Re-authorize soon: `seb-sync auth`.")
    click.echo(json.dumps(app, indent=2))


@_cli.command()
def accounts() -> None:
    """List accounts from the saved session."""
    session = _load_session()
    accs = session.get("accounts", [])
    if not accs:
        click.echo("No accounts in session.")
        return
    for acc in accs:
        click.echo(json.dumps(acc, indent=2))


@_cli.command(name="lm-assets")
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


@_cli.command()
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


def _norm_payee(p: str | None) -> str:
    return (p or "").strip().upper()


@_cli.command(name="sync-all")
@click.option("--dry-run/--commit", default=True, help="Preview vs actually insert.")
@click.option(
    "--date-tolerance",
    type=int,
    default=3,
    show_default=True,
    help="Days of slack when matching against existing rows. Lunch Flow dated "
    "the same transaction 1-2 days off from Enable Banking, so exact date "
    "matching would duplicate the handover period.",
)
@click.option(
    "--lookback-days",
    type=int,
    default=14,
    show_default=True,
    help="How far back to re-examine. Bigger is safe — anything already in "
    "Lunch Money is filtered out, so this only costs a little API time.",
)
@click.option(
    "--map-file",
    default="~/.config/enablebanking/accounts.json",
    show_default=True,
    help="IBAN → asset_id map. Keyed by IBAN because account_uids rotate on "
    "every re-consent.",
)
def sync_all(dry_run, date_tolerance, lookback_days, map_file) -> None:
    """Sync every mapped account, inserting only what Lunch Money lacks.

    Built for repeated unattended runs. Rather than tracking a high-water mark
    (which loses transactions that post later on a day already synced), this
    re-examines a window and filters against what Lunch Money actually holds:

      * same `external_id`  → already inserted by this tool
      * same (date, |amount|) → almost certainly the same transaction from
        another sync (Lunch Flow/GoCardless rows carry different external_ids,
        so id-matching alone would happily duplicate them)

    That makes the run idempotent and safe at any frequency.
    """
    session = _load_session()
    mapping = json.loads(Path(os.path.expanduser(map_file)).read_text())

    today = dt.date.today()
    start = (today - dt.timedelta(days=lookback_days)).isoformat()
    end = (today + dt.timedelta(days=365)).isoformat()  # pending sit in the future

    eb = EnableBanking()
    lm = LunchMoney()
    grand_total = 0

    for acc in session.get("accounts", []):
        iban = (acc.get("account_id") or {}).get("iban")
        entry = mapping.get(iban or "")
        if not entry:
            click.echo(f"– {acc.get('product')} ({iban}): not in map, skipping.")
            continue

        asset_id, label = entry["asset_id"], entry["label"]
        existing = lm.transactions(asset_id, start, end)
        seen_ids = {t.get("external_id") for t in existing if t.get("external_id")}
        # (amount, payee) -> dates already present. Deliberately NOT keyed on
        # date: Lunch Flow/GoCardless dated the same transaction 1-2 days
        # differently from Enable Banking, so exact date matching would let
        # historical rows through as "new" and duplicate them.
        seen_by_amt: dict[tuple[str, str], list[dt.date]] = {}
        for t in existing:
            key = (f'{abs(float(t["amount"])):.2f}', _norm_payee(t.get("payee")))
            seen_by_amt.setdefault(key, []).append(dt.date.fromisoformat(t["date"]))

        raw = [t for t in eb.transactions(acc["uid"], date_from=start)
               if t.get("status") != "PDNG"]
        mapped = mapper.map_all(raw, asset_id)

        fresh = []
        for m in mapped:
            if not m["date"] or m["date"] < start:
                continue
            if m["external_id"] in seen_ids:
                continue
            key = (f'{abs(float(m["amount"])):.2f}', _norm_payee(m["payee"]))
            near = seen_by_amt.get(key, [])
            d = dt.date.fromisoformat(m["date"])
            if any(abs((d - o).days) <= date_tolerance for o in near):
                continue
            fresh.append(m)

        click.echo(
            f"{'•' if fresh else '–'} {label}: {len(existing)} in LM, "
            f"{len(mapped)} from SEB since {start} → {len(fresh)} new"
        )
        for m in fresh:
            click.echo(f"    {m['date']}  {m['amount']:>12}  {m['payee'][:38]}")

        grand_total += len(fresh)
        if fresh and not dry_run:
            res = lm.insert_transactions(fresh)
            click.echo(f"    inserted {len(res.get('ids', []))}")

    if dry_run:
        click.echo(f"\nDRY RUN — {grand_total} would be inserted. Use --commit.")
    else:
        click.echo(f"\nDone — {grand_total} inserted.")


if __name__ == "__main__":
    cli()
