# Excel Upload — First Launch & Admin Reload Plan

## Decisions locked in

1. **File input**: plain `<input type="file">` (no drag-and-drop)
2. **Admin can re-upload**: yes — inventory tab gets an upload button
3. **Setup is uncloseable**: no skip — app stays on setup screen until a valid file is uploaded
4. **Save to disk**: yes — the uploaded file is saved next to the exe as `catalog.xlsx`;
   subsequent launches read from disk and skip the setup screen if the file + products exist

---

## Architecture after this change

```
First launch (products = 0)
  └─► show #view-setup (uncloseable)
      └─► user picks catalog.xlsx
          └─► POST /api/setup/upload_products  (multipart)
              ├─► saves file to disk as catalog.xlsx (next to exe)
              ├─► imports products in memory
              └─► success → frontend shows #view-login

Subsequent launches (products > 0)
  └─► show #view-login immediately (setup is skipped)

Admin "Recargar catálogo" (inventory tab)
  └─► user picks a new .xlsx
      └─► POST /api/admin/upload_catalog  (multipart, force=True)
          ├─► overwrites catalog.xlsx on disk
          └─► re-imports catalog (upsert + delete removed products)
```

---

## Shared helper

Extract a single `import_from_fileobj(fileobj, force=False) -> (int, str)`
function in `pos_backend.py` that both endpoints call. Returns `(imported_count, error_message_or_None)`.

The old `import_excel_data(force)` file-path function is kept only as a thin
wrapper around the new helper for backwards compatibility during transition, then
removed once all callers are migrated.

---

## Sub-Tasks

---

### Sub-Task 1 — Backend helper + `GET /api/setup/status`

**Intent**
Provide a single in-memory import helper that both upload endpoints share,
and a lightweight status check the frontend calls on page load.

**Expected Outcomes**
- `import_from_fileobj(fileobj, force=False)` function exists in `pos_backend.py`
  — reads a BytesIO/file-like object, validates columns, upserts products, returns `(count, None)` or `(0, error_str)`
- `GET /api/setup/status` returns `{ "needs_setup": true }` when products table is empty, `false` otherwise
- `EXCEL_FILE` constant and old `import_excel_data()` are left untouched for now (removed in Sub-Task 3)

**Todo List**
1. Add `import_from_fileobj(fileobj, force=False)` in `pos_backend.py` — move column-detection and upsert logic from `import_excel_data()` into it
2. Add `GET /api/setup/status` endpoint (~8 lines)

**Relevant Context**
- [`pos_backend.py:141-202`](pos_backend.py:141) — `import_excel_data()` — logic to extract into helper
- [`pos_backend.py:23-26`](pos_backend.py:23) — `get_db()` — used in both new functions

**Status** — [ ] pending

---

### Sub-Task 2 — `POST /api/setup/upload_products` endpoint

**Intent**
Accept a multipart `.xlsx` upload on first launch, save the file to disk as
`catalog.xlsx` next to the running exe, then import products using the shared helper.

**Expected Outcomes**
- `POST /api/setup/upload_products` with `multipart/form-data`, field name `file`
- Validates: file present, extension is `.xlsx`
- Saves file to `get_data_dir() / catalog.xlsx` (see path helper below)
- Calls `import_from_fileobj(fileobj, force=False)`
- Returns `{ "success": true, "imported": N }` or `{ "success": false, "message": "..." }`

**Path helper**
```python
def get_data_dir():
    """Returns the writable data directory next to the exe (or cwd in dev)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.getcwd()
```
`catalog.xlsx` is always at `os.path.join(get_data_dir(), 'catalog.xlsx')`.

**Todo List**
1. Add `get_data_dir()` helper to `pos_backend.py` (needs `import sys` — already present via `app.py`; add to `pos_backend.py` imports)
2. Add `POST /api/setup/upload_products` endpoint
3. Update `EXCEL_FILE` constant to use `get_data_dir()` so `reload_products` still works

**Relevant Context**
- [`pos_backend.py:1-14`](pos_backend.py:1) — imports and `EXCEL_FILE` constant
- [`pos_backend.py:690-696`](pos_backend.py:690) — existing `reload_products` endpoint that reads `EXCEL_FILE`

**Status** — [ ] pending

---

### Sub-Task 3 — `POST /api/admin/upload_catalog` endpoint (admin re-upload)

**Intent**
Let the admin upload a new catalog file at any time from the inventory tab.
Uses same helper as setup, with `force=True` to remove products no longer in the new file.

**Expected Outcomes**
- `POST /api/admin/upload_catalog` with `multipart/form-data`, field `file`
- Overwrites `catalog.xlsx` on disk
- Calls `import_from_fileobj(fileobj, force=True)`
- Returns `{ "success": true, "imported": N, "message": "..." }`
- The old `POST /api/admin/reload_products` (file-path based) is removed

