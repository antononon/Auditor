#!/usr/bin/env bash
# Exposes the server's virtual display over VNC so a human can do the one-time
# Google login here. VNC binds to loopback only -- reach it through an SSH
# tunnel. Never open 5900 to the internet.
set -uo pipefail

export DISPLAY=":99"
export NOTEBOOKLM_HOME=/opt/Auditor/.notebooklm

if ! systemctl is-active --quiet auditor-xvfb; then
  echo "virtual display is not running; start auditor-xvfb first" >&2
  exit 1
fi

# Clear out anything left over from a previous attempt. Done here rather than
# from an interactive ssh command, where a -f pattern can match the caller's own
# command line and kill the session issuing it.
pkill -f "server_login.py" 2>/dev/null || true
pkill -f "google-chrome" 2>/dev/null || true
pkill -f "x11vnc -display :99" 2>/dev/null || true
sleep 2

pgrep -f "fluxbox" >/dev/null 2>&1 || (fluxbox >/tmp/fluxbox.log 2>&1 &)
sleep 1

# macOS Screen Sharing refuses "no authentication" servers, so use a password
# file when one exists. Loopback binding is still what actually protects this.
VNC_AUTH=(-nopw)
[[ -r /opt/Auditor/.vncpass ]] && VNC_AUTH=(-rfbauth /opt/Auditor/.vncpass)

x11vnc -display :99 -localhost -rfbport 5900 "${VNC_AUTH[@]}" -forever -shared -quiet \
  >/tmp/x11vnc.log 2>&1 &
sleep 2

if ! pgrep -f "x11vnc -display :99" >/dev/null; then
  echo "x11vnc failed to start, see /tmp/x11vnc.log" >&2
  exit 1
fi

echo "VNC listening on 127.0.0.1:5900 (loopback only)"
echo "Launching login browser..."

exec /opt/Auditor/.venv/bin/python /opt/Auditor/ops/server_login.py
