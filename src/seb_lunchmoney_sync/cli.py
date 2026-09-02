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
import urllib.parse
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


def _confirm_target_session() -> None:
    """Warn before a bank round if the destination already holds a session.

    `auth` writes to `config.session_path` (EB_SESSION_PATH, default
    session.json). Authorizing a *second* consent — a business one alongside a
    working personal one — silently destroys the first unless that variable is
    set. Ask up front, not after the bank round: a prompt you hit *after* SCA
    means re-doing the whole thing to answer it.
    """
    p = Path(os.path.expanduser(config.session_path))
    if not p.exists():
        return
    try:
        cur = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return
    click.echo(
        f"! {p} already holds a {cur.get('psu_type')} session with "
        f"{len(cur.get('accounts', []))} account(s), valid until "
        f"{(cur.get('access') or {}).get('valid_until')}"
    )
    click.echo("  Set EB_SESSION_PATH to keep both (e.g. a session-business.json).")
    click.confirm("  Overwrite it?", abort=True)


def _code_from(raw: str) -> str:
    """Accept either a bare code or the whole callback URL pasted from the
    browser's address bar — which is what you actually have in hand when the
    redirect failed to reach the listener."""
    raw = raw.strip()
    if "code=" in raw:
        qs = urllib.parse.urlparse(raw).query or raw.split("?", 1)[-1]
        found = urllib.parse.parse_qs(qs).get("code")
        if found:
            return found[0]
    return raw


@_cli.command()
@click.option(
    "--psu-type",
    type=click.Choice(["personal", "business"]),
    default=None,
    help="Must match the 'usage type' used when linking in the EB control panel.",
)
@click.option(
    "--manual",
    is_flag=True,
    default=False,
    help="Don't run the callback listener. Prints the consent URL and waits for "
    "you to paste the code (or the whole redirect URL) back. Use this whenever "
    "the browser is on a different machine than this command — over SSH the "
    "redirect hits the browser host's localhost, not this one.",
)
@click.option(
    "--code",
    default=None,
    help="Exchange an authorization code you already have. Recovers a run whose "
    "redirect never reached the listener, without spending another bank round — "
    "codes are short-lived, so do it promptly. Accepts the full callback URL too.",
)
def auth(psu_type, manual, code) -> None:
    """One-time BankID consent. Opens the bank's auth page, captures the
    redirect, and stores the session (valid ~90 days) at EB_SESSION_PATH.

    The default flow serves a one-shot HTTPS listener on localhost:8080, which
    only works if the browser completing the consent runs on THIS machine. If
    it doesn't (SSH, headless, a remote container), use `--manual` and paste the
    code back, or `--code` to redeem one you already captured.
    """
    eb = EnableBanking()
    _confirm_target_session()

    if code:
        session = eb.create_session(_code_from(code))
        _save_session(session)
        click.echo(f"Session saved → {config.session_path}")
        _describe_session(session)
        return

    click.echo(f"psu_type={psu_type or config.eb_psu_type}")
    started = eb.start_authorization(psu_type=psu_type)
    url = started.get("url")
    click.echo(f"Opening BankID consent:\n  {url}\n")

    if manual:
        click.echo("Complete the consent in any browser, then paste what the")
        click.echo("redirect lands on — the whole URL is fine, code= is enough.")
        raw = click.prompt("code (or callback URL)")
        session = eb.create_session(_code_from(raw))
    else:
        click.echo(
            "(Your browser will warn about the self-signed localhost cert — proceed.)"
        )
        if url:
            webbrowser.open(url)
        try:
            captured = callback_server.wait_for_code()
        except OSError as exc:
            # Almost always a previous `auth` still sitting on the port. The
            # bank round has already been spent by this point, so say how to
            # rescue it rather than just dying.
            raise click.ClickException(
                f"Could not listen on {config.callback_host}:{config.callback_port} "
                f"({exc}).\n"
                f"  Something else holds the port — often an earlier `auth` still "
                f"waiting. Find it with:  ss -lntp | grep {config.callback_port}\n"
                f"  This consent round is still usable: complete it in the browser, "
                f"then run\n"
                f"    seb-sync auth --code '<the callback URL>'"
            ) from exc
        session = eb.create_session(captured)

    _save_session(session)
    click.echo(f"Session saved → {config.session_path}")
    _describe_session(session)


def _describe_session(session: dict) -> None:
    click.echo(f"  psu_type    {session.get('psu_type')}")
    click.echo(f"  valid_until {(session.get('access') or {}).get('valid_until')}")
    for acc in session.get("accounts", []):
        iban = ((acc.get("account_id") or {}).get("iban")) or ""
        masked = f"{iban[:6]}…{iban[-4:]}" if len(iban) > 10 else iban
        click.echo(f"  {masked}  {acc.get('product')}  uid={acc.get('uid')}")


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


