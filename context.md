# POS NXT Solutions — Project Context

> Last updated: after all hardening sessions (exchange rate sync, multi-store audit fixes, auth, DB path, export fix).  
> Use this file to orient a new developer or AI agent on any machine.

---

## Purpose

Point-of-Sale (POS) application for a chain of liquor stores.  
- **Store machines** run a local copy (PyWebView desktop app, Windows).  
- **Central server** runs the same `pos_backend.py` as a headless Flask app on Railway (Linux).  
- Each machine has its own SQLite database; sales are pushed to the central server every 60 s.

---

## Infrastructure Architecture

```
Central Server (Railway — Linux)
  pos_backend.py  ←  receives remote_sync from stores
  pos_database.db     stores all sales from all branches for admin reporting
  config.py           STORE_ID="0", STORE_NAME="Central", SYNC_SECRET="..."

Store Machine A               Store Machine B
  pos_backend.py (EXE)          pos_backend.py (EXE)
  pos_database.db               pos_database.db
  config.py                     config.py
    STORE_ID="1"                  STORE_ID="2"
    STORE_NAME="Guadalajara"      STORE_NAME="Aeropuerto"
    CENTRAL_SERVER_URL="https://web-production-8df97.up.railway.app/"
    SYNC_SECRET="<same secret as central>"
```

**Store identity** is set entirely by `config.py` — swapping that file is all that changes one deployment to another.

---

## File Structure

| File | Role |
|---|---|
| `app.py` | Desktop entry point — starts Flask in a thread, opens PyWebView window |
| `pos_backend.py` | All backend logic: Flask API, SQLite, auth, sync |
| `config.py` | Per-deployment identity: `STORE_ID`, `STORE_NAME`, `CENTRAL_SERVER_URL`, `SYNC_SECRET` |
| `templates/index.html` | Single-page frontend shell |
| `static/script.js` | All frontend JS (POS, admin dashboard, auth) |
| `static/style.css` | Styles |
| `static/logo.jpg` | Brand logo |
| `pos_database.db` | SQLite database (auto-created on first run) |
| `Basededatos_Actualizada.xlsx` | Initial product catalog (dev/seed only) |
| `requirements.txt` | Python dependencies |
| `build.bat` | PyInstaller build script (Windows) |
| `NXT_POS.spec` | PyInstaller spec file |
| `test_backend.py` | Unit/integration test suite (19 tests, all passing) |

---

## config.py — Required Fields

Every deployment must have a `config.py` next to `pos_backend.py`:

```python
STORE_ID = "1"                          # Unique numeric string per store
STORE_NAME = "Nombre Tienda"            # Human name stamped on every sale
CENTRAL_SERVER_URL = "https://..."      # URL of the central Railway server
SYNC_SECRET = "a-strong-random-string"  # MUST match the central server's value
```

If `config.py` is missing, fallback defaults are used (`STORE_NAME="Aeropuerto"`, `localhost:5000`).  
The central server's `config.py` sets `STORE_ID="0"` and can leave `CENTRAL_SERVER_URL` pointing to itself (sync push is skipped if URL is self-referential, but it won't error).

---

## Database Schema

### `users`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | auto |
| username | TEXT UNIQUE | |
| password_hash | TEXT | werkzeug PBKDF2 |
| role | TEXT | `admin`, `cashier`, `almacen` |
| store | TEXT | store name the user belongs to |

### `products`
| Column | Type |
|---|---|
| codigo | TEXT PK |
| descripcion | TEXT |
| precio | REAL |
| stock | INTEGER |

### `sales`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | auto, local to each machine |
| user_id | INTEGER FK | NULL for degustación sales |
| subtotal | REAL | before discount |
| discount | REAL | |
| discount_currency | TEXT | `mxn` or `usd` |
| total | REAL | |
| payment_method | TEXT | `efectivo`, `tarjeta`, `mixto` |
| cash_amount | REAL | |
| cash_currency | TEXT | `mxn` or `usd` |
| card_amount | REAL | |
| timestamp | TEXT | ISO8601 |
| is_synced | INTEGER | 0=pending, 1=pushed to central |
| store | TEXT | store name from user record |
| vendor | TEXT | optional vendor name override |
| source_store | TEXT | set only on central: originating store name |
| source_sale_id | INTEGER | set only on central: original local sale id |

### `sale_items`
| Column | Type |
|---|---|
| id | INTEGER PK |
| sale_id | INTEGER FK |
| product_codigo | TEXT FK |
| quantity | INTEGER |
| subtotal | REAL |

### `config`
| Key | Description |
|---|---|
| `exchange_rate` | USD→MXN rate, default 17.5 |
| `secret_key` | HMAC secret for auth tokens, auto-generated on first boot |

---

## Authentication

### Browser (store/admin users)
- `POST /api/login` returns `{ token, user }`.
- Token format: `username:role:<HMAC-SHA256 sig>` signed with `secret_key` from DB.
- All admin/write endpoints require header `X-Auth-Token: <token>`.
- `authToken` is stored in JS memory; cleared on logout.
- Token is valid for the lifetime of the DB's `secret_key` (persists across server restarts).

### Machine-to-machine (store → central sync)
- `POST /api/remote_sync` requires header `X-Sync-Secret: <SYNC_SECRET>`.
- `SYNC_SECRET` must be the same string in `config.py` on every machine.

### Public endpoints (no auth required)
`GET /`, `GET /api/setup/status`, `POST /api/login`, `GET /api/products`, `POST /api/sales`,  
`GET /api/sales/<id>/items`, `GET /api/receipt/<id>`, `GET /api/exchange-rate`

