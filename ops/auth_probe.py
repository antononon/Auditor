"""Exit 0 only if the stored NotebookLM session can really talk to Google.

`notebooklm auth check --test` exits 0 even when its own "Token fetch" row says
fail, so it cannot drive monitoring. This makes an actual authenticated call and
lets the exit code mean something.

  0 -- session works
  2 -- session is dead, a human has to log in again
  3 -- inconclusive (network trouble, transient server error)
"""

import asyncio
import os
import sys

os.environ.setdefault("NOTEBOOKLM_HOME", "/opt/Auditor/.notebooklm")

PROBE_TIMEOUT_SECONDS = 60


async def probe() -> int:
    from notebooklm import NotebookLMClient, exceptions as nlm

    try:
        async with await NotebookLMClient.from_storage() as client:
            await client.notebooks.list()
        print("auth: ok")
        return 0
    except (nlm.AuthError, nlm.ConfigurationError) as exc:
        print(f"auth: dead ({type(exc).__name__}: {exc})")
        return 2
    except FileNotFoundError as exc:
        print(f"auth: dead (missing storage: {exc})")
        return 2
    except Exception as exc:
        message = str(exc).lower()
        # The library surfaces the Google sign-in redirect as a plain ValueError.
        if "authentication expired" in message or "accounts.google.com" in message:
            print(f"auth: dead ({type(exc).__name__})")
            return 2
        print(f"auth: inconclusive ({type(exc).__name__}: {exc})")
        return 3


async def main() -> int:
    try:
        return await asyncio.wait_for(probe(), timeout=PROBE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        print("auth: inconclusive (probe timed out)")
        return 3


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
