# Auditor

Auditor turns a YouTube video into a structured Obsidian-ready Markdown note using NotebookLM.

It can save notes either:

- locally into an Obsidian vault folder;
- remotely to a server folder over `ssh`/`scp`.

It can also run as a quiet Telegram bot: send it a YouTube link and it saves the note without replying in chat.

## What It Does

1. Creates a NotebookLM notebook.
2. Adds the YouTube video as a source.
3. Asks NotebookLM to summarize it in a fixed note format.
4. Saves the answer as a Markdown file with frontmatter.
5. Optionally uploads the Markdown file to your server.

## Requirements

- macOS or Linux
- Python 3.12 or 3.13
- Google account with access to NotebookLM
- Browser where you are already logged into Google
- For remote storage: working `ssh` access to your server

## Install

Clone the repo and create a virtual environment:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 python -m pip install -r requirements.txt
```

The `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1` prefix helps install the browser-cookie dependency on newer Python versions.

## First Run

Run with browser cookie import:

```bash
python script.py "https://www.youtube.com/watch?v=VIDEO_ID" --browser-cookies chrome
```

Other supported browser values:

```bash
chrome
safari
firefox
edge
auto
```

After the first successful run, cookies are saved locally in `.notebooklm/`, so future runs can usually omit `--browser-cookies`:

```bash
python script.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

## Save To Local Obsidian

Use `--vault`:

```bash
python script.py "https://www.youtube.com/watch?v=VIDEO_ID" --vault "/Users/you/ObsidianVault/Videos"
```

You can also set a default:

```bash
export OBSIDIAN_VIDEO_VAULT="/Users/you/ObsidianVault/Videos"
```

## Save To Server

Use `--remote-vault`:

```bash
python script.py "https://www.youtube.com/watch?v=VIDEO_ID" --remote-vault "user@server:/srv/obsidian/Videos"
```

The script will:

1. create the remote folder if needed;
2. generate the Markdown note locally in a temporary folder;
3. upload it to the server with `scp`.

You can also set a default:

```bash
export OBSIDIAN_REMOTE_VAULT="user@server:/srv/obsidian/Videos"
```

Then run:

```bash
python script.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `NOTEBOOKLM_YOUTUBE_URL` | Default YouTube URL |
| `OBSIDIAN_VIDEO_VAULT` | Default local save folder |
| `OBSIDIAN_REMOTE_VAULT` | Default remote server folder |
| `NOTEBOOKLM_PROFILE` | Optional NotebookLM profile name |
| `NOTEBOOKLM_HOME` | Optional auth storage folder |

## Security Notes

Do not commit `.notebooklm/`. It contains browser-derived auth cookies.

The repo ignores:

- `.notebooklm/`
- `.venv/`
- `.venv313/`
- `__pycache__/`

## Example

```bash
python script.py "https://www.youtube.com/watch?v=tXiK8PmYgZk" --remote-vault "antonio@example.com:/srv/obsidian/Videos"
```

## Telegram Bot

Create a bot with [@BotFather](https://t.me/BotFather), copy the token, then set:

```bash
export TELEGRAM_BOT_TOKEN="123456:telegram-bot-token"
export OBSIDIAN_VIDEO_VAULT="/srv/obsidian/Videos"
```

Start the bot:

```bash
python bot.py
```

Now send the bot a YouTube link. It will process the video and save the Markdown note without replying in Telegram.

To restrict the bot to specific Telegram users:

```bash
export TELEGRAM_ALLOWED_USER_IDS="123456789,987654321"
```

If the bot runs on one machine but should upload notes to another server:

```bash
export OBSIDIAN_REMOTE_VAULT="user@server:/srv/obsidian/Videos"
```

### systemd Service

Example `/etc/systemd/system/auditor-bot.service`:

```ini
[Unit]
Description=Auditor Telegram bot
After=network-online.target

[Service]
WorkingDirectory=/opt/Auditor
Environment=TELEGRAM_BOT_TOKEN=123456:telegram-bot-token
Environment=OBSIDIAN_VIDEO_VAULT=/srv/obsidian/Videos
ExecStart=/opt/Auditor/.venv/bin/python /opt/Auditor/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now auditor-bot
sudo journalctl -u auditor-bot -f
```
