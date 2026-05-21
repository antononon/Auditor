import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

from script import create_note, upload_to_remote


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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed_user_ids: set[int] = context.application.bot_data["allowed_user_ids"]
    user = update.effective_user
    if allowed_user_ids and (user is None or user.id not in allowed_user_ids):
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
                remote_path = await asyncio.to_thread(upload_to_remote, output_path, remote_vault)
                logging.info("Saved %s to %s", url, remote_path)
            return

        output_path = await create_note(url, Path(vault).expanduser(), profile)
        logging.info("Saved %s to %s", url, output_path)
    except BaseException:
        logging.exception("Could not process %s", url)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    token = get_required_env("TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).build()

    app.bot_data["allowed_user_ids"] = parse_allowed_user_ids()
    app.bot_data["vault"] = os.environ.get(
        "OBSIDIAN_VIDEO_VAULT",
        str(Path.home() / "ObsidianVault" / "Videos"),
    )
    app.bot_data["remote_vault"] = os.environ.get("OBSIDIAN_REMOTE_VAULT")
    app.bot_data["profile"] = os.environ.get("NOTEBOOKLM_PROFILE")

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logging.info("Auditor Telegram bot started")
    app.run_polling(allowed_updates=Update.MESSAGE)


if __name__ == "__main__":
    main()
