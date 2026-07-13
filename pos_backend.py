import os
import sys
import sqlite3
import pandas as pd
import io
import time
import threading
import subprocess
import hmac
import hashlib
import secrets
from functools import wraps
import requests
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

app = Flask(__name__)
EXCEL_FILE = "Basededatos_Actualizada.xlsx"


def get_data_dir():
    """Returns the writable directory next to the exe (or cwd in development)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.getcwd()


# DB and catalog live next to the exe (or cwd in dev), never relative to cwd at runtime
DB_FILE = os.path.join(get_data_dir(), "pos_database.db")


def get_catalog_path():
    return os.path.join(get_data_dir(), 'catalog.xlsx')

try:
    from config import STORE_ID, STORE_NAME, CENTRAL_SERVER_URL, SYNC_SECRET
except ImportError:
    STORE_ID = "1"
    STORE_NAME = "Aeropuerto"
    CENTRAL_SERVER_URL = "http://localhost:5000"
    SYNC_SECRET = "change-me-in-config"

# ── Auth helpers ──────────────────────────────────────────────────────────────
# A lightweight token: HMAC-SHA256(username:role, SECRET_KEY).
# Generated once at startup and stored in the DB so it survives restarts.
# Not a full JWT — sufficient for a single-server LAN/cloud deployment.

def _get_or_create_secret(conn):
    secret = get_config_value(conn, 'secret_key')
    if not secret:
        secret = secrets.token_hex(32)
        set_config_value(conn, 'secret_key', secret)
    return secret


def _make_token(username: str, role: str, secret: str) -> str:
    payload = f"{username}:{role}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def _verify_token(token: str, secret: str):
    """Return (username, role) if valid, else None."""
    try:
        parts = token.rsplit(':', 1)
        if len(parts) != 2:
            return None
        payload, sig = parts
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        username, role = payload.split(':', 1)
        return username, role
    except Exception:
        return None


def require_auth(*allowed_roles):
    """Decorator that enforces X-Auth-Token header with one of the allowed roles."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            token = request.headers.get('X-Auth-Token', '')
            conn = get_db()
            secret = get_config_value(conn, 'secret_key', '')
            conn.close()
            result = _verify_token(token, secret) if secret else None
            if not result:
                return jsonify({"success": False, "message": "No autenticado"}), 401
            _, role = result
            if allowed_roles and role not in allowed_roles:
                return jsonify({"success": False, "message": "Sin permisos"}), 403
            return f(*args, **kwargs)
        return wrapped
    return decorator


