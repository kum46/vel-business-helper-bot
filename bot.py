import os
import asyncio
import threading
import logging

from flask import Flask, request
from telegram import Update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
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

ADMIN_USER_ID = 8804669460

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("VelBusinessHelper")

app = Flask(__name__)

@app.route("/")
def home():
    return "Vel Business Helper is running!"

@app.route("/health")
def health():
    return "OK"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🛠 Services", callback_data="services")],
        [InlineKeyboardButton("💰 Price", callback_data="price")],
        [InlineKeyboardButton("📞 Contact", callback_data="contact")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    await update.message.reply_text("👋 Welcome to Vel Business Helper!\n\nనేను మీ business కి సంబంధించిన basic information, services, prices మరియు contact details అందించడానికి సహాయం చేస్తాను.\n\nకింద ఉన్న option ఎంచుకోండి 👇", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "services":
        text = "🛠 SERVICES\n\n• Business information\n• Product information\n• Pump information\n• Customer enquiry support\n• Contact details\n\nమరిన్ని services త్వరలో add చేస్తాము."
    elif query.data == "price":
        text = "💰 PRICE INFORMATION\n\nProduct/model మీద price మారుతుంది.\n\nమీకు కావాల్సిన product లేదా model పేరు పంపండి.\n\nఉదాహరణ:\nCRI pump\n1 HP pump\n2 HP motor\nOpenwell pump"
    elif query.data == "contact":
        text = "📞 CONTACT\n\nLaxman Rela\nDealer - C.R.I. PUMPS\nVAARAAHI ENGINEERING COMPANY\n\n📍 D.No. 7-30-24/2, Main Road,\nRajamahendravaram, A.P. - 533101\n\n📱 94908 35009"
    else:
        text = "❓ HELP\n\nమీకు కావాల్సిన విషయం message గా పంపండి.\n\nఉదాహరణలు:\n• Contact\n• Price\n• CRI pump\n• 1 HP pump\n• Services\n\n/start పంపితే main menu వస్తుంది."
    await query.edit_message_text(text)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip().lower()
    if text in ["hi", "hello", "hey", "హాయ్", "హలో"]:
        await update.message.reply_text("👋 Hello!\n\nVel Business Helper కి Welcome!\n\n/start నొక్కండి.")
    elif "contact" in text:
        await update.message.reply_text("📞 CONTACT\n\nLaxman Rela\nDealer - C.R.I. PUMPS\nVAARAAHI ENGINEERING COMPANY\n\n📍 D.No. 7-30-24/2, Main Road,\nRajamahendravaram, A.P. - 533101\n\n📱 94908 35009")
    elif "price" in text or "ధర" in text:
        await update.message.reply_text("💰 Price తెలుసుకోవడానికి product/model పేరు పంపండి.\n\nఉదాహరణ:\nCRI 1 HP\nCRI 2 HP\nOpenwell pump")
    elif "pump" in text or "పంప్" in text:
        await update.message.reply_text("🔧 PUMP INFORMATION\n\nమీకు కావాల్సిన pump details కోసం model పేరు పంపండి.\n\nఉదాహరణ:\n1 HP pump\n2 HP pump\nOpenwell pump\nSubmersible pump")
    else:
        await update.message.reply_text("🙂 మీ message అందింది.\n\nదయచేసి /start పంపి option ఎంచుకోండి.\n\nలేదా మీకు కావాల్సిన product పేరు పంపండి.")

async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Telegram handler error: %s", context.error, exc_info=context.error)

def _get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Add Product", callback_data="admin_add_product")],
        [InlineKeyboardButton("📦 View Products", callback_data="admin_view_products")],
        [InlineKeyboardButton("✏️ Edit Product", callback_data="admin_edit_product")],
        [InlineKeyboardButton("💰 Change Price", callback_data="admin_change_price")],
        [InlineKeyboardButton("📝 Edit Details", callback_data="admin_edit_details")],
        [InlineKeyboardButton("🗑️ Delete Product", callback_data="admin_delete_product")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else None
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ You are not authorized to access the Admin Panel.")
        return
    await update.message.reply_text("🔐 ADMIN PANEL\n\nWelcome Admin! కింద ఉన్న option ఎంచుకోండి 👇", reply_markup=_get_admin_keyboard())

# FIX 2: Admin callback - security check ముందు, answer ఒక్కసారే
async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id if query.from_user else None
    if user_id != ADMIN_USER_ID:
        await query.answer("❌ Not authorized", show_alert=True)
        return
    await query.answer()
    placeholders = {
        "admin_add_product": "➕ Add Product\n\nProduct management feature త్వరలో అందుబాటులో ఉంటుంది.",
        "admin_view_products": "📦 View Products\n\nProduct management feature త్వరలో అందుబాటులో ఉంటుంది.",
        "admin_edit_product": "✏️ Edit Product\n\nProduct management feature త్వరలో అందుబాటులో ఉంటుంది.",
        "admin_change_price": "💰 Change Price\n\nProduct management feature త్వరలో అందుబాటులో ఉంటుంది.",
        "admin_edit_details": "📝 Edit Details\n\nProduct management feature త్వరలో అందుబాటులో ఉంటుంది.",
        "admin_delete_product": "🗑️ Delete Product\n\nProduct management feature త్వరలో అందుబాటులో ఉంటుంది.",
    }
    text = placeholders.get(query.data, "Product management feature త్వరలో అందుబాటులో ఉంటుంది.")
    await query.edit_message_text(text)

telegram_app = Application.builder().token(BOT_TOKEN).build()
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("admin", admin_command))
telegram_app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
telegram_app.add_handler(CallbackQueryHandler(button_handler))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
telegram_app.add_error_handler(telegram_error_handler)

telegram_loop = asyncio.new_event_loop()

def _run_telegram_loop():
    asyncio.set_event_loop(telegram_loop)
    telegram_loop.run_forever()

telegram_thread = threading.Thread(target=_run_telegram_loop, name="TelegramLoopThread", daemon=True)
telegram_thread.start()

async def _init_telegram():
    await telegram_app.initialize()
    await telegram_app.start()

async def _shutdown_telegram():
    try:
        await telegram_app.stop()
        await telegram_app.shutdown()
    except Exception:
        pass

try:
    future = asyncio.run_coroutine_threadsafe(_init_telegram(), telegram_loop)
    future.result(timeout=30)
    logger.info("Telegram application initialized in persistent loop - webhook mode ready.")
except Exception:
    logger.exception("Failed to initialize Telegram application in persistent loop")

WEBHOOK_PATH = "/telegram-webhook"

@app.route(WEBHOOK_PATH, methods=["POST"])
def telegram_webhook():
    try:
        json_data = request.get_json(force=True)
        if not json_data:
            return "OK", 200
        update = Update.de_json(json_data, telegram_app.bot)
        async def _process():
            await telegram_app.process_update(update)
        future = asyncio.run_coroutine_threadsafe(_process(), telegram_loop)
        future.result(timeout=30)
    except Exception:
        logger.exception("Error in webhook endpoint")
    return "OK", 200

def _get_webhook_url():
    full_url = os.getenv("WEBHOOK_URL")
    if full_url:
        return full_url
    base_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("WEBHOOK_BASE_URL")
    if base_url:
        base_url = base_url.rstrip("/")
        return f"{base_url}{WEBHOOK_PATH}"
    return None

def _register_webhook_if_needed():
    webhook_url = _get_webhook_url()
    if not webhook_url:
        logger.info("WEBHOOK_URL / RENDER_EXTERNAL_URL not set, skipping webhook registration.")
        return
    try:
        async def _set():
            current = await telegram_app.bot.get_webhook_info()
            if current.url == webhook_url:
                logger.info("Webhook already correctly configured: %s", webhook_url)
                return
            # FIX 3: drop_pending_updates=False - pending messages కోల్పోకుండా ఉండటానికి
            await telegram_app.bot.set_webhook(url=webhook_url, drop_pending_updates=False)
            logger.info("Webhook set to: %s", webhook_url)
        future = asyncio.run_coroutine_threadsafe(_set(), telegram_loop)
        future.result(timeout=30)
    except Exception:
        logger.exception("Failed to set webhook, but Flask will continue running")

_register_webhook_if_needed()

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", "10000"))
    # FIX 1: ఈ line ఒకే line గా ఉండాలి - మధ్యలో break వద్దు
    logger.info("Starting Flask Main Web Server on 0.0.0.0:%s", PORT)
    try:
        # FIX 1: ఇది కూడా ఒకే line గా ఉండాలి
        app.run(host="0.0.0.0", port=PORT, use_reloader=False)
    finally:
        try:
            future = asyncio.run_coroutine_threadsafe(_shutdown_telegram(), telegram_loop)
            future.result(timeout=10)
        except Exception:
            pass
