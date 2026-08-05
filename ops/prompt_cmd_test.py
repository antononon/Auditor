"""Exercises /prompt, /setprompt and /promptundo the way Telegram delivers them."""

import asyncio
import os
import sys

os.environ.setdefault("NOTEBOOKLM_HOME", "/opt/Auditor/.notebooklm")
sys.path.insert(0, "/opt/Auditor")

import bot  # noqa: E402
import script  # noqa: E402


class FakeMessage:
    def __init__(self, text, reply_to=None):
        self.text = text
        self.reply_to_message = reply_to
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeUpdate:
    def __init__(self, message, uid=100000001):
        self.effective_message = message
        self.effective_user = FakeUser(uid)


class FakeContext:
    def __init__(self, allowed):
        self.application = type("A", (), {"bot_data": {"allowed_user_ids": allowed}})()


ALLOWED = {100000001, 100000002}


async def run_cmd(handler, text, uid=100000001, reply_to=None):
    msg = FakeMessage(text, reply_to)
    await handler(FakeUpdate(msg, uid), FakeContext(ALLOWED))
    return msg.replies


async def main() -> int:
    original = script.load_prompt_template()
    failures = []

    print("=== /prompt shows current prompt ===")
    replies = await run_cmd(bot.show_prompt, "/prompt")
    body = "\n".join(replies)
    ok = len(replies) >= 2 and "строгий аналитик" in body
    print(f"  replies: {len(replies)}, contains prompt: {'строгий аналитик' in body}")
    for r in replies:
        if len(r) > bot.TELEGRAM_MESSAGE_LIMIT:
            ok = False
            print("  !! chunk over telegram limit")
    if not ok:
        failures.append("/prompt")

    print("\n=== /setprompt rejects a stray placeholder ===")
    replies = await run_cmd(bot.set_prompt, "/setprompt Текст {source} {date} и {мусор}")
    print(f"  -> {replies[0][:70]}")
    if "Не сохранил" not in replies[0] or script.load_prompt_template() != original:
        failures.append("setprompt-reject-placeholder")

    print("\n=== /setprompt rejects an unpaired brace ===")
    replies = await run_cmd(bot.set_prompt, "/setprompt Текст {source} {date} и {")
    print(f"  -> {replies[0][:70]}")
    if "Не сохранил" not in replies[0] or script.load_prompt_template() != original:
        failures.append("setprompt-reject-brace")

    print("\n=== /setprompt with no text explains itself ===")
    replies = await run_cmd(bot.set_prompt, "/setprompt")
    print(f"  -> {replies[0][:70]}")
    if "нужен сам текст" not in replies[0]:
        failures.append("setprompt-empty")

    print("\n=== /setprompt saves a valid multi-line prompt ===")
    new_prompt = "ТЕСТ.\nИсточник: {source}\nДата: {date}\nЛитерал: {{ok}}"
    replies = await run_cmd(bot.set_prompt, f"/setprompt {new_prompt}")
    print(f"  -> {replies[0][:70]}")
    saved = script.load_prompt_template()
    print(f"  saved correctly: {saved == new_prompt}")
    if saved != new_prompt:
        failures.append("setprompt-save")

    print("\n=== change is live for the next link ===")
    q = script.build_query("https://youtu.be/XYZ")
    print(f"  built query: {q!r}")
    if "https://youtu.be/XYZ" not in q or "{ok}" not in q:
        failures.append("live-pickup")

    print("\n=== /setprompt via reply to another message ===")
    replied = FakeMessage("Из ответа: {source} {date}")
    replies = await run_cmd(bot.set_prompt, "/setprompt", reply_to=replied)
    print(f"  -> {replies[0][:70]}")
    if script.load_prompt_template() != "Из ответа: {source} {date}":
        failures.append("setprompt-reply")

    print("\n=== /promptundo rolls back ===")
    replies = await run_cmd(bot.undo_prompt, "/promptundo")
    print(f"  -> {replies[0][:70]}")
    print(f"  back to previous edit: {script.load_prompt_template() == new_prompt}")
    if script.load_prompt_template() != new_prompt:
        failures.append("promptundo")

    print("\n=== whitelist blocks a stranger ===")
    replies = await run_cmd(bot.show_prompt, "/prompt", uid=111111)
    print(f"  replies to non-whitelisted user: {len(replies)}")
    if replies:
        failures.append("whitelist")

    print("\n=== restoring original prompt ===")
    script.save_prompt(original)
    print(f"  restored: {script.load_prompt_template() == original}")
    if script.load_prompt_template() != original:
        failures.append("restore")

    if failures:
        print(f"\nFAILED: {failures}")
        return 1
    print("\nALL PROMPT COMMAND CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
