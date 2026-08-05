import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from script import (
    AuthExpired,
    create_note,
    latest_prompt_backup,
    load_prompt_template,
    save_prompt,
    upload_to_remote,
    validate_prompt,
)


TELEGRAM_MESSAGE_LIMIT = 3900
YOUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/watch\?[^ \n]+|youtu\.be/[^ \n]+)",
    re.IGNORECASE,
)


def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Set {name} before starting the bot.")
    return value


def parse_allowed_user_ids() -> set[int]:
    raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    if not raw:
        return set()

    allowed = set()
    for item in raw.split(","):
        item = item.strip()
        if item:
            allowed.add(int(item))
    return allowed


def extract_youtube_url(text: str) -> str | None:
    match = YOUTUBE_URL_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(").,;]")


def split_telegram_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    chunks = []
    current = ""

    for line in text.splitlines(keepends=True):
        if len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            for index in range(0, len(line), limit):
                chunks.append(line[index : index + limit])
            continue

        if len(current) + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current += line

    if current:
        chunks.append(current)

    return chunks


async def send_note(message, note: str) -> None:
    for chunk in split_telegram_message(note):
        await message.reply_text(chunk, disable_web_page_preview=True)


def is_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    allowed_user_ids: set[int] = context.application.bot_data["allowed_user_ids"]
    user = update.effective_user
    if not allowed_user_ids:
        return True
    return user is not None and user.id in allowed_user_ids


async def show_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the current prompt so it can be copied, edited and sent back."""
    if not is_allowed(update, context):
        return

    message = update.effective_message
    prompt = load_prompt_template()

    await message.reply_text(
        "Текущий промпт ниже. Скопируй, поправь и пришли обратно как:\n"
        "/setprompt <текст>\n\n"
        "Плейсхолдеры {source} и {date} должны остаться. "
        "Откатить последнюю правку — /promptundo",
        disable_web_page_preview=True,
    )
    # Sent raw, with no parse_mode: the prompt is full of #, * and [] that any
    # markdown parser would either mangle or reject outright.
    for chunk in split_telegram_message(prompt):
        await message.reply_text(chunk, disable_web_page_preview=True)


async def set_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Replace the prompt with the text following the command, or a replied-to message."""
    if not is_allowed(update, context):
        return

    message = update.effective_message
    text = message.text or ""

    # Everything after "/setprompt", newlines and all.
    _, _, new_prompt = text.partition(" ")
    if not new_prompt.strip() and message.reply_to_message:
        new_prompt = message.reply_to_message.text or ""

    new_prompt = new_prompt.strip()
    if not new_prompt:
        await message.reply_text(
            "После /setprompt нужен сам текст промпта "
            "(или ответь этой командой на сообщение с ним).",
            disable_web_page_preview=True,
        )
        return

    error = validate_prompt(new_prompt)
    if error:
        await message.reply_text(f"Не сохранил: {error}", disable_web_page_preview=True)
        return

    try:
        backup = save_prompt(new_prompt)
    except OSError as exc:
        logging.exception("Could not save prompt")
        await message.reply_text(f"Не смог записать файл: {exc}", disable_web_page_preview=True)
        return

    logging.info("Prompt updated (%d chars), backup: %s", len(new_prompt), backup)
    await message.reply_text(
        f"Промпт сохранён ({len(new_prompt)} символов). "
        "Применится со следующей ссылки. Откатить — /promptundo",
        disable_web_page_preview=True,
    )


async def undo_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Roll back to the version saved before the last edit."""
    if not is_allowed(update, context):
        return

    message = update.effective_message
    backup = latest_prompt_backup()
    if backup is None:
        await message.reply_text("Откатывать нечего — сохранённых версий нет.")
        return

    try:
        previous = backup.read_text(encoding="utf-8")
        save_prompt(previous)
    except OSError as exc:
        logging.exception("Could not restore prompt")
        await message.reply_text(f"Не смог восстановить: {exc}", disable_web_page_preview=True)
        return

    logging.info("Prompt rolled back to %s", backup.name)
    await message.reply_text(
        f"Откатил к версии от {backup.stem.removeprefix('prompt-')}. "
        "Ещё раз /promptundo вернёт обратно.",
        disable_web_page_preview=True,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update, context):
        return

    message = update.effective_message
    text = message.text if message else None
    if not text:
        return

    url = extract_youtube_url(text)
    if not url:
        return

    vault = context.application.bot_data["vault"]
    remote_vault = context.application.bot_data["remote_vault"]
    profile = context.application.bot_data["profile"]

    try:
        if remote_vault:
            with tempfile.TemporaryDirectory(prefix="auditor-bot-") as temp_dir:
                output_path = await create_note(url, Path(temp_dir), profile)
                note = output_path.read_text(encoding="utf-8")
                remote_path = await asyncio.to_thread(upload_to_remote, output_path, remote_vault)
                await send_note(message, note)
                logging.info("Saved %s to %s", url, remote_path)
            return

        output_path = await create_note(url, Path(vault).expanduser(), profile)
        note = output_path.read_text(encoding="utf-8")
        await send_note(message, note)
        logging.info("Saved %s to %s", url, output_path)
    except AuthExpired:
        # Distinct from a normal failure: nothing about this link is wrong, the
        # server just needs a fresh Google login. Say so instead of hiding it in
        # a generic message, which is how this stayed broken for two months.
        logging.error("NotebookLM auth expired while processing %s", url)
        await message.reply_text(
            "Авторизация NotebookLM истекла — нужен повторный вход на сервере. "
            "Ссылка не обработана.",
            disable_web_page_preview=True,
        )
    except Exception:
        logging.exception("Could not process %s", url)
        await message.reply_text(
            "Не смог обработать ссылку. Ошибка уже записана в лог сервера.",
            disable_web_page_preview=True,
        )


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)

    token = get_required_env("TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).build()

    app.bot_data["allowed_user_ids"] = parse_allowed_user_ids()
    app.bot_data["vault"] = os.environ.get(
        "OBSIDIAN_VIDEO_VAULT",
        str(Path.home() / "ObsidianVault" / "Videos"),
    )
    app.bot_data["remote_vault"] = os.environ.get("OBSIDIAN_REMOTE_VAULT")
    app.bot_data["profile"] = os.environ.get("NOTEBOOKLM_PROFILE")

    app.add_handler(CommandHandler("prompt", show_prompt))
    app.add_handler(CommandHandler("setprompt", set_prompt))
    app.add_handler(CommandHandler("promptundo", undo_prompt))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logging.info("Auditor Telegram bot started")
    app.run_polling(allowed_updates=Update.MESSAGE)


if __name__ == "__main__":
    main()
