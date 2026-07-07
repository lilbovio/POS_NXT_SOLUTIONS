# POS NXT Solutions — Critical & High-Impact Improvements Plan

## Top-Level Overview

Fix 8 confirmed bugs and UX issues found by simulating cashier usage of the POS.
All changes are confined to `static/script.js`, `templates/index.html`, `static/style.css`, and `pos_backend.py`.
No new dependencies, no schema changes, no new endpoints (except one small addition for the confirmation modal).

---

## Sub-Tasks

---

### Sub-Task 1 — Stock guard: prevent selling out-of-stock / over-stock items

**Intent**
The backend does `stock - quantity` with no check, so a product with 0 stock goes negative.
The cashier currently has no visual signal about stock in the POS grid either.

**Expected Outcomes**
- Products with `stock = 0` display a "Agotado" pill on their card in the POS grid and cannot be added to the cart (click does nothing / shows toast).
- `addToCart()` warns if total cart quantity for a product would exceed its known stock (soft warning, not hard block, because stock may be stale).
- Backend `register_sale()` checks each item's stock before writing, returns a clear error if any item would go negative.

**Todo List**
1. **Backend** (`pos_backend.py` → `register_sale()`): Before inserting `sale_items`, query each item's current stock. If `stock < quantity`, return `400` with `{"success": false, "message": "Stock insuficiente para: <descripcion>"}`.
2. **Frontend – product card** (`script.js` → `renderProducts()`): Add a CSS class `product-card--out` when `p.stock === 0`. Render a small "Agotado" badge inside the card. Block the `onclick` when `p.stock === 0`.
3. **Frontend – addToCart** (`script.js` → `addToCart()`): After incrementing, if `existing.quantity > product.stock` (and `product.stock > 0`), show a warning toast "Stock disponible: X unidades" but still allow (soft guard).
4. **CSS** (`style.css`): Add `.product-card--out` style — reduced opacity, red border tint, `cursor: not-allowed`.

**Relevant Context**
- [`pos_backend.py:388-401`](pos_backend.py:388) — the item loop that updates stock without checking first
- [`script.js:192-202`](static/script.js:192) — `renderProducts()` card rendering
- [`script.js:208-224`](static/script.js:208) — `addToCart()`

**Status** — [x] done

---

### Sub-Task 2 — Editable quantity input + stock cap in cart

**Intent**
The quantity `<input>` in cart items is `readonly`. A cashier selling 12 bottles must click `+` 12 times.
Additionally, there is no upper-bound check on quantity.

**Expected Outcomes**
- The quantity input in each cart row is editable (type a number directly).
- Typing a number calls `updateCartQuantity()` with the new value.
- Input of `0` or negative removes the item (existing behavior).
- Input above the product's known stock shows a warning toast.

**Todo List**
1. **`renderCart()`** (`script.js:289`): Remove `readonly` from the quantity input. Add `onchange="updateCartQuantity('...', this.value)"` with the proper `escapeAttr` code pattern. Change `type="number"` to include `min="1"`.
2. **`updateCartQuantity()`** (`script.js:226`): Accept a string from the input event, parse it to int. Existing `qty <= 0` removal logic remains. Add a soft stock-warning toast if `qty > product.stock` (look up from `products` array by `codigo`).

**Relevant Context**
- [`script.js:226-237`](static/script.js:226) — `updateCartQuantity()`
- [`script.js:287-292`](static/script.js:287) — quantity input in `renderCart()`

**Status** — [x] done

---

### Sub-Task 3 — Product search: show empty state until user types (no load-all on open)

**Intent**
On POS open, `loadProducts('')` fetches and renders all 755 products at once. On every search keystroke, all 755 are re-fetched and re-rendered. This is a real performance problem with this catalog size.

**Expected Outcomes**
- On POS open, the product grid shows the "Busca un producto para comenzar" empty state — no API call is made.
- Search fires only after the user types at least 1 character (debounce 200 ms unchanged).
- The search bar gets auto-focused when the POS view loads.
- `loadExchangeRate()` is still called on POS open (decoupled from `loadProducts`).

**Todo List**
1. **`loadProducts()`** (`script.js:135`): Add an early return if `query.trim() === ''` — call `renderProducts()` with the existing empty `products = []` and show the "search to begin" state.
2. **`adminGoToPOS()` / login flow** (`script.js:97-107`, `script.js:997`): Replace `loadProducts()` call with `loadExchangeRate()` only; then focus the search input.
3. **`renderProducts()`** (`script.js:183`): The existing empty state message already says "Busca un producto para comenzar" when `products.length === 0` — verify it is shown correctly when `products = []`.
4. **Search listener** (`script.js:158`): Already fires on input — no change needed beyond ensuring the empty-query guard in step 1 is in place.

**Relevant Context**
- [`script.js:135-144`](static/script.js:135) — `loadProducts()`
- [`script.js:97-107`](static/script.js:97) — login → cashier branch
- [`script.js:997-1002`](static/script.js:997) — `adminGoToPOS()`

**Status** — [x] done

---

### Sub-Task 4 — Sale confirmation dialog before submitting

**Intent**
Clicking "Cobrar e Imprimir" immediately fires the sale. A simple confirmation modal with the final total prevents accidental double-processing and gives the cashier one last chance to verify.

**Expected Outcomes**
- Clicking "Cobrar e Imprimir" opens a small native-looking confirmation modal showing: items count, subtotal, discount, **total**, and payment method.
- Modal has two buttons: "Confirmar venta" (proceeds) and "Cancelar" (closes modal, no action).
- Only after clicking "Confirmar venta" does `processSale()` proceed with the fetch.

