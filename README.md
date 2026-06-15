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

Early scaffold. See [PRIVACY.md](PRIVACY.md) and [TERMS.md](TERMS.md) (required
URLs for the Enable Banking application registration).

## Secrets

The Enable Banking private key and `application_id` live **outside this repo**
(`~/.config/enablebanking/` locally, 1Password for durable storage). Never
commit `*.pem` or `.env`.