def _amount_key(value) -> str:
    return f"{abs(float(value)):.2f}"


def select_new(
    mapped: list[dict], existing: list[dict], start: str
) -> list[dict]:
    """Transactions to insert: those Lunch Money doesn't already have by
    `external_id`.

    Deliberately the *only* dedup rule for steady-state syncing. Each real
    transaction has its own stable Enable Banking `entry_reference`, so two
    genuine same-amount/same-payee purchases get distinct external_ids and are
    both kept, while a re-run of the same transaction reuses its id and is
    skipped. No amount/date heuristic — that would silently drop legitimate
    repeated purchases (see `find_cross_source` for the migration-only case).

    Pure and side-effect free so it can be unit-tested without the network.
    """
    seen_ids = {t.get("external_id") for t in existing if t.get("external_id")}
    out = []
    for m in mapped:
        if not m.get("date") or m["date"] < start:
            continue
        if m.get("external_id") in seen_ids:
            continue
        out.append(m)
    return out


def find_cross_source(
    mapped: list[dict], existing: list[dict], window_days: int = 3
) -> list[tuple[dict, list[dict]]]:
    """Fresh transactions that are new by `external_id` yet look like they may
    already exist from another source — same |amount| and payee within
    `window_days`, but a different id (e.g. a prior Lunch Flow / GoCardless row,
    which the two providers date 1–2 days apart).

    Read-only and non-committal on purpose: it *surfaces* ambiguity for a human
    during a migration backfill instead of guessing. Returns
    (fresh_txn, [matching_existing_rows]) pairs. Pure/testable.
    """
    seen_ids = {t.get("external_id") for t in existing if t.get("external_id")}
    by_key: dict[tuple[str, str], list[dict]] = {}
    for t in existing:
        by_key.setdefault(
            (_amount_key(t["amount"]), _norm_payee(t.get("payee"))), []
        ).append(t)

    pairs: list[tuple[dict, list[dict]]] = []
    for m in mapped:
        if m.get("external_id") in seen_ids:
            continue
        key = (_amount_key(m["amount"]), _norm_payee(m["payee"]))
        d = dt.date.fromisoformat(m["date"])
        near = [
            t
            for t in by_key.get(key, [])
            if abs((d - dt.date.fromisoformat(t["date"])).days) <= window_days
        ]
        if near:
            pairs.append((m, near))
    return pairs


def find_foreign_duplicates(
    mapped: list[dict], existing: list[dict], window_days: int = 3
) -> list[tuple[dict, dict]]:
    """Rows already in Lunch Money that duplicate one of *ours* but arrived
    from a different sync.

    `select_new` cannot see this. It dedupes on `external_id`, so when a second
    feed (a Lunch Flow / GoCardless connection assumed dead, say) inserts the
    same transaction under its own id *after* we inserted ours, both rows sit
    in the ledger and nothing ever complains. That happened here over
    2026-07/08: 94 duplicated rows across three accounts, unnoticed for six
    weeks, roughly doubling reported spending.

    "Ours" is the set of external_ids Enable Banking just gave us for this
    window — no guessing about which id shape belongs to whom, and it keeps
    working if the other provider changes theirs. Anything else in the window
    carrying an external_id (so: from some automated feed, not a hand-entered
    row) that matches one of ours on |amount| + payee within `window_days` is
    reported.

    Pairing is greedy, 1:1 and date-nearest, so a genuine repeated purchase —
    two identical coffees days apart, or the recurring 818 kr Swish payments —
    can be claimed at most once and cannot fan out into a pile of false
    positives.

    Read-only by design: returns (foreign_row, our_row) pairs for a human. The
    two feeds date and name the same transaction differently, so deleting
    automatically here would eventually destroy real data — the exact class of
    bug that got the old fuzzy match removed from the insert path.
    """
    ours_ids = {m.get("external_id") for m in mapped if m.get("external_id")}
    ours_rows = [t for t in existing if t.get("external_id") in ours_ids]

    by_key: dict[tuple[str, str], list[dict]] = {}
    for t in ours_rows:
        by_key.setdefault(
            (_amount_key(t["amount"]), _norm_payee(t.get("payee"))), []
        ).append(t)

    claimed: set[int] = set()
    pairs: list[tuple[dict, dict]] = []
    for f in sorted(existing, key=lambda t: t["date"]):
        eid = f.get("external_id")
        if not eid or eid in ours_ids:
            continue
        d = dt.date.fromisoformat(f["date"])
        best: tuple[int, dict] | None = None
        for t in by_key.get(
            (_amount_key(f["amount"]), _norm_payee(f.get("payee"))), []
        ):
            if id(t) in claimed:
                continue
            gap = abs((d - dt.date.fromisoformat(t["date"])).days)
            if gap <= window_days and (best is None or gap < best[0]):
                best = (gap, t)
        if best is not None:
            claimed.add(id(best[1]))
            pairs.append((f, best[1]))
    return pairs


