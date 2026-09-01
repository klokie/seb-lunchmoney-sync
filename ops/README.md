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

## Linux (systemd user units)

```bash
cp ops/systemd/seb-lunchmoney-sync.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now seb-lunchmoney-sync.timer
```

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
