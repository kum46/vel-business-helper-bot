import os
import threading

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

BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

app = Flask(__name__)

@app.route("/")
def home():
    return "Vel Business Helper is running!"

@app.route("/health")
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

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
        reply_markup=InlineKeyboardMarkup(keyboard)
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
            "మీకు కావాల్సిన product లేదా model పేరు పంపండి.\n"
            "ఉదాహరణ:\n\n"
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
    text = update.message.text.strip().lower()

    if text in ["hi", "hello", "hey", "హాయ్", "హలో"]:
        await update.message.reply_text(
            "👋 Hello!\n\n"
            "Vel Business Helper కి Welcome!\n"
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

def run_bot():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    print("Vel Business Helper Bot Started")
    print("Telegram polling is running...")

    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Telegram polling stays in the MAIN THREAD.
    run_bot()