def _load_account_map(map_file: str) -> dict:
    return json.loads(Path(os.path.expanduser(map_file)).read_text())


# Prefer the *booked* balance over the available one. `sync` skips pending
# transactions, so booked is the figure that reconciles with the ledger we
# actually write; available (ITAV) includes pending and would never tie out.
# SEB returns ITBD (interim booked) rather than CLBD.
_BALANCE_PREFERENCE = ("CLBD", "PRCD", "ITBD", "ITAV", "XPCD")


def _pick_balance(balances: list[dict]) -> tuple[str | None, str | None, str | None]:
    """-> (amount, currency, balance_type). Defensive: field shapes vary."""
    by_type: dict[str, dict] = {}
    for b in balances:
        t = (b.get("balance_type") or b.get("name") or "").upper()
        by_type.setdefault(t, b)
    for want in _BALANCE_PREFERENCE:
        b = by_type.get(want)
        if b:
            amt = b.get("balance_amount") or {}
            if amt.get("amount") is not None:
                return str(amt["amount"]), amt.get("currency"), want
    for b in balances:  # nothing recognised — take whatever has an amount
        amt = b.get("balance_amount") or {}
        if amt.get("amount") is not None:
            return str(amt["amount"]), amt.get("currency"), (
                b.get("balance_type") or "?"
            )
    return None, None, None


@_cli.command()
@click.option("--dry-run/--commit", default=True, help="Preview vs write to Lunch Money.")
@click.option(
    "--map-file",
    default="~/.config/enablebanking/accounts.json",
    show_default=True,
    help="IBAN → asset_id map (same file sync-all uses).",
)
def balances(dry_run, map_file) -> None:
    """Write current bank balances onto the mapped Lunch Money assets.

    Separate from `sync-all` on purpose: this spends a second PSD2 request per
    account, and the bank allows only ~4 per account per day. Run it once
    daily, not on every sync.
    """
    session = _load_session()
    mapping = _load_account_map(map_file)
    eb = EnableBanking()
    lm = LunchMoney()
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    for acc in session.get("accounts", []):
        iban = (acc.get("account_id") or {}).get("iban")
        entry = mapping.get(iban or "")
        if not entry:
            continue
        asset_id, label = entry["asset_id"], entry["label"]
        raw = eb.balances(acc["uid"])
        amount, currency, btype = _pick_balance(raw)
        if amount is None:
            click.echo(f"– {label}: no usable balance in {len(raw)} entries")
            continue
        click.echo(f"• {label}: {amount} {currency or ''} [{btype}] → asset {asset_id}")
        if not dry_run:
            lm.update_asset_balance(asset_id, amount, now)

    click.echo("\nDRY RUN — use --commit to write." if dry_run else "\nBalances updated.")