def require_sync_secret(f):
    """Decorator for machine-to-machine endpoints: validates X-Sync-Secret header."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        provided = request.headers.get('X-Sync-Secret', '')
        if not provided or not hmac.compare_digest(provided, SYNC_SECRET):
            return jsonify({"success": False, "message": "Sync secret inválido"}), 401
        return f(*args, **kwargs)
    return wrapped

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def add_column_if_not_exists(conn, table, column, definition):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    columns = [row['name'] for row in cur.fetchall()]
    if column not in columns:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def create_config_entry_if_missing(conn, key, value):
    cur = conn.cursor()
    cur.execute("SELECT value FROM config WHERE key = ?", (key,))
    if not cur.fetchone():
        cur.execute("INSERT INTO config (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()


def get_config_value(conn, key, default=None):
    cur = conn.cursor()
    row = cur.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return row['value'] if row else default


def set_config_value(conn, key, value):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value))
    )
    conn.commit()


def create_user_if_missing(cursor, username, password, role, store='Tienda Principal'):
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (username, password_hash, role, store) VALUES (?, ?, ?, ?)",
            (username, generate_password_hash(password), role, store)
        )


def init_db():
    conn = get_db()
    c = conn.cursor()
    # Create tables
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    store TEXT DEFAULT 'Tienda Principal'
                )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS products (
                    codigo TEXT PRIMARY KEY,
                    descripcion TEXT NOT NULL,
                    precio REAL NOT NULL,
                    stock INTEGER DEFAULT 0
                )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    total REAL NOT NULL,
                    timestamp TEXT NOT NULL,
                    is_synced INTEGER DEFAULT 0,
                    store TEXT DEFAULT 'Sin tienda',
                    subtotal REAL DEFAULT 0,
                    discount REAL DEFAULT 0,
                    payment_method TEXT DEFAULT 'efectivo',
                    cash_amount REAL DEFAULT 0,
                    card_amount REAL DEFAULT 0,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS sale_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sale_id INTEGER,
                    product_codigo TEXT,
                    quantity INTEGER,
                    subtotal REAL,
                    descripcion TEXT DEFAULT '',
                    FOREIGN KEY(sale_id) REFERENCES sales(id),
                    FOREIGN KEY(product_codigo) REFERENCES products(codigo)
                )''')

    c.execute('''CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )''')

    # Central-server stores registry
    c.execute('''CREATE TABLE IF NOT EXISTS stores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_key TEXT UNIQUE NOT NULL,
                    name TEXT,
                    address TEXT DEFAULT '',
                    active INTEGER DEFAULT 1,
                    last_sync TEXT
                )''')

    # Add origin-tracing columns to sales (only created if missing on older DBs)
    add_column_if_not_exists(conn, 'sales', 'source_store', "TEXT DEFAULT NULL")
    add_column_if_not_exists(conn, 'sales', 'source_sale_id', "INTEGER DEFAULT NULL")

    # Add missing columns for older databases
    add_column_if_not_exists(conn, 'users', 'store', "TEXT DEFAULT 'Tienda Principal'")
    add_column_if_not_exists(conn, 'sales', 'store', "TEXT DEFAULT 'Sin tienda'")
    add_column_if_not_exists(conn, 'sales', 'subtotal', "REAL DEFAULT 0")
    add_column_if_not_exists(conn, 'sales', 'discount', "REAL DEFAULT 0")
    add_column_if_not_exists(conn, 'sales', 'payment_method', "TEXT DEFAULT 'efectivo'")
    add_column_if_not_exists(conn, 'sales', 'cash_amount', "REAL DEFAULT 0")
    add_column_if_not_exists(conn, 'sales', 'card_amount', "REAL DEFAULT 0")
    add_column_if_not_exists(conn, 'sales', 'discount_currency', "TEXT DEFAULT 'mxn'")
    add_column_if_not_exists(conn, 'sales', 'cash_currency', "TEXT DEFAULT 'mxn'")
    add_column_if_not_exists(conn, 'sales', 'vendor', "TEXT")
    # Central-server columns added via migration so existing DBs get them too
    add_column_if_not_exists(conn, 'sales', 'store_key', "TEXT DEFAULT NULL")
    add_column_if_not_exists(conn, 'sales', 'local_sale_id', "INTEGER DEFAULT NULL")
    add_column_if_not_exists(conn, 'sales', 'cashier', "TEXT DEFAULT ''")
    add_column_if_not_exists(conn, 'sales', 'sale_type', "TEXT DEFAULT 'normal'")
    add_column_if_not_exists(conn, 'sale_items', 'descripcion', "TEXT DEFAULT ''")

    create_config_entry_if_missing(conn, 'exchange_rate', 17.5)
    
    create_user_if_missing(c, 'admin', 'admin123', 'admin', 'Central')
    create_user_if_missing(c, 'caja', 'caja123', 'cashier', 'Tienda Principal')
    create_user_if_missing(c, 'caja1', 'caja1234', 'cashier', 'Tienda Principal')
    create_user_if_missing(c, 'caja2', 'caja12345', 'cashier', 'Tienda Principal')
    create_user_if_missing(c, 'almacen', 'almacen123', 'almacen', 'Almacen Central')
        
    conn.commit()
    conn.close()

def import_excel_data(force=False):
    conn = get_db()
    c = conn.cursor()

    table_exists = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='products'").fetchone()
    if not table_exists:
        conn.close()
        init_db()
        conn = get_db()
        c = conn.cursor()

    if not force:
        c.execute("SELECT COUNT(*) as count FROM products")
        count = c.fetchone()['count']
        if count > 0:
            conn.close()
            return

    if os.path.exists(EXCEL_FILE):
        try:
            df = pd.read_excel(EXCEL_FILE)
            df.columns = df.columns.str.strip()

            # Detect price column names
            price_column = None
            for candidate in ['PM', 'Precio venta', 'Precio', 'precio', 'precio venta', 'price']:
                if candidate in df.columns:
                    price_column = candidate
                    break

            if price_column is None:
                raise ValueError('No se encontró columna de precio válida en el Excel.')

            df[price_column] = pd.to_numeric(df[price_column], errors='coerce').fillna(0)
            imported = 0
            excel_codes = []

            for _, row in df.iterrows():
                codigo = str(row.get('Codigo', '')).strip()
                desc = str(row.get('Descripcion', '')).strip()
                precio = float(row.get(price_column, 0))

                if codigo and codigo != 'nan' and desc and desc != 'nan':
                    excel_codes.append(codigo)
                    existing = c.execute("SELECT stock FROM products WHERE codigo = ?", (codigo,)).fetchone()
                    if existing:
                        c.execute("UPDATE products SET descripcion = ?, precio = ? WHERE codigo = ?",
                                  (desc, precio, codigo))
                    else:
                        c.execute("INSERT INTO products (codigo, descripcion, precio, stock) VALUES (?, ?, ?, ?)",
                                  (codigo, desc, precio, 0))
                    imported += 1

            if force and excel_codes:
                placeholders = ",".join("?" for _ in excel_codes)
                c.execute(f"DELETE FROM products WHERE codigo NOT IN ({placeholders})", tuple(excel_codes))

            conn.commit()
            print(f"Imported/updated {imported} products from Excel.")
        except Exception as e:
            print(f"Error importing excel: {e}")
    conn.close()


def import_from_fileobj(fileobj, force=False):
    """
    Import/update products from an in-memory file-like object (BytesIO or werkzeug FileStorage).
    Returns (imported_count, error_message_or_None).
    """
    try:
        df = pd.read_excel(fileobj)
        df.columns = df.columns.str.strip()

        price_column = None
        for candidate in ['PM', 'Precio venta', 'Precio', 'precio', 'precio venta', 'price']:
            if candidate in df.columns:
                price_column = candidate
                break

        if price_column is None:
            return 0, 'No se encontro columna de precio valida. Columnas esperadas: Precio venta, PM, Precio.'

        if 'Codigo' not in df.columns:
            return 0, 'No se encontro la columna "Codigo" en el archivo.'

        if 'Descripcion' not in df.columns:
            return 0, 'No se encontro la columna "Descripcion" en el archivo.'

        df[price_column] = pd.to_numeric(df[price_column], errors='coerce').fillna(0)

        conn = get_db()
        c = conn.cursor()
        imported = 0
        excel_codes = []

        for _, row in df.iterrows():
            codigo = str(row.get('Codigo', '')).strip()
            desc = str(row.get('Descripcion', '')).strip()
            precio = float(row.get(price_column, 0))

            if codigo and codigo != 'nan' and desc and desc != 'nan':
                excel_codes.append(codigo)
                existing = c.execute("SELECT stock FROM products WHERE codigo = ?", (codigo,)).fetchone()
                if existing:
                    c.execute("UPDATE products SET descripcion = ?, precio = ? WHERE codigo = ?",
                              (desc, precio, codigo))
                else:
                    c.execute("INSERT INTO products (codigo, descripcion, precio, stock) VALUES (?, ?, ?, ?)",
                              (codigo, desc, precio, 0))
                imported += 1

        if force and excel_codes:
            placeholders = ",".join("?" for _ in excel_codes)
            c.execute(f"DELETE FROM products WHERE codigo NOT IN ({placeholders})", tuple(excel_codes))

        conn.commit()
        conn.close()
        return imported, None

    except Exception as e:
        return 0, str(e)


@app.route('/')
def index():
    # Central-server health check: return JSON when called without an HTML browser
    # (e.g. Railway health probe, curl). Store browser always sends Accept: text/html.
    accept = request.headers.get('Accept', '')
    if 'text/html' not in accept:
        conn = get_db()
        try:
            conn.execute("SELECT 1")
            db_status = "connected"
        except Exception:
            db_status = "error"
        finally:
            conn.close()
        return jsonify({
            "status": "ok",
            "service": "NXT POS Central Server",
            "version": "2.2",
            "database_url_set": bool(DB_FILE),
            "sync_secret_set": bool(SYNC_SECRET and SYNC_SECRET != "change-me-to-a-strong-random-string"),
            "database_status": db_status
        })
    return render_template('index.html')


# Setup status — called on page load to decide whether to show setup screen
@app.route('/api/setup/status', methods=['GET'])
def setup_status():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM products").fetchone()['c']
    conn.close()
    return jsonify({"needs_setup": count == 0})


# Authentication
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user and check_password_hash(user['password_hash'], password):
        secret = _get_or_create_secret(conn)
        token = _make_token(user['username'], user['role'], secret)
        conn.close()
        return jsonify({
            "success": True,
            "token": token,
            "user": {
                "id": user['id'],
                "username": user['username'],
                "role": user['role'],
                "store": user['store'] or 'Sin tienda'
            }
        })
    conn.close()
    return jsonify({"success": False, "message": "Credenciales inválidas"}), 401

# Products
@app.route('/api/products', methods=['GET'])
def get_products():
    query = request.args.get('q', '').lower()
    conn = get_db()
    if query:
        products = conn.execute("SELECT * FROM products WHERE lower(codigo) LIKE ? OR lower(descripcion) LIKE ?", (f"%{query}%", f"%{query}%")).fetchall()
    else:
        products = conn.execute("SELECT * FROM products ORDER BY descripcion ASC").fetchall()
    conn.close()
    return jsonify([dict(p) for p in products])

# Update product stock (Admin)
@app.route('/api/products/<codigo>/stock', methods=['PUT'])
@require_auth('admin', 'almacen')
def update_stock(codigo):
    data = request.json
    new_stock = data.get('stock', 0)
    conn = get_db()
    conn.execute("UPDATE products SET stock = ? WHERE codigo = ?", (new_stock, codigo))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# Update product price (Admin)
@app.route('/api/products/<codigo>', methods=['PUT'])
@require_auth('admin', 'almacen')
def update_product(codigo):
    data = request.json
    conn = get_db()
    if 'precio' in data:
        conn.execute("UPDATE products SET precio = ? WHERE codigo = ?", (data['precio'], codigo))
    if 'stock' in data:
        conn.execute("UPDATE products SET stock = ? WHERE codigo = ?", (data['stock'], codigo))
    if 'descripcion' in data:
        conn.execute("UPDATE products SET descripcion = ? WHERE codigo = ?", (data['descripcion'], codigo))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# Sales Registration
@app.route('/api/sales', methods=['POST'])
def register_sale():
    data = request.json
    user_id = data.get('user_id')
    items = data.get('items', [])

    def safe_float(val, default=0.0):
        if val is None or val == '':
            return float(default)
        try:
            return float(val)
        except (ValueError, TypeError):
            return float(default)

    discount = safe_float(data.get('discount', 0))
    discount_currency = data.get('discount_currency', 'mxn')
    payment_method = data.get('payment_method', 'efectivo')
    cash_amount = safe_float(data.get('cash_amount', 0))
    cash_currency = data.get('cash_currency', 'mxn')
    card_amount = safe_float(data.get('card_amount', 0))
    vendor = data.get('vendor')
    
    if not items:
        return jsonify({"success": False, "message": "El carrito está vacío"}), 400

    if discount < 0:
        return jsonify({"success": False, "message": "El descuento no puede ser negativo"}), 400

    valid_methods = ['efectivo', 'tarjeta', 'mixto']

    if payment_method not in valid_methods:
        return jsonify({"success": False, "message": "Método de pago inválido"}), 400

    subtotal = 0

    for item in items:
        quantity = int(item.get('quantity', 0))
        precio = float(item.get('precio', 0))

        if quantity <= 0:
            return jsonify({"success": False, "message": "Cantidad inválida"}), 400

        subtotal += precio * quantity

    if discount > subtotal:
        return jsonify({"success": False, "message": "El descuento no puede ser mayor al subtotal"}), 400

    is_degustacion = (discount == subtotal and subtotal > 0)
    if is_degustacion:
        user_id = None

    total = subtotal - discount

    if payment_method == 'efectivo':
        cash_amount = total
        card_amount = 0

    elif payment_method == 'tarjeta':
        cash_amount = 0
        card_amount = total

    elif payment_method == 'mixto':
        if cash_amount < 0 or card_amount < 0:
            return jsonify({"success": False, "message": "Los montos no pueden ser negativos"}), 400

        # Use tolerance for floating point comparison (1.00 tolerance to handle currency conversion rounding)
        payment_sum = round(cash_amount + card_amount, 2)
        total_rounded = round(total, 2)
        tolerance = 1.00
        
        if abs(payment_sum - total_rounded) > tolerance:
            print(f"DEBUG Backend: payment_sum={payment_sum}, total={total_rounded}, difference={abs(payment_sum - total_rounded)}")
            return jsonify({"success": False, "message": "La suma del pago mixto debe ser igual al total"}), 400
        
    conn = get_db()
    c = conn.cursor()

    # Stock check — reject before writing anything
    for item in items:
        codigo = item.get('codigo', '')
        quantity = int(item.get('quantity', 0))
        row = c.execute("SELECT stock, descripcion FROM products WHERE codigo = ?", (codigo,)).fetchone()
        if row and row['stock'] < quantity:
            conn.close()
            return jsonify({
                "success": False,
                "message": f"Stock insuficiente para: {row['descripcion']} (disponible: {row['stock']})"
            }), 400

    timestamp = datetime.now().isoformat()

    store = 'Sin tienda'
    if user_id:
        user = c.execute("SELECT store FROM users WHERE id = ?", (user_id,)).fetchone()
        if user:
            store = user['store'] or store
    
    c.execute("""
        INSERT INTO sales (
            user_id,
            subtotal,
            discount,
            discount_currency,
            total,
            payment_method,
            cash_amount,
            cash_currency,
            card_amount,
            timestamp,
            is_synced,
            store,
            vendor
        ) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
    """, (
        user_id,
        subtotal,
        discount,
        discount_currency,
        total,
        payment_method,
        cash_amount,
        cash_currency,
        card_amount,
        timestamp,
        store,
        vendor
    ))

    sale_id = c.lastrowid
    
    for item in items:
        quantity = int(item['quantity'])
        precio = float(item.get('precio', 0))
        subtotal_producto = precio * quantity

        c.execute(
            "INSERT INTO sale_items (sale_id, product_codigo, quantity, subtotal) VALUES (?, ?, ?, ?)",
            (sale_id, item['codigo'], quantity, subtotal_producto)
        )

        c.execute(
            "UPDATE products SET stock = stock - ? WHERE codigo = ?",
            (quantity, item['codigo'])
        )
        
    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "sale_id": sale_id,
        "timestamp": timestamp,
        "subtotal": subtotal,
        "discount": discount,
        "discount_currency": discount_currency,
        "total": total,
        "payment_method": payment_method,
        "cash_amount": cash_amount,
        "cash_currency": cash_currency,
        "card_amount": card_amount,
        "store": store,
        "vendor": vendor
    })
# Get sale details (items) for a specific sale
@app.route('/api/sales/<int:sale_id>/items', methods=['GET'])
def get_sale_items(sale_id):
    conn = get_db()
    items = conn.execute("""
        SELECT si.*, p.descripcion 
        FROM sale_items si 
        LEFT JOIN products p ON si.product_codigo = p.codigo 
        WHERE si.sale_id = ?
    """, (sale_id,)).fetchall()
    conn.close()
    return jsonify([dict(i) for i in items])

# Get a digital receipt by sale_id
@app.route('/api/receipt/<int:sale_id>', methods=['GET'])
def get_receipt(sale_id):
    conn = get_db()
    sale = conn.execute("""
        SELECT s.*, u.username 
        FROM sales s 
        LEFT JOIN users u ON s.user_id = u.id 
        WHERE s.id = ?
    """, (sale_id,)).fetchone()
    
    if not sale:
        conn.close()
        return jsonify({"success": False, "message": "Venta no encontrada"}), 404
    
    items = conn.execute("""
        SELECT si.*, p.descripcion, p.precio as unit_price
        FROM sale_items si 
        LEFT JOIN products p ON si.product_codigo = p.codigo 
        WHERE si.sale_id = ?
    """, (sale_id,)).fetchall()
    conn.close()
    
    return jsonify({
        "success": True,
        "receipt": {
            "sale_id": sale['id'],
            "cashier": sale['vendor'] or sale['username'] or 'Desconocido',
            "vendor": sale['vendor'],
            "subtotal": sale['subtotal'],
            "discount": sale['discount'],
            "discount_currency": sale['discount_currency'] or 'mxn',
            "total": sale['total'],
            "payment_method": sale['payment_method'],
            "cash_amount": sale['cash_amount'],
            "cash_currency": sale['cash_currency'] or 'mxn',
            "card_amount": sale['card_amount'],
            "timestamp": sale['timestamp'],
            "is_synced": sale['is_synced'],
            "items": [dict(i) for i in items]
        }
    })

# Admin endpoints
@app.route('/api/admin/income', methods=['GET'])
@require_auth('admin')
def get_income():
    conn = get_db()
    # Today's income
    today_start = datetime.now().strftime("%Y-%m-%d") + "T00:00:00"
    today_total = conn.execute("SELECT SUM(total) as t FROM sales WHERE timestamp >= ?", (today_start,)).fetchone()['t']
    
    # Overall stats
    total_sales_count = conn.execute("SELECT COUNT(*) as c FROM sales").fetchone()['c']
    total_products = conn.execute("SELECT COUNT(*) as c FROM products").fetchone()['c']
    unsynced_count = conn.execute("SELECT COUNT(*) as c FROM sales WHERE is_synced = 0").fetchone()['c']
    all_time_total = conn.execute("SELECT SUM(total) as t FROM sales").fetchone()['t']
    
    exchange_rate = float(get_config_value(conn, 'exchange_rate', 17.5) or 17.5)
    if exchange_rate <= 0:
        exchange_rate = 17.5

    # All sales
    sales = conn.execute("""
        SELECT s.id, u.username, s.total, s.timestamp, s.is_synced, s.store 
        FROM sales s 
        LEFT JOIN users u ON s.user_id = u.id 
        ORDER BY s.timestamp DESC
    """).fetchall()

    store_sales = conn.execute("""
        SELECT COALESCE(store, 'Sin tienda') AS store, COUNT(*) AS sales_count, SUM(total) AS total
        FROM sales
        GROUP BY store
        ORDER BY total DESC
    """).fetchall()

    cashier_sales = conn.execute("""
        SELECT COALESCE(u.username, 'Sin cajero') AS cashier, COUNT(*) AS sales_count, SUM(s.total) AS total
        FROM sales s
        LEFT JOIN users u ON s.user_id = u.id
        GROUP BY u.username
        ORDER BY total DESC
    """).fetchall()

    sales_list = []
    for sale in sales:
        sale_dict = dict(sale)
        sale_dict['total_usd'] = round((sale_dict['total'] or 0) / exchange_rate, 2)
        sales_list.append(sale_dict)

    conn.close()
    
    return jsonify({
        "today_total": today_total or 0,
        "today_total_usd": round((today_total or 0) / exchange_rate, 2),
        "total_sales_count": total_sales_count,
        "total_products": total_products,
        "unsynced_count": unsynced_count,
        "exchange_rate": exchange_rate,
        "all_time_total": all_time_total or 0,
        "all_time_total_usd": round((all_time_total or 0) / exchange_rate, 2),
        "sales": sales_list,
        "store_sales": [dict(s) for s in store_sales],
        "cashier_sales": [dict(c) for c in cashier_sales]
    })

@app.route('/api/exchange-rate', methods=['GET'])
def get_exchange_rate():
    conn = get_db()
    rate = float(get_config_value(conn, 'exchange_rate', 17.5) or 17.5)
    conn.close()
    return jsonify({"exchange_rate": rate})

@app.route('/api/admin/exchange-rate', methods=['POST'])
@require_auth('admin')
def update_exchange_rate():
    data = request.json
    rate = data.get('exchange_rate')
    try:
        rate = float(rate)
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Tipo de cambio inválido"}), 400

    if rate <= 0:
        return jsonify({"success": False, "message": "El tipo de cambio debe ser mayor a 0"}), 400

    conn = get_db()
    set_config_value(conn, 'exchange_rate', rate)
    conn.close()
    return jsonify({"success": True, "exchange_rate": rate})

# Admin: inventory with low stock alert
@app.route('/api/admin/inventory', methods=['GET'])
@require_auth('admin', 'almacen')
def get_inventory():
    query = request.args.get('q', '').lower()
    sort = request.args.get('sort', 'descripcion')
    order = request.args.get('order', 'asc')
    
    conn = get_db()
    
    valid_sorts = {'descripcion': 'descripcion', 'precio': 'precio', 'stock': 'stock', 'codigo': 'codigo'}
    sort_col = valid_sorts.get(sort, 'descripcion')
    order_dir = 'DESC' if order == 'desc' else 'ASC'
    
    if query:
        products = conn.execute(
            f"SELECT * FROM products WHERE lower(codigo) LIKE ? OR lower(descripcion) LIKE ? ORDER BY {sort_col} {order_dir}",
            (f"%{query}%", f"%{query}%")
        ).fetchall()
    else:
        products = conn.execute(f"SELECT * FROM products ORDER BY {sort_col} {order_dir}").fetchall()
    
    conn.close()
    return jsonify([dict(p) for p in products])

def do_sync_to_central():
    if not CENTRAL_SERVER_URL:
        return 0, "No central server configured."
        
    conn = get_db()
    unsynced_sales = conn.execute("SELECT * FROM sales WHERE is_synced = 0").fetchall()
    
    if not unsynced_sales:
        conn.close()
        return 0, "Nada que sincronizar."
        
    payload = []
    for sale in unsynced_sales:
        sale_dict = dict(sale)

        # §7.2 — resolve cashier username from users table
        user = conn.execute(
            "SELECT username FROM users WHERE id = ?", (sale['user_id'],)
        ).fetchone()
        sale_dict['cashier'] = user['username'] if user else ''

        items = conn.execute("SELECT * FROM sale_items WHERE sale_id = ?", (sale['id'],)).fetchall()
        item_list = []
        for item in items:
            item_dict = dict(item)
            # §7.3 — resolve product description from products table
            prod = conn.execute(
                "SELECT descripcion FROM products WHERE codigo = ?", (item['product_codigo'],)
            ).fetchone()
            item_dict['descripcion'] = prod['descripcion'] if prod else ''
            item_list.append(item_dict)
        sale_dict['items'] = item_list

        if sale_dict.get('store') == 'Sin tienda' or not sale_dict.get('store'):
            sale_dict['store'] = STORE_NAME

        # §7.1 — stable store key so renaming STORE_NAME doesn't create duplicate store rows
        sale_dict['store_id'] = STORE_ID

        # Carry origin info so the central server can trace each sale back
        sale_dict['source_store'] = STORE_NAME
        sale_dict['source_sale_id'] = sale_dict['id']

        payload.append(sale_dict)
        
    conn.close()
    
    try:
        url = f"{CENTRAL_SERVER_URL.rstrip('/')}/api/remote_sync"
        response = requests.post(
            url,
            json={"sales": payload},
            headers={"X-Sync-Secret": SYNC_SECRET},
            timeout=10,
        )
        
        if response.status_code == 200 and response.json().get('success'):
            conn = get_db()
            placeholders = ",".join("?" for _ in unsynced_sales)
            sale_ids = [s['id'] for s in unsynced_sales]
            conn.execute(f"UPDATE sales SET is_synced = 1 WHERE id IN ({placeholders})", tuple(sale_ids))
            conn.commit()
            conn.close()
            return len(unsynced_sales), f"Se sincronizaron {len(unsynced_sales)} ventas a la base de datos remota"
        else:
            return 0, f"Error del servidor central: {response.text}"
    except Exception as e:
        return 0, f"Error de conexión: {str(e)}"

# Real Sync
@app.route('/api/sync', methods=['POST'])
@require_auth('admin')
def sync_data():
    count, msg = do_sync_to_central()
    success = count > 0 or "Nada que sincronizar" in msg
    return jsonify({"success": success, "message": msg})

def _process_remote_sync(data):
    """
    Shared logic for /api/remote_sync and /api/receive_sync (§10 compatibility alias).
    Handles both the modern embedded-items format and the legacy top-level items dict.
    Returns a Flask response.
    """
    sales = data.get('sales', [])
    if not sales:
        return jsonify({"success": True, "received": 0, "inserted": 0, "errors": []})

    # §10 — normalise legacy format: top-level "items" dict keyed by sale id
    legacy_items = data.get('items')
    if legacy_items and isinstance(legacy_items, dict):
        for sale in sales:
            sid = str(sale.get('id', ''))
            if sid in legacy_items:
                sale['items'] = legacy_items[sid]

    conn = get_db()
    c = conn.cursor()
    inserted = 0
    errors = []

    for sale in sales:
        try:
            store_name = sale.get('store') or 'Desconocido'
            store_key  = str(sale.get('store_id') or store_name)
            local_sale_id = sale.get('id')

            # §6 — auto-register / update store row
            now_iso = datetime.now().isoformat(timespec='seconds')
            c.execute("""
                INSERT INTO stores (store_key, name, last_sync)
                VALUES (?, ?, ?)
                ON CONFLICT(store_key) DO UPDATE SET name = excluded.name, last_sync = excluded.last_sync
            """, (store_key, store_name, now_iso))

            subtotal         = float(sale.get('subtotal') or 0)
            discount         = float(sale.get('discount') or 0)
            total            = float(sale.get('total')    or 0)
            cash_amount      = float(sale.get('cash_amount')  or 0)
            card_amount      = float(sale.get('card_amount')  or 0)
            discount_currency = sale.get('discount_currency') or 'mxn'
            cash_currency    = sale.get('cash_currency')      or 'mxn'
            payment_method   = sale.get('payment_method')     or 'efectivo'
            cashier          = sale.get('cashier') or ''
            vendor           = sale.get('vendor')
            timestamp        = sale.get('timestamp')
            source_store     = sale.get('source_store') or store_name
            source_sale_id   = sale.get('source_sale_id')

            # §6 — degustación detection (server-side, no field required from store)
            is_degu   = subtotal > 0 and discount >= subtotal
            sale_type = 'degustacion' if is_degu else 'normal'

            # §6 — deduplication: skip if (store_key, local_sale_id) already exists
            if local_sale_id is not None:
                existing = c.execute(
                    "SELECT id FROM sales WHERE store_key = ? AND local_sale_id = ?",
                    (store_key, local_sale_id)
                ).fetchone()
                if existing:
                    continue

            c.execute("""
                INSERT INTO sales (
                    subtotal, discount, discount_currency, total,
                    payment_method, cash_amount, cash_currency, card_amount,
                    cashier, vendor, timestamp, is_synced, store,
                    store_key, local_sale_id, sale_type,
                    source_store, source_sale_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
            """, (
                subtotal, discount, discount_currency, total,
                payment_method, cash_amount, cash_currency, card_amount,
                cashier, vendor, timestamp, store_name,
                store_key, local_sale_id, sale_type,
                source_store, source_sale_id
            ))

            new_sale_id = c.lastrowid
            inserted += 1

            items = sale.get('items', [])
            for item in items:
                c.execute(
                    "INSERT INTO sale_items (sale_id, product_codigo, quantity, subtotal, descripcion) VALUES (?, ?, ?, ?, ?)",
                    (
                        new_sale_id,
                        item.get('product_codigo'),
                        int(item.get('quantity') or 1),
                        float(item.get('subtotal') or 0),
                        item.get('descripcion') or '',
                    )
                )
                # Do NOT update stock here — central DB is reporting-only.

        except Exception as exc:
            errors.append(f"sale id={sale.get('id')}: {exc}")

    conn.commit()
    conn.close()
    return jsonify({
        "success": True,
        "received": len(sales),
        "inserted": inserted,
        "errors": errors
    })


@app.route('/api/remote_sync', methods=['POST'])
@require_sync_secret
def remote_sync():
    return _process_remote_sync(request.json or {})


# §10 — backwards-compatibility alias
@app.route('/api/receive_sync', methods=['POST'])
@require_sync_secret
def receive_sync():
    return _process_remote_sync(request.json or {})

# ── §8 Central Admin API ──────────────────────────────────────────────────────

@app.route('/api/admin/dashboard', methods=['GET'])
@require_auth('admin')
def admin_dashboard():
    """Aggregated stats across all stores (§8)."""
    conn = get_db()
    today = datetime.now().strftime("%Y-%m-%d")

    row = conn.execute("""
        SELECT COALESCE(SUM(total), 0) as today_total, COUNT(*) as today_count
        FROM sales
        WHERE sale_type != 'degustacion' AND timestamp LIKE ?
    """, (f"{today}%",)).fetchone()

    grand = conn.execute("""
        SELECT COALESCE(SUM(total), 0) as grand_total
        FROM sales WHERE sale_type != 'degustacion'
    """).fetchone()

    stores_rows = conn.execute("""
        SELECT s.store_key, s.name, s.last_sync,
               COUNT(sa.id) as total_sales,
               COALESCE(SUM(CASE WHEN sa.sale_type != 'degustacion' THEN sa.total ELSE 0 END), 0) as total_revenue,
               COUNT(CASE WHEN sa.sale_type = 'degustacion' THEN 1 END) as degustaciones
        FROM stores s
        LEFT JOIN sales sa ON sa.store_key = s.store_key
        GROUP BY s.store_key, s.name, s.last_sync
        ORDER BY s.name
    """).fetchall()

    cashiers_rows = conn.execute("""
        SELECT cashier, COUNT(*) as total_sales,
               COALESCE(SUM(total), 0) as total_revenue
        FROM sales
        WHERE sale_type != 'degustacion' AND cashier != ''
        GROUP BY cashier
        ORDER BY total_revenue DESC
        LIMIT 20
    """).fetchall()

    conn.close()
    return jsonify({
        "today_total":  row['today_total'],
        "today_count":  row['today_count'],
        "grand_total":  grand['grand_total'],
        "stores":   [dict(r) for r in stores_rows],
        "cashiers": [dict(r) for r in cashiers_rows],
    })


@app.route('/api/admin/sales', methods=['GET'])
@require_auth('admin')
def admin_sales():
    """Raw sales rows with optional filters (§8)."""
    store  = request.args.get('store')
    month  = request.args.get('month')
    limit  = min(int(request.args.get('limit', 200)), 1000)

    where_clauses = []
    params = []

    if store:
        where_clauses.append("store_key = ?")
        params.append(store)
    if month:
        where_clauses.append("timestamp LIKE ?")
        params.append(f"{month}%")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    params.append(limit)

    conn = get_db()
    rows = conn.execute(
        f"SELECT * FROM sales {where_sql} ORDER BY timestamp DESC LIMIT ?",
        params
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/admin/reports/cashier', methods=['GET'])
@require_auth('admin')
def admin_cashier_report():
    """Per-cashier aggregation for a given month (§8)."""
    month = request.args.get('month', datetime.now().strftime("%Y-%m"))
    store = request.args.get('store')

    where_clauses = ["timestamp LIKE ?", "sale_type != 'degustacion'", "cashier != ''"]
    params = [f"{month}%"]

    if store:
        where_clauses.append("store_key = ?")
        params.append(store)

    where_sql = "WHERE " + " AND ".join(where_clauses)

    conn = get_db()
    rows = conn.execute(f"""
        SELECT cashier, store_key,
               COUNT(*) as total_sales,
               COALESCE(SUM(total), 0) as total_revenue,
               COUNT(CASE WHEN sale_type = 'degustacion' THEN 1 END) as degustaciones
        FROM sales
        {where_sql}
        GROUP BY cashier, store_key
        ORDER BY total_revenue DESC
    """, params).fetchall()
    conn.close()
    return jsonify({"month": month, "cashiers": [dict(r) for r in rows]})


@app.route('/api/admin/stores', methods=['GET'])
@require_auth('admin')
def admin_stores():
    """All rows from the stores table (§8)."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM stores ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# ── End §8 Central Admin API ──────────────────────────────────────────────────

# First-launch catalog upload
@app.route('/api/setup/upload_products', methods=['POST'])
def setup_upload_products():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No se recibió ningún archivo."}), 400

    f = request.files['file']
    if not f.filename.lower().endswith('.xlsx'):
        return jsonify({"success": False, "message": "Solo se aceptan archivos .xlsx"}), 400

    file_bytes = io.BytesIO(f.read())

    # Save to disk as catalog.xlsx for future use
    catalog_path = get_catalog_path()
    try:
        file_bytes.seek(0)
        with open(catalog_path, 'wb') as out:
            out.write(file_bytes.read())
    except Exception as e:
        return jsonify({"success": False, "message": f"No se pudo guardar el archivo: {e}"}), 500

    file_bytes.seek(0)
    imported, error = import_from_fileobj(file_bytes, force=False)
    if error:
        return jsonify({"success": False, "message": error}), 400

    return jsonify({"success": True, "imported": imported,
                    "message": f"{imported} productos importados correctamente."})


# Admin catalog re-upload (replaces old reload_products)
@app.route('/api/admin/upload_catalog', methods=['POST'])
@require_auth('admin')
def admin_upload_catalog():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No se recibió ningún archivo."}), 400

    f = request.files['file']
    if not f.filename.lower().endswith('.xlsx'):
        return jsonify({"success": False, "message": "Solo se aceptan archivos .xlsx"}), 400

    file_bytes = io.BytesIO(f.read())

    # Overwrite catalog.xlsx on disk
    catalog_path = get_catalog_path()
    try:
        file_bytes.seek(0)
        with open(catalog_path, 'wb') as out:
            out.write(file_bytes.read())
    except Exception as e:
        return jsonify({"success": False, "message": f"No se pudo guardar el archivo: {e}"}), 500

    file_bytes.seek(0)
    imported, error = import_from_fileobj(file_bytes, force=True)
    if error:
        return jsonify({"success": False, "message": error}), 400

    return jsonify({"success": True, "imported": imported,
                    "message": f"Catalogo actualizado: {imported} productos importados."})

# Export sales to Excel
@app.route('/api/admin/export', methods=['GET'])
@require_auth('admin')
def export_excel():
    try:
        conn = get_db()

        # Query sales
        sales_df = pd.read_sql_query("""
            SELECT s.id as ID, u.username as Cajero, s.vendor as Vendedor, s.store as Tienda, s.timestamp as Fecha,
                   s.subtotal as Subtotal, s.discount as Descuento, s.total as Total,
                   s.payment_method as Metodo_Pago, s.cash_amount as Efectivo, s.card_amount as Tarjeta
            FROM sales s
            LEFT JOIN users u ON s.user_id = u.id
            ORDER BY s.timestamp DESC
        """, conn)

        # Query items
        items_df = pd.read_sql_query("""
            SELECT si.sale_id as Venta_ID, p.codigo as Codigo, p.descripcion as Descripcion,
                   si.quantity as Cantidad, si.subtotal as Subtotal_Item
            FROM sale_items si
            LEFT JOIN products p ON si.product_codigo = p.codigo
        """, conn)

        # Calculate unit price safely
        if not items_df.empty:
            items_df['Precio_Unitario'] = items_df.apply(
                lambda row: row['Subtotal_Item'] / row['Cantidad'] if row['Cantidad'] > 0 else 0,
                axis=1
            )
            items_df = items_df[['Venta_ID', 'Codigo', 'Descripcion', 'Cantidad', 'Precio_Unitario', 'Subtotal_Item']]

        conn.close()

        # Write to Excel in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            sales_df.to_excel(writer, sheet_name='Ventas', index=False)
            items_df.to_excel(writer, sheet_name='Articulos Vendidos', index=False)

            # Auto-adjust column widths for 'Ventas'
            worksheet = writer.sheets['Ventas']
            for i, col in enumerate(sales_df.columns):
                max_len = (max([len(str(x)) for x in sales_df[col].values] + [len(col)]) + 2
                           if len(sales_df) > 0 else len(col) + 2)
                worksheet.column_dimensions[
                    worksheet.cell(row=1, column=i + 1).column_letter
                ].width = min(max_len, 50)

            # Auto-adjust column widths for 'Articulos Vendidos'
            worksheet_items = writer.sheets['Articulos Vendidos']
            for i, col in enumerate(items_df.columns):
                max_len = (max([len(str(x)) for x in items_df[col].values] + [len(col)]) + 2
                           if len(items_df) > 0 else len(col) + 2)
                worksheet_items.column_dimensions[
                    worksheet_items.cell(row=1, column=i + 1).column_letter
                ].width = min(max_len, 50)

        output.seek(0)

        filename = f"Ventas_NXT_POS_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        # Use get_data_dir() so the path is always correct whether running as exe or in dev
        export_dir = os.path.join(get_data_dir(), 'Exportaciones')
        os.makedirs(export_dir, exist_ok=True)
        export_path = os.path.join(export_dir, filename)

        with open(export_path, 'wb') as f:
            f.write(output.read())

        # Open Explorer to the file — Windows only, silently skipped on Linux/Mac
        if os.name == 'nt':
            try:
                subprocess.run(['explorer', '/select,', os.path.normpath(export_path)])
            except Exception as e:
                print(f"Error opening explorer: {e}")

        return jsonify({"success": True, "message": f"Guardado en Exportaciones: {filename}"})
    except Exception as e:
        print(f"Error exporting Excel: {e}")
        return jsonify({"success": False, "message": f"Error al exportar: {str(e)}"}), 500

def pull_exchange_rate_from_central():
    """Fetch the exchange rate from the central server and persist it locally."""
    if not CENTRAL_SERVER_URL:
        return
    try:
        url = f"{CENTRAL_SERVER_URL.rstrip('/')}/api/exchange-rate"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            rate = response.json().get('exchange_rate')
            if rate and float(rate) > 0:
                conn = get_db()
                set_config_value(conn, 'exchange_rate', float(rate))
                conn.close()
    except Exception:
        pass


def background_sync_task():
    while True:
        time.sleep(60)
        try:
            do_sync_to_central()
        except Exception:
            pass
        try:
            pull_exchange_rate_from_central()
        except Exception:
            pass

# Start background sync thread automatically
sync_thread = threading.Thread(target=background_sync_task)
sync_thread.daemon = True
sync_thread.start()

if __name__ == '__main__':
    init_db()
    import_excel_data()
    app.run(port=5000)
