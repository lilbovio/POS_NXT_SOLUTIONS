/* ============================================
   NXT POS - Application Logic
   ============================================ */

let currentUser = null;
let cart = [];
let products = [];
let currentReceiptData = null; // For reprinting
let inventorySortOrder = 'asc';
let storeSalesChart = null;
let cashierSalesChart = null;
let exchangeRate = 17.5;
let isUsdMode = false;

// ============================================
// DOM Elements
// ============================================
const views = document.querySelectorAll('.view');
const loginForm = document.getElementById('login-form');
const productSearch = document.getElementById('product-search');
const productGrid = document.getElementById('product-grid');
const cartItemsEl = document.getElementById('cart-items');
const cartTotal = document.getElementById('cart-total');
const toastContainer = document.getElementById('toast-container');

// ============================================
// View Management
// ============================================
function showView(viewId) {
    views.forEach(v => {
        v.classList.remove('active');
        v.style.display = 'none';
    });
    const target = document.getElementById(viewId);
    target.style.display = 'block';
    // Small delay for CSS transition
    requestAnimationFrame(() => {
        target.classList.add('active');
    });
}

// ============================================
// Toast Notifications (Enhanced)
// ============================================
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    const icons = {
        success: 'fa-circle-check',
        error: 'fa-circle-xmark',
        info: 'fa-circle-info'
    };
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<i class="fa-solid ${icons[type] || icons.info}"></i> ${message}`;
    toastContainer.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('removing');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ============================================
// Authentication
// ============================================
loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    const errorMsg = document.getElementById('login-error');
    errorMsg.textContent = '';

    const btn = document.getElementById('login-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Verificando...';

    try {
        const res = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();

        if (data.success) {
            currentUser = data.user;
            showToast(`Bienvenido, ${currentUser.username}`, 'success');

            if (currentUser.role === 'admin') {
                document.getElementById('admin-user-label').textContent = currentUser.username;
                document.getElementById('nav-dashboard').style.display = 'inline-flex';
                document.getElementById('nav-pos').style.display = 'inline-flex';
                if(document.getElementById('btn-sync')) document.getElementById('btn-sync').style.display = 'inline-flex';
                showView('view-admin');
                loadAdminData();
            } else if (currentUser.role === 'almacen') {
                document.getElementById('admin-user-label').textContent = currentUser.username;
                document.getElementById('nav-dashboard').style.display = 'none';
                document.getElementById('nav-pos').style.display = 'none';
                if(document.getElementById('btn-sync')) document.getElementById('btn-sync').style.display = 'none';
                showView('view-admin');
                switchAdminTab('inventory');
            } else {
                document.getElementById('pos-user-label').textContent = currentUser.username;
                document.getElementById('pos-admin-nav').style.display = 'none';
                showView('view-pos');
                loadProducts();
            }
        } else {
            errorMsg.textContent = data.message;
            showToast('Credenciales inválidas', 'error');
        }
    } catch (err) {
        errorMsg.textContent = "Error de conexión con el servidor";
        showToast('Error de conexión', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-right-to-bracket"></i> Ingresar';
    }
});

function logout() {
    currentUser = null;
    cart = [];
    document.getElementById('username').value = '';
    document.getElementById('password').value = '';
    document.getElementById('login-error').textContent = '';
    document.getElementById('pos-admin-nav').style.display = 'none';
    showView('view-login');
    showToast('Sesión cerrada', 'info');
}

// ============================================
// POS - Products
// ============================================
async function loadProducts(query = '') {
    try {
        await loadExchangeRate();
        const res = await fetch(`/api/products?q=${encodeURIComponent(query)}`);
        products = await res.json();
        renderProducts();
    } catch (err) {
        showToast('Error al cargar productos', 'error');
    }
}

async function loadExchangeRate() {
    try {
        const res = await fetch('/api/exchange-rate');
        const data = await res.json();
        exchangeRate = data.exchange_rate || exchangeRate;
        updateCurrencyToggleButton();
    } catch (err) {
        console.warn('No se pudo cargar el tipo de cambio', err);
    }
}

let searchTimeout;
productSearch.addEventListener('input', (e) => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
        loadProducts(e.target.value);
    }, 200);
});

// Keyboard shortcut for search (Ctrl+K)
document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'k') {
        e.preventDefault();
        if (productSearch) {
            productSearch.focus();
            productSearch.select();
        }
    }
});

function renderProducts() {
    productGrid.innerHTML = '';

    // Update count badge
    const countBadge = document.getElementById('product-count-badge');
    if (countBadge) countBadge.textContent = `${products.length} productos`;

    if (products.length === 0) {
        productGrid.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-box-open"></i>
                <p>No se encontraron productos</p>
            </div>`;
        return;
    }

    products.forEach(p => {
        const card = document.createElement('div');
        card.className = 'product-card';
        card.innerHTML = `
            <div class="product-code">${escapeHtml(p.codigo)}</div>
            <div class="product-name">${escapeHtml(p.descripcion)}</div>
            <div class="product-price">${getDisplayedPrice(p.precio)}</div>
        `;
        card.onclick = () => addToCart(p);
        productGrid.appendChild(card);
    });
}

