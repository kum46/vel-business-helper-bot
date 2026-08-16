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

# =========================
# BOT TOKEN
# =========================

BOT_TOKEN = os.environ["BOT_TOKEN"]

# =========================
# FLASK WEB SERVER
# =========================

app = Flask(__name__)


@app.route("/")
def home():
    return "Vel Business Helper is running!"


# =========================
# MAIN MENU
# =========================

def main_keyboard():
    keyboard = [
        [InlineKeyboardButton("🛍️ Services", callback_data="services")],
        [InlineKeyboardButton("🔧 Products", callback_data="products")],
        [InlineKeyboardButton("💰 Price", callback_data="price")],
        [InlineKeyboardButton("📞 Contact", callback_data="contact")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================
# PRODUCTS MENU
# =========================

def products_keyboard():
    keyboard = [
        [InlineKeyboardButton(
            "🔵 Borewell Submersible Pumps",
            callback_data="borewell"
        )],
        [InlineKeyboardButton(
            "🔵 Openwell Submersible Pumps",
            callback_data="openwell"
        )],
        [InlineKeyboardButton(
            "🔵 Centrifugal Monoblock Pumps",
            callback_data="monoblock"
        )],
        [InlineKeyboardButton(
            "🔵 Self-Priming Pumps",
            callback_data="selfpriming"
        )],
        [InlineKeyboardButton(
            "🔵 I-Smart Pumps",
            callback_data="ismart"
        )],
        [InlineKeyboardButton(
            "🔵 Mini Pumps",
            callback_data="mini"
        )],
        [InlineKeyboardButton(
            "🔵 Pressure Booster Pumps",
            callback_data="booster"
        )],
        [InlineKeyboardButton(
            "🔵 Swimming Pool Pumps",
            callback_data="pool"
        )],
        [InlineKeyboardButton(
            "🔵 Waste Water Pumps",
            callback_data="wastewater"
        )],
        [InlineKeyboardButton(
            "⬅️ Main Menu",
            callback_data="main"
        )],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================
# START COMMAND
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "👋 Welcome to Vel Business Helper!\n\n"
        "I can help you with simple business information,\n"
        "services, products, prices and customer support.\n\n"
        "Please select an option below:",
        reply_markup=main_keyboard()
    )


# =========================
# BUTTON HANDLER
# =========================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    # =====================
    # MAIN MENU
    # =====================

    if query.data == "main":

        await query.message.reply_text(
            "🏠 MAIN MENU\n\n"
            "Please select an option:",
            reply_markup=main_keyboard()
        )

    # =====================
    # SERVICES
    # =====================

    elif query.data == "services":

        await query.message.reply_text(
            "🛍️ SERVICES\n\n"
            "• Pump selection guidance\n"
            "• Product information\n"
            "• Customer support\n"
            "• Pump application guidance\n"
            "• Basic product assistance\n\n"
            "📞 Contact our dealer for detailed assistance."
        )

    # =====================
    # PRODUCTS
    # =====================

    elif query.data == "products":

        await query.message.reply_text(
            "🔧 C.R.I. PUMPS - PRODUCTS\n\n"
            "Please select a product category:",
            reply_markup=products_keyboard()
        )

    # =====================
    # BOREWELL
    # =====================

    elif query.data == "borewell":

        await query.message.reply_text(
            "🔵 BOREWELL SUBMERSIBLE PUMPS\n\n"
            "C.R.I. borewell submersible pumps "
            "are designed for water pumping applications.\n\n"
            "📌 For exact model, HP, head and availability, "
            "please contact our dealer."
        )

    # =====================
    # OPENWELL
    # =====================

    elif query.data == "openwell":

        await query.message.reply_text(
            "🔵 OPENWELL SUBMERSIBLE PUMPS\n\n"
            "Suitable for openwell water pumping applications.\n\n"
            "📌 For exact model and specifications, "
            "please contact our dealer."
        )

    # =====================
    # MONOBLOCK
    # =====================

    elif query.data == "monoblock":

        await query.message.reply_text(
            "🔵 CENTRIFUGAL MONOBLOCK PUMPS\n\n"
            "Used for various domestic, agricultural "
            "and water transfer applications.\n\n"
            "📌 Contact our dealer for model and HP details."
        )

    # =====================
    # SELF PRIMING
    # =====================

    elif query.data == "selfpriming":

        await query.message.reply_text(
            "🔵 SELF-PRIMING PUMPS\n\n"
            "Designed for suitable water pumping applications "
            "where self-priming operation is required.\n\n"
            "📌 Contact our dealer for exact model details."
        )

    # =====================
    # I-SMART
    # =====================

    elif query.data == "ismart":

        await query.message.reply_text(
            "🔵 I-SMART PUMPS\n\n"
            "C.R.I. I-Smart range of pumps.\n\n"
            "📌 For exact model, HP and specifications, "
            "please contact our dealer."
        )

    # =====================
    # MINI PUMPS
    # =====================

    elif query.data == "mini":

        await query.message.reply_text(
            "🔵 MINI PUMPS\n\n"
            "Compact pump solutions for suitable applications.\n\n"
            "📌 Contact our dealer for model and availability."
        )

    # =====================
    # PRESSURE BOOSTER
    # =====================

    elif query.data == "booster":

        await query.message.reply_text(
            "🔵 PRESSURE BOOSTER PUMPS\n\n"
            "Used for water pressure boosting applications.\n\n"
            "📌 Contact our dealer for suitable model selection."
        )

    # =====================
    # SWIMMING POOL
    # =====================

    elif query.data == "pool":

        await query.message.reply_text(
            "🔵 SWIMMING POOL PUMPS\n\n"
            "Pump solutions for swimming pool applications.\n\n"
            "📌 Contact our dealer for model and specifications."
        )

    # =====================
    # WASTE WATER
    # =====================

    elif query.data == "wastewater":

        await query.message.reply_text(
            "🔵 WASTE WATER PUMPS\n\n"
            "Pump solutions for suitable waste water applications.\n\n"
            "📌 Contact our dealer for exact model details."
        )

    # =====================
    # PRICE
    # =====================

    elif query.data == "price":

        await query.message.reply_text(
            "💰 PRICE\n\n"
            "Please contact us for current pricing,\n"
            "availability and service details.\n\n"
            "📞 Dealer: Laxman Rela\n"
            "📱 94908 35009"
        )

    # =====================
    # CONTACT
    # =====================

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

        await query.message.reply_text(text)

    # =====================
    # HELP
    # =====================

    elif query.data == "help":

        await query.message.reply_text(
            "❓ HELP\n\n"
            "You can ask me about:\n\n"
            "• Services\n"
            "• Products\n"
            "• Prices\n"
            "• Customer support\n"
            "• Contact details"
        )


# =========================
# TEXT HANDLER
# =========================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.message.text.strip().lower()

    # ---------------------
    # SERVICES
    # ---------------------

    if message in ["service", "services"]:

        await update.message.reply_text(
            "🛍️ SERVICES\n\n"
            "• Pump selection guidance\n"
            "• Product information\n"
            "• Customer support\n"
            "• Pump application guidance\n"
            "• Basic product assistance"
        )

    # ---------------------
    # PRODUCTS
    # ---------------------

    elif message in [
        "product",
        "products",
        "pump",
        "pumps",
        "cri",
        "cri pumps"
    ]:

        await update.message.reply_text(
            "🔧 C.R.I. PUMPS - PRODUCTS\n\n"
            "Please select a product category:",
            reply_markup=products_keyboard()
        )

    # ---------------------
    # PRICE
    # ---------------------

    elif message in [
        "price",
        "prices",
        "cost",
        "rate",
        "rates"
    ]:

        await update.message.reply_text(
            "💰 PRICE\n\n"
            "Please contact us for current pricing,\n"
            "availability and service details.\n\n"
            "📞 Laxman Rela\n"
            "📱 94908 35009"
        )

    # ---------------------
    # CONTACT
    # ---------------------

    elif message in [
        "contact",
        "phone",
        "mobile",
        "dealer"
    ]:

        text = (
            "📞 CONTACT\n\n"
            "Laxman Rela\n"
            "Dealer - C.R.I. PUMPS\n"
            "VAARAAHI ENGINEERING COMPANY\n\n"
            "📍 D.No. 7-30-24/2, Main Road,\n"
            "Rajamahendravaram, A.P. - 533101\n\n"
            "📱 94908 35009"
        )

        await update.message.reply_text(text)

    # ---------------------
    # HELP
    # ---------------------

    elif message in ["help", "?", "hi", "hello", "hii"]:

        await update.message.reply_text(
            "👋 Hello! Welcome to Vel Business Helper!\n\n"
            "How can I help you today?",
            reply_markup=main_keyboard()
        )

    # ---------------------
    # OTHER MESSAGE
    # ---------------------

    else:

        await update.message.reply_text(
            "🤖 Thanks for your message!\n\n"
            "I can help with:\n"
            "• Services\n"
            "• Products\n"
            "• Prices\n"
            "• Contact\n"
            "• Customer support\n\n"
            "Please type 'Products', 'Price' or 'Contact'."
        )


# =========================
# RUN BOT
# =========================

def run_bot():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CallbackQueryHandler(button_handler)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    application.run_polling(
        drop_pending_updates=True
    )


# =========================
# START FLASK + BOT
# =========================

if __name__ == "__main__":

    bot_thread = threading.Thread(
        target=run_bot,
        daemon=True
    )

    bot_thread.start()

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
