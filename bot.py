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

# --- NEW: TURSO SQLite/libSQL STORAGE (Replaces products.json) ---
_turso_client = None
_turso_lock = threading.Lock()
_turso_initialized = False

def _get_turso_credentials():
    # IMPORTANT: Use os.getenv only, no hard-code, no logs of secrets
    url = os.getenv("TURSO_DATABASE_URL")
    token = os.getenv("TURSO_AUTH_TOKEN")
    return url, token

def _get_turso_client():
    global _turso_client
    url, token = _get_turso_credentials()
    if not url or not token:
        return None

    with _turso_lock:
        if _turso_client is not None:
            return _turso_client
        try:
            # libsql is the official Turso Python SDK
            try:
                import libsql
                _turso_client = libsql.connect(database=url, auth_token=token)
            except ImportError:
                # Fallback for older package name libsql_client
                import libsql_client
                _turso_client = libsql_client.create_client_sync(url=url, auth_token=token)
            logger.info("Turso client connected")
            return _turso_client
        except Exception:
            logger.exception("Failed to connect to Turso database - check TURSO_DATABASE_URL / TURSO_AUTH_TOKEN")
            return None

def _init_turso_db():
    global _turso_initialized
    if _turso_initialized:
        return True

    client = _get_turso_client()
    if client is None:
        logger.error("Turso credentials missing or client failed - products storage will fail until env vars are set")
        return False

    try:
        with _turso_lock:
            client.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    price TEXT NOT NULL,
                    details TEXT
                )
            """)
            client.commit()
        _turso_initialized = True
        logger.info("Turso products table ensured")
        _migrate_json_if_needed()
        return True
    except Exception:
        logger.exception("Failed to initialize Turso products table")
        return False

def _migrate_json_if_needed():
    """Safe migration: If data/products.json exists and Turso table is empty, import it."""
    try:
        json_path = os.path.join(os.path.dirname(__file__), "data", "products.json")
        if not os.path.exists(json_path):
            return
        import json as _json
        with open(json_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        if not isinstance(data, list) or not data:
            return

        client = _get_turso_client()
        if client is None:
            return

        with _turso_lock:
            cur = client.execute("SELECT COUNT(*) FROM products")
            count = cur.fetchone()[0] if cur else 0
            if count > 0:
                logger.info("Turso already has products, skipping JSON migration")
                return
            for p in data:
                name = p.get("name", "").strip()
                price = str(p.get("price", "")).strip()
                details = p.get("details", "").strip()
                if not name:
                    continue
                client.execute("INSERT INTO products (name, price, details) VALUES (?, ?, ?)", (name, price, details))
            client.commit()
            logger.info("Migrated %s products from products.json to Turso", len(data))
    except Exception:
        logger.exception("JSON to Turso migration failed - continuing without migration")

def _load_products():
    client = _get_turso_client()
    if client is None:
        logger.error("Turso client not available for _load_products")
        return []
    try:
        with _turso_lock:
            rs = client.execute("SELECT id, name, price, details FROM products ORDER BY id ASC")
            rows = rs.fetchall() if hasattr(rs, 'fetchall') else rs
            products = []
            for r in rows:
                # libsql returns tuple or Row object
                try:
                    pid, name, price, details = r[0], r[1], r[2], r[3]
                except Exception:
                    pid = r["id"] if isinstance(r, dict) else getattr(r, "id", 0)
                    name = r["name"] if isinstance(r, dict) else getattr(r, "name", "")
                    price = r["price"] if isinstance(r, dict) else getattr(r, "price", "")
                    details = r["details"] if isinstance(r, dict) else getattr(r, "details", "")
                products.append({"id": pid, "name": name, "price": price, "details": details})
            return products
    except Exception:
        logger.exception("Failed to load products from Turso")
        return []

def _add_product_to_turso(name, price, details):
    client = _get_turso_client()
    if client is None:
        logger.error("Turso client not available for _add_product")
        return None
    try:
        with _turso_lock:
            client.execute("INSERT INTO products (name, price, details) VALUES (?, ?, ?)", (name, price, details))
            client.commit()
            # Get last inserted id
            rs = client.execute("SELECT last_insert_rowid()")
            row = rs.fetchone() if hasattr(rs, 'fetchone') else None
            new_id = row[0] if row else None
            return new_id
    except Exception:
        logger.exception("Failed to insert product into Turso")
        return None

def _format_price_display(price_str):
    try:
        clean = price_str.replace(",", "").strip()
        num = float(clean)
        if num.is_integer():
            return f"₹{int(num):,}"
        else:
            return f"₹{num:,.2f}"
    except Exception:
        return f"₹{price_str}"

def _format_products_list(products):
    if not products:
        return "📦 PRODUCTS\n\nNo products added yet."
    lines = ["📦 PRODUCTS\n"]
    for idx, p in enumerate(products, 1):
        name = p.get("name", "Unknown")
        price = p.get("price", "")
        price_display = _format_price_display(str(price)) if price else ""
        lines.append(f"{idx}. {name}")
        if price_display:
            lines.append(f"   💰 {price_display}")
        lines.append("")
    return "\n".join(lines).strip()

# --- Existing Telegram handlers ---
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

    user_id = update.effective_user.id if update.effective_user else None
    if user_id == ADMIN_USER_ID:
        flow = context.user_data.get("admin_flow")
        if flow == "add_product":
            step = context.user_data.get("admin_step")
            msg_text = update.message.text.strip()

            if step == "awaiting_name":
                if not msg_text:
                    await update.message.reply_text("Product name ఖాళీగా ఉండకూడదు. మళ్ళీ పంపండి:")
                    return
                context.user_data["temp_product"] = {"name": msg_text}
                context.user_data["admin_step"] = "awaiting_price"
                await update.message.reply_text("💰 Price పంపండి:")
                return

            elif step == "awaiting_price":
                if not msg_text:
                    await update.message.reply_text("Price ఖాళీగా ఉండకూడదు. మళ్ళీ పంపండి:")
                    return
                context.user_data["temp_product"]["price"] = msg_text
                context.user_data["admin_step"] = "awaiting_details"
                await update.message.reply_text("📝 Product details పంపండి:")
                return

            elif step == "awaiting_details":
                temp = context.user_data.get("temp_product", {})
                temp["details"] = msg_text

                # Save to Turso
                new_id = _add_product_to_turso(temp.get("name", ""), temp.get("price", ""), temp.get("details", ""))
                if new_id is None:
                    await update.message.reply_text("❌ Database connection failed. Product save కాలేదు. Turso credentials check చేసి మళ్ళీ try చేయండి.")
                    return

                context.user_data.pop("admin_flow", None)
                context.user_data.pop("admin_step", None)
                context.user_data.pop("temp_product", None)

                price_display = _format_price_display(temp.get("price", ""))
                await update.message.reply_text(f"✅ Product saved successfully!\n\nProduct:\n{temp.get('name')}\n\nPrice:\n{price_display}")
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

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id if query.from_user else None
    if user_id != ADMIN_USER_ID:
        await query.answer("❌ Not authorized", show_alert=True)
        return
    await query.answer()
    data = query.data
    if data == "admin_add_product":
        context.user_data["admin_flow"] = "add_product"
        context.user_data["admin_step"] = "awaiting_name"
        context.user_data.pop("temp_product", None)
        await query.edit_message_text("➕ ADD PRODUCT\n\nProduct name పంపండి:")
        return
    if data == "admin_view_products":
        products = _load_products()
        text = _format_products_list(products)
        await query.edit_message_text(text)
        return
    placeholders = {
        "admin_edit_product": "✏️ Edit Product\n\nProduct management feature త్వరలో అందుబాటులో ఉంటుంది.",
        "admin_change_price": "💰 Change Price\n\nProduct management feature త్వరలో అందుబాటులో ఉంటుంది.",
        "admin_edit_details": "📝 Edit Details\n\nProduct management feature త్వరలో అందుబాటులో ఉంటుంది.",
        "admin_delete_product": "🗑️ Delete Product\n\nProduct management feature త్వరలో అందుబాటులో ఉంటుంది.",
    }
    text = placeholders.get(data, "Product management feature త్వరలో అందుబాటులో ఉంటుంది.")
    await query.edit_message_text(text)

telegram_app = Application.builder().token(BOT_TOKEN).build()
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("admin", admin_command))
telegram_app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
telegram_app.add_handler(CallbackQueryHandler(button_handler))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
telegram_app.add_error_handler(telegram_error_handler)

# --- Persistent Loop & Turso Init (Architecture unchanged) ---
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

# Initialize Turso DB before Telegram app start (safe, no secrets logged)
_init_turso_db()

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
            await telegram_app.bot.set_webhook(url=webhook_url, drop_pending_updates=False)
            logger.info("Webhook set to: %s", webhook_url)
        future = asyncio.run_coroutine_threadsafe(_set(), telegram_loop)
        future.result(timeout=30)
    except Exception:
        logger.exception("Failed to set webhook, but Flask will continue running")

_register_webhook_if_needed()

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", "10000"))
    logger.info("Starting Flask Main Web Server on 0.0.0.0:%s", PORT)
    try:
        app.run(host="0.0.0.0", port=PORT, use_reloader=False)
    finally:
        try:
            future = asyncio.run_coroutine_threadsafe(_shutdown_telegram(), telegram_loop)
            future.result(timeout=10)
        except Exception:
            pass
