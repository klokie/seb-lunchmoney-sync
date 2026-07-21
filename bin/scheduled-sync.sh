#!/bin/zsh
# Unattended SEB -> Lunch Money sync. Driven by launchd; see
# ops/com.klokie.seb-lunchmoney-sync.plist.
#
# Deliberately does NOT depend on the `op` CLI: launchd jobs cannot answer a
# 1Password unlock prompt. Secrets come from the env file written by
# `seb-sync bootstrap` (re-run that after rotating a credential).

set -u
setopt pipefail

REPO="${SEB_SYNC_REPO:-$HOME/src/seb-lunchmoney-sync}"
ENV_FILE="${SEB_SYNC_ENV:-$HOME/.config/enablebanking/env}"
LOG="${SEB_SYNC_LOG:-$HOME/Library/Logs/seb-lunchmoney-sync.log}"
BIN="$REPO/.venv/bin/seb-sync"

mkdir -p "$(dirname "$LOG")"

log() { print -r -- "$(date '+%Y-%m-%d %H:%M:%S')  $*" >>"$LOG"; }

log "--- run start ---"

if [[ ! -x "$BIN" ]]; then
  log "FATAL: $BIN missing. Rebuild with: cd $REPO && make install"
  exit 1
fi
if [[ ! -r "$ENV_FILE" ]]; then
  log "FATAL: $ENV_FILE missing. Create it with: $BIN bootstrap"
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

# Surfaces cert health and, importantly, consent expiry — consent lapses every
# ~90 days and re-auth needs BankID, so it can never be unattended. Without
# this the first symptom is transactions quietly going missing.
if ! check_out=$("$BIN" check 2>&1); then
  log "FAIL check: $check_out"
  exit 1
fi
log "$(print -r -- "$check_out" | grep -E '^[✓!]' | tr '\n' ' ')"

if ! sync_out=$("$BIN" sync-all --commit 2>&1); then
  log "FAIL sync-all:"
  print -r -- "$sync_out" | while IFS= read -r line; do log "  $line"; done
  exit 1
fi

print -r -- "$sync_out" | grep -Ev '^\s*$' | while IFS= read -r line; do
  log "  $line"
done
log "--- run ok ---"