// ============================================
// POS - Cart
// ============================================
function addToCart(product) {
    const existing = cart.find(item => item.codigo === product.codigo);
    if (existing) {
        existing.quantity += 1;
        existing.subtotal = existing.quantity * existing.precio;
    } else {
        cart.push({
            codigo: product.codigo,
            descripcion: product.descripcion,
            precio: product.precio,
            quantity: 1,
            subtotal: product.precio
        });
    }
    renderCart();
    showToast(`${product.descripcion} agregado`, 'success');
}

function updateCartQuantity(codigo, qty) {
    const item = cart.find(i => i.codigo === codigo);
    if (item) {
        if (qty <= 0) {
            cart = cart.filter(i => i.codigo !== codigo);
        } else {
            item.quantity = qty;
            item.subtotal = item.quantity * item.precio;
        }
        renderCart();
    }
}

function clearCart() {
    if (cart.length === 0) return;
    cart = [];
    document.getElementById('sale-discount').value = 0;
    document.getElementById('discount-currency').value = 'mxn';
    document.getElementById('cash-amount').value = 0;
    document.getElementById('cash-currency').value = 'mxn';
    document.getElementById('card-amount').value = 0;
    document.getElementById('payment-method').value = 'efectivo';
    document.getElementById('mixed-payment-fields').style.display = 'none';
    renderCart();
    showToast('Carrito limpiado', 'info');
}

function renderCart() {
    cartItemsEl.innerHTML = '';
    let total = 0;
    let itemCount = 0;

    // Update cart count badge
    const cartCountBadge = document.getElementById('cart-count');
    cartCountBadge.textContent = cart.length;

    if (cart.length === 0) {
        cartItemsEl.innerHTML = `
            <div class="cart-empty-state" id="cart-empty">
                <i class="fa-solid fa-cart-shopping"></i>
                <p>Carrito vacío</p>
                <span>Selecciona productos para agregar</span>
            </div>`;
        cartTotal.textContent = getDisplayedPrice(0);
        document.getElementById('cart-item-count').textContent = '0';
        return;
    }

    cart.forEach(item => {
        total += item.subtotal;
        itemCount += item.quantity;
        const div = document.createElement('div');
        div.className = 'cart-item';
        div.innerHTML = `
            <div class="cart-item-info">
                <h4>${escapeHtml(item.descripcion)}</h4>
                <span class="cart-item-price">${getDisplayedPrice(item.precio)} c/u</span>
                <div class="cart-item-subtotal">${getDisplayedPrice(item.subtotal)}</div>
            </div>
            <div class="cart-controls">
                <button class="btn btn-icon" onclick="updateCartQuantity('${escapeAttr(item.codigo)}', ${item.quantity - 1})"><i class="fa-solid fa-minus"></i></button>
                <input type="number" value="${item.quantity}" readonly>
                <button class="btn btn-icon" onclick="updateCartQuantity('${escapeAttr(item.codigo)}', ${item.quantity + 1})"><i class="fa-solid fa-plus"></i></button>
                <button class="btn btn-icon" onclick="updateCartQuantity('${escapeAttr(item.codigo)}', 0)" style="color:var(--danger);"><i class="fa-solid fa-trash"></i></button>
            </div>
        `;
        cartItemsEl.appendChild(div);
    });

    const discountValue =
        parseFloat(document.getElementById('sale-discount')?.value) || 0;
    
    const discountCurrency = 
        document.getElementById('discount-currency')?.value || 'mxn';
    
    // Convert discount to MXN if it's in USD
    let discountInMxn = discountValue;
    if (discountCurrency === 'usd') {
        discountInMxn = discountValue * exchangeRate;
    }

    const validDiscount =
        Math.min(Math.max(discountInMxn), total);

    const finalTotal = total - validDiscount;

    document.getElementById('cart-subtotal').textContent =
        getDisplayedPrice(total);

    document.getElementById('cart-discount').textContent =
        '-' + getDisplayedPrice(validDiscount);

    cartTotal.textContent =
        getDisplayedPrice(finalTotal);

    document.getElementById('cart-item-count').textContent =
        itemCount;
}

