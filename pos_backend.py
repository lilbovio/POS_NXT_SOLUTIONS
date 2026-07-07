import os
import sqlite3
import pandas as pd
import io
import time
import threading
import requests
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

app = Flask(__name__)
DB_FILE = "pos_database.db"
EXCEL_FILE = "Basededatos_Actualizada.xlsx"

try: 
    from config import STORE_ID, STORE_NAME, CENTRAL_SERVER_URL
except ImportError:
    STORE_ID = "1"
    STORE_NAME = "Aeropuerto"
    CENTRAL_SERVER_URL = "http://localhost:5000"

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
                    FOREIGN KEY(sale_id) REFERENCES sales(id),
                    FOREIGN KEY(product_codigo) REFERENCES products(codigo)
                )''')

    c.execute('''CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )''')

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

@app.route('/')
def index():
    return render_template('index.html')

# Authentication
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    
    if user and check_password_hash(user['password_hash'], password):
        return jsonify({
            "success": True,
            "user": {
                "id": user['id'],
                "username": user['username'],
                "role": user['role'],
                "store": user['store'] or 'Sin tienda'
            }
        })
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
        items = conn.execute("SELECT * FROM sale_items WHERE sale_id = ?", (sale['id'],)).fetchall()
        sale_dict['items'] = [dict(i) for i in items]
        
        if sale_dict.get('store') == 'Sin tienda' or not sale_dict.get('store'):
            sale_dict['store'] = STORE_NAME
            
        payload.append(sale_dict)
        
    conn.close()
    
    try:
        url = f"{CENTRAL_SERVER_URL.rstrip('/')}/api/remote_sync"
        response = requests.post(url, json={"sales": payload}, timeout=10)
        
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
def sync_data():
    count, msg = do_sync_to_central()
    success = count > 0 or "Nada que sincronizar" in msg
    return jsonify({"success": success, "message": msg})

@app.route('/api/remote_sync', methods=['POST'])
def remote_sync():
    data = request.json
    sales = data.get('sales', [])
    if not sales:
        return jsonify({"success": True})
        
    conn = get_db()
    c = conn.cursor()
    
    for sale in sales:
        user_id = sale.get('user_id')
        
        c.execute("""
            INSERT INTO sales (
                user_id, subtotal, discount, total, payment_method, 
                cash_amount, card_amount, timestamp, is_synced, store
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (
            user_id, sale.get('subtotal', 0), sale.get('discount', 0), sale.get('total', 0),
            sale.get('payment_method', 'efectivo'), sale.get('cash_amount', 0), sale.get('card_amount', 0),
            sale.get('timestamp'), sale.get('store', 'Desconocido')
        ))
        
        new_sale_id = c.lastrowid
        
        items = sale.get('items', [])
        for item in items:
            c.execute(
                "INSERT INTO sale_items (sale_id, product_codigo, quantity, subtotal) VALUES (?, ?, ?, ?)",
                (new_sale_id, item.get('product_codigo'), item.get('quantity'), item.get('subtotal'))
            )
            c.execute(
                "UPDATE products SET stock = stock - ? WHERE codigo = ?",
                (item.get('quantity'), item.get('product_codigo'))
            )
            
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# Reload Excel products into inventory
@app.route('/api/admin/reload_products', methods=['POST'])
def reload_products():
    if not os.path.exists(EXCEL_FILE):
        return jsonify({"success": False, "message": f"Archivo de Excel no encontrado: {EXCEL_FILE}"}), 404
    import_excel_data(force=True)
    return jsonify({"success": True, "message": "Inventario recargado desde el archivo Excel."})

# Export sales to Excel
@app.route('/api/admin/export', methods=['GET'])
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
        
        # Query items - safe division handling
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
            # Reorder columns to include Precio_Unitario
            items_df = items_df[['Venta_ID', 'Codigo', 'Descripcion', 'Cantidad', 'Precio_Unitario', 'Subtotal_Item']]
        
        conn.close()
        
        # Write to Excel in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            sales_df.to_excel(writer, sheet_name='Ventas', index=False)
            items_df.to_excel(writer, sheet_name='Articulos Vendidos', index=False)
            
            # Auto-adjust columns widths for 'Ventas'
            worksheet = writer.sheets['Ventas']
            for i, col in enumerate(sales_df.columns):
                if len(sales_df) > 0:
                    max_len = max([len(str(x)) for x in sales_df[col].values] + [len(col)]) + 2
                else:
                    max_len = len(col) + 2
                # openpyxl uses 1-based indexing for columns (A=1)
                column_letter = worksheet.cell(row=1, column=i+1).column_letter
                worksheet.column_dimensions[column_letter].width = min(max_len, 50)
                
        # Auto-adjust columns widths for 'Articulos Vendidos'
            worksheet_items = writer.sheets['Articulos Vendidos']
            for i, col in enumerate(items_df.columns):
                if len(items_df) > 0:
                    max_len = max([len(str(x)) for x in items_df[col].values] + [len(col)]) + 2
                else:
                    max_len = len(col) + 2
                column_letter = worksheet_items.cell(row=1, column=i+1).column_letter
                worksheet_items.column_dimensions[column_letter].width = min(max_len, 50)
                
        output.seek(0)
        
        import os
        import subprocess
        
        filename = f"Ventas_NXT_POS_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        export_dir = os.path.join(os.getcwd(), 'Exportaciones')
        os.makedirs(export_dir, exist_ok=True)
        export_path = os.path.join(export_dir, filename)
        
        # Save to disk
        with open(export_path, 'wb') as f:
            f.write(output.read())
            
        # Try to open the folder and select the file
        try:
            if os.name == 'nt':
                subprocess.run(['explorer', '/select,', os.path.normpath(export_path)])
        except Exception as e:
            print(f"Error opening explorer: {e}")
            
        return jsonify({"success": True, "message": f"Guardado en Exportaciones: {filename}"})
    except Exception as e:
        print(f"Error exporting Excel: {e}")
        return jsonify({"success": False, "message": f"Error al exportar: {str(e)}"}), 500

def background_sync_task():
    while True:
        time.sleep(60)
        try:
            do_sync_to_central()
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
