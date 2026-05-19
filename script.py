import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path


DEFAULT_NOTEBOOKLM_HOME = Path(__file__).resolve().parent / ".notebooklm"
os.environ.setdefault("NOTEBOOKLM_HOME", str(DEFAULT_NOTEBOOKLM_HOME))

QUERY = """
Проанализируй видео и верни строго в формате:

tags: [#видео, #тема1, #тема2]
## Главная идея
## Ключевые тезисы (с таймкодами)
## Связанные темы для Obsidian
## Открытые вопросы
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Создать заметку Obsidian по YouTube-видео через NotebookLM."
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=os.environ.get("NOTEBOOKLM_YOUTUBE_URL"),
        help="YouTube URL. Можно также задать через NOTEBOOKLM_YOUTUBE_URL.",
    )
    parser.add_argument(
        "--vault",
        default=os.environ.get(
            "OBSIDIAN_VIDEO_VAULT",
            str(Path.home() / "ObsidianVault" / "Videos"),
        ),
        help="Папка для markdown-файла. По умолчанию ~/ObsidianVault/Videos.",
    )
    parser.add_argument(
        "--remote-vault",
        default=os.environ.get("OBSIDIAN_REMOTE_VAULT"),
        help='Серверная папка в формате user@host:/path/to/ObsidianVault/Videos.',
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("NOTEBOOKLM_PROFILE"),
        help="Профиль NotebookLM, если используешь несколько Google-аккаунтов.",
    )
    parser.add_argument(
        "--browser-cookies",
        choices=("chrome", "firefox", "edge", "safari", "auto"),
        help="Импортировать cookies из браузера перед запуском.",
    )
    return parser.parse_args()


def fail(message: str) -> None:
    print(f"Ошибка: {message}", file=sys.stderr)
    raise SystemExit(1)


def validate_youtube_url(url: str | None) -> str:
    if not url:
        fail('Передай ссылку: python3 script.py "https://www.youtube.com/watch?v=..."')

    if "youtube.com/watch" not in url and "youtu.be/" not in url:
        fail("похоже, это не YouTube-ссылка.")

    return url


def slugify(value: str, fallback: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"[\s_-]+", "-", value, flags=re.UNICODE).strip("-")
    return (value or fallback)[:70]


def extract_title(response: str) -> str:
    match = re.search(r"##\s*Главная идея\s*\n+(.+)", response)
    if match:
        return match.group(1).strip()

    for line in response.splitlines():
        cleaned = line.strip("# -*\t")
        if cleaned and not cleaned.lower().startswith("tags:"):
            return cleaned

    return f"video-{date.today()}"


def storage_path_for_profile(profile: str | None) -> Path:
    home = Path(os.environ["NOTEBOOKLM_HOME"])
    profile_name = profile or os.environ.get("NOTEBOOKLM_PROFILE") or "default"
    return home / "profiles" / profile_name / "storage_state.json"


def parse_remote_vault(remote: str) -> tuple[str, str]:
    host, separator, path = remote.partition(":")
    if not separator or not host or not path.startswith("/"):
        fail('remote-vault должен быть в формате user@host:/absolute/path')
    return host, path.rstrip("/")


def upload_to_remote(local_file: Path, remote: str) -> str:
    if shutil.which("ssh") is None or shutil.which("scp") is None:
        fail("для отправки на сервер нужны команды ssh и scp.")

    host, remote_dir = parse_remote_vault(remote)
    subprocess.run(["ssh", host, "mkdir", "-p", remote_dir], check=True)

    remote_path = f"{host}:{remote_dir}/{local_file.name}"
    subprocess.run(["scp", str(local_file), remote_path], check=True)
    return remote_path


def import_browser_cookies(browser: str, profile: str | None) -> None:
    try:
        import rookiepy
        from notebooklm.auth import (
            ALLOWED_COOKIE_DOMAINS,
            convert_rookiepy_cookies_to_storage_state,
        )
    except ImportError as exc:
        fail(
            "для импорта cookies установи зависимости: "
            'python3 -m pip install "notebooklm-py[cookies]"'
        )

    cookie_reader = rookiepy.load if browser == "auto" else getattr(rookiepy, browser)
    raw_cookies = cookie_reader(domains=list(ALLOWED_COOKIE_DOMAINS))
    storage_state = convert_rookiepy_cookies_to_storage_state(raw_cookies)

    path = storage_path_for_profile(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(storage_state, ensure_ascii=False), encoding="utf-8")

    if os.name != "nt":
        os.chmod(path, 0o600)

    print(f"Cookies сохранены: {path}")


async def create_note(url: str, vault_path: Path, profile: str | None) -> Path:
    try:
        from notebooklm import NotebookLMClient
    except ImportError:
        fail("пакет не установлен. Выполни: python3 -m pip install notebooklm-py")

    vault_path.mkdir(parents=True, exist_ok=True)

    client_kwargs = {}
    if profile:
        client_kwargs["profile"] = profile

    try:
        async with await NotebookLMClient.from_storage(**client_kwargs) as client:
            notebook = await client.notebooks.create(f"Video {date.today()}")
            await client.sources.add_url(notebook.id, url, wait=True)
            result = await client.chat.ask(notebook.id, QUERY)
    except FileNotFoundError:
        fail(
            "NotebookLM не авторизован. Запусти скрипт с "
            "--browser-cookies chrome, если ты уже вошёл в Google в Chrome."
        )

    response = getattr(result, "answer", str(result))
    title = extract_title(response)
    filename = f"{slugify(title, f'video-{date.today()}')}.md"
    output_path = vault_path / filename

    frontmatter = f"""---
tags: [видео, к-обработке]
source: {url}
date: {date.today()}
type: video-note
---

"""
    output_path.write_text(frontmatter + response + "\n", encoding="utf-8")
    return output_path


async def main() -> None:
    args = parse_args()
    url = validate_youtube_url(args.url)

    if args.browser_cookies:
        import_browser_cookies(args.browser_cookies, args.profile)

    if args.remote_vault:
        with tempfile.TemporaryDirectory(prefix="notebooklm-note-") as temp_dir:
            output_path = await create_note(url, Path(temp_dir), args.profile)
            remote_path = upload_to_remote(output_path, args.remote_vault)
            print(f"Сохранено на сервере: {remote_path}")
        return

    output_path = await create_note(url, Path(args.vault).expanduser(), args.profile)
    print(f"Сохранено локально: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
