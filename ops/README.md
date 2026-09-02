# Scheduling the sync

Two scheduler flavours, same wrapper (`bin/scheduled-sync.sh`) and same
behaviour: `check` → `sync-all --commit` → `balances --commit` (mornings only).

**Edit the files here, then reinstall.** Hand-editing the installed copy means
the next reinstall silently reverts it.

## Why twice a day

SEB enforces the PSD2 unattended quota of **~4 requests per account per day**.
`sync-all` costs 1 request per account, `balances` another 1. The 08:10 run
spends 2 and the 20:10 run spends 1, leaving **one spare** for a manual run. A
`--dry-run` costs exactly as much as a `--commit` — it still calls the bank.

Going hourly would spend the budget before lunch and every later run would fail
with `ASPSP_RATE_LIMIT_EXCEEDED` until the bank's day rolls over. Retrying does
not help.

## Two profiles

One bank consent covers one `psu_type`. A business consent alongside a personal
one is therefore a second _profile_, not a second account in the same map:

|                             | personal                  | business                 |
| --------------------------- | ------------------------- | ------------------------ |
| session (`EB_SESSION_PATH`) | `session.json`            | `session-business.json`  |
| map (`SEB_SYNC_MAP`)        | `accounts.json`           | `accounts-business.json` |
| log (`SEB_SYNC_LOG`)        | `seb-lunchmoney-sync.log` | `…-business.log`         |
| timer                       | 08:10 / 20:10             | 12:10 / 23:10            |

Both run the same `bin/scheduled-sync.sh`; only the environment differs. The
quota is per _account_, so the profiles do not compete for it — the offset slots
are about not running two consents at once and keeping the logs readable.

## Linux (systemd user units)

```bash
cp ops/systemd/seb-lunchmoney-sync.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now seb-lunchmoney-sync.timer

# business profile, once its consent and map exist
cp ops/systemd/seb-lunchmoney-sync-business.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now seb-lunchmoney-sync-business.timer
```

> [!danger] Don't enable a profile while another sync still feeds those assets
> Two live feeds writing the same Lunch Money asset duplicate every row: the
> dedupe is per-`external_id` and each feed uses its own ids. Disconnect the old
> one first, then scope the first run to the switch-over date.

Check it:

```bash
systemctl --user list-timers seb-lunchmoney-sync.timer   # next + last run
systemctl --user start seb-lunchmoney-sync.service       # run now
journalctl --user -u seb-lunchmoney-sync.service -n 50   # unit-level output
tail -f ~/.local/state/seb-lunchmoney-sync.log           # what it actually did
systemctl --user disable --now seb-lunchmoney-sync.timer # stop
```

The units use `%h`, so they work for any user without editing.

## macOS (launchd)

```bash
cp ops/com.klokie.seb-lunchmoney-sync.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.klokie.seb-lunchmoney-sync.plist
```

Check it:

```bash
launchctl print gui/$(id -u)/com.klokie.seb-lunchmoney-sync      # status, run count
launchctl kickstart -p gui/$(id -u)/com.klokie.seb-lunchmoney-sync  # run now
tail -f ~/Library/Logs/seb-lunchmoney-sync.log                   # what it did
launchctl bootout gui/$(id -u)/com.klokie.seb-lunchmoney-sync    # stop
```

`RunAtLoad` is deliberately **false** — loading the agent should not fire a
sync and spend quota.

> [!warning] Run this on exactly ONE machine
> Two hosts on the same consent double-spend the PSD2 quota and race on
> `session.json`. Before enabling on a new machine, disable it on the old one.
