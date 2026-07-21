# seb-lunchmoney-sync

Personal tool to fetch my own SEB bank account transactions via
[Enable Banking](https://enablebanking.com) and import them into
[Lunch Money](https://lunchmoney.app).

Built to route around the broken Lunch Flow / GoCardless SEB integration.

## Architecture

```
SEB Företag ──(BankID consent)──> Enable Banking API ──> sync script ──> Lunch Money API
                                  (fetch transactions)   (dedupe + map)  (insert_transactions)
```

- **Aggregator:** Enable Banking (Restricted Production), JWT-signed REST API.
- **Sink:** Lunch Money API (`POST /v1/transactions`), batch insert with
  `external_id` dedupe.
- **Schedule:** daily (cron / Lambda).

## Status

Scaffold + first implementation. Auth flow, transaction fetch, mapping, and
Lunch Money insert are wired with a `--dry-run` default. Field shapes from
Enable Banking are per the public docs and should be confirmed on the first
live `sync --dry-run` (it prints the mapped objects).

See [PRIVACY.md](PRIVACY.md) and [TERMS.md](TERMS.md) (the required URLs for the
Enable Banking application registration).

## Install

```bash
cd ~/src/seb-lunchmoney-sync
make install
make run ARGS='--help'        # or: ./.venv/bin/seb-sync --help
```

> **Do not run a bare `pip install -e .`.** On these machines `pip`/`python3`
> resolve to Anaconda at `/opt/homebrew/anaconda3/bin` — which is both broken
> ("bad interpreter") on some boxes and pollutes the conda **base** env on
> others, producing a wall of `ERROR: ... dependency conflicts` about conda's
> own packages. The `Makefile` always builds an isolated venv from Homebrew's
> `python3.13`, sidestepping both problems. Override the interpreter with
> `make install PY=/path/to/python3` if needed.

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
seb-sync auth                       # one-time BankID consent (~90-day session)
seb-sync accounts                   # list authorized accounts + uids
seb-sync sync --dry-run             # fetch + map, print proposed inserts (no POST)
seb-sync sync --asset-id <N> --commit   # insert into Lunch Money
```

## Components

| Module               | Role                                                       |
| -------------------- | ---------------------------------------------------------- |
| `config.py`          | env-driven config + defaults                               |
| `secrets.py`         | resolve key/app_id/token from 1Password (`op`, pinned acct)|
| `enablebanking.py`   | JWT auth, consent flow, sessions, transactions             |
| `lunchmoney.py`      | thin Lunch Money client (`POST /v1/transactions`)          |
| `mapper.py`          | EB transaction → Lunch Money insert (+ `external_id` dedupe)|
| `callback_server.py` | one-shot HTTPS `localhost:8080` listener for the redirect  |
| `cli.py`             | `auth` / `accounts` / `sync` commands                      |

Reference pattern: `ynab-api-import` (aggregator → budgeting app).

## Secrets

The Enable Banking private key and `application_id` live **outside this repo**
(`~/.config/enablebanking/` locally, 1Password for durable storage). Never
commit `*.pem` or `.env`.