---

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | — | Renders index.html |
| GET | `/api/setup/status` | — | Returns `needs_setup: true` if no products |
| POST | `/api/login` | — | Returns `{ success, token, user }` |
| GET | `/api/products` | — | List / search products (`?q=`) |
| PUT | `/api/products/<codigo>/stock` | admin, almacen | Update stock |
| PUT | `/api/products/<codigo>` | admin, almacen | Update price/description/stock |
| POST | `/api/sales` | — | Register a sale, decrement stock |
| GET | `/api/sales/<id>/items` | — | Sale line items |
| GET | `/api/receipt/<id>` | — | Full receipt data |
| GET | `/api/exchange-rate` | — | Current USD→MXN rate |
| GET | `/api/admin/income` | admin | Dashboard stats + sales list |
| POST | `/api/admin/exchange-rate` | admin | Update exchange rate |
| GET | `/api/admin/inventory` | admin, almacen | Inventory table with filters |
| POST | `/api/sync` | admin | Manually trigger push to central |
| POST | `/api/remote_sync` | X-Sync-Secret | Central receives sales from a store |
| GET | `/api/setup/upload_products` | — | First-run catalog upload (xlsx) |
| POST | `/api/admin/upload_catalog` | admin | Re-upload/replace product catalog |
| GET | `/api/admin/export` | admin | Export sales to Excel, saves to `Exportaciones/` |

---

## Background Sync (automatic, every 60 s)

`background_sync_task()` runs in a daemon thread on every machine:

1. **`do_sync_to_central()`** — finds all `sales` with `is_synced=0`, POSTs them to  
   `CENTRAL_SERVER_URL/api/remote_sync` with `X-Sync-Secret` header.  
   On success marks them `is_synced=1`. Payload includes `source_store` and `source_sale_id`.

2. **`pull_exchange_rate_from_central()`** — GETs `CENTRAL_SERVER_URL/api/exchange-rate`  
   and writes the result to the local `config` table.  
   This is how an admin's rate change on the central server reaches all stores within ≤60 s.

**Important — `remote_sync` on the central server does NOT decrement product stock.**  
Stock is managed locally on each store machine only. The central DB is reporting-only.

---

## Exchange Rate Flow

1. Admin on central dashboard → `POST /api/admin/exchange-rate` → saves to central DB.  
2. Every store's background thread → `pull_exchange_rate_from_central()` → writes to local DB.  
3. Store cashier's browser → `GET /api/exchange-rate` → reads freshly updated local value.  
Max propagation delay: 60 seconds.

---

## Sale Origin Tracing

When a store pushes to central, every sale row on the central DB stores:
- `source_store` — `STORE_NAME` from the store's `config.py`
- `source_sale_id` — the sale's original `id` on the store's local DB

This allows auditing and deduplication since each store's IDs start from 1.

---

## File Paths (cross-platform safe)

`get_data_dir()` returns:
- **Frozen (PyInstaller EXE):** directory of the executable.
- **Development:** `os.getcwd()`.

Used for:
- `DB_FILE = os.path.join(get_data_dir(), "pos_database.db")`
- `catalog.xlsx`
- `Exportaciones/` (Excel export output folder)

---

## Default Seed Users (testing only — change before production)

| Username | Password | Role | Store |
|---|---|---|---|
| admin | admin123 | admin | Central |
| caja | caja123 | cashier | Tienda Principal |
| caja1 | caja1234 | cashier | Tienda Principal |
| caja2 | caja12345 | cashier | Tienda Principal |
| almacen | almacen123 | almacen | Almacen Central |

> ⚠️ These are seeded by `init_db()` on every deployment. Replace with real users before going live.

---

## Excel Catalog Format

Required columns: `Codigo`, `Descripcion`, and one price column.  
Accepted price column names: `PM`, `Precio venta`, `Precio`, `precio`, `precio venta`, `price`.  
Non-numeric prices are coerced to 0. Force-reload (`/api/admin/upload_catalog`) deletes products not in the file.

---

## Deployment Checklist — Central Server (Railway)

- [ ] `config.py` has `STORE_ID="0"`, correct `STORE_NAME`, `SYNC_SECRET` set to a strong value.
- [ ] `CENTRAL_SERVER_URL` in all store `config.py` files matches the Railway public URL.
- [ ] `SYNC_SECRET` is identical on central and every store machine.
- [ ] Default seed passwords changed or real users added via DB.
- [ ] `pos_database.db` is on a persistent volume (Railway ephemeral filesystem loses it on redeploy).
- [ ] `Exportaciones/` folder — not needed on central (export is a local/admin-only feature).

## Deployment Checklist — Store Machine (Windows EXE)

- [ ] `config.py` next to the EXE: unique `STORE_ID`, `STORE_NAME`, correct `CENTRAL_SERVER_URL`, matching `SYNC_SECRET`.
- [ ] First launch shows the setup screen (upload catalog `.xlsx`).
- [ ] After upload, login with the store's cashier credentials.
- [ ] Admin can log in and verify exchange rate is being pulled from central (check after 60 s).

---

## Build (Windows EXE)

```bat
build.bat
```

Runs PyInstaller `--onedir --windowed`, bundles `templates/`, `static/`, names output `NXT_POS`.  
Output: `dist\NXT_POS\NXT_POS.exe`.  
`config.py` must be placed next to the EXE in `dist\NXT_POS\` before distribution.

---

## Run Locally (development)

```bash
# Desktop mode (PyWebView window)
python app.py

# Headless server mode (same as Railway)
python pos_backend.py
```

---

## Tests

```bash
python -m pytest test_backend.py -v
```

19 tests, all passing. Covers: DB init, login, products, sales (cash/mixed/degustación/invalid),  
receipt, exchange rate (with auth), admin income/inventory, sync endpoints (auth + secret),  
remote_sync (stock not decremented, origin tracing verified), Excel export.
