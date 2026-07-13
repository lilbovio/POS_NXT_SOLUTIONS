# NXT POS — Central Server Contract v2.2

> **Purpose of this file:** Hand this to any AI agent or developer reviewing
> `pos_backend.py` on a store machine. It describes exactly what the central
> server expects to receive, how it processes data, and what the store's sync
> code must send for everything to work correctly end-to-end.

---

## 1. Architecture Overview

```
Store Machine A  ─┐
Store Machine B  ─┼──►  POST /api/remote_sync  ──►  Central Server (Railway)
Store Machine N  ─┘                                   PostgreSQL database
```

- **Hub:** `app.py` on Railway — receives sales, stores them in PostgreSQL,
  exposes admin reporting endpoints.
- **Spokes:** Each store runs `pos_backend.py` as a Windows EXE (PyWebView).
  Every store is identical code; only `config.py` differs between deployments.
- **Sync direction:** One-way. Stores push to central. Central never pushes back.
- **Frequency:** Background thread in `pos_backend.py` calls `do_sync_to_central()`
  every 60 seconds.
- **No stock management on central.** The central DB is reporting-only. Stock is
  managed locally on each store machine.

---

## 2. Environment Variables (Railway)

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | **Yes** | PostgreSQL connection string. Set via `${{Postgres.DATABASE_URL}}` |
| `SECRET_KEY` | Yes | JWT signing key for admin dashboard tokens |
| `SYNC_SECRET` | **Yes** | Shared secret validating store → central sync requests |
| `ADMIN_USERNAME` | No | Dashboard login username (default: `admin`) |
| `ADMIN_PASSWORD` | No | Dashboard login password (default: `admin123`) |

If `DATABASE_URL` is missing the server prints a fatal message on every request
but does not crash. If `SYNC_SECRET` is missing, sync endpoints accept all
requests but log a warning — this is a degraded mode for initial deployment only.

---

## 3. Database Schema (PostgreSQL)

### `stores`

Automatically created/updated on first sync from each store.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | auto |
| `store_key` | TEXT UNIQUE | stable identifier — `STORE_ID` from store's `config.py` if sent, else `STORE_NAME` |
| `name` | TEXT | human display name — `STORE_NAME` from `config.py` |
| `address` | TEXT | default empty |
| `active` | BOOLEAN | default true |
| `last_sync` | TIMESTAMP | updated on every sync |

