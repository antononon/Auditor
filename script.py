import argparse
import asyncio
import json
import logging
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

# One NotebookLM round-trip has to upload a source, wait for it to be indexed and
# then wait for the answer, so the ceiling is generous. Without it a stuck request
# hangs the bot forever.
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("NOTEBOOKLM_TIMEOUT", "600"))
MAX_ATTEMPTS = int(os.environ.get("NOTEBOOKLM_MAX_ATTEMPTS", "4"))
INITIAL_BACKOFF_SECONDS = float(os.environ.get("NOTEBOOKLM_BACKOFF", "5"))
# Escape hatch for debugging a bad answer: keeps the scratch notebook around.
KEEP_NOTEBOOKS = os.environ.get("NOTEBOOKLM_KEEP_NOTEBOOKS", "").lower() in ("1", "true", "yes")

logger = logging.getLogger(__name__)


class AuthExpired(RuntimeError):
    """The NotebookLM session is dead. Retrying cannot fix it -- only a new login can."""

DEFAULT_PROMPT_TEMPLATE = """
Ты — строгий аналитик и ассистент по извлечению знаний. Твоя задача — анализировать источники (видео, статьи, документы) и выдавать максимально плотную, структурированную выжимку без «воды», вводных слов и лирики, на русском языке. 

ЦЕЛЬ: Создать идеальную заметку для базы знаний Obsidian. Извлекай ВСЕ полезные факты, алгоритмы, правила и концепции.

ПРАВИЛО ТЕГОВ:
Используй ТОЛЬКО теги из этого списка (выбери от 1 до 3 самых подходящих). Запрещено придумывать новые теги.

Список: 
Система и база знаний: #система, #база_знаний, #инсайт, #референс, #алгоритм.
Медиа, Фото и Видео: #фотография, #видеомонтаж, #режиссура, #цветокоррекция, #сценарий, #DaVinciResolve, #Sony, #композиция, #свет, #оптика, #кино, #YouTube, #контент, #оборудование.
Инженерия, Электрика и Электроника: #электрика, #электроника, #схемотехника, #умный_дом, #KNX, #автоматизация, #монтаж, #инструмент, #безопасность, #VDE.
IT, Программирование и Серверы: #программирование, #сервер, #Linux, #TypeScript, #AI, #нейросети, #боты, #Telegram_API, #архитектура_ПО, #хостинг, #bash, #prompt_engineering.
Финансы и Инвестиции: #финансы, #инвестиции, #ETF, #криптовалюта, #бюджет, #капитал, #пассивный_доход, #акции, #экономика, #трейдинг, #налоги, #VWL.
Здоровье, Спорт и Outdoor: #здоровье, #спорт, #бег, #тренажерный_зал, #фитнес, #питание, #биохакинг, #восстановление, #сон, #походы, #горы, #Альпы, #треккинг, #экипировка, #outdoor.
Музыка и Звук: #музыка, #DJing, #саунд_дизайн, #сведение, #мастеринг, #теория_музыки, #синтезаторы, #акустика.
Обучение и Языки: #немецкий, #английский, #обучение, #Ausbildung, #экзамены.
Психология, Продуктивность и Отношения: #психология, #саморазвитие, #зависимости, #продуктивность, #тайм_менеджмент, #планирование, #СДВГ, #фокус, #дисциплина, #мышление, #отношения, #коммуникация, #соционика, #лидерство.
Духовность и Мировоззрение: #духовное, #служение, #собрание, #Библия, #философия.



ОБЯЗАТЕЛЬНАЯ СТРУКТУРА ОТВЕТА (выдавай строго в формате Markdown):

---
tags: [#ВыбранныйТег1, #ВыбранныйТег2]
source: {source}
date: {date}
type: knowledge-base
---

# Сгенерируй емкое, точное и цепляющее название для этого материала (максимум 5-7 слов)**

## 🎯 Суть (в одном предложении)
Максимально емкое определение того, о чем материал

## 🧠 Ключевые концепции и термины
Если в материале есть новые термины, законы или определения — выпиши их сюда в формате: **Термин** — определение.

## 📌 Глубокая выжимка
Здесь должна быть плотная, структурированная информация. Используй подзаголовки (###), маркированные списки и таблицы. Выпиши все важные мысли и аргументы. Указывай (источник: мм:сс). 

	Главный инсайт
> Выдели сюда самую прорывную мысль блока

ЗАПРОС:
Проанализируй этот источник и сделай выжимку строго по нашей структуре для Obsidian. Достань всю фактологию, пошаговые инструкции и скрытые смыслы. Никакой воды, только сухой остаток. Назначь теги из разрешенного списка
""".strip()


