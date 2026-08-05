#!/usr/bin/env bash
# Restarts just the login watcher against the existing persistent browser
# profile. x11vnc is left alone so an attached viewer keeps its session.
set -uo pipefail

export DISPLAY=":99"
export NOTEBOOKLM_HOME=/opt/Auditor/.notebooklm

# SIGTERM, not SIGKILL: Chrome needs a chance to flush the profile to disk,
# and that profile is what carries the login across this restart.
pkill -f "server_login.py" 2>/dev/null || true
sleep 3
pkill -f "google-chrome" 2>/dev/null || true
sleep 3

exec /opt/Auditor/.venv/bin/python /opt/Auditor/ops/server_login.py