// ============================================
// POS - Sale Processing
// ============================================
async function processSale() {
    if (cart.length === 0) {
        showToast("El carrito está vacío", 'error');
        return;
    }
    const subtotal = cart.reduce((sum, item) => sum + item.subtotal, 0);

    const discountValue =
        parseFloat(document.getElementById('sale-discount')?.value) || 0;
    
    const discountCurrency = 
        document.getElementById('discount-currency')?.value || 'mxn';

    // Convert discount to MXN if it's in USD
    let discountInMxn = discountValue;
    if (discountCurrency === 'usd') {
        discountInMxn = discountValue * exchangeRate;
    }

    if (discountInMxn < 0) {
        showToast("El descuento no puede ser negativo", 'error');
        return;
    }

    if (discountInMxn > subtotal) {
        showToast("El descuento no puede ser mayor al total", 'error');
        return;
    }

    if (discountInMxn == subtotal && subtotal > 0) {
        showToast("El descuento es del 100%, la botella se registrará para degustación", "info");
    }

    const total = subtotal - discountInMxn;

    const paymentMethod =
        document.getElementById('payment-method')?.value || 'efectivo';

    let cashAmount =
        parseFloat(document.getElementById('cash-amount')?.value) || 0;
    
    const cashCurrency = 
        document.getElementById('cash-currency')?.value || 'mxn';

    let cardAmount =
        parseFloat(document.getElementById('card-amount')?.value) || 0;

    // Convert cash amount to MXN if it's in USD
    let cashAmountInMxn = cashAmount;
    if (cashCurrency === 'usd') {
        cashAmountInMxn = cashAmount * exchangeRate;
    }

    if (paymentMethod === 'efectivo') {
        cashAmountInMxn = total;
        cardAmount = 0;
    }

    if (paymentMethod === 'tarjeta') {
        cashAmountInMxn = 0;
        cardAmount = total;
    }

    if (paymentMethod === 'mixto') {
        if (cashAmountInMxn < 0 || cardAmount < 0) {
            showToast("Los montos de pago no pueden ser negativos", 'error');
            return;
        }

        // When in USD mode, validate USD amounts directly to avoid floating point errors
        // When in MXN mode, validate MXN amounts after conversion
        let isValidPayment = false;
        
        if (isUsdMode) {
            // In USD mode, compare the USD amounts (no conversion)
            let totalUsd = total / exchangeRate;
            let cashUsd = cashAmount;
            let cardUsd = cardAmount;
            
            console.log('DEBUG - Mixed Payment Validation (USD Mode):');
            console.log('totalUsd:', totalUsd);
            console.log('cashUsd:', cashUsd);
            console.log('cardUsd:', cardUsd);
            console.log('Sum:', cashUsd + cardUsd);
            
            // Use tolerance for floating point comparison
            const sumUsd = roundMoney(cashUsd + cardUsd);
            const totalUsdRounded = roundMoney(totalUsd);
            const usdTolerance = 0.1;
            const matchUsd = Math.abs(sumUsd - totalUsdRounded) <= usdTolerance;
            
            console.log('Match:', matchUsd);
            
            isValidPayment = matchUsd;
        } else {
            // In MXN mode, compare the MXN amounts
            let cardAmountInMxn = cardAmount;
            
            console.log('DEBUG - Mixed Payment Validation (MXN Mode):');
            console.log('total:', total);
            console.log('cashAmountInMxn:', cashAmountInMxn);
            console.log('cardAmountInMxn:', cardAmountInMxn);
            console.log('Sum:', cashAmountInMxn + cardAmountInMxn);
            
            // Use tolerance for floating point comparison
            const sumMxn = roundMoney(cashAmountInMxn + cardAmountInMxn);
            const totalMxn = roundMoney(total);
            const mxnTolerance = 1.00;
            const matchMxn = Math.abs(sumMxn - totalMxn) <= mxnTolerance;
            
            console.log('Match:', matchMxn);
            
            isValidPayment = matchMxn;
        }

        if (!isValidPayment) {
            console.error('Validation FAILED - Mixed payment sum does not equal total');
            showToast("La suma del pago mixto debe ser igual al total con descuento", 'error');
            return;
        }
    }
    const btn = document.getElementById('btn-checkout');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Procesando...';

    // Ensure card amount is converted to MXN for backend
    let finalCardAmount = cardAmount;
    if (isUsdMode) {
        finalCardAmount = cardAmount * exchangeRate;
    }

    try {
        const res = await fetch('/api/sales', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: currentUser.id,
                items: cart,
                discount: discountInMxn,
                discount_currency: discountCurrency,
                payment_method: paymentMethod,
                cash_amount: cashAmountInMxn,
                cash_currency: cashCurrency,
                card_amount: finalCardAmount,
                exchange_rate: exchangeRate
            })
        });
        const data = await res.json();

        if (data.success) {
            showToast(`Venta #${data.sale_id} registrada ✓`, 'success');

            const totalUsd = total / exchangeRate;

            // Build receipt data
            const receiptData = {
                sale_id: data.sale_id,
                timestamp: data.timestamp,
                cashier: currentUser.username,

                items: [...cart],

                subtotal: subtotal,
                discount: discountInMxn,
                discount_currency: discountCurrency,

                total: total,

                payment_method: paymentMethod,
                cash_amount: cashAmountInMxn,
                cash_currency: cashCurrency,
                card_amount: cardAmount,

                total_usd: parseFloat(totalUsd.toFixed(2)),
                exchange_rate: exchangeRate
            };

            // Show digital receipt modal
            showReceiptModal(receiptData);

            // Also prepare the hidden print receipt
            fillPrintReceipt(receiptData);

            // Store for reprinting
            currentReceiptData = receiptData;

            // Clear cart after processing
            cart = [];
            renderCart();
            document.getElementById('sale-discount').value = 0;
            document.getElementById('discount-currency').value = 'mxn';
            document.getElementById('cash-amount').value = 0;
            document.getElementById('cash-currency').value = 'mxn';
            document.getElementById('card-amount').value = 0;
            renderCart();
        } else {
            showToast("Error: " + data.message, 'error');
        }
    } catch (err) {
        showToast("Error al registrar la venta", 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-check-circle"></i> Cobrar e Imprimir';
    }
}

