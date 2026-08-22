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
        # --- NEW: Business Settings table (separate, no impact on products) ---
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
        # Ensure single row id=1 exists
        _execute_turso("""
            INSERT OR IGNORE INTO business_settings (id, business_name, address, phone, whatsapp, email, description)
            VALUES (1, '', '', '', '', '', '')
        """)
        _turso_initialized = True
        logger.info("Turso products and business_settings tables ensured")
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

# --- NEW: Business Settings Functions ---
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
    # Use parameterized column via string formatting (safe because allowed set)
    sql = f"UPDATE business_settings SET {field} = ? WHERE id = 1"
    result = _execute_turso(sql, (value,))
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
    # Show only saved data, no hard-coded fallback
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

def _get_product_selection_keyboard(products, prefix, include_back=True):
    keyboard = []
    for p in products:
        pid = p.get("id")
        name = p.get("name", "Unknown")[:30]
        keyboard.append([InlineKeyboardButton(f"{pid}. {name}", callback_data=f"{prefix}{pid}")])
    if include_back:
        keyboard.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_back")])
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
        [InlineKeyboardButton("🛠 Services", callback_data="services")],
        [InlineKeyboardButton("💰 Price", callback_data="price")],
        [InlineKeyboardButton("🏢 Business Info", callback_data="business_info")],
        [InlineKeyboardButton("🤖 About This Bot", callback_data="about")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Vel Business Helper!\n\nనేను మీ business కి సంబంధించిన basic information, services, prices మరియు contact details అందించడానికి సహాయం చేస్తాను.\n\nకింద ఉన్న option ఎంచుకోండి 👇",
        reply_markup=_get_customer_main_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "services":
        text = "🛠 SERVICES\n\n• Business information\n• Product information\n• Pump information\n• Customer enquiry support\n• Contact details\n\nమరిన్ని services త్వరలో add చేస్తాము."
        keyboard = [[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    elif query.data == "price":
        text = "💰 PRICE INFORMATION\n\nProduct/model మీద price మారుతుంది.\n\nమీకు కావాల్సిన product లేదా model పేరు పంపండి.\n\nఉదాహరణ:\nCRI pump\n1 HP pump\n2 HP motor\nOpenwell pump"
        keyboard = [[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
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
    elif query.data == "back_to_start":
        text = "👋 Welcome to Vel Business Helper!\n\nనేను మీ business కి సంబంధించిన basic information, services, prices మరియు contact details అందించడానికి సహాయం చేస్తాను.\n\nకింద ఉన్న option ఎంచుకోండి 👇"
        await query.edit_message_text(text, reply_markup=_get_customer_main_keyboard())
        return
    else:
        text = "❓ HELP\n\nమీకు కావాల్సిన విషయం message గా పంపండి.\n\nఉదాహరణలు:\n• Contact\n• Price\n• CRI pump\n• 1 HP pump\n• Services\n\n/start పంపితే main menu వస్తుంది."
        keyboard = [[InlineKeyboardButton("⬅️ Back to Start", callback_data="back_to_start")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_id = update.effective_user.id if update.effective_user else None
    if user_id == ADMIN_USER_ID:
        flow = context.user_data.get("admin_flow")
        # Add Product
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
                new_id = _add_product_to_turso(temp.get("name", ""), temp.get("price", ""), temp.get("details", ""))
                if new_id is None:
                    await update.message.reply_text("❌ Database connection failed. Turso credentials check చేసి మళ్ళీ try చేయండి.")
                    return
                context.user_data.pop("admin_flow", None)
                context.user_data.pop("admin_step", None)
                context.user_data.pop("temp_product", None)
                price_display = _format_price_display(temp.get("price", ""))
                await update.message.reply_text(f"✅ Product saved successfully!\n\n📦 Name: {temp.get('name')}\n💰 Price: {price_display}\n📝 Details: {temp.get('details')}")
                return
        # Edit Product
        elif flow == "edit_product":
            step = context.user_data.get("admin_step")
            msg_text = update.message.text.strip()
            if step == "awaiting_edit_name":
                if not msg_text:
                    await update.message.reply_text("Product name ఖాళీగా ఉండకూడదు. మళ్ళీ పంపండి:")
                    return
                context.user_data["edit_temp"] = {"name": msg_text}
                context.user_data["admin_step"] = "awaiting_edit_price"
                await update.message.reply_text("💰 కొత్త Price పంపండి:")
                return
            elif step == "awaiting_edit_price":
                if not msg_text:
                    await update.message.reply_text("Price ఖాళీగా ఉండకూడదు. మళ్ళీ పంపండి:")
                    return
                context.user_data["edit_temp"]["price"] = msg_text
                context.user_data["admin_step"] = "awaiting_edit_details"
                await update.message.reply_text("📝 కొత్త Details పంపండి:")
                return
            elif step == "awaiting_edit_details":
                edit_id = context.user_data.get("edit_product_id")
                edit_temp = context.user_data.get("edit_temp", {})
                edit_temp["details"] = msg_text
                if not edit_id:
                    await update.message.reply_text("❌ Product ID not found. /admin నుంచి మళ్ళీ ప్రయత్నించండి.")
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
        # Change Price
        elif flow == "change_price":
            step = context.user_data.get("admin_step")
            msg_text = update.message.text.strip()
            if step == "awaiting_new_price":
                edit_id = context.user_data.get("edit_product_id")
                if not edit_id:
                    await update.message.reply_text("❌ Product ID not found.")
                    return
                if not msg_text:
                    await update.message.reply_text("Price ఖాళీగా ఉండకూడదు. మళ్ళీ పంపండి:")
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
        # Edit Details
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
        # Business Settings - NEW
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
                    await update.message.reply_text("Business Name ఖాళీగా ఉండకూడదు. మళ్ళీ పంపండి:")
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

    text = update.message.text.strip().lower()
    if text in ["hi", "hello", "hey", "హాయ్", "హలో"]:
        await update.message.reply_text("👋 Hello!\n\nVel Business Helper కి Welcome!\n\n/start నొక్కండి.", reply_markup=_get_customer_main_keyboard())
    elif "contact" in text or "business" in text:
        settings = _get_business_settings()
        if settings and any(settings.values()):
            await update.message.reply_text(_format_business_settings_customer(settings), reply_markup=_get_customer_main_keyboard())
        else:
            await update.message.reply_text("🏢 BUSINESS INFORMATION\n\nBusiness information not yet configured.\nPlease contact admin.", reply_markup=_get_customer_main_keyboard())
    elif "price" in text or "ధర" in text:
        await update.message.reply_text("💰 Price తెలుసుకోవడానికి product/model పేరు పంపండి.\n\nఉదాహరణ:\nCRI 1 HP\nCRI 2 HP\nOpenwell pump", reply_markup=_get_customer_main_keyboard())
    elif "pump" in text or "పంప్" in text:
        await update.message.reply_text("🔧 PUMP INFORMATION\n\nమీకు కావాల్సిన pump details కోసం model పేరు పంపండి.\n\nఉదాహరణ:\n1 HP pump\n2 HP pump\nOpenwell pump\nSubmersible pump", reply_markup=_get_customer_main_keyboard())
    else:
        await update.message.reply_text("🙂 మీ message అందింది.\n\nదయచేసి /start పంపి option ఎంచుకోండి.\n\nలేదా మీకు కావాల్సిన product పేరు పంపండి.", reply_markup=_get_customer_main_keyboard())

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
        context.user_data.pop("edit_product_id", None)
        context.user_data.pop("edit_temp", None)
        await query.edit_message_text("➕ ADD PRODUCT\n\nProduct name పంపండి:")
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
        current_text = f"✏️ EDITING PRODUCT ID: {product_id}\n\nCurrent Details:\n📦 Name: {product.get('name')}\n💰 Price: {_format_price_display(str(product.get('price','')))}\n📝 Details: {product.get('details','')}\n\n➡️ కొత్త Product Name పంపండి:"
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
        await query.edit_message_text(f"💰 CHANGE PRICE - ID: {product_id}\n\nCurrent:\n📦 {product.get('name')}\n💰 {_format_price_display(str(product.get('price','')))}\n\n➡️ కొత్త Price పంపండి:")
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
        await query.edit_message_text(f"📝 EDIT DETAILS - ID: {product_id}\n\nCurrent:\n📦 {product.get('name')}\n📝 {product.get('details','')}\n\n➡️ కొత్త Details పంపండి:")
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

    # --- NEW: Business Settings ---
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
        await query.edit_message_text("🏢 EDIT BUSINESS NAME\n\nCurrent Business Name will be replaced.\n\n➡️ కొత్త Business Name పంపండి:")
        return
    if data == "admin_biz_edit_address":
        context.user_data["admin_flow"] = "business_settings"
        context.user_data["admin_step"] = "awaiting_biz_address"
        await query.edit_message_text("📍 EDIT ADDRESS\n\n➡️ కొత్త Address పంపండి:")
        return
    if data == "admin_biz_edit_phone":
        context.user_data["admin_flow"] = "business_settings"
        context.user_data["admin_step"] = "awaiting_biz_phone"
        await query.edit_message_text("📞 EDIT PHONE\n\n➡️ కొత్త Phone Number పంపండి:")
        return
    if data == "admin_biz_edit_whatsapp":
        context.user_data["admin_flow"] = "business_settings"
        context.user_data["admin_step"] = "awaiting_biz_whatsapp"
        await query.edit_message_text("📱 EDIT WHATSAPP\n\n➡️ కొత్త WhatsApp Number పంపండి:")
        return
    if data == "admin_biz_edit_email":
        context.user_data["admin_flow"] = "business_settings"
        context.user_data["admin_step"] = "awaiting_biz_email"
        await query.edit_message_text("📧 EDIT EMAIL\n\n➡️ కొత్త Email పంపండి:")
        return
    if data == "admin_biz_edit_desc":
        context.user_data["admin_flow"] = "business_settings"
        context.user_data["admin_step"] = "awaiting_biz_desc"
        await query.edit_message_text("📝 EDIT DESCRIPTION\n\n➡️ కొత్త Business Description పంపండి:")
        return

    if data == "admin_back":
        context.user_data.pop("admin_flow", None)
        context.user_data.pop("admin_step", None)
        context.user_data.pop("edit_product_id", None)
        context.user_data.pop("edit_temp", None)
        context.user_data.pop("biz_field", None)
        await query.edit_message_text("🔐 ADMIN PANEL\n\nWelcome Admin! కింద ఉన్న option ఎంచుకోండి 👇", reply_markup=_get_admin_keyboard())
        return

    await query.edit_message_text("Product management feature త్వరలో అందుబాటులో ఉంటుంది.")

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
