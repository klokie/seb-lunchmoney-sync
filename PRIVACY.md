# Privacy Notice

**Last updated: 2026-06-15**

`seb-lunchmoney-sync` is a personal, single-user application operated by its
owner for their own use only. It is not offered to, or used by, any other party.

## What it does

The application uses [Enable Banking](https://enablebanking.com) (a licensed
Account Information Service Provider regulated by the Finnish Financial
Supervisory Authority) to access **the owner's own bank accounts only**, with
the owner's explicit BankID consent. It retrieves account balances and
transaction data and imports that data into the owner's own
[Lunch Money](https://lunchmoney.app) account for personal financial tracking.

## Data handling

- **Whose data:** only the account owner's own banking data. No third-party
  data is accessed.
- **Access basis:** the owner's explicit consent via BankID (PSD2 / Open
  Banking), renewed at least every 90 days.
- **Use:** the data is used solely to populate the owner's personal Lunch Money
  account.
- **Sharing:** the data is not sold, shared, or disclosed to any third party.
  It moves only between the owner's bank (via Enable Banking) and the owner's
  Lunch Money account.
- **Storage:** processed locally / in the owner's own infrastructure.
  Credentials (API keys, signing key) are stored as secrets, never in source
  control.
- **Retention:** transaction data is retained in the owner's Lunch Money
  account at the owner's discretion.

## Contact

For any data protection question, contact the owner at `klokie@klokie.com`.
