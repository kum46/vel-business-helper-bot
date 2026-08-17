import os
import threading
import asyncio
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing - Set it in Render Dashboard > Environment")

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Vel Business Helper is running!"

@flask_app.route("/health")
def health():
    return "OK", 200

# ---------- Telegram Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🛠 Services", callback_data="services")],
        [InlineKeyboardButton("💰 Price", callback_data="price")],
        [InlineKeyboardButton("📞 Contact", callback_data="contact")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    await update.message.reply_text(
        "👋 Welcome to Vel Business Helper!\n\n"
        "నేను మీ business కి సంబంధించిన basic information, services, prices మరియు contact details అందించడానికి సహాయం చేస్తాను.\n\n"
        "కింద ఉన్న option ఎంచుకోండి 👇",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "services":
        text = "🛠 SERVICES\n\n• Business information\n• Product information\n• Pump information\n• Customer enquiry support\n• Contact details\n\nమరిన్ని services త్వరలో add చేస్తాము."
    elif query.data == "price":
        text = "💰 PRICE INFORMATION\n\nProduct/model మీద price మారుతుంది.\n\nమీకు కావాల్సిన product లేదా model పేరు పంపండి.\nఉదా: CRI pump, 1 HP pump, 2 HP motor, Openwell pump"
    elif query.data == "contact":
        text = "📞 CONTACT\n\nLaxman Rela\nDealer - C.R.I. PUMPS\nVAARAAHI ENGINEERING COMPANY\n\n📍 D.No. 7-30-24/2, Main Road, Rajamahendravaram, A.P. - 533101\n\n📱 94908 35009"
    else:
        text = "❓ HELP\n\nమీకు కావాల్సిన విషయం message గా పంపండి.\n\nఉదా:\n• Contact\n• Price\n• CRI pump\n• 1 HP pump\n• Services\n\n/start పంపితే main menu వస్తుంది."
    await query.edit_message_text(text)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip().lower()
    if txt in ["hi", "hello", "hey", "హాయ్", "హలో"]:
        await update.message.reply_text("👋 Hello!\n\nVel Business Helper కి Welcome!\n/start నొక్కండి.")
    elif "contact" in txt:
        await update.message.reply_text("📞 CONTACT\n\nLaxman Rela\nDealer - C.R.I. PUMPS\nVAARAAHI ENGINEERING COMPANY\n📍 Rajamahendravaram\n📱 94908 35009")
    elif "price" in txt or "ధర" in txt:
        await update.message.reply_text("💰 Price తెలుసుకోవడానికి product/model పేరు పంపండి.\nఉదా: CRI 1 HP, Openwell pump")
    elif "pump" in txt or "పంప్" in txt:
        await update.message.reply_text("🔧 PUMP INFORMATION\n\nమీకు కావాల్సిన pump details కోసం model పేరు పంపండి.\nఉదా: 1 HP pump, 2 HP pump, Openwell pump, Submersible pump")
    else:
        await update.message.reply_text("🙂 మీ message అందింది.\n\nదయచేసి /start పంపి option ఎంచుకోండి.\n\nలేదా మీకు కావాల్సిన product పేరు పంపండి.")

# ---------- Background Bot Runner (Fix for set_wakeup_fd error) ----------
def run_bot():
    async def _run():
        application = Application.builder().token(BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

        print("Vel Business Helper Bot Initializing...", flush=True)
        await application.initialize()
        await application.start()
        # drop_pending_updates=True to avoid old messages
        await application.updater.start_polling(drop_pending_updates=True)
        print("Telegram polling is running... /start works", flush=True)
        await application.updater.idle()
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    except RuntimeError as e:
        print(f"Bot thread error: {e}", flush=True)

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting Flask HTTP server on 0.0.0.0:{port} for Render health check", flush=True)
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)

if __name__ == "__main__":
    # Main thread -> Flask (Render binds 0.0.0.0:$PORT and health checks open port)
    # Background thread -> Telegram Bot polling with manual async startup (avoids set_wakeup_fd error)
    bot_thread = threading.Thread(target=run_bot, name="TelegramBotThread", daemon=True)
    bot_thread.start()
    run_flask()
