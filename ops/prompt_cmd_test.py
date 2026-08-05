"""Exercises the prompt controls the way Telegram delivers them.

Covers both entry points: the inline keyboard buttons and the slash commands,
plus the ForceReply edit flow, which is the one that has to be told apart from
someone pasting a YouTube link.
"""

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
        self.markups = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        return FakeMessage(text)


class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeQuery:
    def __init__(self, data, message, uid):
        self.data = data
        self.message = message
        self.from_user = FakeUser(uid)
        self.answered = []

    async def answer(self, text=None, **kwargs):
        self.answered.append(text)


class FakeUpdate:
    def __init__(self, message=None, uid=100000001, query=None):
        self.effective_message = message
        self.effective_user = FakeUser(uid)
        self.callback_query = query


class FakeContext:
    def __init__(self, allowed):
        self.application = type("A", (), {"bot_data": {"allowed_user_ids": allowed}})()


ALLOWED = {100000001, 100000002}


async def press(data, uid=100000001):
    msg = FakeMessage("Управление промптом:")
    query = FakeQuery(data, msg, uid)
    await bot.on_button(FakeUpdate(uid=uid, query=query), FakeContext(ALLOWED))
    return msg, query


async def send(handler, text, uid=100000001, reply_to=None):
    msg = FakeMessage(text, reply_to)
    await handler(FakeUpdate(msg, uid), FakeContext(ALLOWED))
    return msg


async def main() -> int:
    original = script.load_prompt_template()
    fails = []

    print("=== /menu offers three buttons ===")
    msg = await send(bot.show_menu, "/menu")
    markup = next((m for m in msg.markups if m is not None), None)
    labels = [b.text for row in markup.inline_keyboard for b in row] if markup else []
    print(f"  buttons: {labels}")
    if len(labels) != 3:
        fails.append("menu-buttons")

    print("\n=== button: show prompt ===")
    msg, query = await press("prompt:show")
    body = "\n".join(msg.replies)
    print(f"  replies: {len(msg.replies)}, contains prompt: {'строгий аналитик' in body}")
    print(f"  callback answered: {query.answered == [None]}")
    if "строгий аналитик" not in body or not query.answered:
        fails.append("button-show")
    for r in msg.replies:
        if len(r) > bot.TELEGRAM_MESSAGE_LIMIT:
            fails.append("chunk-too-long")

    print("\n=== button: edit opens a reply box ===")
    msg, _ = await press("prompt:edit")
    has_force = any(type(m).__name__ == "ForceReply" for m in msg.markups)
    print(f"  marker sent: {bot.EDIT_REPLY_MARKER in msg.replies[0]}")
    print(f"  ForceReply attached: {has_force}")
    if bot.EDIT_REPLY_MARKER not in msg.replies[0] or not has_force:
        fails.append("button-edit")

    print("\n=== replying to that box saves the prompt (not treated as a link) ===")
    force_msg = FakeMessage(f"{bot.EDIT_REPLY_MARKER}\n\nтекст")
    new_prompt = "ТЕСТ ЧЕРЕЗ КНОПКУ.\n{source} / {date}\nЛитерал: {{ok}}"
    msg = await send(bot.handle_message, new_prompt, reply_to=force_msg)
    print(f"  -> {msg.replies[0][:60]}")
    saved = script.load_prompt_template()
    print(f"  saved: {saved == new_prompt}")
    if saved != new_prompt:
        fails.append("reply-edit-save")

    print("\n=== live for the next link ===")
    q = script.build_query("https://youtu.be/XYZ")
    if "https://youtu.be/XYZ" not in q or "{ok}" not in q:
        fails.append("live-pickup")
    print(f"  {q!r}")

    print("\n=== invalid edit via reply is rejected ===")
    msg = await send(bot.handle_message, "Плохой {мусор} {source} {date}", reply_to=force_msg)
    print(f"  -> {msg.replies[0][:60]}")
    if "Не сохранил" not in msg.replies[0] or script.load_prompt_template() != new_prompt:
        fails.append("reply-edit-reject")

    print("\n=== a real link is still recognised ===")
    print(f"  extract_youtube_url: {bot.extract_youtube_url('https://youtu.be/E4Xkq9n6nl8')}")
    if not bot.extract_youtube_url("https://youtu.be/E4Xkq9n6nl8"):
        fails.append("link-still-works")

    print("\n=== button: undo ===")
    msg, _ = await press("prompt:undo")
    print(f"  -> {msg.replies[0][:60]}")
    if script.load_prompt_template() == new_prompt:
        fails.append("button-undo")

    print("\n=== /setprompt still works ===")
    msg = await send(bot.set_prompt, "/setprompt Команда: {source} {date}")
    print(f"  -> {msg.replies[0][:60]}")
    if script.load_prompt_template() != "Команда: {source} {date}":
        fails.append("setprompt")

    print("\n=== stranger is refused, on buttons too ===")
    msg = await send(bot.show_menu, "/menu", uid=999999)
    _, query = await press("prompt:show", uid=999999)
    print(f"  menu replies: {len(msg.replies)}, button alert: {query.answered}")
    if msg.replies or query.answered == [None]:
        fails.append("whitelist")

    print("\n=== restoring original ===")
    script.save_prompt(original)
    print(f"  restored: {script.load_prompt_template() == original}")
    if script.load_prompt_template() != original:
        fails.append("restore")

    if fails:
        print(f"\nFAILED: {fails}")
        return 1
    print("\nALL PROMPT CONTROL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