// ============================================
// Receipt System (Digital + Print)
// ============================================
function showReceiptModal(data) {
    const rate = data.exchange_rate || exchangeRate;
    const isDegustacion = (data.discount === data.subtotal && data.subtotal > 0);
    document.getElementById('modal-receipt-degustacion').style.display = isDegustacion ? 'block' : 'none';

    document.getElementById('modal-receipt-id').textContent = data.sale_id;
    document.getElementById('modal-receipt-date').textContent = formatDate(data.timestamp);
    document.getElementById('modal-receipt-cashier').textContent = data.cashier || 'N/A';
    document.getElementById('modal-receipt-subtotal').textContent = `$${formatNumber(data.subtotal || data.total)}`;
    
    // Show discount with currency
    const discountCurrency = data.discount_currency ? data.discount_currency.toUpperCase() : 'MXN';
    document.getElementById('modal-receipt-discount').textContent = `-$${formatNumber(data.discount || 0)} ${discountCurrency}`;

    const isModalPaidInUsd = data.cash_currency === 'usd' || (data.payment_method === 'mixto' && data.cash_currency === 'usd');
    const modalUsdTotal = data.total_usd || (data.total / rate || 0);

    if (isModalPaidInUsd) {
        document.getElementById('modal-receipt-total').textContent = `US$ ${formatNumber(modalUsdTotal)}`;
        document.getElementById('modal-receipt-total-usd').textContent = `$${formatNumber(data.total)}`;
    } else {
        document.getElementById('modal-receipt-total').textContent = `$${formatNumber(data.total)}`;
        document.getElementById('modal-receipt-total-usd').textContent = `US$ ${formatNumber(modalUsdTotal)}`;
    }

    document.getElementById('modal-receipt-payment').textContent = data.payment_method || 'efectivo';
    const modalPaymentDetails = document.getElementById('modal-payment-details');

    if (data.payment_method === 'mixto') {
        // Show cash currency
        const cashCurrency = data.cash_currency ? data.cash_currency.toUpperCase() : 'MXN';
        modalPaymentDetails.innerHTML = `
        <div class="receipt-total-digital">
            <span>EFECTIVO (${cashCurrency})</span>
            <span>$${formatNumber(data.cash_amount || 0)}</span>
        </div>

        <div class="receipt-total-digital">
            <span>TARJETA</span>
            <span>$${formatNumber(data.card_amount || 0)}</span>
        </div>
    `;
    } else {
        modalPaymentDetails.innerHTML = '';
    }
    document.getElementById('modal-exchange-rate').textContent = `1 USD = $${formatNumber(rate)} MXN`;
    const modalUsdRow = document.getElementById('modal-receipt-total-usd').parentElement;
    modalUsdRow.style.display = 'flex';

    if (isModalPaidInUsd) {
        document.getElementById('modal-receipt-total-usd').textContent = `$${formatNumber(data.total)}`;
    } else {
        document.getElementById('modal-receipt-total-usd').textContent = `US$ ${formatNumber(modalUsdTotal)}`;
    }

    const tbody = document.getElementById('modal-receipt-items');
    tbody.innerHTML = '';

    data.items.forEach(item => {
        const tr = document.createElement('tr');
        const unitPrice = item.unit_price || item.precio || (item.subtotal / item.quantity);
        tr.innerHTML = `
            <td>${item.quantity}</td>
            <td>${escapeHtml(item.descripcion)}</td>
            <td>$${formatNumber(unitPrice)}</td>
            <td>$${formatNumber(item.subtotal)}</td>
        `;
        tbody.appendChild(tr);
    });

    // Store for printing
    currentReceiptData = data;

    document.getElementById('receipt-modal').classList.add('active');
}

function closeReceiptModal() {
    document.getElementById('receipt-modal').classList.remove('active');
}