**Todo List**
1. Add `POST /api/admin/upload_catalog` endpoint in `pos_backend.py`
2. Remove the old `reload_products` endpoint

**Relevant Context**
- [`pos_backend.py:690-696`](pos_backend.py:690) — old `reload_products` to remove

**Status** — [ ] pending

---

### Sub-Task 4 — Remove Excel from build artifacts + app.py startup call

**Intent**
The Excel is no longer bundled. Remove it from PyInstaller config and from
the startup sequence.

**Expected Outcomes**
- `build.bat` no longer includes `--add-data "Basededatos_Actualizada.xlsx;."`
- `NXT_POS.spec` no longer has the xlsx in `datas`
- `app.py` no longer calls `import_excel_data()` at startup
- `pos_backend.py` removes the old `import_excel_data()` function and `EXCEL_FILE` constant (replaced by `get_data_dir()` path)

**Todo List**
1. Edit `build.bat` — remove the xlsx `--add-data` flag
2. Edit `NXT_POS.spec` — remove xlsx from `datas` list
3. Edit `app.py` — remove `import_excel_data` import and call
4. Edit `pos_backend.py` — remove `import_excel_data()` and `EXCEL_FILE = "..."` constant

**Relevant Context**
- [`build.bat:7`](build.bat:7)
- [`NXT_POS.spec:8`](NXT_POS.spec:8)
- [`app.py:7`](app.py:7), [`app.py:16`](app.py:16)
- [`pos_backend.py:14`](pos_backend.py:14), [`pos_backend.py:141-202`](pos_backend.py:141)

**Status** — [ ] pending

---

### Sub-Task 5 — New HTML view: `#view-setup`

**Intent**
A setup screen shown only on first launch. Uncloseable — no logout or skip.
Same visual language as the login card.

**Expected Outcomes**
- `#view-setup` div exists in `index.html` between `<div id="app">` and `#view-login`
- Contains: logo, title "Configuración Inicial", instruction paragraph,
  `<input type="file" id="setup-file" accept=".xlsx">`, "Importar catálogo" button,
  status message div `#setup-status`
- No close button, no logout link

**Todo List**
1. Add `#view-setup` HTML block in `templates/index.html` as the first child of `#app`
2. Style: reuses `.login-wrapper`, `.glass-card`, `.login-card`, `.btn-primary`, `.error-msg`
   — no new CSS classes needed except a `.file-input-label` wrapper for the file picker styling

**Relevant Context**
- [`templates/index.html:26-59`](templates/index.html:26) — `#view-login` as pattern to follow
- [`static/style.css`](static/style.css) — existing classes to reuse

**Status** — [ ] pending

---

### Sub-Task 6 — Frontend JS: page-load status check + upload handlers

**Intent**
Wire the frontend to the new endpoints: check setup status on load, handle
the first-launch upload, handle the admin re-upload from the inventory tab.

**Expected Outcomes**

**Page load**
- `showView('view-login')` at the bottom of `script.js` is replaced by an async
  `initApp()` call that hits `GET /api/setup/status` first:
  - `needs_setup: true` → `showView('view-setup')`
  - `needs_setup: false` → `showView('view-login')`
- `showView()` extended: `'view-setup'` also gets `display: flex` (same as `'view-login'`)

**Setup upload (`uploadCatalog()`)**
- Reads `#setup-file` input, validates a file is selected
- Builds `FormData`, POSTs to `/api/setup/upload_products`
- Shows spinner on button during upload
- On success: toast "X productos importados ✓", then `showView('view-login')`
- On error: shows `data.message` in `#setup-status` (stays on setup screen)

**Admin re-upload (`uploadAdminCatalog()`)**
- Triggered by a new "Subir nuevo catálogo" button + file input in the inventory tab header
- POSTs to `/api/admin/upload_catalog` with the selected file
- On success: toast, reload inventory
- On error: toast with error message

**Todo List**
1. Replace final `showView('view-login')` with `initApp()` async function
2. Add `uploadCatalog()` function wired to the setup button
3. Extend `showView()` condition to include `'view-setup'`
4. Add `uploadAdminCatalog()` function
5. Add file input + button to inventory tab header in `index.html`
6. Remove any JS reference to the old `reloadProducts()` / `reload_products` endpoint

**Relevant Context**
- [`static/script.js:1`](static/script.js) — bottom of file where `showView('view-login')` lives
- [`static/script.js:29-40`](static/script.js:29) — `showView()` to extend
- [`templates/index.html:399-440`](templates/index.html:399) — inventory tab header

**Status** — [ ] pending

---

## Implementation Order

```
Sub-Task 1  →  Sub-Task 2  →  Sub-Task 3  →  Sub-Task 4
     (backend helper + status)   (setup upload)   (admin upload)   (clean build)
          then
Sub-Task 5  →  Sub-Task 6
   (HTML view)    (JS wiring)
```
