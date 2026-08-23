import os
import asyncio
import threading
import logging
from datetime import datetime

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

# --- TURSO STORAGE via HTTP API ---
class _TursoResult:
    def __init__(self, rows):
        self.rows = rows

class _TursoHttpClient:
    def __init__(self, db_url, auth_token):
        import httpx
        self._httpx = httpx
        https_url = db_url.strip()
        if https_url.startswith("libsql://"):
            https_url = "https://" + https_url[len("libsql://"):]
        https_url = https_url.rstrip("/")
        self._endpoint = f"{https_url}/v2/pipeline"
        self._auth_token = auth_token

    def _to_hrana_arg(self, v):
        if v is None:
            return {"type": "null"}
        if isinstance(v, bool):
            return {"type": "integer", "value": "1" if v else "0"}
        if isinstance(v, int):
            return {"type": "integer", "value": str(v)}
        if isinstance(v, float):
            return {"type": "float", "value": v}
        return {"type": "text", "value": str(v)}

    def _from_hrana_value(self, hv):
        if not isinstance(hv, dict):
            return hv
        t = hv.get("type")
        val = hv.get("value")
        if t == "null":
            return None
        if t == "integer":
            try:
                return int(val)
            except Exception:
                return val
        if t == "float":
            try:
                return float(val)
            except Exception:
                return val
        if t == "text":
            return val
        if t == "blob":
            return val
        return val

    def execute(self, sql, params=None):
        args = [self._to_hrana_arg(p) for p in (params or [])]
        payload = {
            "requests": [
                {"type": "execute", "stmt": {"sql": sql, "args": args}},
                {"type": "close"}
            ]
        }
        headers = {
            "Authorization": f"Bearer {self._auth_token}",
            "Content-Type": "application/json"
        }
        with self._httpx.Client(timeout=20.0) as client:
            resp = client.post(self._endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                return _TursoResult([])
            first = results[0]
            if first.get("type") == "error":
                err = first.get("error", {})
                raise RuntimeError(f"Turso error: {err}")
            res_obj = first.get("response", {}).get("result", {})
            raw_rows = res_obj.get("rows", [])
            py_rows = []
            for raw_row in raw_rows:
                py_row = tuple(self._from_hrana_value(v) for v in raw_row)
                py_rows.append(py_row)
            return _TursoResult(py_rows)

_turso_client = None
_turso_lock = threading.Lock()
_turso_initialized = False

def _get_turso_client():
    global _turso_client
    url = os.getenv("TURSO_DATABASE_URL")
    token = os.getenv("TURSO_AUTH_TOKEN")
    if not url or not token:
        return None
    with _turso_lock:
        if _turso_client is not None:
            return _turso_client
        try:
            _turso_client = _TursoHttpClient(db_url=url, auth_token=token)
            logger.info("Turso HTTP client created")
            return _turso_client
        except Exception:
            logger.exception("Failed to create Turso HTTP client")
            return None

def _execute_turso(sql, params=None):
    client = _get_turso_client()
    if client is None:
        return None
    try:
        return client.execute(sql, params)
    except Exception:
        logger.exception("Turso execute failed")
        return None

def _init_turso_db():
    global _turso_initialized
    if _turso_initialized:
        return True
    client = _get_turso_client()
    if client is None:
        logger.error("Turso credentials missing - set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN")
        return False
    try:
        _execute_turso("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price TEXT NOT NULL,
                details TEXT
            )
        """)
        _execute_turso("""
            CREATE TABLE IF NOT EXISTS business_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                business_name TEXT,
                address TEXT,
                phone TEXT,
                whatsapp TEXT,
                email TEXT,
                description TEXT
            )
        """)
        _execute_turso("""
            INSERT OR IGNORE INTO business_settings (id, business_name, address, phone, whatsapp, email, description)
            VALUES (1, '', '', '', '', '', '')
        """)
        # --- NEW: Enquiries table ---
        _execute_turso("""
            CREATE TABLE IF NOT EXISTS enquiries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER,
                product_name TEXT NOT NULL,
                product_price TEXT,
                customer_name TEXT,
                telegram_user_id INTEGER,
                username TEXT,
                status TEXT DEFAULT 'new',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _turso_initialized = True
        logger.info("Turso products, business_settings and enquiries tables ensured")
        _migrate_json_if_needed()
        return True
    except Exception:
        logger.exception("Failed to init Turso tables")
        return False