# Kept outside the code so it can be edited without a deploy. Read fresh on every
# request, so an edit takes effect on the next link with no restart.
PROMPT_FILE = Path(
    os.environ.get("AUDITOR_PROMPT_FILE", str(Path(__file__).resolve().parent / "prompt.md"))
)


def load_prompt_template() -> str:
    """The edited prompt if there is one, otherwise the built-in default."""
    try:
        text = PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_PROMPT_TEMPLATE
    return text or DEFAULT_PROMPT_TEMPLATE


def build_query(source: str) -> str:
    return load_prompt_template().format(source=source, date=date.today())


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
    match = re.search(r"^#\s+\*\*(.+?)\*\*\s*$", response, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()

    match = re.search(r"^#\s+(.+?)\s*$", response, flags=re.MULTILINE)
    if match:
        return match.group(1).strip("* ").strip()

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


def ensure_frontmatter(response: str, url: str) -> str:
    response = response.strip()
    if response.startswith("---"):
        return response + "\n"

    return f"""---
tags: [#YouTube]
source: {url}
date: {date.today()}
type: knowledge-base
---

{response}
"""


def _error_groups():
    """Exception classes split by whether retrying stands a chance.

    Imported lazily because notebooklm is an optional import everywhere else here.
    """
    from notebooklm import exceptions as nlm

    fatal_auth = (nlm.AuthError, nlm.ConfigurationError)
    retryable = (
        nlm.NetworkError,
        nlm.RPCTimeoutError,
        nlm.ServerError,
        nlm.RateLimitError,
    )
    return fatal_auth, retryable


async def _ask_notebooklm(url: str, profile: str | None) -> str:
    """A single full round-trip: create a notebook, add the source, ask the question.

    The notebook is a scratchpad -- the answer is all we keep -- so it is deleted
    afterwards. Leaving them behind slowly fills the account up to the NotebookLM
    notebook cap, at which point creation starts failing and the bot dies with it.
    """
    from notebooklm import NotebookLMClient

    client_kwargs = {}
    if profile:
        client_kwargs["profile"] = profile

    async with await NotebookLMClient.from_storage(**client_kwargs) as client:
        notebook = await client.notebooks.create(f"Video {date.today()}")
        try:
            await client.sources.add_url(notebook.id, url, wait=True)
            result = await client.chat.ask(notebook.id, build_query(url))
        finally:
            if not KEEP_NOTEBOOKS:
                await _discard_notebook(client, notebook.id)

    return getattr(result, "answer", str(result))


async def _discard_notebook(client, notebook_id: str) -> None:
    """Best-effort cleanup. A failed delete must never sink an otherwise good note."""
    try:
        await client.notebooks.delete(notebook_id)
        logger.info("Deleted scratch notebook %s", notebook_id)
    except Exception:
        logger.warning("Could not delete scratch notebook %s", notebook_id, exc_info=True)


async def fetch_answer(url: str, profile: str | None) -> str:
    """Ask NotebookLM, retrying transient failures with exponential backoff.

    Auth failures short-circuit: the session is gone and hammering it only makes
    Google more suspicious of the account.
    """
    fatal_auth, retryable = _error_groups()
    delay = INITIAL_BACKOFF_SECONDS
    last_error: BaseException | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await asyncio.wait_for(
                _ask_notebooklm(url, profile), timeout=REQUEST_TIMEOUT_SECONDS
            )
        except fatal_auth as exc:
            raise AuthExpired(str(exc)) from exc
        except FileNotFoundError as exc:
            raise AuthExpired("storage_state.json is missing") from exc
        except (*retryable, asyncio.TimeoutError) as exc:
            last_error = exc
            label = type(exc).__name__
            if attempt == MAX_ATTEMPTS:
                logger.error("%s on final attempt %d/%d", label, attempt, MAX_ATTEMPTS)
                break
            logger.warning(
                "%s on attempt %d/%d, retrying in %.0fs", label, attempt, MAX_ATTEMPTS, delay
            )
            await asyncio.sleep(delay)
            delay *= 2

    assert last_error is not None
    raise last_error


async def create_note(url: str, vault_path: Path, profile: str | None) -> Path:
    try:
        import notebooklm  # noqa: F401
    except ImportError:
        fail("пакет не установлен. Выполни: python3 -m pip install notebooklm-py")

    vault_path.mkdir(parents=True, exist_ok=True)

    answer = await fetch_answer(url, profile)

    response = ensure_frontmatter(answer, url)
    title = extract_title(response)
    filename = f"{slugify(title, f'video-{date.today()}')}.md"
    output_path = vault_path / filename
    output_path.write_text(response, encoding="utf-8")
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
