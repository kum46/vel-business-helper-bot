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
            "You can ask me about:\n"
            "• Services\n"
            "• Products\n"
            "• Prices\n"
            "• Customer support"
        )

    await query.edit_message_text(text)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text.strip().lower()

    if message in ["hello", "hi", "hey"]:
        await update.message.reply_text(
            "👋 Hello! Welcome to Vel Business Helper!\n\n"
            "How can I help you today?"
        )

    elif message in ["services", "service"]:
        await update.message.reply_text(
            "🛍️ SERVICES\n\n"
            "• Customer reply support\n"
            "• Product information\n"
            "• Business questions\n"
            "• Simple daily business tasks"
        )

    elif message in ["price", "prices", "cost"]:
        await update.message.reply_text(
            "💰 PRICE\n\n"
            "Please contact us for pricing and service details."
        )

    elif message in ["contact", "phone", "support"]:
        await update.message.reply_text(
            "📞 CONTACT\n\n"
            "Laxman Rela\n"
            "Dealer - C.R.I. PUMPS\n"
            "VAARAAHI ENGINEERING COMPANY\n\n"
            "📍 D.No. 7-30-24/2, Main Road,\n"
            "Rajamahendravaram, A.P. - 533101\n\n"
            "📱 94908 35009"
        )

    elif message in ["help"]:
        await update.message.reply_text(
            "❓ HELP\n\n"
            "You can ask me about:\n"
            "• Services\n"
            "• Products\n"
            "• Prices\n"
            "• Customer support"
        )

    else:
        await update.message.reply_text(
            "🤖 Thanks for your message!\n\n"
            "I can help with services, products, prices "
            "and customer support.\n\n"
            "Please type 'services', 'price', 'contact' or 'help'."
        )


def run_web():
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )


def main():
    threading.Thread(target=run_web, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(buttons))

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    print("Vel Business Helper started!")

    application.run_polling()


if __name__ == "__main__":
    main()
