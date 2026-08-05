"""A one-textarea web editor for the Auditor prompt.

Binds to loopback only -- reach it over an SSH tunnel. There is no login here, so
it must never be bound to a public interface.

The prompt is a .format() template: {source} and {date} get substituted per run.
A stray brace anywhere else raises at request time and would break the bot on the
next link, so every save is validated before it is allowed to land.
"""

import html
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, "/opt/Auditor")

# Shared with the Telegram commands, so the two entry points can never disagree
# about what counts as a valid prompt.
from script import PROMPT_FILE, save_prompt, validate_prompt  # noqa: E402

HOST = os.environ.get("AUDITOR_EDITOR_HOST", "127.0.0.1")
PORT = int(os.environ.get("AUDITOR_EDITOR_PORT", "8765"))
MAX_BODY_BYTES = 512 * 1024

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Промпт Auditor</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 0; padding: 24px; background: #f6f7f9; color: #16181d;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #14161a; color: #e6e8ec; }}
    textarea {{ background: #1c1f26 !important; color: #e6e8ec !important; border-color: #333 !important; }}
    .card {{ background: #1c1f26 !important; border-color: #2a2e37 !important; }}
  }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  p.sub {{ margin: 0 0 16px; opacity: .7; font-size: 13px; }}
  .card {{ background: #fff; border: 1px solid #e3e5ea; border-radius: 10px; padding: 16px; }}
  textarea {{
    width: 100%; box-sizing: border-box; min-height: 60vh; padding: 12px;
    font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace;
    border: 1px solid #d8dbe2; border-radius: 8px; resize: vertical; background: #fff;
  }}
  .row {{ display: flex; align-items: center; gap: 12px; margin-top: 14px; }}
  button {{
    font-size: 15px; font-weight: 600; padding: 10px 22px; border: 0;
    border-radius: 8px; background: #2c6bed; color: #fff; cursor: pointer;
  }}
  button:hover {{ background: #1f57cc; }}
  .msg {{ font-size: 13px; }}
  .ok {{ color: #1a7f3c; }}
  .err {{ color: #c0392b; }}
  code {{ background: rgba(127,127,127,.15); padding: 1px 5px; border-radius: 4px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Промпт Auditor</h1>
  <p class="sub">
    Правится на месте. Изменения подхватываются со следующей ссылки — перезапускать бот не нужно.
    Плейсхолдеры <code>{{source}}</code> и <code>{{date}}</code> должны остаться.
  </p>
  <form method="POST" action="/save">
    <div class="card">
      <textarea name="prompt" spellcheck="false">{prompt}</textarea>
      <div class="row">
        <button type="submit">Сохранить</button>
        <span class="msg {css}">{message}</span>
      </div>
    </div>
  </form>
</div>
</body>
</html>
"""


def read_prompt() -> str:
    try:
        return PROMPT_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


class Handler(BaseHTTPRequestHandler):
    server_version = "AuditorPromptEditor"

    def _render(self, prompt: str, message: str = "", css: str = "", status: int = 200) -> None:
        body = PAGE.format(
            prompt=html.escape(prompt), message=html.escape(message), css=css
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        self._render(read_prompt())

    def do_POST(self) -> None:
        if self.path != "/save":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._render(read_prompt(), "Некорректный размер запроса.", "err", 400)
            return

        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        submitted = parse_qs(raw, keep_blank_values=True).get("prompt", [""])[0]
        # Browsers submit CRLF; keep the file in unix line endings.
        submitted = submitted.replace("\r\n", "\n")

        error = validate_prompt(submitted)
        if error:
            # Hand back what they typed so nothing is lost to a validation bounce.
            self._render(submitted, error, "err", 400)
            return

        try:
            save_prompt(submitted)
        except OSError as exc:
            self._render(submitted, f"Не удалось сохранить: {exc}", "err", 500)
            return

        self._render(read_prompt(), "Сохранено.", "ok")

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("prompt-editor: " + fmt % args + "\n")


def main() -> int:
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"refusing to bind {HOST}: this editor has no authentication and must "
            "stay on loopback behind an ssh tunnel",
            file=sys.stderr,
        )
        return 2

    server = HTTPServer((HOST, PORT), Handler)
    print(f"prompt editor on http://{HOST}:{PORT} (loopback only)", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
