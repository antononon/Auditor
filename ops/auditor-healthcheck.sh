#!/usr/bin/env bash
# Keeps the bot running and, more importantly, notices when the NotebookLM
# session dies. The previous setup failed silently for two months because
# nothing ever checked whether auth actually worked -- only that a file existed.
set -uo pipefail

BOT_SERVICE="${AUDITOR_BOT_SERVICE:-auditor-bot.service}"
PROJECT_DIR="${AUDITOR_PROJECT_DIR:-/opt/Auditor}"
VENV_BIN="${PROJECT_DIR}/.venv/bin"
ENV_FILE="${AUDITOR_ENV_FILE:-/etc/auditor-bot.env}"
STATE_DIR="${AUDITOR_STATE_DIR:-/var/lib/auditor}"
RUN_AS_USER="${AUDITOR_RUN_AS_USER:-ubuntu}"
ALERT_STAMP="${STATE_DIR}/last-auth-alert"
# Don't re-alert more often than this, or a dead session becomes a notification flood.
ALERT_COOLDOWN_SECONDS="${AUDITOR_ALERT_COOLDOWN:-21600}"

export NOTEBOOKLM_HOME="${NOTEBOOKLM_HOME:-${PROJECT_DIR}/.notebooklm}"

log() { printf 'auditor-healthcheck: %s\n' "$*"; }

mkdir -p "$STATE_DIR" 2>/dev/null || true

# shellcheck disable=SC1090
[[ -r "$ENV_FILE" ]] && set -a && . "$ENV_FILE" && set +a

notify() {
  local text="$1"
  local chat_id="${TELEGRAM_ALERT_CHAT_ID:-${TELEGRAM_ALLOWED_USER_IDS%%,*}}"
  if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "$chat_id" ]]; then
    log "no telegram credentials, skipping alert"
    return
  fi
  curl -sS --max-time 15 \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${chat_id}" \
    --data-urlencode "text=${text}" >/dev/null \
    && log "alert sent" || log "alert failed to send"
}

alert_throttled() {
  local now last
  now="$(date +%s)"
  last=0
  [[ -f "$ALERT_STAMP" ]] && last="$(cat "$ALERT_STAMP" 2>/dev/null || echo 0)"
  if (( now - last < ALERT_COOLDOWN_SECONDS )); then
    log "alert suppressed (cooldown)"
    return
  fi
  echo "$now" > "$ALERT_STAMP"
  notify "$1"
}

log "heartbeat start: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

if ! systemctl is-active --quiet "$BOT_SERVICE"; then
  log "$BOT_SERVICE inactive; restarting"
  systemctl restart "$BOT_SERVICE"
  alert_throttled "⚠️ Auditor: сервис бота лежал, перезапустил."
else
  log "$BOT_SERVICE active"
fi

# Belt and braces: if anything ever writes the session file as another user, the
# bot loses access to it without a word. Put it back before it bites.
STORAGE_FILE="${NOTEBOOKLM_HOME}/profiles/${NOTEBOOKLM_PROFILE:-default}/storage_state.json"
if [[ -e "$STORAGE_FILE" ]] && [[ "$(stat -c '%U' "$STORAGE_FILE")" != "$RUN_AS_USER" ]]; then
  log "session file was owned by $(stat -c '%U' "$STORAGE_FILE"); restoring to ${RUN_AS_USER}"
  chown "${RUN_AS_USER}:${RUN_AS_USER}" "$STORAGE_FILE"
fi

# The real check: does the stored session still authenticate against Google?
# Deliberately not `notebooklm auth check` -- that exits 0 even when its own
# "Token fetch" row reports failure, so it can never trigger an alert.
# Must run as the bot's user. The probe makes a real authenticated call, and the
# library writes rotated cookies back to storage_state.json -- doing that as root
# leaves the file root-owned and silently locks the bot out of its own session.
probe_output="$(runuser -u "$RUN_AS_USER" -- env \
  "NOTEBOOKLM_HOME=${NOTEBOOKLM_HOME}" \
  "${VENV_BIN}/python" "${PROJECT_DIR}/ops/auth_probe.py" 2>&1)"
probe_rc=$?
log "$probe_output"

case "$probe_rc" in
  0)
    log "auth ok"
    rm -f "$ALERT_STAMP"
    ;;
  2)
    log "auth DEAD"
    alert_throttled "🔴 Auditor: авторизация NotebookLM умерла. Бот не может обрабатывать ссылки — нужен повторный вход на сервере."
    exit 1
    ;;
  *)
    log "auth inconclusive, not alerting"
    ;;
esac

log "uptime: $(uptime -p)"
log "heartbeat ok"
