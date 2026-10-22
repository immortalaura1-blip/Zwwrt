import os
import asyncio
import logging
import threading
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    filters, CallbackQueryHandler
)

from bot import (
    BOT_TOKEN, cmd_start, cmd_admin, cmd_cancel, handle_zip_file, menu_click,
    verify_cb, color_cb, post_init, build_convs
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)
ptb_app = None

# Render ka auto URL check karo, nahi toh tera manual URL fallback use karo
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL") or "https://zippasscrack.onrender.com"

@app.route("/")
@app.route("/health")
def health():
    return "OK", 200

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    if request.method == "POST":
        try:
            update = Update.de_json(request.get_json(force=True), ptb_app.bot)
            # Background thread ke loop me update bhejo
            loop = ptb_app.loop
            asyncio.run_coroutine_threadsafe(ptb_app.process_update(update), loop)
            return "ok", 200
        except Exception as e:
            log.error(f"Webhook error: {e}")
            return "error", 500
    return "ok", 200

async def setup_bot():
    global ptb_app
    log.info("Starting bot setup...")
    
    ptb_app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Saare handlers add karo
    for c in build_convs():
        ptb_app.add_handler(c)
    
    ptb_app.add_handler(CallbackQueryHandler(verify_cb, pattern="^verify$"))
    ptb_app.add_handler(CallbackQueryHandler(color_cb, pattern="^color:"))
    
    ptb_app.add_handler(CommandHandler("start", cmd_start))
    ptb_app.add_handler(CommandHandler("admin", cmd_admin))
    ptb_app.add_handler(CommandHandler("cancel", cmd_cancel))
    
    ptb_app.add_handler(MessageHandler(filters.Document.ZIP, handle_zip_file))
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_click))
    
    await ptb_app.initialize()
    
    # Webhook set karo
    webhook_path = f"{WEBHOOK_URL}/webhook/{BOT_TOKEN}"
    log.info(f"Setting webhook to: {webhook_path}")
    await ptb_app.bot.set_webhook(webhook_path)
    
    await ptb_app.start()
    log.info("Bot is running via webhook!")
    
    # Loop ko zinda rakho
    await asyncio.Event().wait()

def run_bot_in_background():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ptb_app.loop = loop
    loop.run_until_complete(setup_bot())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    
    # Bot ko alag thread me chalao taaki Flask block na ho
    bot_thread = threading.Thread(target=run_bot_in_background, daemon=True)
    bot_thread.start()
    
    log.info(f"Starting Flask on port {port}")
    app.run(host="0.0.0.0", port=port)