function fillPrintReceipt(data) {
    const rate = data.exchange_rate || exchangeRate;
    const isDegustacion = (data.discount === data.subtotal && data.subtotal > 0);
    document.getElementById('receipt-degustacion').style.display = isDegustacion ? 'block' : 'none';

    document.getElementById('receipt-id').textContent = data.sale_id;
    document.getElementById('receipt-date').textContent = formatDate(data.timestamp);
    document.getElementById('receipt-cashier').textContent = data.cashier || currentUser?.username || 'N/A';
    document.getElementById('receipt-subtotal').textContent = formatNumber(data.subtotal || data.total);
    
    // Show discount with currency
    const discountCurrency = data.discount_currency ? data.discount_currency.toUpperCase() : 'MXN';
    document.getElementById('receipt-discount').textContent = '-' + formatNumber(data.discount || 0) + ' ' + discountCurrency;

    const isReceiptPaidInUsd = data.cash_currency === 'usd' || (data.payment_method === 'mixto' && data.cash_currency === 'usd');
    const usdTotalValue = data.total_usd || (data.total / rate || 0);

    if (isReceiptPaidInUsd) {
        document.getElementById('receipt-total-label').textContent = 'Total USD:';
        document.getElementById('receipt-total-amount').textContent = `US$ ${formatNumber(usdTotalValue)}`;
        document.getElementById('receipt-secondary-label').textContent = 'Total MXN:';
        document.getElementById('receipt-total-usd').textContent = `$${formatNumber(data.total)}`;
    } else {
        document.getElementById('receipt-total-label').textContent = 'Total:';
        document.getElementById('receipt-total-amount').textContent = `$${formatNumber(data.total)}`;
        document.getElementById('receipt-secondary-label').textContent = 'Total USD:';
        document.getElementById('receipt-total-usd').textContent = `US$ ${formatNumber(usdTotalValue)}`;
    }
    
    const paymentInfo = document.getElementById('receipt-payment-details');

    if (data.payment_method === 'mixto') {
        // Show cash currency
        const cashCurrency = data.cash_currency ? data.cash_currency.toUpperCase() : 'MXN';
        paymentInfo.innerHTML = `
        <p>Método: Mixto</p>
        <p>Efectivo (${cashCurrency}): $${formatNumber(data.cash_amount || 0)}</p>
        <p>Tarjeta: $${formatNumber(data.card_amount || 0)}</p>
    `;
    } else {
        paymentInfo.innerHTML = `
        <p>Método: ${data.payment_method || 'efectivo'}</p>
    `;
    }
    document.getElementById('receipt-exchange-rate').textContent = `1 USD = $${formatNumber(rate)} MXN`;
    const printUsdRow = document.getElementById('receipt-total-usd').parentElement;
    printUsdRow.style.display = 'block';

    if (isReceiptPaidInUsd) {
        document.getElementById('receipt-total-usd').textContent = `$${formatNumber(data.total)}`;
    } else {
        document.getElementById('receipt-total-usd').textContent = `US$ ${formatNumber(usdTotalValue)}`;
    }

    const tbody = document.getElementById('receipt-items');
    tbody.innerHTML = '';

    data.items.forEach(item => {
        const unitPrice = item.unit_price || item.precio || (item.subtotal / item.quantity);
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${item.quantity}</td>
            <td>${escapeHtml(item.descripcion)}</td>
            <td>$${formatNumber(unitPrice)}</td>
            <td>$${formatNumber(item.subtotal)}</td>
        `;
        tbody.appendChild(tr);
    });
}

function printCurrentReceipt() {
    if (currentReceiptData) {
        fillPrintReceipt(currentReceiptData);
    }
    window.print();
}

// Load and show receipt from server (for admin history)
async function viewReceipt(saleId) {
    try {
        const res = await fetch(`/api/receipt/${saleId}`);
        const data = await res.json();

        if (data.success) {
            const r = data.receipt;
            const usdTotal = (r.total || 0) / exchangeRate;
            showReceiptModal({
                sale_id: r.sale_id,
                timestamp: r.timestamp,
                cashier: r.cashier,
                subtotal: r.subtotal || r.total,
                discount: r.discount || 0,
                discount_currency: r.discount_currency || 'mxn',
                total: r.total,
                payment_method: r.payment_method,
                cash_amount: r.cash_amount || 0,
                cash_currency: r.cash_currency || 'mxn',
                card_amount: r.card_amount || 0,
                total_usd: parseFloat(usdTotal.toFixed(2)),
                exchange_rate: exchangeRate,
                items: r.items
            });
        } else {
            showToast('No se pudo cargar el recibo', 'error');
        }
    } catch (err) {
        showToast('Error al cargar recibo', 'error');
    }
}

// ============================================
// Admin - Dashboard
// ============================================
async function loadAdminData() {
    try {
        const res = await fetch('/api/admin/income');
        const data = await res.json();

        exchangeRate = data.exchange_rate || exchangeRate;
        document.getElementById('today-income').textContent = `$${formatNumber(data.today_total)}`;
        document.getElementById('all-time-income').textContent = `$${formatNumber(data.all_time_total)}`;
        document.getElementById('total-sales-count').textContent = data.total_sales_count;
        document.getElementById('unsynced-count').textContent = data.unsynced_count;
        document.getElementById('all-time-income-usd').textContent = `US$ ${formatNumber(data.all_time_total_usd)}`;
        document.getElementById('today-income-usd').textContent = `US$ ${formatNumber(data.today_total_usd)}`;
        document.getElementById('all-time-income-usd-small').textContent = `US$ ${formatNumber(data.all_time_total_usd)}`;
        document.getElementById('exchange-rate-value').textContent = formatNumber(exchangeRate);
        document.getElementById('exchange-rate-input').value = exchangeRate;
        updateCurrencyToggleButton();

        const tbody = document.getElementById('sales-table-body');
        tbody.innerHTML = '';

        if (!data.sales || data.sales.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" style="text-align:center; color:var(--text-muted); padding:2rem;">
                        No hay ventas registradas
                    </td>
                </tr>`;
            renderSalesCharts(data.store_sales || [], data.cashier_sales || []);
            return;
        }

        data.sales.forEach(s => {
            const tr = document.createElement('tr');
            const syncBadge = s.is_synced
                ? '<span class="badge synced"><i class="fa-solid fa-check"></i> Sincronizado</span>'
                : '<span class="badge pending"><i class="fa-solid fa-clock"></i> Pendiente</span>';
            tr.innerHTML = `
                <td><strong>#${s.id}</strong></td>
                <td>${escapeHtml(s.username || 'Desconocido')}</td>
                <td>${escapeHtml(s.store || 'Sin tienda')}</td>
                <td>${formatDate(s.timestamp)}</td>
                <td><strong>$${formatNumber(s.total)}</strong></td>
                <td><strong>US$ ${formatNumber(s.total_usd)}</strong></td>
                <td>${syncBadge}</td>
                <td>
                    <button class="btn btn-sm btn-ghost" onclick="viewReceipt(${s.id})" title="Ver recibo">
                        <i class="fa-solid fa-receipt"></i> Ver
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });

        renderSalesCharts(data.store_sales || [], data.cashier_sales || []);
    } catch (err) {
        showToast("Error al cargar datos de administración", 'error');
    }
}

async function saveExchangeRate() {
    const input = document.getElementById('exchange-rate-input');
    const btn = document.getElementById('save-exchange-rate-btn');
    const value = parseFloat(input.value);

    if (!value || value <= 0) {
        showToast('Ingrese un tipo de cambio válido', 'error');
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Guardando...';

    try {
        const res = await fetch('/api/admin/exchange-rate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ exchange_rate: value })
        });
        const data = await res.json();

        if (data.success) {
            exchangeRate = data.exchange_rate;
            document.getElementById('exchange-rate-value').textContent = formatNumber(exchangeRate);
            updateCurrencyToggleButton();
            loadAdminData();
            showToast('Tipo de cambio actualizado', 'success');
        } else {
            showToast(data.message || 'Error al actualizar el tipo de cambio', 'error');
        }
    } catch (err) {
        showToast('Error al actualizar el tipo de cambio', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> Guardar';
    }
}

function toggleCurrencyMode() {
    isUsdMode = !isUsdMode;
    updateCurrencyToggleButton();
    renderProducts();
    renderCart();
}

function renderSalesCharts(storeSales, cashierSales) {
    const storeList = document.getElementById('store-sales-list');
    const storeCtx = document.getElementById('store-sales-chart');
    const cashierList = document.getElementById('cashier-sales-list');
    const cashierCtx = document.getElementById('cashier-sales-chart');

    storeList.innerHTML = '';
    cashierList.innerHTML = '';

    // Handle Store Sales
    if (!storeSales || storeSales.length === 0) {
        storeList.innerHTML = '<p class="empty-state">No hay ventas por tienda</p>';
        if (storeSalesChart) storeSalesChart.destroy();
        storeSalesChart = null;
    } else {
        storeSales.forEach(store => {
            const row = document.createElement('div');
            row.className = 'store-sales-row';
            row.innerHTML = `
                <div class="store-sales-name">${escapeHtml(store.store || 'Sin tienda')}</div>
                <div class="store-sales-value">$${formatNumber(store.total)}</div>
                <div class="store-sales-meta">${store.sales_count} venta${store.sales_count === 1 ? '' : 's'}</div>
            `;
            storeList.appendChild(row);
        });

        const storeLabels = storeSales.map(item => item.store || 'Sin tienda');
        const storeTotals = storeSales.map(item => item.total);

        if (storeSalesChart) storeSalesChart.destroy();
        storeSalesChart = new Chart(storeCtx, {
            type: 'bar',
            data: {
                labels: storeLabels,
                datasets: [{
                    label: 'Ventas ($)',
                    data: storeTotals,
                    backgroundColor: '#6366f1',
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: { beginAtZero: true }
                }
            }
        });
    }

    // Handle Cashier Sales
    if (!cashierSales || cashierSales.length === 0) {
        cashierList.innerHTML = '<p class="empty-state">No hay ventas por cajero</p>';
        if (cashierSalesChart) cashierSalesChart.destroy();
        cashierSalesChart = null;
    } else {
        cashierSales.forEach(c => {
            const row = document.createElement('div');
            row.className = 'store-sales-row';
            row.innerHTML = `
                <div class="store-sales-name">${escapeHtml(c.cashier || 'Sin cajero')}</div>
                <div class="store-sales-value">$${formatNumber(c.total)}</div>
                <div class="store-sales-meta">${c.sales_count} venta${c.sales_count === 1 ? '' : 's'}</div>
            `;
            cashierList.appendChild(row);
        });

        const cashierLabels = cashierSales.map(item => item.cashier || 'Sin cajero');
        const cashierTotals = cashierSales.map(item => item.total);

        if (cashierSalesChart) cashierSalesChart.destroy();
        cashierSalesChart = new Chart(cashierCtx, {
            type: 'bar',
            data: {
                labels: cashierLabels,
                datasets: [{
                    label: 'Ventas ($)',
                    data: cashierTotals,
                    backgroundColor: '#22c55e',
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: { beginAtZero: true }
                }
            }
        });
    }
}

// ============================================
// Export to Excel
// ============================================
function exportToExcel() {
    const btn = document.querySelector('button[onclick="exportToExcel()"]');
    const originalHtml = btn.innerHTML;
    btn.innerHTML = '<span class="spinner"></span> Generando...';
    btn.disabled = true;

    fetch('/api/admin/export')
        .then(response => {
            if (!response.ok) throw new Error('Error en el servidor');
            return response.blob();
        })
        .then(blob => {
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            // Generate simple filename
            const dateStr = new Date().toISOString().slice(0,10).replace(/-/g, "");
            a.download = `Ventas_NXT_POS_${dateStr}.xlsx`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
            showToast('Descarga iniciada', 'success');
        })
        .catch(err => {
            console.error(err);
            showToast('Error al exportar a Excel', 'error');
        })
        .finally(() => {
            btn.innerHTML = originalHtml;
            btn.disabled = false;
        });
}

// ============================================
// Admin - Tabs
// ============================================
function switchAdminTab(tab) {
    // Update nav buttons
    document.querySelectorAll('.btn-nav').forEach(btn => btn.classList.remove('active'));
    document.getElementById(`nav-${tab}`).classList.add('active');

    // Update tab content
    document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
    document.getElementById(`tab-${tab}`).classList.add('active');

    // Load data
    if (tab === 'dashboard') {
        loadAdminData();
    } else if (tab === 'inventory') {
        loadInventory();
    }
}

function adminGoToPOS() {
    document.getElementById('pos-user-label').textContent = currentUser.username;
    document.getElementById('pos-admin-nav').style.display = 'flex';
    showView('view-pos');
    loadProducts();
}

function showAdminView() {
    document.getElementById('pos-admin-nav').style.display = 'none';
    showView('view-admin');
}

function adminNavigateFromPOS(tab) {
    showAdminView();
    switchAdminTab(tab);
}

// ============================================
// Admin - Inventory
// ============================================
async function loadInventory(query = '') {
    const sort = document.getElementById('inventory-sort')?.value || 'descripcion';

    try {
        const res = await fetch(`/api/admin/inventory?q=${encodeURIComponent(query)}&sort=${sort}&order=${inventorySortOrder}`);
        const data = await res.json();
        renderInventory(data);
    } catch (err) {
        showToast('Error al cargar inventario', 'error');
    }
}

function renderInventory(products) {
    const tbody = document.getElementById('inventory-table-body');
    const countBadge = document.getElementById('inventory-count-badge');

    if (countBadge) countBadge.textContent = `${products.length} productos`;
    tbody.innerHTML = '';

    if (products.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" style="text-align:center; color:var(--text-muted); padding:2rem;">
                    No se encontraron productos
                </td>
            </tr>`;
        return;
    }

    products.forEach(p => {
        const tr = document.createElement('tr');
        let stockBadge;
        if (p.stock <= 0) {
            stockBadge = '<span class="badge stock-out"><i class="fa-solid fa-xmark"></i> Agotado</span>';
        } else if (p.stock <= 10) {
            stockBadge = '<span class="badge stock-low"><i class="fa-solid fa-triangle-exclamation"></i> Bajo</span>';
        } else {
            stockBadge = '<span class="badge stock-ok"><i class="fa-solid fa-check"></i> OK</span>';
        }

        tr.innerHTML = `
            <td><strong>${escapeHtml(p.codigo)}</strong></td>
            <td>${escapeHtml(p.descripcion)}</td>
            <td>$${formatNumber(p.precio)}</td>
            <td>${p.stock}</td>
            <td>${stockBadge}</td>
            <td>
                <button class="btn btn-sm btn-ghost" onclick="openEditModal('${escapeAttr(p.codigo)}', '${escapeAttr(p.descripcion)}', ${p.precio}, ${p.stock})" title="Editar">
                    <i class="fa-solid fa-pen"></i>
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

// Inventory search
const inventorySearch = document.getElementById('inventory-search');
if (inventorySearch) {
    let invSearchTimeout;
    inventorySearch.addEventListener('input', (e) => {
        clearTimeout(invSearchTimeout);
        invSearchTimeout = setTimeout(() => {
            loadInventory(e.target.value);
        }, 200);
    });
}

// Inventory sort change
const inventorySort = document.getElementById('inventory-sort');
if (inventorySort) {
    inventorySort.addEventListener('change', () => {
        const query = document.getElementById('inventory-search')?.value || '';
        loadInventory(query);
    });
}

function toggleSortOrder() {
    inventorySortOrder = inventorySortOrder === 'asc' ? 'desc' : 'asc';
    const icon = document.getElementById('sort-order-icon');
    icon.className = inventorySortOrder === 'asc' ? 'fa-solid fa-arrow-down-a-z' : 'fa-solid fa-arrow-down-z-a';
    const query = document.getElementById('inventory-search')?.value || '';
    loadInventory(query);
}

// ============================================
// Edit Product Modal
// ============================================
function openEditModal(codigo, descripcion, precio, stock) {
    document.getElementById('edit-codigo').value = codigo;
    document.getElementById('edit-descripcion').value = descripcion;
    document.getElementById('edit-precio').value = precio;
    document.getElementById('edit-stock').value = stock;

    if (currentUser && currentUser.role === 'almacen') {
        document.getElementById('edit-descripcion').readOnly = true;
        document.getElementById('edit-precio').readOnly = true;
        document.getElementById('edit-descripcion').style.opacity = '0.6';
        document.getElementById('edit-precio').style.opacity = '0.6';
    } else {
        document.getElementById('edit-descripcion').readOnly = false;
        document.getElementById('edit-precio').readOnly = false;
        document.getElementById('edit-descripcion').style.opacity = '1';
        document.getElementById('edit-precio').style.opacity = '1';
    }

    document.getElementById('edit-product-modal').classList.add('active');
}

function closeEditModal() {
    document.getElementById('edit-product-modal').classList.remove('active');
}

document.getElementById('edit-product-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const codigo = document.getElementById('edit-codigo').value;
    const descripcion = document.getElementById('edit-descripcion').value;
    const precio = parseFloat(document.getElementById('edit-precio').value);
    const stock = parseInt(document.getElementById('edit-stock').value);

    try {
        const res = await fetch(`/api/products/${encodeURIComponent(codigo)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ descripcion, precio, stock })
        });
        const data = await res.json();

        if (data.success) {
            showToast('Producto actualizado ✓', 'success');
            closeEditModal();
            loadInventory(document.getElementById('inventory-search')?.value || '');
        }
    } catch (err) {
        showToast('Error al actualizar', 'error');
    }
});

