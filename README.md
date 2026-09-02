# seb-lunchmoney-sync

Sync European bank transactions into [Lunch Money](https://lunchmoney.app) via
[Enable Banking](https://enablebanking.com), a PSD2 account-information
provider with good Nordic coverage.

Built for **SEB (Sweden)** — which is what it has been tested against — but the
bank is configuration, not code: set `EB_ASPSP_NAME` / `EB_ASPSP_COUNTRY` for
any institution Enable Banking supports.

Useful if your bank has no native Lunch Money connection, or its managed sync
keeps breaking. Enable Banking's self-serve tier is free for personal use, so
you run the aggregator layer yourself.

## Architecture

```
Your bank ──(BankID / bank consent)──> Enable Banking API ──> sync ──> Lunch Money API
                                       (fetch transactions)  (map +   (insert_transactions)
                                                              dedupe)
```

- **Aggregator:** Enable Banking (Restricted Production), JWT-signed REST API.
- **Sink:** Lunch Money API (`POST /v1/transactions`), chunked insert with
  `external_id` dedupe.
- **Schedule:** twice a day; `sync-all` is idempotent, but the bank's PSD2
  quota (~4 requests/account/day) is the real limit. systemd and launchd units
  plus the reasoning are in [`ops/`](ops/README.md).

## Status

Working and in daily use against SEB. Auth, fetch, mapping, and insert are all
verified against live data; `sync` defaults to `--dry-run`.

Behaviour worth knowing before you point it at an account that already has
history:

- **`sync-all` only inserts what Lunch Money lacks**, deduping on `external_id`
  alone. Enable Banking gives every transaction a stable `entry_reference`, so
  re-running never duplicates and two genuine repeated purchases (same amount
  and payee, days apart) are both kept. Safe to run unattended at any frequency.
- **Migrating an account that already has history from another sync?** Point the
  first run at your switch-over date (`sync --date-from`) so you don't re-import
  rows a previous tool already added — `external_id` can't recognise them,
  because that other tool used different ids. `sync-all --reconcile` (read-only)
  lists any such overlap so you can check before a wide back-fill.
- **Turn the other sync off, don't just assume it's dead.** If it keeps writing,
  it writes _on top of_ this one forever: its rows carry its own ids, so
  `external_id` dedupe never sees them and the ledger silently doubles. Every
  `sync-all` run therefore also reports rows that look like duplicates from
  another source and ends with a greppable `DUPLICATE WARNING` line. It only
  ever warns — the two feeds date and name the same transaction differently, so
  deleting automatically would eventually destroy real data. `--dup-window 0`
  silences it.
- **Pending transactions are skipped by default.** They carry no stable id, so
  inserting one now means a duplicate when it books later. `--include-pending`
  if you want them anyway.
- **Consent expires (~90 days)** and renewal needs interactive bank auth, so a
  scheduled job can never be fully unattended. `seb-sync check` reports days
  remaining.

See [PRIVACY.md](PRIVACY.md) and [TERMS.md](TERMS.md) — the URLs required when
registering an Enable Banking application.

## Install

Requires Python 3.11+ and an [Enable Banking](https://enablebanking.com)
application (free self-serve; see [Setup](#setup)).

```bash
git clone https://github.com/klokie/seb-lunchmoney-sync
cd seb-lunchmoney-sync
make install                  # builds an isolated .venv
make run ARGS='--help'        # or: ./.venv/bin/seb-sync --help
```

`make install` deliberately builds a venv rather than running a bare
`pip install -e .`, which on machines where `python3` resolves to Anaconda
installs into the conda **base** environment and emits a wall of dependency
conflicts. Override the interpreter with `make install PY=/path/to/python3`.

## Setup

1. Register an application at
   [enablebanking.com](https://enablebanking.com) — Production (Restricted)
   lets you whitelist your own accounts. Choose _"Generate outside the browser
   and import public certificate"_ and paste in your certificate:
   ```bash
   openssl genrsa -out private.key 2048
   openssl req -new -x509 -days 3650 -key private.key -out public.crt \
     -subj "/CN=my-lunchmoney-sync"
   ```
   The certificate **cannot be replaced later** — rotating the key means
   registering a new application.
2. Link your account in the control panel ("Activate by linking accounts").
   Note the _usage type_ you pick: it must match `EB_PSU_TYPE`.
3. Copy `.env.example` → `.env` and fill in the ids, or run
   `seb-sync bootstrap` if you keep secrets in 1Password.
4. `seb-sync check` → `seb-sync auth` → `seb-sync sync-all --dry-run`.

Map your accounts to Lunch Money assets in
`~/.config/enablebanking/accounts.json`, keyed by IBAN (account uids rotate on
every re-consent):

```json
{
  "SE0000000000000000000000": {
    "asset_id": 123456,
    "label": "Everyday account"
  }
}
```

Find `asset_id` values with `seb-sync lm-assets`. Verify the pairing against
real data before committing — an account's reported product name is not always
what you assume it is.

## Secrets

Interactively, everything is read from **1Password** via the `op` CLI — the
Enable Banking private key and `application_id` from one item, the Lunch Money
token from another. Point at your own items with `OP_ACCOUNT`, `EB_ITEM` and
`LM_OP_ITEM`; see [`.env.example`](.env.example). Two gotchas worth knowing:

- **Reference items by ID, not title.** `op read` rejects titles containing
  characters like an em-dash: _"invalid character in secret reference"_.
- **Pin `OP_ACCOUNT`** if you are signed into more than one account, otherwise
  `op` resolves against whichever it prefers.

### Unattended runs

A cron/launchd job cannot answer a 1Password unlock prompt, and 1Password
service accounts need a Business/Teams plan. So resolve everything once and
let scheduled runs read the result:

```bash
seb-sync bootstrap     # one unlock prompt -> ~/.config/enablebanking/env (0600)
```

Scheduled runs source that file and never invoke `op`:

```bash
set -a; . ~/.config/enablebanking/env; set +a
seb-sync sync-all --commit
```

Of the three values, only the Lunch Money token is really a secret — the
`application_id` is public (it travels as the JWT `kid` on every request) and
the private key is already on disk at 0600. Re-run `bootstrap` after rotating
anything.

## Usage

```bash
seb-sync check                      # cert health + consent days remaining
seb-sync auth                       # one-time bank consent (~90-day session)
seb-sync auth --manual              # same, but paste the code back yourself
seb-sync auth --code '<url|code>'   # redeem a code a failed redirect left you
seb-sync accounts                   # list authorized accounts + uids
seb-sync lm-assets                  # list Lunch Money assets (to find asset_id)

seb-sync sync-all --dry-run         # preview across all mapped accounts
seb-sync sync-all --commit          # insert whatever Lunch Money is missing
seb-sync sync-all --reconcile       # read-only: overlap with a previous sync

# single account, explicit window
seb-sync sync --account-uid <uid> --asset-id <N> --date-from 2026-07-01 --dry-run
```

`sync-all` is what you schedule; `sync` is for one-off backfills where you want
to control the date range yourself.

> [!warning] `auth` over SSH needs `--manual`
> The default flow serves a one-shot HTTPS listener on `localhost:8080` and
> waits for the bank to redirect to it. That only works if the browser
> completing the consent runs on the **same machine** as the command — over SSH
> the redirect hits the browser host's localhost and the listener waits forever.
> Use `--manual` and paste the code back. If you only realise afterwards, the
> round is not wasted: copy the callback URL out of the address bar and run
> `seb-sync auth --code '<url>'` promptly, before the code expires.

> [!warning] `auth` writes to one file — set `EB_SESSION_PATH` for a second consent
> Authorizing a business consent alongside a personal one overwrites the first
> unless you redirect the write. `auth` now warns and asks before clobbering a
> populated session, _before_ spending the bank round.
>
> ```bash
> EB_SESSION_PATH=~/.config/enablebanking/session-business.json \
>   seb-sync auth --psu-type business --manual
> ```

> [!warning] Every call to the bank costs quota
> SEB caps unattended access at **~4 requests per account per day** and a
> `--dry-run` costs exactly as much as a `--commit`. The twice-daily schedule
> already spends 3, so budget your manual runs. `ASPSP_RATE_LIMIT_EXCEEDED`
> means wait for the bank's day to roll over; retrying does not help.

## Components

| Module               | Role                                                         |
| -------------------- | ------------------------------------------------------------ |
| `config.py`          | env-driven config + defaults                                 |
| `secrets.py`         | resolve key/app_id/token from 1Password (`op`, pinned acct)  |
| `enablebanking.py`   | JWT auth, consent flow, sessions, transactions               |
| `lunchmoney.py`      | thin Lunch Money client (`POST /v1/transactions`)            |
| `mapper.py`          | EB transaction → Lunch Money insert (+ `external_id` dedupe) |
| `callback_server.py` | one-shot HTTPS `localhost:8080` listener for the redirect    |
| `cli.py`             | `auth` / `accounts` / `sync` commands                        |

Reference pattern: `ynab-api-import` (aggregator → budgeting app).

## Secrets

The Enable Banking private key and `application_id` live **outside this repo**
(`~/.config/enablebanking/` locally, 1Password for durable storage). Never
commit `*.pem` or `.env`.