def _migrate_json_if_needed():
    try:
        json_path = os.path.join(os.path.dirname(__file__), "data", "products.json")
        if not os.path.exists(json_path):
            return
        import json as _json
        with open(json_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        if not isinstance(data, list) or not data:
            return
        result = _execute_turso("SELECT COUNT(*) FROM products")
        count = result.rows[0][0] if result and result.rows else 0
        if count > 0:
            logger.info("Turso already has products, skipping JSON migration")
            return
        for p in data:
            name = p.get("name", "").strip()
            price = str(p.get("price", "")).strip()
            details = p.get("details", "").strip()
            if not name:
                continue
            _execute_turso("INSERT INTO products (name, price, details) VALUES (?, ?, ?)", (name, price, details))
        logger.info("Migrated %s products from JSON to Turso", len(data))
    except Exception:
        logger.exception("JSON migration failed")

def _load_products():
    result = _execute_turso("SELECT id, name, price, details FROM products ORDER BY id ASC")
    if result is None:
        logger.error("Turso client not available for load")
        return []
    try:
        products = []
        for r in result.rows:
            pid = r[0] if len(r) > 0 else None
            name = r[1] if len(r) > 1 else ""
            price = r[2] if len(r) > 2 else ""
            details = r[3] if len(r) > 3 else ""
            products.append({"id": pid, "name": name, "price": price, "details": details})
        return products
    except Exception:
        logger.exception("Failed to parse products")
        return []

def _get_product_by_id(product_id):
    result = _execute_turso("SELECT id, name, price, details FROM products WHERE id = ?", (product_id,))
    if result is None or not result.rows:
        return None
    try:
        r = result.rows[0]
        return {"id": r[0], "name": r[1], "price": r[2], "details": r[3] if len(r) > 3 else ""}
    except Exception:
        return None

def _search_products(query):
    result = _execute_turso("SELECT id, name, price, details FROM products WHERE name LIKE ? OR details LIKE ? ORDER BY id ASC", (f"%{query}%", f"%{query}%"))
    if result is None:
        return []
    products = []
    try:
        for r in result.rows:
            products.append({"id": r[0], "name": r[1], "price": r[2], "details": r[3] if len(r) > 3 else ""})
    except Exception:
        pass
    return products

def _add_product_to_turso(name, price, details):
    result = _execute_turso("INSERT INTO products (name, price, details) VALUES (?, ?, ?)", (name, price, details))
    if result is None:
        return None
    try:
        res2 = _execute_turso("SELECT last_insert_rowid()")
        if res2 and res2.rows:
            return res2.rows[0][0]
        return 1
    except Exception:
        logger.exception("Failed to get last insert id")
        return 1

def _update_product_in_turso(product_id, name, price, details):
    result = _execute_turso("UPDATE products SET name = ?, price = ?, details = ? WHERE id = ?", (name, price, details, product_id))
    return result is not None

def _update_product_price(product_id, price):
    result = _execute_turso("UPDATE products SET price = ? WHERE id = ?", (price, product_id))
    return result is not None

def _update_product_details(product_id, details):
    result = _execute_turso("UPDATE products SET details = ? WHERE id = ?", (details, product_id))
    return result is not None

def _delete_product(product_id):
    result = _execute_turso("DELETE FROM products WHERE id = ?", (product_id,))
    return result is not None

# Business Settings
def _get_business_settings():
    result = _execute_turso("SELECT business_name, address, phone, whatsapp, email, description FROM business_settings WHERE id = 1")
    if result is None or not result.rows:
        return {"business_name": "", "address": "", "phone": "", "whatsapp": "", "email": "", "description": ""}
    try:
        r = result.rows[0]
        return {
            "business_name": r[0] or "",
            "address": r[1] or "",
            "phone": r[2] or "",
            "whatsapp": r[3] or "",
            "email": r[4] or "",
            "description": r[5] or ""
        }
    except Exception:
        logger.exception("Failed to parse business settings")
        return {"business_name": "", "address": "", "phone": "", "whatsapp": "", "email": "", "description": ""}

def _update_business_field(field, value):
    allowed = {"business_name", "address", "phone", "whatsapp", "email", "description"}
    if field not in allowed:
        return False
    sql = f"UPDATE business_settings SET {field} = ? WHERE id = 1"
    result = _execute_turso(sql, (value,))
    return result is not None

# Enquiries
def _create_enquiry(product_id, product_name, product_price, customer_name, telegram_user_id, username):
    result = _execute_turso(
        "INSERT INTO enquiries (product_id, product_name, product_price, customer_name, telegram_user_id, username, status) VALUES (?, ?, ?, ?, ?, ?, 'new')",
        (product_id, product_name, product_price, customer_name, telegram_user_id, username or "")
    )
    if result is None:
        return None
    try:
        res2 = _execute_turso("SELECT last_insert_rowid()")
        if res2 and res2.rows:
            return res2.rows[0][0]
        return 1
    except Exception:
        logger.exception("Failed to get enquiry id")
        return 1

def _load_enquiries():
    result = _execute_turso("SELECT id, product_id, product_name, product_price, customer_name, telegram_user_id, username, status, created_at FROM enquiries ORDER BY id DESC")
    if result is None:
        return []
    enqs = []
    try:
        for r in result.rows:
            enqs.append({
                "id": r[0],
                "product_id": r[1],
                "product_name": r[2],
                "product_price": r[3],
                "customer_name": r[4],
                "telegram_user_id": r[5],
                "username": r[6],
                "status": r[7],
                "created_at": r[8]
            })
    except Exception:
        logger.exception("Failed to parse enquiries")
    return enqs

def _get_enquiry_by_id(enq_id):
    result = _execute_turso("SELECT id, product_id, product_name, product_price, customer_name, telegram_user_id, username, status, created_at FROM enquiries WHERE id = ?", (enq_id,))
    if result is None or not result.rows:
        return None
    try:
        r = result.rows[0]
        return {
            "id": r[0],
            "product_id": r[1],
            "product_name": r[2],
            "product_price": r[3],
            "customer_name": r[4],
            "telegram_user_id": r[5],
            "username": r[6],
            "status": r[7],
            "created_at": r[8]
        }
    except Exception:
        return None

def _update_enquiry_status(enq_id, status):
    result = _execute_turso("UPDATE enquiries SET status = ? WHERE id = ?", (status, enq_id))
    return result is not None

def _delete_enquiry(enq_id):
    result = _execute_turso("DELETE FROM enquiries WHERE id = ?", (enq_id,))
    return result is not None

def _format_business_settings_admin(settings):
    lines = ["⚙️ BUSINESS SETTINGS\n"]
    lines.append(f"🏢 Business Name: {settings.get('business_name') or 'Not set'}")
    lines.append(f"📍 Address: {settings.get('address') or 'Not set'}")
    lines.append(f"📞 Phone: {settings.get('phone') or 'Not set'}")
    lines.append(f"📱 WhatsApp: {settings.get('whatsapp') or 'Not set'}")
    lines.append(f"📧 Email: {settings.get('email') or 'Not set'}")
    lines.append(f"📝 Description: {settings.get('description') or 'Not set'}")
    return "\n".join(lines)

def _format_business_settings_customer(settings):
    if not any([settings.get("business_name"), settings.get("address"), settings.get("phone"), settings.get("whatsapp"), settings.get("email"), settings.get("description")]):
        return "🏢 BUSINESS INFORMATION\n\nBusiness information not yet configured.\nPlease contact admin."
    lines = ["🏢 BUSINESS INFORMATION\n"]
    if settings.get("business_name"):
        lines.append(f"🏢 {settings.get('business_name')}")
    if settings.get("address"):
        lines.append(f"📍 {settings.get('address')}")
    if settings.get("phone"):
        lines.append(f"📞 Phone: {settings.get('phone')}")
    if settings.get("whatsapp"):
        lines.append(f"📱 WhatsApp: {settings.get('whatsapp')}")
    if settings.get("email"):
        lines.append(f"📧 Email: {settings.get('email')}")
    if settings.get("description"):
        lines.append(f"\n📝 {settings.get('description')}")
    return "\n".join(lines)

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
    for p in products:
        actual_id = p.get("id")
        name = p.get("name", "Unknown")
        price = p.get("price", "")
        details = p.get("details", "")
        price_display = _format_price_display(str(price)) if price else ""
        lines.append(f"ID: {actual_id}")
        lines.append(f"📦 {name}")
        if price_display:
            lines.append(f"💰 {price_display}")
        if details:
            lines.append(f"📝 {details}")
        lines.append("")
    return "\n".join(lines).strip()

def _format_customer_product(product):
    name = product.get("name", "Unknown") or "Unknown"
    price = product.get("price", "") or ""
    details = product.get("details", "") or ""
    price_display = _format_price_display(str(price)) if price and str(price).strip() else "Not available"
    lines = [f"📦 Product Name: {name}", f"💰 Price: {price_display}"]
    if details.strip():
        lines.append(f"📝 Details: {details.strip()}")
    return "\n".join(lines)

def _get_product_selection_keyboard(products, prefix, include_back=True):
    keyboard = []
    for p in products:
        pid = p.get("id")
        name = p.get("name", "Unknown")[:30]
        keyboard.append([InlineKeyboardButton(f"{pid}. {name}", callback_data=f"{prefix}{pid}")])
    if include_back:
        keyboard.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_back")])
    return InlineKeyboardMarkup(keyboard)

def _get_customer_product_list_keyboard(products):
    keyboard = []
    for p in products:
        pid = p.get("id")
        name = p.get("name", "Unknown")[:25]
        keyboard.append([InlineKeyboardButton(f"📦 {name} - 📩 Enquire", callback_data=f"cust_enq_{pid}")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")])
    return InlineKeyboardMarkup(keyboard)

def _get_business_settings_edit_keyboard():
    keyboard = [
        [InlineKeyboardButton("🏢 Edit Business Name", callback_data="admin_biz_edit_name")],
        [InlineKeyboardButton("📍 Edit Address", callback_data="admin_biz_edit_address")],
        [InlineKeyboardButton("📞 Edit Phone", callback_data="admin_biz_edit_phone")],
        [InlineKeyboardButton("📱 Edit WhatsApp", callback_data="admin_biz_edit_whatsapp")],
        [InlineKeyboardButton("📧 Edit Email", callback_data="admin_biz_edit_email")],
        [InlineKeyboardButton("📝 Edit Description", callback_data="admin_biz_edit_desc")],
        [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_back")],
    ]
    return InlineKeyboardMarkup(keyboard)

def _get_customer_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("📦 Products & Prices", callback_data="price")],
        [InlineKeyboardButton("🏢 Business Info", callback_data="business_info")],
        [InlineKeyboardButton("🛠 Services", callback_data="services")],
        [InlineKeyboardButton("🤖 About This Bot", callback_data="about")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Vel Business Helper!\n\nI help you browse products, check prices and details, send enquiries, and get business information.\n\nPlease choose an option below 👇",
        reply_markup=_get_customer_main_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    # --- SAVE BUSINESS CONTACT (Customer only) ---
    if query.data == "save_business_contact":
        try:
            settings = _get_business_settings()
            if settings is None:
                await query.message.reply_text(
                    "❌ Business phone number is not available.\n\nPlease contact the business directly.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]])
                )
                return

            raw_phone = (settings.get("phone") or "").strip()
            business_name = (settings.get("business_name") or "").strip()

            if not business_name:
                business_name = "Business"

            # Normalize phone only for sending (do not modify DB)
            cleaned_phone = "".join(ch for ch in raw_phone if ch.isdigit() or ch == "+")
            # Remove spaces, dashes, brackets already handled; keep + and digits
            if cleaned_phone.startswith("+"):
                # keep plus
                digits = "".join(ch for ch in cleaned_phone[1:] if ch.isdigit())
                cleaned_phone = "+" + digits
            else:
                cleaned_phone = "".join(ch for ch in cleaned_phone if ch.isdigit())

            if not cleaned_phone or len(cleaned_phone) < 7:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="❌ Business phone number is not available.\n\nPlease contact the business directly.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]])
                )
                return

            # First name is required by Telegram API, truncate to 32 chars
            first_name = business_name[:32] if business_name else "Business"

            # Send contact card - PTB 20.7 compatible
            await context.bot.send_contact(
                chat_id=query.message.chat_id,
                phone_number=cleaned_phone,
                first_name=first_name
            )

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    "📞 Business Contact\n\n"
                    "The business contact has been sent.\n\n"
                    "Please tap \"Add Contact\" in Telegram to save it to your contacts."
                ),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]])
            )
        except Exception as e:
            logger.exception("Failed to send business contact card: %s", e)
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="❌ Could not send business contact.\n\nPlease try again later.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]])
                )
            except Exception:
                pass
        return


    # Customer Product Enquiry - Confirmation Screen
    if query.data.startswith("cust_enq_"):
        # cust_enq_{id} -> confirmation
        # cust_enq_send_{id} -> send
        # cust_enq_cancel_{id} -> cancel
        # cust_enq_detail_{id} -> detail (not used, but handle)
        data = query.data

        # Send Enquiry
        if data.startswith("cust_enq_send_"):
            try:
                product_id = int(data.replace("cust_enq_send_", ""))
            except Exception:
                await query.edit_message_text("❌ Invalid product ID.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]))
                return
            product = _get_product_by_id(product_id)
            if not product:
                await query.edit_message_text("❌ Product not found. It may have been deleted.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]))
                return

            customer_name = (user.full_name if user else "Customer") or "Customer"
            telegram_user_id = user.id if user else 0
            username = user.username if user and user.username else "Not available"

            # Create enquiry in Turso
            enq_id = _create_enquiry(product_id, product.get("name"), str(product.get("price")), customer_name, telegram_user_id, username)
            if enq_id is None:
                logger.error("Failed to create enquiry for product %s", product_id)
                await query.edit_message_text(
                    "❌ Enquiry could not be submitted.\nPlease try again later.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]])
                )
                return

            # Notify Admin
            try:
                price_display = _format_price_display(str(product.get("price") or ""))
                admin_text = (
                    f"🔔 NEW CUSTOMER ENQUIRY\n\n"
                    f"📦 Product: {product.get('name')}\n"
                    f"🆔 Product ID: {product_id}\n"
                    f"💰 Price: {price_display}\n\n"
                    f"👤 Customer: {customer_name}\n"
                    f"🆔 Telegram ID: {telegram_user_id}\n"
                    f"🔗 Username: {username}\n\n"
                    f"📅 Enquiry received."
                )
                await telegram_app.bot.send_message(chat_id=ADMIN_USER_ID, text=admin_text)
            except Exception:
                logger.exception("Failed to send admin notification for enquiry %s", enq_id)

            # Customer confirmation with Save Business Contact
            customer_confirm = (
                f"✅ Enquiry Sent Successfully!\n\n"
                f"📦 Product: {product.get('name')}\n\n"
                f"The business admin will contact you soon."
            )
            keyboard = [
                [InlineKeyboardButton("📞 Save Business Contact", callback_data="save_business_contact")],
                [InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]
            ]
            await query.edit_message_text(customer_confirm, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        # Cancel Enquiry
        if data.startswith("cust_enq_cancel_"):
            try:
                product_id = int(data.replace("cust_enq_cancel_", ""))
            except Exception:
                await query.edit_message_text("Enquiry cancelled.", reply_markup=_get_customer_main_keyboard())
                return
            product = _get_product_by_id(product_id)
            if not product:
                await query.edit_message_text("Enquiry cancelled.", reply_markup=_get_customer_main_keyboard())
                return
            # Return to previous product screen
            text = _format_customer_product(product)
            keyboard = [
                [InlineKeyboardButton("📩 Enquire Now", callback_data=f"cust_enq_{product_id}")],
                [InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        # Initial Enquiry Request -> Show Confirmation
        if data.startswith("cust_enq_"):
            # This is cust_enq_{id}
            try:
                # Remove prefix, but avoid parsing send/cancel which already handled
                pid_str = data.replace("cust_enq_", "")
                # If pid_str contains non-digit due to send/cancel, already handled
                product_id = int(pid_str)
            except Exception:
                await query.edit_message_text("❌ Invalid product.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]))
                return
            product = _get_product_by_id(product_id)
            if not product:
                await query.edit_message_text("❌ Product not found. It may have been deleted.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]))
                return

            price_display = _format_price_display(str(product.get("price") or ""))
            confirm_text = (
                f"📩 PRODUCT ENQUIRY\n\n"
                f"📦 Product: {product.get('name')}\n"
                f"💰 Price: {price_display}\n\n"
                f"Would you like to send an enquiry to the business?"
            )
            keyboard = [
                [InlineKeyboardButton("✅ Send Enquiry", callback_data=f"cust_enq_send_{product_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"cust_enq_cancel_{product_id}")]
            ]
            await query.edit_message_text(confirm_text, reply_markup=InlineKeyboardMarkup(keyboard))
            return

    if query.data == "services":
        text = (
            "🛠 SERVICES\n\n"
            "Vel Business Helper helps businesses provide information and support to their customers through Telegram.\n\n"
            "✨ AVAILABLE SERVICES\n\n"
            "• 📦 Product Catalog\n"
            "• 💰 Product Prices\n"
            "• 📝 Product Details\n"
            "• 📩 Customer Enquiries\n"
            "• 🏢 Business Information\n"
            "• 📞 Save Business Contact\n"
            "• ⚡ Automated Customer Support\n\n"
            "Customers can browse products, check prices and details, send product enquiries, view business information and save the business contact."
        )
        keyboard = [[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    elif query.data == "price":
        products = _load_products()
        if not products:
            text = "📦 PRODUCTS & PRICES\n\nNo products available yet.\nPlease check back later."
            keyboard = [[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        lines = ["📦 PRODUCTS\n"]
        for idx, p in enumerate(products, start=1):
            name = p.get("name", "Unknown") or "Unknown"
            price = p.get("price", "") or ""
            details = p.get("details", "") or ""
            price_display = _format_price_display(str(price)) if price and str(price).strip() else "Not available"
            lines.append(f"{idx}.")
            lines.append(f"📦 Product Name: {name}")
            lines.append(f"💰 Price: {price_display}")
            if details.strip():
                lines.append(f"📝 Details: {details.strip()}")
            lines.append("")
        text = "\n".join(lines).strip()
        if len(text) > 4000:
            text = text[:4000] + "\n\n... and more products available. Tap a product below to enquire."
        keyboard = _get_customer_product_list_keyboard(products)
        await query.edit_message_text(text, reply_markup=keyboard)
        return
    elif query.data == "business_info":
        settings = _get_business_settings()
        if settings is None:
            text = "❌ Database connection failed. Please try again later."
            keyboard = [[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        text = _format_business_settings_customer(settings)
        keyboard = [[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    elif query.data == "about":
        text = (
            "🤖 VEL BUSINESS HELPER\n\n"
            "A professional Telegram Business Helper Bot designed to help businesses manage products and provide information to customers.\n\n"
            "✨ FEATURES\n\n"
            "• 📦 Add & manage products\n"
            "• 💰 Manage product prices\n"
            "• 📝 Add & edit product details\n"
            "• 🔎 Customer product information\n"
            "• 🗑️ Delete products\n"
            "• 🗄️ Cloud database storage\n"
            "• 🔐 Protected Admin Panel\n"
            "• 📱 Customer-friendly menu\n"
            "• ⚡ Automated responses\n"
            "• 🔄 Business information management\n\n"
            "More features can be customized according to your business needs."
        )
        keyboard = [[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    elif query.data == "help":
        text = (
            "❓ HELP\n\n"
            "How to use Vel Business Helper:\n\n"
            "1️⃣ Open Products & Prices to view available products.\n\n"
            "2️⃣ Products & Prices shows product name, price and details.\n\n"
            "3️⃣ Choose a product and use 📩 Enquire Now to send an enquiry to the business.\n\n"
            "4️⃣ Use 🏢 Business Info to view the business information.\n\n"
            "5️⃣ After sending an enquiry, use 📞 Save Business Contact to receive the business contact card.\n\n"
            "6️⃣ Use ⬅️ Back to Start to return to the main customer menu.\n\n"
            "For any assistance, choose an option from the main menu or send a message to the bot."
        )
        keyboard = [[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    elif query.data == "back_to_start":
        text = "👋 Welcome to Vel Business Helper!\n\nI help you browse products, check prices and details, send enquiries, and get business information.\n\nPlease choose an option below 👇"
        await query.edit_message_text(text, reply_markup=_get_customer_main_keyboard())
        return
    else:
        text = "❓ HELP\n\nPlease choose an option from the main menu or use Products & Prices to browse available products.\n\n⬅️ Back to Start"
        keyboard = [[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

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
                    await update.message.reply_text("Product name cannot be empty. Please try again:")
                    return
                context.user_data["temp_product"] = {"name": msg_text}
                context.user_data["admin_step"] = "awaiting_price"
                await update.message.reply_text("💰 Please enter the price:")
                return
            elif step == "awaiting_price":
                if not msg_text:
                    await update.message.reply_text("Price cannot be empty. Please try again:")
                    return
                context.user_data["temp_product"]["price"] = msg_text
                context.user_data["admin_step"] = "awaiting_details"
                await update.message.reply_text("📝 Please enter the product details:")
                return
            elif step == "awaiting_details":
                temp = context.user_data.get("temp_product", {})
                temp["details"] = msg_text
                new_id = _add_product_to_turso(temp.get("name", ""), temp.get("price", ""), temp.get("details", ""))
                if new_id is None:
                    await update.message.reply_text("❌ Database connection failed.\nPlease check the database configuration and try again.")
                    return
                context.user_data.pop("admin_flow", None)
                context.user_data.pop("admin_step", None)
                context.user_data.pop("temp_product", None)
                price_display = _format_price_display(temp.get("price", ""))
                await update.message.reply_text(f"✅ Product saved successfully!\n\n📦 Name: {temp.get('name')}\n💰 Price: {price_display}\n📝 Details: {temp.get('details')}")
                return
        elif flow == "edit_product":
            step = context.user_data.get("admin_step")
            msg_text = update.message.text.strip()
            if step == "awaiting_edit_name":
                if not msg_text:
                    await update.message.reply_text("Product name cannot be empty. Please try again:")
                    return
                context.user_data["edit_temp"] = {"name": msg_text}
                context.user_data["admin_step"] = "awaiting_edit_price"
                await update.message.reply_text("💰 Please enter the new price:")
                return
            elif step == "awaiting_edit_price":
                if not msg_text:
                    await update.message.reply_text("Price cannot be empty. Please try again:")
                    return
                context.user_data["edit_temp"]["price"] = msg_text
                context.user_data["admin_step"] = "awaiting_edit_details"
                await update.message.reply_text("📝 Please enter the new details:")
                return
            elif step == "awaiting_edit_details":
                edit_id = context.user_data.get("edit_product_id")
                edit_temp = context.user_data.get("edit_temp", {})
                edit_temp["details"] = msg_text
                if not edit_id:
                    await update.message.reply_text("❌ Product ID not found. Please try again from /admin.")
                    return
                success = _update_product_in_turso(edit_id, edit_temp.get("name", ""), edit_temp.get("price", ""), edit_temp.get("details", ""))
                if not success:
                    await update.message.reply_text("❌ Database update failed.")
                    return
                context.user_data.pop("admin_flow", None)
                context.user_data.pop("admin_step", None)
                context.user_data.pop("edit_product_id", None)
                context.user_data.pop("edit_temp", None)
                price_display = _format_price_display(edit_temp.get("price", ""))
                await update.message.reply_text(f"✅ Product updated successfully!\n\nID: {edit_id}\n📦 Name: {edit_temp.get('name')}\n💰 Price: {price_display}\n📝 Details: {edit_temp.get('details')}")
                return
        elif flow == "change_price":
            step = context.user_data.get("admin_step")
            msg_text = update.message.text.strip()
            if step == "awaiting_new_price":
                edit_id = context.user_data.get("edit_product_id")
                if not edit_id:
                    await update.message.reply_text("❌ Product ID not found.")
                    return
                if not msg_text:
                    await update.message.reply_text("Price cannot be empty. Please try again:")
                    return
                success = _update_product_price(edit_id, msg_text)
                if not success:
                    await update.message.reply_text("❌ Price update failed.")
                    return
                product = _get_product_by_id(edit_id)
                context.user_data.pop("admin_flow", None)
                context.user_data.pop("admin_step", None)
                context.user_data.pop("edit_product_id", None)
                price_display = _format_price_display(msg_text)
                name = product.get("name") if product else "Product"
                await update.message.reply_text(f"✅ Price updated successfully!\n\nID: {edit_id}\n📦 {name}\n💰 New Price: {price_display}")
                return
        elif flow == "edit_details":
            step = context.user_data.get("admin_step")
            msg_text = update.message.text.strip()
            if step == "awaiting_new_details":
                edit_id = context.user_data.get("edit_product_id")
                if not edit_id:
                    await update.message.reply_text("❌ Product ID not found.")
                    return
                success = _update_product_details(edit_id, msg_text)
                if not success:
                    await update.message.reply_text("❌ Details update failed.")
                    return
                product = _get_product_by_id(edit_id)
                context.user_data.pop("admin_flow", None)
                context.user_data.pop("admin_step", None)
                context.user_data.pop("edit_product_id", None)
                name = product.get("name") if product else "Product"
                await update.message.reply_text(f"✅ Details updated successfully!\n\nID: {edit_id}\n📦 {name}\n📝 New Details: {msg_text}")
                return
        elif flow == "business_settings":
            step = context.user_data.get("admin_step")
            msg_text = update.message.text.strip()
            field_map = {
                "awaiting_biz_name": "business_name",
                "awaiting_biz_address": "address",
                "awaiting_biz_phone": "phone",
                "awaiting_biz_whatsapp": "whatsapp",
                "awaiting_biz_email": "email",
                "awaiting_biz_desc": "description"
            }
            field = field_map.get(step)
            if field:
                if not msg_text and field in ["business_name"]:
                    await update.message.reply_text("Business Name cannot be empty. Please try again:")
                    return
                success = _update_business_field(field, msg_text)
                if not success:
                    await update.message.reply_text("❌ Database connection failed. Please try again.")
                    return
                context.user_data.pop("admin_flow", None)
                context.user_data.pop("admin_step", None)
                context.user_data.pop("biz_field", None)
                await update.message.reply_text(f"✅ Business settings updated successfully!\n\nUpdated {field.replace('_',' ').title()}: {msg_text}")
                return

    # Customer search - show products with Enquire button
    text = update.message.text.strip()
    text_lower = text.lower()

    if text_lower in ["hi", "hello", "hey"]:
        await update.message.reply_text("👋 Hello!\n\nWelcome to Vel Business Helper!\n\nPlease press /start to open the main menu.", reply_markup=_get_customer_main_keyboard())
        return

    # Try product search first for customer
    if len(text) >= 2:
        matched = _search_products(text)
        if matched:
            # Show up to 5 matching products with Enquire buttons
            for prod in matched[:3]:
                prod_text = _format_customer_product(prod)
                keyboard = [
                    [InlineKeyboardButton("📩 Enquire Now", callback_data=f"cust_enq_{prod.get('id')}")],
                    [InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]
                ]
                await update.message.reply_text(prod_text, reply_markup=InlineKeyboardMarkup(keyboard))
            return

    if "contact" in text_lower or "business" in text_lower:
        settings = _get_business_settings()
        if settings and any(settings.values()):
            await update.message.reply_text(_format_business_settings_customer(settings), reply_markup=_get_customer_main_keyboard())
        else:
            await update.message.reply_text("🏢 BUSINESS INFORMATION\n\nBusiness information not yet configured.\nPlease contact admin.", reply_markup=_get_customer_main_keyboard())
        return
    elif "price" in text_lower:
        products = _load_products()
        if products:
            text_msg = "💰 PRODUCTS - Tap Enquire to contact business\n\nSelect a product to enquire:"
            keyboard = _get_customer_product_list_keyboard(products)
            await update.message.reply_text(text_msg, reply_markup=keyboard)
        else:
            await update.message.reply_text("💰 To check prices, please send a product name.\n\nExamples:\nCRI 1 HP\nCRI 2 HP\nOpenwell pump", reply_markup=_get_customer_main_keyboard())
        return
    elif "pump" in text_lower:
        matched = _search_products(text)
        if matched:
            for prod in matched[:3]:
                prod_text = _format_customer_product(prod)
                keyboard = [
                    [InlineKeyboardButton("📩 Enquire Now", callback_data=f"cust_enq_{prod.get('id')}")],
                    [InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]
                ]
                await update.message.reply_text(prod_text, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        await update.message.reply_text("🔧 PRODUCT INFORMATION\n\nPlease send a product or model name to get details.\n\nExamples:\n1 HP pump\n2 HP pump\nOpenwell pump\nSubmersible pump", reply_markup=_get_customer_main_keyboard())
        return
    else:
        await update.message.reply_text("🙂 Message received.\n\nPlease send /start to open the main menu,\n\nor send a product name to search.", reply_markup=_get_customer_main_keyboard())

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
        [InlineKeyboardButton("⚙️ Business Settings", callback_data="admin_business_settings")],
        [InlineKeyboardButton("📩 Customer Enquiries", callback_data="admin_enquiries")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else None
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ You are not authorized to access the Admin Panel.")
        return
    await update.message.reply_text("🔐 ADMIN PANEL\n\nWelcome Admin! Please select an option below 👇", reply_markup=_get_admin_keyboard())

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
        context.user_data.pop("edit_product_id", None)
        context.user_data.pop("edit_temp", None)
        await query.edit_message_text("➕ ADD PRODUCT\n\nPlease enter the product name:")
        return

    if data == "admin_view_products":
        products = _load_products()
        text = _format_products_list(products)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_back")]])
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    if data == "admin_edit_product":
        products = _load_products()
        if not products:
            await query.edit_message_text("✏️ EDIT PRODUCT\n\nNo products available to edit.")
            return
        keyboard = _get_product_selection_keyboard(products, "admin_edit_select_")
        await query.edit_message_text("✏️ EDIT PRODUCT\n\nSelect product to edit:", reply_markup=keyboard)
        return

    if data.startswith("admin_edit_select_"):
        try:
            product_id = int(data.replace("admin_edit_select_", ""))
        except Exception:
            await query.edit_message_text("❌ Invalid product ID.")
            return
        product = _get_product_by_id(product_id)
        if not product:
            await query.edit_message_text("❌ Product not found.")
            return
        context.user_data["admin_flow"] = "edit_product"
        context.user_data["admin_step"] = "awaiting_edit_name"
        context.user_data["edit_product_id"] = product_id
        context.user_data["edit_temp"] = {}
        current_text = f"✏️ EDITING PRODUCT ID: {product_id}\n\nCurrent Details:\n📦 Name: {product.get('name')}\n💰 Price: {_format_price_display(str(product.get('price','')))}\n📝 Details: {product.get('details','')}\n\n➡️ Please enter the new product name:"
        await query.edit_message_text(current_text)
        return

    if data == "admin_change_price":
        products = _load_products()
        if not products:
            await query.edit_message_text("💰 CHANGE PRICE\n\nNo products available.")
            return
        keyboard = _get_product_selection_keyboard(products, "admin_changeprice_select_")
        await query.edit_message_text("💰 CHANGE PRICE\n\nSelect product to change price:", reply_markup=keyboard)
        return

    if data.startswith("admin_changeprice_select_"):
        try:
            product_id = int(data.replace("admin_changeprice_select_", ""))
        except Exception:
            await query.edit_message_text("❌ Invalid product ID.")
            return
        product = _get_product_by_id(product_id)
        if not product:
            await query.edit_message_text("❌ Product not found.")
            return
        context.user_data["admin_flow"] = "change_price"
        context.user_data["admin_step"] = "awaiting_new_price"
        context.user_data["edit_product_id"] = product_id
        await query.edit_message_text(f"💰 CHANGE PRICE - ID: {product_id}\n\nCurrent:\n📦 {product.get('name')}\n💰 {_format_price_display(str(product.get('price','')))}\n\n➡️ Please enter the new price:")
        return

    if data == "admin_edit_details":
        products = _load_products()
        if not products:
            await query.edit_message_text("📝 EDIT DETAILS\n\nNo products available.")
            return
        keyboard = _get_product_selection_keyboard(products, "admin_editdetails_select_")
        await query.edit_message_text("📝 EDIT DETAILS\n\nSelect product to edit details:", reply_markup=keyboard)
        return

    if data.startswith("admin_editdetails_select_"):
        try:
            product_id = int(data.replace("admin_editdetails_select_", ""))
        except Exception:
            await query.edit_message_text("❌ Invalid product ID.")
            return
        product = _get_product_by_id(product_id)
        if not product:
            await query.edit_message_text("❌ Product not found.")
            return
        context.user_data["admin_flow"] = "edit_details"
        context.user_data["admin_step"] = "awaiting_new_details"
        context.user_data["edit_product_id"] = product_id
        await query.edit_message_text(f"📝 EDIT DETAILS - ID: {product_id}\n\nCurrent:\n📦 {product.get('name')}\n📝 {product.get('details','')}\n\n➡️ Please enter the new details:")
        return

    if data == "admin_delete_product":
        products = _load_products()
        if not products:
            await query.edit_message_text("🗑️ DELETE PRODUCT\n\nNo products available.")
            return
        keyboard = _get_product_selection_keyboard(products, "admin_delete_select_")
        await query.edit_message_text("🗑️ DELETE PRODUCT\n\nSelect product to delete:", reply_markup=keyboard)
        return

    if data.startswith("admin_delete_select_"):
        try:
            product_id = int(data.replace("admin_delete_select_", ""))
        except Exception:
            await query.edit_message_text("❌ Invalid product ID.")
            return
        product = _get_product_by_id(product_id)
        if not product:
            await query.edit_message_text("❌ Product not found.")
            return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"admin_delete_confirm_{product_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]
        ])
        await query.edit_message_text(f"⚠️ DELETE CONFIRMATION\n\nAre you sure you want to delete?\n\nID: {product_id}\n📦 {product.get('name')}\n💰 {_format_price_display(str(product.get('price','')))}\n\nThis action cannot be undone.", reply_markup=keyboard)
        return

    if data.startswith("admin_delete_confirm_"):
        try:
            product_id = int(data.replace("admin_delete_confirm_", ""))
        except Exception:
            await query.edit_message_text("❌ Invalid product ID.")
            return
        product = _get_product_by_id(product_id)
        name = product.get("name") if product else f"ID {product_id}"
        success = _delete_product(product_id)
        if not success:
            await query.edit_message_text("❌ Delete failed. Try again.")
            return
        await query.edit_message_text(f"🗑️ Product deleted successfully!\n\nDeleted: {name} (ID: {product_id})")
        return

    if data == "admin_business_settings":
        settings = _get_business_settings()
        if settings is None:
            await query.edit_message_text("❌ Database connection failed. Please try again.")
            return
        text = _format_business_settings_admin(settings)
        await query.edit_message_text(text, reply_markup=_get_business_settings_edit_keyboard())
        return

    if data == "admin_biz_edit_name":
        context.user_data["admin_flow"] = "business_settings"
        context.user_data["admin_step"] = "awaiting_biz_name"
        await query.edit_message_text("🏢 EDIT BUSINESS NAME\n\nCurrent Business Name will be replaced.\n\n➡️ Please enter the new business name:")
        return
    if data == "admin_biz_edit_address":
        context.user_data["admin_flow"] = "business_settings"
        context.user_data["admin_step"] = "awaiting_biz_address"
        await query.edit_message_text("📍 EDIT ADDRESS\n\n➡️ Please enter the new address:")
        return
    if data == "admin_biz_edit_phone":
        context.user_data["admin_flow"] = "business_settings"
        context.user_data["admin_step"] = "awaiting_biz_phone"
        await query.edit_message_text("📞 EDIT PHONE\n\n➡️ Please enter the new phone number:")
        return
    if data == "admin_biz_edit_whatsapp":
        context.user_data["admin_flow"] = "business_settings"
        context.user_data["admin_step"] = "awaiting_biz_whatsapp"
        await query.edit_message_text("📱 EDIT WHATSAPP\n\n➡️ Please enter the new WhatsApp number:")
        return
    if data == "admin_biz_edit_email":
        context.user_data["admin_flow"] = "business_settings"
        context.user_data["admin_step"] = "awaiting_biz_email"
        await query.edit_message_text("📧 EDIT EMAIL\n\n➡️ Please enter the new email address:")
        return
    if data == "admin_biz_edit_desc":
        context.user_data["admin_flow"] = "business_settings"
        context.user_data["admin_step"] = "awaiting_biz_desc"
        await query.edit_message_text("📝 EDIT DESCRIPTION\n\n➡️ Please enter the new business description:")
        return

    # --- NEW: Customer Enquiries ---
    if data == "admin_enquiries":
        enquiries = _load_enquiries()
        if not enquiries:
            text = "📩 CUSTOMER ENQUIRIES\n\nNo customer enquiries yet."
            keyboard = [[InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_back")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        # Show list with buttons
        keyboard = []
        lines = ["📩 CUSTOMER ENQUIRIES\n"]
        for enq in enquiries[:15]:  # Limit to 15 to avoid too many buttons
            enq_id = enq.get("id")
            prod = enq.get("product_name", "Unknown")[:20]
            cust = enq.get("customer_name", "Customer")[:15]
            status_emoji = "🆕" if enq.get("status") == "new" else "✅"
            lines.append(f"{status_emoji} ID:{enq_id} 📦{prod} 👤{cust} 📌{enq.get('status')}")
            keyboard.append([InlineKeyboardButton(f"{status_emoji} ID:{enq_id} {prod} - {cust}", callback_data=f"admin_enq_view_{enq_id}")])
        keyboard.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_back")])
        text = "\n".join(lines)
        # Telegram message limit, truncate if needed
        if len(text) > 3800:
            text = text[:3800] + "\n...more"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("admin_enq_view_"):
        try:
            enq_id = int(data.replace("admin_enq_view_", ""))
        except Exception:
            await query.edit_message_text("❌ Invalid enquiry ID.")
            return
        enq = _get_enquiry_by_id(enq_id)
        if not enq:
            await query.edit_message_text("❌ Enquiry not found.")
            return
        price_display = _format_price_display(str(enq.get("product_price") or ""))
        text = (
            f"📩 ENQUIRY ID: {enq.get('id')}\n\n"
            f"📦 Product: {enq.get('product_name')}\n"
            f"🆔 Product ID: {enq.get('product_id')}\n"
            f"💰 Price: {price_display}\n\n"
            f"👤 Customer: {enq.get('customer_name')}\n"
            f"🆔 Telegram ID: {enq.get('telegram_user_id')}\n"
            f"🔗 Username: {enq.get('username') or 'Not available'}\n"
            f"📌 Status: {enq.get('status')}\n"
            f"📅 Date: {enq.get('created_at')}\n"
        )
        keyboard = [
            [InlineKeyboardButton("✅ Mark as Contacted", callback_data=f"admin_enq_contact_{enq_id}")],
            [InlineKeyboardButton("🗑️ Delete Enquiry", callback_data=f"admin_enq_del_{enq_id}")],
            [InlineKeyboardButton("⬅️ Back to Enquiries", callback_data="admin_enquiries")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("admin_enq_contact_"):
        try:
            enq_id = int(data.replace("admin_enq_contact_", ""))
        except Exception:
            await query.edit_message_text("❌ Invalid enquiry ID.")
            return
        success = _update_enquiry_status(enq_id, "contacted")
        if not success:
            await query.edit_message_text("❌ Failed to update status.")
            return
        enq = _get_enquiry_by_id(enq_id)
        text = f"✅ Enquiry ID {enq_id} marked as contacted.\n\n📦 {enq.get('product_name') if enq else ''} - 👤 {enq.get('customer_name') if enq else ''}"
        keyboard = [
            [InlineKeyboardButton("⬅️ Back to Enquiries", callback_data="admin_enquiries")],
            [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_back")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("admin_enq_del_"):
        # Could be admin_enq_del_{id} or admin_enq_del_yes_{id}
        if data.startswith("admin_enq_del_yes_"):
            try:
                enq_id = int(data.replace("admin_enq_del_yes_", ""))
            except Exception:
                await query.edit_message_text("❌ Invalid enquiry ID.")
                return
            success = _delete_enquiry(enq_id)
            if not success:
                await query.edit_message_text("❌ Delete failed.")
                return
            await query.edit_message_text(f"🗑️ Enquiry ID {enq_id} deleted successfully.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Enquiries", callback_data="admin_enquiries")]]))
            return
        elif data.startswith("admin_enq_del_no"):
            # Cancel delete
            await query.edit_message_text("Delete cancelled.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Enquiries", callback_data="admin_enquiries")]]))
            return
        else:
            # Initial delete request -> confirmation
            try:
                enq_id = int(data.replace("admin_enq_del_", ""))
            except Exception:
                await query.edit_message_text("❌ Invalid enquiry ID.")
                return
            enq = _get_enquiry_by_id(enq_id)
            if not enq:
                await query.edit_message_text("❌ Enquiry not found.")
                return
            text = (
                f"⚠️ DELETE ENQUIRY\n\n"
                f"Are you sure you want to delete this enquiry?\n\n"
                f"ID: {enq.get('id')}\n"
                f"📦 {enq.get('product_name')}\n"
                f"👤 {enq.get('customer_name')}\n"
            )
            keyboard = [
                [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"admin_enq_del_yes_{enq_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"admin_enq_view_{enq_id}")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return

    if data == "admin_back":
        context.user_data.pop("admin_flow", None)
        context.user_data.pop("admin_step", None)
        context.user_data.pop("edit_product_id", None)
        context.user_data.pop("edit_temp", None)
        context.user_data.pop("biz_field", None)
        await query.edit_message_text("🔐 ADMIN PANEL\n\nWelcome Admin! Please select an option below 👇", reply_markup=_get_admin_keyboard())
        return

    await query.edit_message_text("This product management feature will be available soon.")

# --- Telegram Application with updater(None) for custom webhook ---
telegram_app = Application.builder().token(BOT_TOKEN).updater(None).build()
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

_init_turso_db()

try:
    future = asyncio.run_coroutine_threadsafe(_init_telegram(), telegram_loop)
    future.result(timeout=30)
    logger.info("Telegram application initialized in persistent loop - webhook mode ready.")
except Exception:
    logger.exception("Failed to initialize Telegram application in persistent loop")

WEBHOOK_PATH = "/telegram-webhook"

def _get_webhook_secret():
    return os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN") or os.getenv("WEBHOOK_SECRET_TOKEN") or os.getenv("TELEGRAM_WEBHOOK_SECRET")

@app.route(WEBHOOK_PATH, methods=["POST"])
def telegram_webhook():
    secret = _get_webhook_secret()
    if secret:
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header_secret != secret:
            logger.warning("Webhook secret mismatch - rejecting request")
            return "Forbidden", 403
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
                secret = _get_webhook_secret()
                if secret:
                    await telegram_app.bot.set_webhook(url=webhook_url, secret_token=secret, drop_pending_updates=False)
                    logger.info("Webhook secret_token ensured")
                return
            secret = _get_webhook_secret()
            if secret:
                await telegram_app.bot.set_webhook(url=webhook_url, secret_token=secret, drop_pending_updates=False)
                logger.info("Webhook set with secret_token to: %s", webhook_url)
            else:
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