**Todo List**
1. **`index.html`**: Add a `#confirm-sale-modal` overlay with the summary fields and two buttons. Reuse the existing `.modal-overlay` / `.glass-card` / `.modal-close` CSS classes — no new styles needed.
2. **`script.js` — `processSale()`**: Extract the actual fetch call into a new inner function `doSale()`. Before calling it, populate the confirmation modal fields and show the modal. The "Confirmar venta" button calls `doSale()` and closes the modal.
3. **`script.js`**: Add `closeConfirmModal()` helper (mirrors `closeReceiptModal()`).

**Relevant Context**
- [`script.js:330-549`](static/script.js:330) — `processSale()` — the fetch will move into `doSale()`
- [`templates/index.html:444-532`](templates/index.html:444) — receipt modal as the pattern to follow
- [`script.js:630-633`](static/script.js:630) — `closeReceiptModal()` pattern

**Status** — [x] done

---

### Sub-Task 5 — Fix `Math.max(discountInMxn)` no-op bug

**Intent**
`renderCart()` line 310: `Math.min(Math.max(discountInMxn), total)` — `Math.max()` with a single argument returns the value unchanged. This means a negative discount value is displayed on screen as a negative discount (reducing what the cart shows as the "discount deduction" and inflating the displayed total), confusing the cashier, even though the backend correctly rejects it.

**Expected Outcomes**
- `validDiscount` is clamped to `[0, total]` — never negative, never above subtotal.
- Displayed discount and total in the cart are always visually correct.

**Todo List**
1. **`renderCart()`** (`script.js:309`): Change `Math.min(Math.max(discountInMxn), total)` → `Math.min(Math.max(0, discountInMxn), total)`.

**Relevant Context**
- [`script.js:309-310`](static/script.js:309) — the one-line fix

**Status** — [x] done

---

### Sub-Task 6 — Mixed payment: live "remaining" indicator

**Intent**
When method is "Mixto", the cashier fills cash and card fields but has no running display of how much is still unaccounted for. They only find out on submit if the sum is wrong.

**Expected Outcomes**
- A small label below the card-amount input shows "Pendiente: $X.XX" (or ✓ when balanced), updating live as cash/card fields change.
- Label turns green when remaining = 0, red when remaining ≠ 0.
- This is purely a display aid — no changes to validation logic.

**Todo List**
1. **`index.html`**: Add a `<div id="mixed-remaining">` element inside `#mixed-payment-fields`, below the card-amount input. Initially hidden.
2. **`script.js`**: Add a `updateMixedRemaining()` function that reads the current cart total (from `cart` + discount), cash amount, card amount, currency, and renders the remaining balance into `#mixed-remaining`.
3. Wire `updateMixedRemaining()` to the `input` events of `#cash-amount`, `#card-amount`, and `#cash-currency`, and to `renderCart()` (so it updates when discount changes too).
4. Show/hide `#mixed-remaining` based on whether payment method is "mixto" (already handled by the existing `paymentMethodSelect` listener — just call `updateMixedRemaining()` there too).

**Relevant Context**
- [`templates/index.html:206-226`](templates/index.html:206) — `#mixed-payment-fields`
- [`script.js:1249-1267`](static/script.js:1249) — payment method change listener
- [`script.js:255-325`](static/script.js:255) — `renderCart()` — call `updateMixedRemaining()` at its end

**Status** — [x] done

---

### Sub-Task 7 — Fix login view centering on logout (`block` vs `flex`)

**Intent**
`showView()` sets all views to `display: block`. But `#view-login` relies on `display: flex` (CSS) for vertical centering. After the first logout, the login card renders top-left instead of centered.

**Expected Outcomes**
- The login card is always perfectly centered, including after logout.

**Todo List**
1. **`showView()`** (`script.js:29-40`): When `viewId === 'view-login'`, set `target.style.display = 'flex'` instead of `'block'`.

**Relevant Context**
- [`script.js:29-40`](static/script.js:29) — `showView()`
- [`style.css:208-214`](static/style.css:208) — `#view-login { display: flex; ... }`

**Status** — [x] done

---

### Sub-Task 8 — Error toasts stay visible longer + vendor error scrolls to field

**Intent**
All toasts dismiss in 3 seconds — too fast for multi-word error messages. When vendor is missing, the error toast appears at the bottom-right but the vendor dropdown is at the bottom of the cart panel. The cashier may not see the connection.

**Expected Outcomes**
- Error toasts stay for 5 seconds; success/info toasts remain at 3 seconds.
- When vendor validation fails, the cart panel scrolls to the vendor selector so it's visible.

**Todo List**
1. **`showToast()`** (`script.js:45`): Accept an optional `duration` parameter. Pass `5000` for `type === 'error'`, `3000` otherwise.
2. **`processSale()`** vendor check (`script.js:338`): After highlighting the border, call `vendorSelect.scrollIntoView({ behavior: 'smooth', block: 'center' })`.

**Relevant Context**
- [`script.js:45-60`](static/script.js:45) — `showToast()`
- [`script.js:337-346`](static/script.js:337) — vendor validation block

**Status** — [x] done

---

## Implementation Order

Sub-tasks are independent and can be done one at a time in order:

1. → Sub-Task 5 (1-line bug fix, safest start)
2. → Sub-Task 7 (1-line bug fix)
3. → Sub-Task 3 (performance — no load-all)
4. → Sub-Task 1 (stock guard — backend + frontend)
5. → Sub-Task 2 (editable quantity input)
6. → Sub-Task 6 (mixed payment remaining indicator)
7. → Sub-Task 8 (toast duration + scroll)
8. → Sub-Task 4 (confirmation modal — most HTML involved, last)