@_cli.command(name="sync-all")
@click.option("--dry-run/--commit", default=True, help="Preview vs actually insert.")
@click.option(
    "--lookback-days",
    type=int,
    default=14,
    show_default=True,
    help="How far back to re-examine. Bigger is safe — rows already in Lunch "
    "Money (by external_id) are filtered out, so this only costs API time.",
)
@click.option(
    "--map-file",
    default="~/.config/enablebanking/accounts.json",
    show_default=True,
    help="IBAN → asset_id map. Keyed by IBAN because account_uids rotate on "
    "every re-consent.",
)
@click.option(
    "--reconcile",
    is_flag=True,
    default=False,
    help="Read-only. Instead of inserting, list transactions that look like "
    "they already exist from another source (same amount+payee within a few "
    "days but a different external_id) — e.g. a prior Lunch Flow / GoCardless "
    "sync. Run this ONCE when adopting an account that already has history, to "
    "spot overlap before a wide backfill. Never writes.",
)
@click.option(
    "--reconcile-window",
    type=int,
    default=3,
    show_default=True,
    help="Days of slack when looking for cross-source matches under --reconcile.",
)
@click.option(
    "--dup-window",
    type=int,
    default=3,
    show_default=True,
    help="Days of slack for the always-on duplicate guard (rows another sync "
    "wrote on top of ours). 0 disables the guard.",
)
@click.option(
    "--dup-limit",
    type=int,
    default=5,
    show_default=True,
    help="Duplicate pairs to print per account before summarising the rest.",
)
def sync_all(
    dry_run, lookback_days, map_file, reconcile, reconcile_window, dup_window, dup_limit
) -> None:
    """Sync every mapped account, inserting only what Lunch Money lacks.

    Dedup is by `external_id` alone. Enable Banking gives every transaction a
    stable `entry_reference`, so re-running never duplicates, and two genuinely
    repeated purchases (same amount, same payee, days apart) are both kept —
    they have different references. This is safe to run unattended at any
    frequency and never silently drops a real transaction.

    The one thing external_id can't catch is the *same* transaction arriving
    under a different id from another sync (a prior Lunch Flow / GoCardless
    feed). Use `--reconcile` (read-only) once before adopting an account that
    already has such history, or simply scope the first run to your switch-over
    date — don't back-fill across a window another tool already covered.

    That is only the *migration* case, though. If the other feed is still live
    it keeps writing on top of us forever, so every run also reports rows that
    look duplicated from another source (`--dup-window 0` to silence). It only
    ever warns; deleting is a human's call.
    """
    session = _load_session()
    mapping = _load_account_map(map_file)

    today = dt.date.today()
    start = (today - dt.timedelta(days=lookback_days)).isoformat()
    end = (today + dt.timedelta(days=365)).isoformat()  # pending sit in the future

    eb = EnableBanking()
    lm = LunchMoney()
    grand_total = 0
    grand_foreign = 0

    for acc in session.get("accounts", []):
        iban = (acc.get("account_id") or {}).get("iban")
        entry = mapping.get(iban or "")
        if not entry:
            click.echo(f"– {acc.get('product')} ({iban}): not in map, skipping.")
            continue

        asset_id, label = entry["asset_id"], entry["label"]
        existing = lm.transactions(asset_id, start, end)
        raw = [t for t in eb.transactions(acc["uid"], date_from=start)
               if t.get("status") != "PDNG"]
        mapped = mapper.map_all(raw, asset_id)

        if reconcile:
            pairs = find_cross_source(mapped, existing, reconcile_window)
            click.echo(
                f"{'?' if pairs else '–'} {label}: {len(pairs)} possible "
                f"cross-source duplicate(s) among {len(mapped)} from SEB"
            )
            for m, near in pairs:
                click.echo(f"    NEW  {m['date']}  {m['amount']:>12}  {m['payee'][:34]}")
                for t in near:
                    src = "ours" if t.get("external_id") else "other-source"
                    click.echo(
                        f"    ~in-LM {t['date']}  {t['amount']:>12}  "
                        f"{(t.get('payee') or '')[:30]:<30} [{src}]"
                    )
            continue

        fresh = select_new(mapped, existing, start)

        click.echo(
            f"{'•' if fresh else '–'} {label}: {len(existing)} in LM, "
            f"{len(mapped)} from SEB since {start} → {len(fresh)} new"
        )
        for m in fresh:
            click.echo(f"    {m['date']}  {m['amount']:>12}  {m['payee'][:38]}")

        # Costs nothing extra — both sides are already in memory — and it is
        # the only thing that notices a second sync quietly double-writing into
        # the same asset. Warn, never delete: see find_foreign_duplicates.
        foreign = (
            find_foreign_duplicates(mapped, existing, dup_window)
            if dup_window > 0
            else []
        )
        if foreign:
            grand_foreign += len(foreign)
            click.echo(
                f"  ! {label}: {len(foreign)} row(s) look like duplicates from "
                f"another sync — review, nothing was deleted"
            )
            for f, ours in foreign[:dup_limit]:
                click.echo(
                    f"      other {f['date']}  {f['amount']:>11}  "
                    f"{(f.get('payee') or '')[:30]:<30} id={f.get('id')}"
                )
                click.echo(
                    f"      ours  {ours['date']}  {ours['amount']:>11}  "
                    f"{(ours.get('payee') or '')[:30]:<30} id={ours.get('id')}"
                )
            if len(foreign) > dup_limit:
                click.echo(f"      … and {len(foreign) - dup_limit} more")

        grand_total += len(fresh)
        if fresh and not dry_run:
            res = lm.insert_transactions(fresh)
            click.echo(f"    inserted {len(res.get('ids', []))}")

    if reconcile:
        click.echo("\nRECONCILE (read-only) — nothing written.")
    elif dry_run:
        click.echo(f"\nDRY RUN — {grand_total} would be inserted. Use --commit.")
    else:
        click.echo(f"\nDone — {grand_total} inserted.")

    # Last line so it survives a `tail` of the log, and worded to be greppable.
    if grand_foreign:
        click.echo(
            f"! DUPLICATE WARNING — {grand_foreign} row(s) across all accounts "
            f"appear to come from another sync writing into the same assets. "
            f"Nothing deleted. Disconnect the other feed, then clean up by hand."
        )


if __name__ == "__main__":
    cli()
