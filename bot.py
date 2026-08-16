import os
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]

app = Flask(__name__)


@app.route("/")
def home():
    return "Vel Business Helper is running! 🤖"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🛍️ Services", callback_data="services")],
        [InlineKeyboardButton("💰 Price", callback_data="price")],
        [InlineKeyboardButton("📞 Contact", callback_data="contact")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]

    await update.message.reply_text(
        "👋 Welcome to Vel Business Helper!\n\n"
        "I can help with simple business questions, "
        "services, prices and customer support.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "services":
        text = (
            "🛍️ SERVICES\n\n"
            "• Customer reply support\n"
            "• Product information\n"
            "• Business questions\n"
            "• Simple daily business tasks"
        )

    elif query.data == "price":
        text = (
            "💰 PRICE\n\n"
            "Please contact us for pricing and service details."
        )

    elif query.data == "contact":
        text = (
            "📞 CONTACT\n\n"
            "Please send your message here.\n"
            "We will get back to you."
        )

    else:
        text = (
            "❓ HELP\n\n"
            "Use the buttons to select a service.\n"
            "You can also type your question."
        )

    await query.edit_message_text(text)


def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


def main():
    threading.Thread(target=run_web, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(buttons))

    print("Vel Business Helper started!")
    application.run_polling()


if __name__ == "__main__":
    main()
