# DK Capital

VPS-ready Python services for DK Capital.

Initial services:

- Telegram listener using a Telegram user session
- Discord bot skeleton
- Docker Compose deployment
- Structured logging and local JSONL event persistence

The Telegram listener is intentionally kept separate from signal parsing and execution logic so those layers can be added safely later.

## Quick start

1. Copy `.env.example` to `.env`.
2. Add your Telegram API ID and API hash from https://my.telegram.org.
3. Generate a Telegram StringSession using `scripts/generate_telegram_session.py`.
4. Put the generated session string in `.env`.
5. Set `TELEGRAM_SOURCE_CHATS` to the channel/group IDs or usernames you want to listen to.
6. Run `docker compose up -d telegram-listener`.
7. Follow logs with `docker compose logs -f telegram-listener`.

Discord is optional at this stage and is behind the `discord` Compose profile.

See the deployment section below for the full VPS flow.

## Telegram events

The listener records:

- new messages
- edited messages
- deleted messages

Normalized events are written to stdout and to `data/telegram-events.jsonl`. This gives us a durable raw event stream to build the later signal parser against.

## VPS deployment

After cloning the repository:

```bash
cd DKCAPITAL
cp .env.example .env
nano .env
docker compose build
```

Generate a Telegram session interactively:

```bash
docker compose run --rm telegram-session
```

Copy the printed session value into `.env` as `TELEGRAM_SESSION_STRING`, then start the listener:

```bash
docker compose up -d telegram-listener
docker compose ps
docker compose logs -f telegram-listener
```

To enable the Discord bot later, add `DISCORD_BOT_TOKEN` to `.env` and run:

```bash
docker compose --profile discord up -d
```

## Security

Never commit `.env`, Telegram session strings, Discord tokens, API hashes, or session files. A Telegram StringSession grants account-level API access and must be treated like a password.
