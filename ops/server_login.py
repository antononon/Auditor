"""One-time interactive Google login performed ON the server.

The whole point: the session must be born here, on this machine and this IP,
and never be shared with another device. Copying cookies from a laptop is what
killed every previous session -- two clients rotating __Secure-1PSIDTS
independently makes Google drop the session.

Run it on the virtual display, log in through VNC, and it saves storage_state
the moment it sees an authenticated NotebookLM page.
"""

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

NOTEBOOKLM_HOME = Path(os.environ.get("NOTEBOOKLM_HOME", "/opt/Auditor/.notebooklm"))
PROFILE = os.environ.get("NOTEBOOKLM_PROFILE", "default")
STORAGE_PATH = NOTEBOOKLM_HOME / "profiles" / PROFILE / "storage_state.json"
BROWSER_PROFILE = NOTEBOOKLM_HOME / "browser-profile"

LOGIN_TIMEOUT_SECONDS = 45 * 60
POLL_SECONDS = 3


def is_logged_in(page) -> bool:
    """True once we are off the sign-in flow and on an authenticated Google page.

    Deliberately not matched against a specific host: NotebookLM has already moved
    from notebook.google.com to the Gemini domain once, and pinning the hostname
    is what made this miss a perfectly good login.
    """
    url = (page.url or "").lower()
    if not url.startswith("http"):
        return False
    if "accounts.google.com" in url:
        return False
    if "/signin" in url or "/login" in url:
        return False
    return "google.com" in url


def main() -> int:
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            headless=False,
            channel="chrome",
            viewport={"width": 1280, "height": 800},
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--password-store=basic",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://notebook.google.com/", wait_until="domcontentloaded")

        print("Browser is up. Log in through the VNC window.", flush=True)
        print("Waiting for an authenticated NotebookLM page...", flush=True)

        deadline = time.time() + LOGIN_TIMEOUT_SECONDS
        while time.time() < deadline:
            try:
                if is_logged_in(page):
                    # Let the post-login redirects settle before snapshotting cookies.
                    time.sleep(5)
                    if is_logged_in(page):
                        break
            except Exception:
                pass
            time.sleep(POLL_SECONDS)
        else:
            print("Timed out waiting for login.", file=sys.stderr)
            context.close()
            return 1

        state = context.storage_state()
        STORAGE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.chmod(STORAGE_PATH, 0o600)

        names = {c["name"] for c in state.get("cookies", [])}
        print(f"Saved {len(state.get('cookies', []))} cookies to {STORAGE_PATH}", flush=True)
        print(f"SID present: {'SID' in names}, 1PSIDTS present: {'__Secure-1PSIDTS' in names}", flush=True)

        context.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
