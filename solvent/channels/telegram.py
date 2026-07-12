"""Telegram channel adapter (OpenClaw long-poll pattern)."""

from __future__ import annotations

import json
import os
import urllib.request

from ..gateway import Gateway, register_outbound


def send_telegram_message(external_id: str, text: str) -> None:
    """Send an outbound message through Telegram's HTTP API."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": int(external_id), "text": text[:4000]}).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=15)
    except Exception:
        pass


def _require_ptb():
    try:
        from telegram import Update
        from telegram.ext import (
            Application,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )

        return Update, Application, CommandHandler, MessageHandler, filters, ContextTypes
    except ImportError as exc:
        raise RuntimeError(
            "python-telegram-bot is required. Install with: "
            "pip install 'solvent-agent[telegram]'"
        ) from exc


async def _reply(update, text: str) -> None:
    if update.message:
        await update.message.reply_text(text[:4000])


def build_application(gateway: Gateway | None = None) -> object:
    Update, Application, CommandHandler, MessageHandler, filters, ContextTypes = _require_ptb()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    gateway = gateway or Gateway()
    register_outbound("telegram", send_telegram_message)

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        user = update.effective_user
        text = update.message.text or ""
        reply = gateway.handle_inbound(
            "telegram",
            str(user.id),
            text,
            user_label=user.username,
        )
        await _reply(update, reply)

    app = Application.builder().token(token).build()
    for command in ("start", "help", "status", "jobs", "quote"):
        app.add_handler(CommandHandler(command, on_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


def main():
    app = build_application()
    print("SOLVENT Telegram bot starting (long-poll)...")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