// ============================================
// Sync
// ============================================
async function syncData() {
    const btn = document.getElementById('btn-sync');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Sincronizando...';

    try {
        const res = await fetch('/api/sync', { method: 'POST' });
        const data = await res.json();
        showToast(data.message, 'success');
        loadAdminData(); // refresh
    } catch (err) {
        showToast("Error en sincronización", 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-cloud-arrow-up"></i> Sincronizar';
    }
}

// ============================================
// Utilities
// ============================================
function formatNumber(num) {
    return (num || 0).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

function formatDate(isoString) {
    try {
        const d = new Date(isoString);
        return d.toLocaleDateString('es-MX', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch {
        return isoString;
    }
}

function formatCurrency(value, currency = 'MXN') {
    const amount = formatNumber(value || 0);
    if (currency === 'USD') {
        return `US$ ${amount}`;
    }
    return `$${amount} MXN`;
}

function getDisplayedPrice(value) {
    if (isUsdMode) {
        return formatCurrency((value || 0) / exchangeRate, 'USD');
    }
    return formatCurrency(value, 'MXN');
}

function updateCurrencyToggleButton() {
    const button = document.getElementById('btn-toggle-currency');
    if (!button) return;
    button.innerHTML = `<i class="fa-solid fa-dollar-sign"></i> ${isUsdMode ? 'USD' : 'MXN'}`;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}

function escapeAttr(str) {
    return (str || '').replace(/'/g, "\\'").replace(/"/g, '\\"');
}

// Close modals on overlay click
document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
            overlay.classList.remove('active');
        }
    });
});

