"""Drives bot.handle_message the way Telegram would, and checks what came back.

Covers the real handler path -- auth, retries, note creation, scratch-notebook
cleanup, reply chunking -- without needing a human to send a message. Delivery
over Telegram's network is already proven separately by the healthcheck alert.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTEBOOKLM_HOME", "/opt/Auditor/.notebooklm")
sys.path.insert(0, "/opt/Auditor")

import bot  # noqa: E402
import script  # noqa: E402

TEST_URL = "https://youtu.be/E4Xkq9n6nl8"


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUser:
    id = 999


class FakeUpdate:
    def __init__(self, message):
        self.effective_message = message
        self.effective_user = FakeUser()


class FakeApplication:
    def __init__(self, bot_data):
        self.bot_data = bot_data


class FakeContext:
    def __init__(self, bot_data):
        self.application = FakeApplication(bot_data)


async def notebook_count() -> int:
    from notebooklm import NotebookLMClient

    async with await NotebookLMClient.from_storage() as client:
        return len(await client.notebooks.list())


async def main() -> int:
    print(f"prompt source: {script.PROMPT_FILE} (exists: {script.PROMPT_FILE.exists()})")
    print(f"retries: {script.MAX_ATTEMPTS}, timeout: {script.REQUEST_TIMEOUT_SECONDS}s")
    print(f"delete scratch notebooks: {not script.KEEP_NOTEBOOKS}")

    before = await notebook_count()
    print(f"\nnotebooks before: {before}")

    with tempfile.TemporaryDirectory(prefix="bot-e2e-") as tmp:
        message = FakeMessage(f"вот интересное видео {TEST_URL} посмотри")
        update = FakeUpdate(message)
        context = FakeContext(
            {
                "allowed_user_ids": set(),  # empty means "no restriction"
                "vault": tmp,
                "remote_vault": None,
                "profile": None,
            }
        )

        print(f"\nsending: {message.text!r}")
        await bot.handle_message(update, context)

        files = list(Path(tmp).glob("*.md"))
        print(f"\nnotes written: {len(files)}")
        for f in files:
            body = f.read_text(encoding="utf-8")
            print(f"  {f.name}  ({len(body)} chars)")
            print(f"  frontmatter: {body.startswith('---')}")
            print(f"  has tags line: {'tags:' in body.splitlines()[1] if len(body.splitlines()) > 1 else False}")
            print(f"  source recorded: {TEST_URL in body}")

        print(f"\nreplies sent to telegram: {len(message.replies)}")
        for i, r in enumerate(message.replies, 1):
            preview = r[:70].replace("\n", " ")
            print(f"  [{i}] {len(r)} chars: {preview}...")
            if len(r) > bot.TELEGRAM_MESSAGE_LIMIT:
                print(f"  !! chunk {i} exceeds telegram limit")
                return 1

        if not files:
            print("\nFAIL: no note produced")
            return 1
        if not message.replies:
            print("\nFAIL: nothing sent back")
            return 1
        if any("Не смог обработать" in r or "Авторизация" in r for r in message.replies):
            print("\nFAIL: handler reported an error")
            return 1

    after = await notebook_count()
    print(f"\nnotebooks after: {after}")
    if after > before:
        print(f"FAIL: leaked {after - before} scratch notebook(s)")
        return 1
    print("scratch notebook cleaned up correctly")

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