### `sales`

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | central auto-id |
| `store_key` | TEXT | FK to `stores.store_key` |
| `local_sale_id` | INTEGER | original `id` from the store's SQLite `sales` table |
| `cashier` | TEXT | username of the cashier (empty if store doesn't resolve it) |
| `vendor` | TEXT | optional vendor override (from `sales.vendor` on store) |
| `subtotal` | REAL | before discount |
| `discount` | REAL | discount amount |
| `discount_currency` | TEXT | `mxn` or `usd` |
| `total` | REAL | final charged amount |
| `payment_method` | TEXT | `efectivo`, `tarjeta`, `mixto` |
| `cash_amount` | REAL | |
| `cash_currency` | TEXT | `mxn` or `usd` |
| `card_amount` | REAL | |
| `sale_type` | TEXT | `normal` or `degustacion` — derived server-side (see §5) |
| `timestamp` | TEXT | ISO8601 from store (`2026-06-08T17:22:12`) |
| `synced_at` | TIMESTAMP | when the row arrived at central |
| UNIQUE | | `(store_key, local_sale_id)` — deduplication key |

### `sale_items`

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `sale_id` | INTEGER FK | references `sales(id)` ON DELETE CASCADE |
| `product_codigo` | TEXT | product code |
| `descripcion` | TEXT | product name (empty if store doesn't send it) |
| `quantity` | INTEGER | |
| `subtotal` | REAL | line total |

---

## 4. Authentication

### Admin dashboard (JWT)

- `POST /api/auth/login` with `{ "username": "...", "password": "..." }`.
- Returns `{ "success": true, "token": "<JWT>" }`.
- Token payload: `{ "user": "<username>", "exp": <utcnow + 12h> }`.
- Signed with `SECRET_KEY` env var, algorithm `HS256`.
- All `/api/admin/*` endpoints require header: `Authorization: Bearer <token>`.
- Returns `401` if missing/expired/invalid.

### Store sync (shared secret)

- `POST /api/remote_sync` and `POST /api/receive_sync` require:
  `X-Sync-Secret: <SYNC_SECRET>`
- `SYNC_SECRET` must be identical in:
  - Railway environment variable `SYNC_SECRET`
  - Every store's `config.py` → `SYNC_SECRET = "..."`
- If the header is wrong or missing → `403 {"error": "Sync secret inválido"}`.
- If `SYNC_SECRET` env var is blank → requests pass through with a warning log
  (degraded mode — do not leave blank in production).

---

## 5. Sync Endpoint — What the Store Must Send

### Endpoint

```
POST /api/remote_sync
Headers:
  Content-Type: application/json
  X-Sync-Secret: <SYNC_SECRET>
```

### Payload structure

```json
{
  "sales": [
    {
      "id": 1,
      "user_id": 2,
      "subtotal": 857.14,
      "discount": 57.14,
      "discount_currency": "mxn",
      "total": 800.0,
      "payment_method": "mixto",
      "cash_amount": 400.0,
      "cash_currency": "mxn",
      "card_amount": 400.0,
      "timestamp": "2026-06-08T17:22:12",
      "is_synced": 0,
      "store": "Aeropuerto",
      "store_id": "2",
      "vendor": "Proveedor X",
      "cashier": "caja1",
      "items": [
        {
          "id": 1,
          "sale_id": 1,
          "product_codigo": "ABC123",
          "descripcion": "Ron Bacardi 1L",
          "quantity": 1,
          "subtotal": 857.14
        }
      ]
    }
  ]
}
```

### Field-by-field contract

| Field | Source in store | Required | Notes |
|---|---|---|---|
| `id` | `sales.id` (SQLite auto PK) | **Yes** | Stored as `local_sale_id`. Forms the dedup key with `store_key`. |
| `store` | `STORE_NAME` from `config.py` | **Yes** | Stored as `stores.name`. Used as `store_key` if `store_id` absent. |
| `store_id` | `STORE_ID` from `config.py` | Strongly recommended | Stable numeric key. Prevents duplicate store rows if the store is renamed. Must be added manually to `do_sync_to_central()`. |
| `subtotal` | `sales.subtotal` | Yes | REAL. Coerced to 0 if missing. |
| `discount` | `sales.discount` | Yes | REAL. Coerced to 0 if missing. |
| `discount_currency` | `sales.discount_currency` | Yes | `mxn` or `usd`. Defaults to `mxn`. |
| `total` | `sales.total` | **Yes** | REAL. Final charged amount. |
| `payment_method` | `sales.payment_method` | Yes | `efectivo`, `tarjeta`, `mixto`. Defaults to `efectivo`. |
| `cash_amount` | `sales.cash_amount` | Yes | REAL. |
| `cash_currency` | `sales.cash_currency` | Yes | `mxn` or `usd`. Defaults to `mxn`. |
| `card_amount` | `sales.card_amount` | Yes | REAL. |
| `timestamp` | `sales.timestamp` | **Yes** | ISO8601 string (`YYYY-MM-DDTHH:MM:SS`). Used for date filtering in reports. |
| `cashier` | **Not in sales table** — must be resolved from `users` | Optional | If blank, cashier reports will be empty. See §7.1. |
| `vendor` | `sales.vendor` | Optional | Passed through if present. Stored in `sales.vendor`. |
| `is_synced` | `sales.is_synced` | Ignored | Sent in payload but server ignores it. |
| `user_id` | `sales.user_id` | Ignored | Sent in payload but server ignores it (it's a local FK, meaningless on central). |
| `items` | Array of `sale_items` rows | **Yes** | Embedded in each sale object. |
| `items[].product_codigo` | `sale_items.product_codigo` | Yes | |
| `items[].quantity` | `sale_items.quantity` | Yes | Defaults to 1. |
| `items[].subtotal` | `sale_items.subtotal` | Yes | Line total. |
| `items[].descripcion` | **Not in sale_items table** — must be resolved from `products` | Optional | If blank, item descriptions in reports will be empty. See §7.3. |

### Response

```json
{
  "success": true,
  "inserted": 3,
  "received": 3,
  "errors": []
}
```

| Field | Meaning |
|---|---|
| `success` | Always `true` if the request was accepted (even if some rows errored) |
| `received` | Total sales in the payload |
| `inserted` | Sales actually written (new rows). Duplicates silently skipped. |
| `errors` | List of per-sale error strings (empty on clean sync) |

The store's `do_sync_to_central()` should check for HTTP 200 + `success: true`
before marking rows as `is_synced = 1` in the local SQLite DB.

---

## 6. Server-Side Logic Details

### Store auto-registration

On every sync, the server does an upsert into `stores`:

```sql
INSERT INTO stores (store_key, name, last_sync)
VALUES ($store_key, $store_name, NOW())
ON CONFLICT (store_key) DO UPDATE SET name = EXCLUDED.name, last_sync = NOW()
```

- First sync from a new store → new row created automatically.
- Subsequent syncs → only `name` and `last_sync` are updated.
- `store_key` never changes once set. If `store_id` is used as key, renaming
  the store in `config.py` only updates the display name, not the key.

### Deduplication

```sql
INSERT INTO sales (...) VALUES (...)
ON CONFLICT (store_key, local_sale_id) DO NOTHING
```

Safe to re-send the same batch multiple times. Duplicate sales are silently
skipped and do not increment `inserted`.

### Degustación detection

Computed server-side — no `sale_type` field is required in the payload:

```python
is_degu   = subtotal > 0 and discount >= subtotal
sale_type = "degustacion" if is_degu else "normal"
```

A sale where the discount equals or exceeds the subtotal (i.e. the customer paid
nothing) is classified as a tasting/degustación. These sales are excluded from
all revenue totals in reports.

### Numeric coercion

All numeric fields use `float(sale.get("field") or 0)`. This means:
- Missing fields default to `0`.
- `None` defaults to `0`.
- Empty string `""` defaults to `0`.

---

## 7. Required Changes in `pos_backend.py` (each store)

These are changes the store app **must** implement for full data fidelity.
The central server is already ready to receive all these fields.

### 7.1 — `store_id` in payload (stable store key)

In `do_sync_to_central()`, after building `sale_dict = dict(sale)`, add:

```python
sale_dict['store_id'] = STORE_ID   # imported from config.py
```

Without this, the store is keyed by `STORE_NAME`. If the name ever changes,
a duplicate store entry is created in the central DB and old sales become
separated from the new ones.

### 7.2 — `cashier` in payload (username resolution)

The `sales` table stores `user_id` (integer FK), not the username string.
In `do_sync_to_central()`, after `sale_dict = dict(sale)`, add:

```python
user = conn.execute(
    "SELECT username FROM users WHERE id = ?", (sale['user_id'],)
).fetchone()
sale_dict['cashier'] = user['username'] if user else ''
```

Without this, `cashier` arrives empty at central and the per-cashier dashboard
and monthly cashier reports show no data.

### 7.3 — `descripcion` in item payload (product name)

The `sale_items` table stores only `product_codigo`, not the product name.
In `do_sync_to_central()`, when building each item dict, add:

```python
prod = conn.execute(
    "SELECT descripcion FROM products WHERE codigo = ?",
    (item['product_codigo'],)
).fetchone()
item_dict['descripcion'] = prod['descripcion'] if prod else ''
```

Without this, item descriptions are empty in the central DB. The codes are
still present, so this is cosmetic but affects readability of sale detail views.

### 7.4 — `X-Sync-Secret` header (already in pos_backend.py — verify)

The HTTP call to central must include:

```python
headers = {
    "Content-Type": "application/json",
    "X-Sync-Secret": SYNC_SECRET   # imported from config.py
}
```

`SYNC_SECRET` in every store's `config.py` must equal the `SYNC_SECRET`
environment variable set in Railway. If they differ, the central server returns
`403` and the sync fails silently (store marks nothing as synced).

---

## 8. Admin API Endpoints

All require `Authorization: Bearer <JWT>` from `POST /api/auth/login`.

### `GET /api/admin/dashboard`

Returns aggregated stats for all stores.

```json
{
  "today_total": 12500.0,
  "today_count": 47,
  "grand_total": 980000.0,
  "stores": [
    {
      "store_key": "1",
      "name": "Guadalajara",
      "last_sync": "2026-06-08T17:30:00",
      "total_sales": 1200,
      "total_revenue": 580000.0,
      "degustaciones": 15
    }
  ],
  "cashiers": [
    {
      "cashier": "caja1",
      "total_sales": 430,
      "total_revenue": 210000.0
    }
  ]
}
```

- `today_total` / `today_count` — based on `timestamp LIKE 'YYYY-MM-DD%'`
  (server's local date, not UTC).
- Revenue and counts exclude `sale_type = 'degustacion'`.
- `cashiers` only includes rows where `cashier != ''` (top 20 by revenue).

### `GET /api/admin/sales?store=<key>&month=<YYYY-MM>&limit=<n>`

Returns raw sales rows. All params optional.

- `store` — filter by `store_key` (use the `STORE_ID` string, e.g. `"1"`).
- `month` — filter by `timestamp LIKE 'YYYY-MM%'`.
- `limit` — default 200, max 1000.
- Returns full `sales` row objects ordered by `timestamp DESC`.

### `GET /api/admin/reports/cashier?month=<YYYY-MM>&store=<key>`

Returns per-cashier aggregation for a given month.

```json
{
  "month": "2026-06",
  "cashiers": [
    {
      "cashier": "caja1",
      "store_key": "1",
      "total_sales": 210,
      "total_revenue": 98500.0,
      "degustaciones": 3
    }
  ]
}
```

- `month` defaults to current month.
- `store` optional filter.

### `GET /api/admin/stores`

Returns all rows from the `stores` table ordered by name.

---

## 9. Health Check

`GET /` — no auth required.

```json
{
  "status": "ok",
  "service": "NXT POS Central Server",
  "version": "2.2",
  "database_url_set": true,
  "sync_secret_set": true,
  "database_status": "connected"
}
```

Use this to verify the deployment is live and the DB connection is healthy
before testing sync. `sync_secret_set: false` means the server is running in
degraded (unauthenticated) sync mode and `SYNC_SECRET` must be set in Railway.

---

## 10. Compatibility Alias

`POST /api/receive_sync` — same behaviour as `/api/remote_sync`. Exists for
backwards compatibility with any older version of `pos_backend.py` that called
this path instead. Also handles the legacy format where `items` was a top-level
dict keyed by sale id instead of embedded in each sale:

```json
{
  "sales": [{ "id": 1, ... }],
  "items": { "1": [{ "product_codigo": "ABC", "quantity": 1, "subtotal": 100 }] }
}
```

The server normalises this into the embedded format before processing.

---

## 11. Startup Behaviour

On every cold start (redeploy, restart) the server runs:

1. `init_db()` — `CREATE TABLE IF NOT EXISTS` for `stores`, `sales`,
   `sale_items`. Safe to run on existing DB.
2. `migrate_db()` — `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for every
   column that may be missing from an older schema version. Currently covers:
   `subtotal`, `discount`, `discount_currency`, `payment_method`,
   `cash_amount`, `cash_currency`, `card_amount`, `cashier`, `vendor`.

If `DATABASE_URL` is not set, both calls fail gracefully with a printed error
and the server still starts (all endpoints will return DB errors).

---

## 12. What the Reviewing Agent Should Check in `pos_backend.py`

Cross-reference `do_sync_to_central()` against this document and verify:

| Check | Expected |
|---|---|
| Sync URL | `f"{CENTRAL_SERVER_URL}/api/remote_sync"` |
| HTTP method | `POST` |
| `Content-Type` header | `application/json` |
| `X-Sync-Secret` header | value from `SYNC_SECRET` in `config.py` |
| Payload key | `{ "sales": [...] }` — list under `"sales"` key |
| Each sale is a flat dict | built from `dict(sale)` where `sale` is a SQLite row |
| `store` field | `STORE_NAME` from `config.py` |
| `store_id` field | `STORE_ID` from `config.py` (must be added — see §7.1) |
| `items` embedded | each sale dict contains `"items": [...]` not top-level |
| `cashier` field | resolved from `users` table by `user_id` (see §7.2) |
| `descripcion` in items | resolved from `products` table by `product_codigo` (see §7.3) |
| `is_synced` marked | store sets `is_synced = 1` only after server returns `success: true` |
| Runs every 60 s | background daemon thread, not blocking the main thread |
| Stock NOT modified | `remote_sync` on central must not touch `products.stock` |
| Exchange rate pull | separate call: `GET CENTRAL_SERVER_URL/api/exchange-rate` writes to local `config` table |
