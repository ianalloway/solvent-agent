"""Telegram channel adapter (OpenClaw long-poll pattern)."""

from __future__ import annotations

import json
import os
import urllib.request

from ..gateway import Gateway, register_outbound


def send_telegram_message(external_id: str, text: str) -> None:
    """Sync outbound via Telegram HTTP API (safe from worker threads)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": int(external_id), "text": text[:4000]}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass


def _require_ptb():
    try:
        from telegram import Update
        from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
        return Update, Application, CommandHandler, MessageHandler, filters, ContextTypes
    except ImportError as exc:
        raise RuntimeError(
            "python-telegram-bot required. Install: pip install -r requirements-telegram.txt"
        ) from exc


async def _reply(update, text: str) -> None:
    if update.message:
        await update.message.reply_text(text[:4000])


def build_application(gateway: Gateway | None = None) -> object:
    Update, Application, CommandHandler, MessageHandler, filters, ContextTypes = _require_ptb()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    gw = gateway or Gateway()
    register_outbound("telegram", send_telegram_message)

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        user = update.effective_user
        text = update.message.text or ""
        username = user.username
        reply = gw.handle_inbound("telegram", str(user.id), text, user_label=username)
        await _reply(update, reply)

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", on_message))
    app.add_handler(CommandHandler("help", on_message))
    app.add_handler(CommandHandler("status", on_message))
    app.add_handler(CommandHandler("jobs", on_message))
    app.add_handler(CommandHandler("quote", on_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


def main():
    import asyncio
    app = build_application()
    print("SOLVENT Telegram bot starting (long-poll)...")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
