"""List, and optionally purge, the scratch notebooks Auditor leaves behind.

Every processed video used to create a notebook that was never removed, so an
account slowly fills up to the NotebookLM cap. `list` is safe; `purge` deletes and
cannot be undone, so it only touches notebooks whose title matches the scratch
pattern Auditor creates.
"""

import asyncio
import os
import re
import sys

os.environ.setdefault("NOTEBOOKLM_HOME", "/opt/Auditor/.notebooklm")

# Exactly what script.py names its throwaway notebooks: "Video YYYY-MM-DD".
SCRATCH_TITLE = re.compile(r"^Video \d{4}-\d{2}-\d{2}$")


async def run(command: str) -> int:
    from notebooklm import NotebookLMClient

    async with await NotebookLMClient.from_storage() as client:
        notebooks = await client.notebooks.list()

        scratch = [n for n in notebooks if SCRATCH_TITLE.match(getattr(n, "title", "") or "")]
        other = [n for n in notebooks if n not in scratch]

        print(f"total notebooks: {len(notebooks)}")
        print(f"  auditor scratch ('Video YYYY-MM-DD'): {len(scratch)}")
        print(f"  everything else: {len(other)}")

        if other:
            print("\nnot touched by purge:")
            for n in other:
                print(f"  - {getattr(n, 'title', '?')}")

        if scratch:
            print("\nscratch notebooks:")
            for n in scratch:
                print(f"  - {getattr(n, 'title', '?')}  ({getattr(n, 'id', '?')[:12]}...)")

        if command != "purge":
            return 0

        if not scratch:
            print("\nnothing to purge")
            return 0

        print(f"\ndeleting {len(scratch)} scratch notebooks...")
        deleted = failed = 0
        for n in scratch:
            try:
                await client.notebooks.delete(n.id)
                deleted += 1
            except Exception as exc:
                failed += 1
                print(f"  failed on {getattr(n, 'id', '?')[:12]}: {exc}")
        print(f"deleted {deleted}, failed {failed}")
        return 1 if failed else 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd not in ("list", "purge"):
        print("usage: notebooks_tool.py [list|purge]", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(run(cmd)))
