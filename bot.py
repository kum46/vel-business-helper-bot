import os
import asyncio
import threading
import logging
import signal
import time

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("VelBusinessHelper")

app = Flask(__name__)

@app.route("/")
def home():
    return "Vel Business Helper is running!"

@app.route("/health")
def health():
    return "OK"

telegram_stop_event = threading.Event()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🛠 Services", callback_data="services")],
        [InlineKeyboardButton("💰 Price", callback_data="price")],
        [InlineKeyboardButton("📞 Contact", callback_data="contact")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]

    await update.message.reply_text(
        "👋 Welcome to Vel Business Helper!\n\n"
        "నేను మీ business కి సంబంధించిన basic information, "
        "services, prices మరియు contact details అందించడానికి సహాయం చేస్తాను.\n\n"
        "కింద ఉన్న option ఎంచుకోండి 👇",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "services":
        text = (
            "🛠 SERVICES\n\n"
            "• Business information\n"
            "• Product information\n"
            "• Pump information\n"
            "• Customer enquiry support\n"
            "• Contact details\n\n"
            "మరిన్ని services త్వరలో add చేస్తాము."
        )

    elif query.data == "price":
        text = (
            "💰 PRICE INFORMATION\n\n"
            "Product/model మీద price మారుతుంది.\n\n"
            "మీకు కావాల్సిన product లేదా model పేరు పంపండి.\n\n"
            "ఉదాహరణ:\n"
            "CRI pump\n"
            "1 HP pump\n"
            "2 HP motor\n"
            "Openwell pump"
        )

    elif query.data == "contact":
        text = (
            "📞 CONTACT\n\n"
            "Laxman Rela\n"
            "Dealer - C.R.I. PUMPS\n"
            "VAARAAHI ENGINEERING COMPANY\n\n"
            "📍 D.No. 7-30-24/2, Main Road,\n"
            "Rajamahendravaram, A.P. - 533101\n\n"
            "📱 94908 35009"
        )

    else:
        text = (
            "❓ HELP\n\n"
            "మీకు కావాల్సిన విషయం message గా పంపండి.\n\n"
            "ఉదాహరణలు:\n"
            "• Contact\n"
            "• Price\n"
            "• CRI pump\n"
            "• 1 HP pump\n"
            "• Services\n\n"
            "/start పంపితే main menu వస్తుంది."
        )

    await query.edit_message_text(text)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip().lower()

    if text in ["hi", "hello", "hey", "హాయ్", "హలో"]:
        await update.message.reply_text(
            "👋 Hello!\n\n"
            "Vel Business Helper కి Welcome!\n\n"
            "/start నొక్కండి."
        )

    elif "contact" in text:
        await update.message.reply_text(
            "📞 CONTACT\n\n"
            "Laxman Rela\n"
            "Dealer - C.R.I. PUMPS\n"
            "VAARAAHI ENGINEERING COMPANY\n\n"
            "📍 D.No. 7-30-24/2, Main Road,\n"
            "Rajamahendravaram, A.P. - 533101\n\n"
            "📱 94908 35009"
        )

    elif "price" in text or "ధర" in text:
        await update.message.reply_text(
            "💰 Price తెలుసుకోవడానికి product/model పేరు పంపండి.\n\n"
            "ఉదాహరణ:\n"
            "CRI 1 HP\n"
            "CRI 2 HP\n"
            "Openwell pump"
        )

    elif "pump" in text or "పంప్" in text:
        await update.message.reply_text(
            "🔧 PUMP INFORMATION\n\n"
            "మీకు కావాల్సిన pump details కోసం model పేరు పంపండి.\n\n"
            "ఉదాహరణ:\n"
            "1 HP pump\n"
            "2 HP pump\n"
            "Openwell pump\n"
            "Submersible pump"
        )

    else:
        await update.message.reply_text(
            "🙂 మీ message అందింది.\n\n"
            "దయచేసి /start పంపి option ఎంచుకోండి.\n\n"
            "లేదా మీకు కావాల్సిన product పేరు పంపండి."
        )


async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(
        "Telegram handler error: %s",
        context.error,
        exc_info=context.error,
    )


def polling_error(error):
    logger.error(
        "Telegram polling error: %s",
        error,
        exc_info=error,
    )


async def telegram_main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )
    application.add_error_handler(telegram_error_handler)

    logger.info("Initializing Telegram application...")

    await application.initialize()

    try:
        logger.info("Deleting Telegram webhook...")

        await application.bot.delete_webhook(
            drop_pending_updates=True
        )

        logger.info("Telegram webhook deleted.")

        await application.start()

        logger.info("Telegram application started.")

        if application.updater is None:
            raise RuntimeError("Telegram updater is not available.")

        await application.updater.start_polling(
            drop_pending_updates=False,
            error_callback=polling_error,
        )

        logger.info("Telegram polling is running...")

        await asyncio.to_thread(
            telegram_stop_event.wait
        )

    finally:
        logger.info("Stopping Telegram polling...")

        if application.updater is not None:
            try:
                await application.updater.stop()
            except Exception:
                logger.exception("Error while stopping Telegram updater")

        try:
            await application.stop()
        except Exception:
            logger.exception("Error while stopping Telegram application")

        try:
            await application.shutdown()
        except Exception:
            logger.exception("Error while shutting down Telegram application")

        logger.info("Telegram bot shutdown completed.")


def telegram_worker():
    logger.info("Telegram background worker starting...")

    while not telegram_stop_event.is_set():
        try:
            asyncio.run(telegram_main())
        except Exception:
            logger.exception("Telegram background worker crashed.")
            if telegram_stop_event.is_set():
                break
            time.sleep(5)
            continue
        else:
            break

    logger.info("Telegram background worker stopped.")


def shutdown_signal(signum, frame):
    logger.info("Shutdown signal received: %s", signum)
    telegram_stop_event.set()
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, shutdown_signal)
    signal.signal(signal.SIGINT, shutdown_signal)

    telegram_thread = threading.Thread(
        target=telegram_worker,
        name="TelegramBotThread",
        daemon=True,
    )

    telegram_thread.start()

    logger.info("Telegram background worker started.")

    PORT = int(os.environ.get("PORT", "10000"))

    logger.info(
        "Starting Flask HTTP server on 0.0.0.0:%s",
        PORT
    )

    try:
        app.run(
            host="0.0.0.0",
            port=PORT,
            use_reloader=False,
        )
    except (KeyboardInterrupt, SystemExit):
        logger.info("Flask server stopping...")
    finally:
        telegram_stop_event.set()

        if telegram_thread.is_alive():
            telegram_thread.join(timeout=10)

        logger.info("Vel Business Helper stopped.")