// Close modals on Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay.active').forEach(m => m.classList.remove('active'));
    }
});
const paymentMethodSelect =
    document.getElementById('payment-method');

if (paymentMethodSelect) {

    paymentMethodSelect.addEventListener('change', () => {

        const mixedFields =
            document.getElementById('mixed-payment-fields');

        if (paymentMethodSelect.value === 'mixto') {

            mixedFields.style.display = 'block';

        } else {

            mixedFields.style.display = 'none';

            document.getElementById('cash-amount').value = 0;
            document.getElementById('card-amount').value = 0;
        }

    });

}

const saleDiscountInput =
    document.getElementById('sale-discount');

if (saleDiscountInput) {

    saleDiscountInput.addEventListener('input', () => {

        renderCart();

    });

}

const discountCurrencySelect =
    document.getElementById('discount-currency');

if (discountCurrencySelect) {

    discountCurrencySelect.addEventListener('change', () => {

        renderCart();

    });

}

const cashCurrencySelect =
    document.getElementById('cash-currency');

if (cashCurrencySelect) {

    cashCurrencySelect.addEventListener('change', () => {

        // No need to re-render cart here, just for future reference

    });

}

function roundMoney(value) {
    return Math.round((value || 0) * 100) / 100;
}
// ============================================
// Initialize
// ============================================
showView('view-login');
