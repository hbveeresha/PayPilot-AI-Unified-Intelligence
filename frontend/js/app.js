const API = '/api';
const state = { page: 'dashboard', pageNumber: 1, pageSize: 10, search: '', status: '', risk: '', dateFrom: '', dateTo: '', chartInstances: [], conversationId: null, conversationSessionKey: null, currency: 'USD', marketUser: null, storeView: 'shop', marketSearch: '', marketCategory: '', marketSort: 'featured', marketMin: '', marketMax: '', imagePreviewUrls: [] };
const pageNames = { dashboard: 'Overview', marketplace: 'Marketplace', assistant: 'AI assistant', transactions: 'Transactions', fraud: 'Fraud intelligence', analytics: 'Analytics', reports: 'Reports', settings: 'Settings' };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const money = (amount, currency = state.currency) => new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 2 }).format(Number(amount || 0));
const dateTime = value => value ? new Date(value).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
function getConversationSessionKey() {
  let key = '';
  try {
    key = sessionStorage.getItem('paypilotConversationSession') || '';
  } catch {}
  if (!/^[a-f0-9]{32}$/.test(key)) {
    const bytes = new Uint8Array(16);
    if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
    else for (let index = 0; index < bytes.length; index++) bytes[index] = Math.floor(Math.random() * 256);
    key = [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
    try { sessionStorage.setItem('paypilotConversationSession', key); } catch {}
  }
  return key;
}
async function api(path, options = {}) {
  const { headers: requestHeaders = {}, ...requestOptions } = options;
  const response = await fetch(`${API}${path}`, { ...requestOptions, headers: { 'Content-Type': 'application/json', ...requestHeaders } });
  if (!response.ok) { let detail = `Request failed (${response.status})`; try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : detail; } catch {} throw new Error(detail); }
  const type = response.headers.get('content-type') || '';
  return type.includes('application/json') ? response.json() : response;
}
function toast(message, isError = false) { const node = $('#toast'); node.textContent = message; node.classList.toggle('error', isError); node.style.display = 'block'; clearTimeout(toast.timer); toast.timer = setTimeout(() => node.style.display = 'none', 3400); }
function heading(title, subtitle, actions = '') { return `<div class="page-heading"><div><div class="eyebrow">PAYPILOT AI · SIMULATION ENVIRONMENT</div><h1>${title}</h1><p>${subtitle}</p></div><div class="heading-actions">${actions}</div></div>`; }
function chart(id, type, labels, datasets, options = {}) {
  const canvas = document.getElementById(id); if (!canvas) return;
  const parent = canvas.parentElement;
  if (typeof Chart === 'undefined') { drawFallback(canvas, labels, datasets); return; }
  const cfg = { type, data: { labels, datasets }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: options.legend ?? false, labels: { color: '#96a3b8', boxWidth: 7, usePointStyle: true, font: { size: 9 } } }, tooltip: { backgroundColor: '#161f2e', borderColor: '#334058', borderWidth: 1, titleColor: '#e7ecf7', bodyColor: '#bac5d5', padding: 9, displayColors: false } }, scales: type === 'doughnut' ? { x: { display: false }, y: { display: false } } : { x: { grid: { display: false }, ticks: { color: '#748198', font: { size: 9 }, maxRotation: 0, autoSkip: true }, border: { display: false } }, y: { grid: { color: '#222b39' }, ticks: { color: '#748198', font: { size: 9 }, maxTicksLimit: 5 }, border: { display: false } } }, ...options.extra } };
  state.chartInstances.push(new Chart(canvas, cfg));
}
function drawFallback(canvas, labels, datasets) {
  const ctx = canvas.getContext('2d'), box = canvas.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  canvas.width = box.width * dpr; canvas.height = box.height * dpr; ctx.scale(dpr, dpr);
  const w = box.width, h = box.height, values = datasets[0]?.data || [], max = Math.max(...values, 1), pad = 24;
  ctx.strokeStyle = '#273144'; ctx.fillStyle = '#75839a'; ctx.font = '10px sans-serif'; ctx.lineWidth = 1;
  for (let i = 0; i < 4; i++) { const y = pad + (h - 2 * pad) * i / 3; ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w - 5, y); ctx.stroke(); }
  values.forEach((v, i) => { const x = pad + (w - pad - 8) * i / Math.max(values.length - 1, 1), y = h - pad - (h - 2 * pad) * v / max; ctx.fillStyle = datasets[0].borderColor || '#748fff'; ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2); ctx.fill(); if (i % Math.ceil(labels.length / 7) === 0) ctx.fillText(String(labels[i]).slice(-5), x - 13, h - 5); });
}
function metric(label, value, icon, tone, foot, prefix = '') { return `<article class="metric-card"><div class="metric-top">${label}<span class="metric-icon ${tone}">${icon}</span></div><div class="metric-value">${prefix}${value}</div><div class="metric-foot">${foot}</div></article>`; }
function transactionRows(items) {
  if (!items?.length) return `<tr><td colspan="6"><div class="empty-state">No transactions match these filters. Try another search or create a simulation.</div></td></tr>`;
  return items.map(tx => `<tr><td><button class="row-link txid" data-detail="${escapeHtml(tx.transaction_id)}">${escapeHtml(tx.transaction_id)}</button></td><td><div class="person-cell"><span class="mini-avatar">${escapeHtml((tx.customer_name || '?').slice(0, 1))}</span><div>${escapeHtml(tx.customer_name)}<small>${escapeHtml(tx.customer_email)}</small></div></div></td><td>${money(tx.amount, tx.currency)}</td><td><span class="status ${escapeHtml(tx.status)}">${escapeHtml(tx.status)}</span></td><td><span class="risk-badge ${escapeHtml(tx.risk_level || 'low')}">${escapeHtml(tx.risk_level || 'low')} · ${Number(tx.risk_score || 0)}</span></td><td>${dateTime(tx.created_at)}</td></tr>`).join('');
}
function table(items, compact = false) { return `<div class="table-wrap"><table class="data-table"><thead><tr><th>Transaction ID</th><th>Customer</th><th>Amount</th><th>Status</th><th>Risk</th><th>Date</th></tr></thead><tbody>${transactionRows(items)}</tbody></table></div>`; }
function clearCharts() { state.chartInstances.forEach(chart => chart.destroy()); state.chartInstances = []; }
async function loadPage() {
  state.imagePreviewUrls.forEach(url => URL.revokeObjectURL(url)); state.imagePreviewUrls = [];
  clearCharts(); $$('.nav-link[data-page]').forEach(el => el.classList.toggle('active', el.dataset.page === state.page));
  $('#page-crumb').textContent = pageNames[state.page] || 'Overview';
  const root = $('#page-content'); root.innerHTML = `<div class="loading-state"><span class="spinner"></span>Loading ${escapeHtml(pageNames[state.page])}…</div>`;
  try { root.innerHTML = await ({ dashboard: dashboardPage, marketplace: marketplacePage, assistant: assistantPage, transactions: transactionsPage, fraud: fraudPage, analytics: analyticsPage, reports: reportsPage, settings: settingsPage }[state.page] || dashboardPage)(); wirePage(); }
  catch (err) { root.innerHTML = `${heading(pageNames[state.page], 'Workspace data could not be loaded.')}<section class="panel"><div class="empty-state">${escapeHtml(err.message)}<br><br><button class="btn" data-reload>Try again</button></div></section>`; wirePage(); }
}
async function dashboardPage() {
  const [data, analytics] = await Promise.all([api('/dashboard'), api('/analytics')]); const t = data.totals;
  const points = analytics.daily_activity.filter((_, i) => i % 2 === 0);
  const actions = `<button class="btn" data-page-link="transactions">View transactions ↗</button><button class="btn btn-primary" data-simulate>＋ Simulate payment</button>`;
  let html = `${heading('Good morning, operator', 'Here’s what’s happening across your simulated payment activity.', actions)}<div class="metric-grid">${metric('Total simulated volume', money(t.volume), '↗', '', `<span class="neutral">${t.transaction_count} total transactions</span>`)}${metric('Successful payments', t.successful.toLocaleString(), '✓', 'teal', `<strong>${t.success_rate}%</strong> overall success rate`)}${metric('Pending transactions', t.pending.toLocaleString(), '◷', 'violet', `<span class="neutral">Awaiting simulated completion</span>`)}${metric('High-risk signals', t.high_risk.toLocaleString(), '⌁', 'orange', `<span class="neutral">Review cues, not fraud findings</span>`)}</div><div class="dashboard-grid"><section class="panel"><div class="panel-header"><div><h2>Payment volume</h2><p>Daily activity · last 14 days</p></div><span class="eyebrow">LAST 14 DAYS</span></div><div class="chart-wrap"><canvas id="volume-chart"></canvas></div></section><section class="panel"><div class="panel-header"><div><h2>AI business insights</h2><p>Generated from local transaction records</p></div><span class="metric-icon violet">✧</span></div>${data.insights.map(x => `<div class="insight-card"><span class="insight-icon">✧</span><p>${escapeHtml(x)}</p></div>`).join('')}<button class="row-link" data-page-link="assistant">Ask the assistant →</button></section></div><section class="panel activity-panel"><div class="panel-header"><div><h2>Recent transactions</h2><p>Latest simulated payment activity</p></div><button class="row-link" data-page-link="transactions">View all →</button></div>${table(data.recent_transactions)}</section>`;
  html = html.replace('</div><div class="dashboard-grid">', `${metric('Failed payments', t.failed.toLocaleString(), '×', 'orange', '<span class="neutral">Current database records</span>')}</div><div class="dashboard-grid">`);

  requestAnimationFrame(() => chart('volume-chart', 'line', points.map(x => new Date(x.date + 'T00:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' })), [{ label: 'Volume', data: points.map(x => x.volume), borderColor: '#8298ff', backgroundColor: '#8298ff25', fill: true, tension: .37, pointRadius: 2, pointHoverRadius: 4 }], { extra: { scales: { x: { grid: { display: false }, ticks: { color: '#748198', font: { size: 9 } }, border: { display: false } }, y: { grid: { color: '#222b39' }, ticks: { color: '#748198', font: { size: 9 }, callback: v => '$' + v }, border: { display: false } } } } }));
  return html;
}
async function transactionsPage() {
  const result = await api('/transactions/search', { method: 'POST', body: JSON.stringify({ search: state.search || null, status: state.status || null, risk: state.risk || null, date_from: state.dateFrom || null, date_to: state.dateTo || null, page: state.pageNumber, page_size: state.pageSize }) });
  return `${heading('Transactions', 'Search, review and export your simulated payment history.', `<a class="btn" href="${API}/export/transactions.csv">⇩ Export CSV</a><button class="btn btn-primary" data-simulate>＋ New simulation</button>`)}<section class="panel"><div class="filter-row"><input id="tx-search" placeholder="Search ID, customer or email" value="${escapeHtml(state.search)}"><select id="tx-status"><option value="">All statuses</option><option value="successful" ${state.status === 'successful' ? 'selected' : ''}>Successful</option><option value="pending" ${state.status === 'pending' ? 'selected' : ''}>Pending</option><option value="failed" ${state.status === 'failed' ? 'selected' : ''}>Failed</option></select><select id="tx-risk"><option value="">All risk levels</option><option value="low" ${state.risk === 'low' ? 'selected' : ''}>Low risk</option><option value="medium" ${state.risk === 'medium' ? 'selected' : ''}>Medium risk</option><option value="high" ${state.risk === 'high' ? 'selected' : ''}>High risk</option></select><input id="tx-date-from" type="date" aria-label="From date" value="${escapeHtml(state.dateFrom)}"><input id="tx-date-to" type="date" aria-label="To date" value="${escapeHtml(state.dateTo)}"><button class="btn" id="clear-filters">Clear filters</button></div>${table(result.items)}<div class="table-footer"><span>Showing ${result.total ? (result.page - 1) * result.page_size + 1 : 0}–${Math.min(result.page * result.page_size, result.total)} of ${result.total} simulated records</span><div class="pagination"><button data-prev ${result.page <= 1 ? 'disabled' : ''}>←</button><span>Page ${result.page} of ${Math.max(result.pages, 1)}</span><button data-next ${result.page >= result.pages ? 'disabled' : ''}>→</button></div></div></section>`;
}
function storeTabs() {
  const tabs = [['shop', 'Discover'], ['cart', 'Cart'], ['orders', 'My orders'], ['merchant', 'Seller studio']];
  return `<div class="store-tabs">${tabs.map(([key, label]) => `<button class="store-tab ${state.storeView === key ? 'active' : ''}" data-store-view="${key}">${label}${key === 'cart' && state.cartCount ? ` <b>${state.cartCount}</b>` : ''}</button>`).join('')}</div>`;
}
const fallbackProductImage = '/static/images/product-fallback.svg';
function productImage(url, name, className = '') {
  return `<img class="${escapeHtml(className)}" src="${escapeHtml(url || fallbackProductImage)}" alt="${escapeHtml(name)}" loading="lazy" decoding="async" onerror="this.onerror=null;this.src='${fallbackProductImage}'">`;
}
function productCard(product) {
  return `<article class="product-card"><button class="product-art ${escapeHtml(product.image_key)}" data-product-detail="${escapeHtml(product.slug)}" aria-label="View ${escapeHtml(product.name)}">${productImage(product.image_url, product.name, 'product-photo')}<span class="art-mark">${escapeHtml(product.category.name)}</span></button><div class="product-copy"><div class="product-category">${escapeHtml(product.category.name)} · ★ ${Number(product.rating).toFixed(1)} (${product.review_count})</div><button class="product-name" data-product-detail="${escapeHtml(product.slug)}">${escapeHtml(product.name)}</button><p>${escapeHtml(product.description)}</p><div class="product-buy"><strong>${money(product.price, product.currency)}</strong><div class="product-actions"><button class="btn" data-add-product="${product.id}" ${product.in_stock ? '' : 'disabled'}>${product.in_stock ? 'Add to cart' : 'Out of stock'}</button><button class="btn btn-primary" data-buy-product="${product.id}" ${product.in_stock ? '' : 'disabled'}>Buy now</button></div></div></div></article>`;
}
function authPrompt(copy = 'Sign in or create a customer account to continue.') {
  return `<section class="panel store-empty"><div class="store-empty-icon">♙</div><h2>Your PayPilot account</h2><p>${escapeHtml(copy)}</p><button class="btn btn-primary" data-open-auth>Sign in or create account</button></section>`;
}
async function marketplacePage() {
  const [categories, auth] = await Promise.all([api('/marketplace/categories'), api('/auth/me')]);
  state.marketUser = auth.user;
  const customerCart = state.marketUser?.role === 'customer' ? await api('/marketplace/cart') : null;
  state.cartCount = customerCart?.item_count || 0;
  const top = `${heading('PayPilot Marketplace', 'Discover useful products with secure, server-validated checkout.', '<span class="store-trust">● PayPal Sandbox · no real money</span>')}${storeTabs()}`;
  if (state.storeView === 'shop') {
    const query = { search: state.marketSearch || null, category: state.marketCategory || null, sort: state.marketSort, min_price: state.marketMin === '' ? null : Number(state.marketMin), max_price: state.marketMax === '' ? null : Number(state.marketMax), page: 1, page_size: 60 };
    const result = await api('/marketplace/products/search', { method: 'POST', body: JSON.stringify(query) });
    const categoriesBar = `<nav class="category-strip" aria-label="Shop by category"><button class="category-chip ${state.marketCategory ? '' : 'active'}" data-category-link="">All picks</button>${categories.items.map(c => `<button class="category-chip ${state.marketCategory === c.slug ? 'active' : ''}" data-category-link="${escapeHtml(c.slug)}">${escapeHtml(c.name)}</button>`).join('')}</nav>`;
    return `${top}<section class="store-hero"><div><span class="eyebrow">PAYPILOT PICKS · EVERYDAY DISCOVERY</span><h2>Find your next favorite.</h2><p>Thoughtful technology, home comforts, style, and more from one simple storefront.</p><div class="hero-points"><span>✦ Curated catalogue</span><span>✓ Stock checked at checkout</span></div></div><div class="hero-orbit" aria-hidden="true"><span>P</span><i></i><b></b></div></section>${categoriesBar}<section class="store-controls"><form id="market-search-form" class="store-search"><input id="market-search" value="${escapeHtml(state.marketSearch)}" maxlength="120" placeholder="Search products and categories" aria-label="Search products"><button class="btn btn-primary" type="submit">Search</button></form><div class="store-filters"><select id="market-category" aria-label="Filter by category"><option value="">All categories</option>${categories.items.map(c => `<option value="${escapeHtml(c.slug)}" ${state.marketCategory === c.slug ? 'selected' : ''}>${escapeHtml(c.name)}</option>`).join('')}</select><select id="market-sort" aria-label="Sort products"><option value="featured" ${state.marketSort === 'featured' ? 'selected' : ''}>Featured</option><option value="rating" ${state.marketSort === 'rating' ? 'selected' : ''}>Top rated</option><option value="price_asc" ${state.marketSort === 'price_asc' ? 'selected' : ''}>Price: low to high</option><option value="price_desc" ${state.marketSort === 'price_desc' ? 'selected' : ''}>Price: high to low</option><option value="newest" ${state.marketSort === 'newest' ? 'selected' : ''}>Recently added</option><input id="market-min" inputmode="decimal" type="number" min="0" max="100000" step="0.01" value="${escapeHtml(state.marketMin)}" placeholder="Min $" aria-label="Minimum price"><input id="market-max" inputmode="decimal" type="number" min="0" max="100000" step="0.01" value="${escapeHtml(state.marketMax)}" placeholder="Max $" aria-label="Maximum price"><button class="btn" id="market-apply-filter" type="button">Apply filters</button><button class="row-link" id="market-clear-filter" type="button">Clear</button></div></section><div class="store-result-label"><span>${result.total} products</span><small>Representative product photography · secure checkout</small></div><section class="product-grid">${result.items.length ? result.items.map(productCard).join('') : '<div class="panel store-empty"><h2>No matching products</h2><p>Try another search or choose a different category.</p><button class="btn" id="market-clear-filter" type="button">Clear filters</button></div>'}</section>`;
  }
  if (state.storeView === 'cart') {
    if (state.marketUser?.role !== 'customer') return `${top}${authPrompt(state.marketUser ? 'Your account has seller access. Sign in as a customer to use a personal shopping cart.' : undefined)}`;
    const [cart, settings] = await Promise.all([Promise.resolve(customerCart), api('/settings')]);
    const rows = cart.items.map(item => `<article class="cart-line"><div class="cart-art">${productImage(item.image_url, item.name, 'cart-photo')}</div><div class="cart-product"><b>${escapeHtml(item.name)}</b><small>${escapeHtml(item.category)} · ${money(item.unit_price, cart.currency)} each</small><span>${item.available ? `${item.stock_quantity} available` : 'Stock changed · update quantity'}</span></div><label class="quantity-field">Qty <input type="number" min="1" max="99" value="${item.quantity}" data-cart-qty="${item.cart_item_id}" data-stock="${item.stock_quantity}" aria-label="Quantity for ${escapeHtml(item.name)}"></label><b class="cart-line-total">${money(item.line_total, cart.currency)}</b><button class="icon-remove" data-cart-remove="${item.cart_item_id}" aria-label="Remove ${escapeHtml(item.name)}">×</button></article>`).join('');
    const payButton = settings.paypal_sandbox_enabled ? `<button class="btn btn-primary checkout-button" id="paypal-cart-checkout" ${cart.can_checkout ? '' : 'disabled'}>Continue to PayPal Sandbox ↗</button>` : '<div class="notice">PayPal Sandbox is not configured yet. Add the Sandbox app credentials to the server .env file to enable checkout.</div>';
    return `${top}<div class="cart-layout"><section class="panel cart-panel"><div class="panel-header"><div><h2>Shopping cart</h2><p>${cart.item_count} item${cart.item_count === 1 ? '' : 's'} · stock checked at checkout</p></div><button class="row-link" data-store-view="shop">← Continue shopping</button></div>${rows || '<div class="store-empty"><div class="store-empty-icon">⌑</div><h2>Your cart is waiting</h2><p>Find something useful and add it here.</p><button class="btn btn-primary" data-store-view="shop">Browse products</button></div>'}</section><aside class="panel order-summary"><h2>Order summary</h2><div class="trend-row"><span>Subtotal</span><b>${money(cart.subtotal, cart.currency)}</b></div><div class="trend-row"><span>Estimated tax (${(cart.tax_rate * 100).toFixed(2)}%)</span><b>${money(cart.tax, cart.currency)}</b></div><div class="trend-row"><span>Shipping</span><b>${cart.shipping === 0 ? 'FREE' : money(cart.shipping, cart.currency)}</b></div>${cart.amount_until_free_shipping > 0 && cart.item_count ? `<p class="free-shipping-note">Add ${money(cart.amount_until_free_shipping, cart.currency)} more for free shipping.</p>` : ''}<div class="summary-total"><span>Total</span><strong>${money(cart.total, cart.currency)}</strong></div><p class="summary-note">Prices, tax, shipping, and inventory are recalculated by PayPilot before the Sandbox order is created.</p>${payButton}<div class="checkout-mark">🔒 Secure PayPal Sandbox</div></aside></div>`;
  }
  if (state.storeView === 'orders') {
    if (state.marketUser?.role !== 'customer') return `${top}${authPrompt(state.marketUser ? 'Order history is available to customer accounts.' : undefined)}`;
    const orders = await api('/marketplace/orders');
    return `${top}<section class="panel orders-panel"><div class="panel-header"><div><h2>Your orders</h2><p>Marketplace orders and Sandbox payment status.</p></div><button class="btn" data-store-view="shop">Discover products</button></div>${orders.items.length ? `<div class="order-list">${orders.items.map(order => `<article class="order-card"><div class="order-main"><small>${escapeHtml(order.order_number)} · ${dateTime(order.created_at)}</small><div class="order-products">${order.items.map(item => `<div class="order-product">${productImage(item.image_url, item.product_name, 'order-thumb')}<span>${escapeHtml(item.product_name)}<small>Qty ${item.quantity}</small></span></div>`).join('')}</div></div><div class="order-status ${escapeHtml(order.status)}">${escapeHtml(order.status.replace('_', ' '))}</div><strong>${money(order.total, order.currency)}</strong>${order.transaction_id ? `<button class="row-link" data-detail="${escapeHtml(order.transaction_id)}">Transaction ${escapeHtml(order.transaction_id)} ↗</button>` : ''}</article>`).join('')}</div>` : '<div class="store-empty"><h2>No orders yet</h2><p>Your completed and pending Marketplace orders will appear here.</p><button class="btn btn-primary" data-store-view="shop">Explore the catalogue</button></div>'}</section>`;
  }
  if (state.storeView === 'merchant') {
    if (state.marketUser?.role !== 'merchant') return `${top}${authPrompt(state.marketUser ? 'Seller Studio is reserved for merchant accounts.' : 'Create a merchant account to list and manage your products.')}`;
    const [products, catalog] = await Promise.all([api('/merchant/products'), Promise.resolve(categories)]);
    const productRows = products.items.map(p => `<tr><td><div class="merchant-product-cell">${productImage(p.image_url, p.name, 'merchant-thumb').replace('class="merchant-thumb"', `data-image-preview="${p.id}" class="merchant-thumb"`)}<div><b>${escapeHtml(p.name)}</b><small class="table-sub">${escapeHtml(p.category.name)}</small><label class="file-picker">Choose photo<input type="file" accept="image/jpeg,image/png,image/webp" data-image-file="${p.id}" aria-label="Choose product photo for ${escapeHtml(p.name)}"></label><button class="btn upload-photo-button" type="button" data-image-save="${p.id}" disabled>Upload photo</button></div></div></td><td><input class="stock-input" type="number" min="0.01" max="100000" step="0.01" value="${p.price}" data-price-input="${p.id}" aria-label="Price for ${escapeHtml(p.name)}"></td><td><input class="stock-input" type="number" min="0" max="100000" value="${p.stock_quantity}" data-stock-input="${p.id}" aria-label="Stock for ${escapeHtml(p.name)}"></td><td>${p.in_stock ? 'Active' : 'Out of stock'}</td><td><button class="btn" data-stock-save="${p.id}">Save price & stock</button></td></tr>`).join('');
    return `${top}<div class="seller-heading"><div><h2>Seller Studio</h2><p>Manage your product photos, prices, and available stock.</p></div><span class="seller-role">Merchant account · ${escapeHtml(state.marketUser.name)}</span></div><div class="seller-layout"><section class="panel"><div class="panel-header"><div><h2>Your products</h2><p>Only you can update these listings. Photos are normalized to WebP.</p></div></div>${products.items.length ? `<div class="table-wrap"><table class="data-table merchant-table"><thead><tr><th>Product & photo</th><th>Price</th><th>Stock</th><th>Availability</th><th></th></tr></thead><tbody>${productRows}</tbody></table></div>` : '<div class="empty-state">You have not listed any products yet.</div>'}</section><section class="panel"><h2 class="subheading">Add a product</h2><form id="merchant-product-form"><div class="field"><label for="product-name">Product name</label><input id="product-name" name="name" minlength="3" maxlength="140" required></div><div class="field"><label for="product-description">Description</label><textarea id="product-description" name="description" minlength="12" maxlength="2000" rows="3" required></textarea></div><div class="field"><label for="product-category">Category</label><select id="product-category" name="category_slug" required>${catalog.items.map(c => `<option value="${escapeHtml(c.slug)}">${escapeHtml(c.name)}</option>`).join('')}</select></div><div class="field-grid"><div class="field"><label for="product-price">Price (USD)</label><input id="product-price" name="price" type="number" min="0.01" max="100000" step="0.01" required></div><div class="field"><label for="product-stock">Initial stock</label><input id="product-stock" name="stock_quantity" type="number" min="0" max="100000" required></div></div><div class="merchant-image-field"><div id="merchant-image-preview-wrap">${productImage(fallbackProductImage, 'Image preview', 'merchant-create-preview')}</div><div><label class="file-picker" for="product-image">Choose product photo</label><input id="product-image" type="file" accept="image/jpeg,image/png,image/webp" aria-describedby="image-upload-help"><small id="image-upload-help">JPEG, PNG, or WebP · up to 5 MB</small></div></div><button class="btn btn-primary" type="submit">Publish product</button></form></section></div>`;
  }
  return top;
}
async function fraudPage() {
  const [feed, analytics] = await Promise.all([api('/risk'), api('/analytics')]); const counts = analytics.risk_distribution;
  const html = `${heading('Fraud intelligence', 'Explainable risk signals help prioritize review. Scores are not proof of fraud.', `<select class="select-dark" id="risk-filter"><option value="">All assessments</option><option value="high">High risk</option><option value="medium">Medium risk</option><option value="low">Low risk</option></select>`)}<div class="notice">Risk assessments are educational indicators produced from simulated activity using transparent rules and anomaly detection. They should not be used as evidence of real fraud.</div><div class="two-col"><section class="panel"><div class="panel-header"><div><h2>Risk distribution</h2><p>Latest assessments from the database</p></div></div><div class="ring-wrap"><canvas id="risk-chart"></canvas></div><div class="risk-summary"><div><b style="color:var(--green)">${counts.low}</b><small>Low</small></div><div><b style="color:var(--amber)">${counts.medium}</b><small>Medium</small></div><div><b style="color:var(--red)">${counts.high}</b><small>High</small></div></div></section><section class="panel"><div class="panel-header"><div><h2>What we look for</h2><p>Transparent signals behind risk scores</p></div></div><div class="trend-row"><span>Large payment amounts</span><b>Amount range</b></div><div class="trend-row"><span>Repeated attempts</span><b>Customer frequency</b></div><div class="trend-row"><span>Unusual amounts</span><b>Isolation Forest</b></div><div class="trend-row"><span>Failed payment outcomes</span><b>Rule based</b></div><div class="trend-row"><span>Low-activity hours</span><b>Transaction time</b></div></section></div><section class="panel activity-panel"><div class="panel-header"><div><h2>Risk assessment feed</h2><p>${feed.total} recent simulated transaction assessments</p></div></div><div id="risk-feed-table">${riskTable(feed.items)}</div></section>`;
  function riskTable(items) { if (!items.length) return '<div class="empty-state">No assessments in this category yet.</div>'; return `<div class="table-wrap"><table class="data-table"><thead><tr><th>Transaction</th><th>Score</th><th>Level</th><th>Assessment reasons</th><th>Recommended action</th></tr></thead><tbody>${items.map(r => `<tr><td><button class="row-link txid" data-detail="${escapeHtml(r.transaction_id)}">${escapeHtml(r.transaction_id)}</button></td><td><b>${r.score}/100</b></td><td><span class="risk-badge ${r.level}">${r.level}</span></td><td>${escapeHtml((r.reasons || []).join(' · '))}</td><td>${escapeHtml(r.recommendation)}</td></tr>`).join('')}</tbody></table></div>`; }
  requestAnimationFrame(() => chart('risk-chart', 'doughnut', ['Low', 'Medium', 'High'], [{ data: [counts.low, counts.medium, counts.high], backgroundColor: ['#52d6ae', '#f5bd66', '#ff7485'], borderColor: '#101521', borderWidth: 4, hoverOffset: 5 }], { legend: true, extra: { cutout: '72%' } }));
  // Capture the filter callback with the already-rendered risk data.
  window.currentRiskItems = feed.items;
  return html;
}
async function analyticsPage() {
  const data = await api('/analytics'); const daily = data.daily_activity;
  const rows = `<div class="trend-row"><span>Successful payments</span><b>${data.status_breakdown.successful}</b></div><div class="trend-row"><span>Pending payments</span><b>${data.status_breakdown.pending}</b></div><div class="trend-row"><span>Failed payments</span><b>${data.status_breakdown.failed}</b></div><div class="trend-row"><span>Total volume</span><b>${money(data.transaction_volume)}</b></div>`;
  const html = `${heading('Analytics', 'Transaction volume, activity and outcomes from the database.', `<a class="btn" href="${API}/export/transactions.csv">⇩ Export data</a>`)}<div class="metric-grid">${metric('Total volume', money(data.transaction_volume), '↗', '', `${data.total_transactions} simulated transactions`)}${metric('Successful', data.status_breakdown.successful, '✓', 'teal', 'Current database records')}${metric('Pending', data.status_breakdown.pending, '◷', 'violet', 'Current database records')}${metric('Failed', data.status_breakdown.failed, '×', 'orange', 'Current database records')}</div><div class="two-col"><section class="panel"><div class="panel-header"><div><h2>Daily activity</h2><p>Volume in the last 14 days</p></div></div><div class="chart-wrap"><canvas id="activity-chart"></canvas></div></section><section class="panel"><div class="panel-header"><div><h2>Payment status breakdown</h2><p>Activity by simulated outcome</p></div></div><div class="ring-wrap"><canvas id="status-chart"></canvas></div>${rows}</section></div>`;
  requestAnimationFrame(() => { chart('activity-chart', 'bar', daily.map(x => new Date(x.date + 'T00:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' })), [{ data: daily.map(x => x.volume), backgroundColor: '#7792ffb0', borderRadius: 4, maxBarThickness: 20 }]); chart('status-chart', 'doughnut', ['Successful', 'Pending', 'Failed'], [{ data: [data.status_breakdown.successful, data.status_breakdown.pending, data.status_breakdown.failed], backgroundColor: ['#52d6ae', '#f5bd66', '#ff7485'], borderColor: '#101521', borderWidth: 4 }], { legend: true, extra: { cutout: '69%' } }); });
  return html;
}
async function reportsPage() {
  const report = await api('/reports?period=daily'), monthly = await api('/reports?period=monthly');
  return `${heading('Reports', 'Generate a quick snapshot of local simulated payment performance.', `<button class="btn" data-report="monthly">Monthly summary</button><button class="btn btn-primary" data-report="daily">Daily summary</button>`)}<div class="notice">Reports summarize local simulation data only. Downloaded transaction CSV files are labeled as simulated and contain no payment credentials.</div><div class="two-col"><section class="report-card"><div class="eyebrow">TODAY · ${new Date().toLocaleDateString()}</div><h3>Daily performance</h3><p>Summary of transactions created since midnight.</p><div class="report-stat">${money(report.volume)}</div><div class="trend-row"><span>Transactions</span><b>${report.transaction_count}</b></div><div class="trend-row"><span>Successful volume</span><b>${money(report.successful_volume)}</b></div><div class="trend-row"><span>Failed</span><b>${report.statuses.failed}</b></div><div class="trend-row"><span>High-risk signals</span><b>${report.high_risk_count}</b></div></section><section class="report-card"><div class="eyebrow">CURRENT MONTH</div><h3>Monthly performance</h3><p>Summary for the current calendar month to date.</p><div class="report-stat">${money(monthly.volume)}</div><div class="trend-row"><span>Transactions</span><b>${monthly.transaction_count}</b></div><div class="trend-row"><span>Successful volume</span><b>${money(monthly.successful_volume)}</b></div><div class="trend-row"><span>Failed</span><b>${monthly.statuses.failed}</b></div><div class="trend-row"><span>High-risk signals</span><b>${monthly.high_risk_count}</b></div></section></div><section class="panel activity-panel"><div class="panel-header"><div><h2>Report data</h2><p>Daily transaction details</p></div><a class="btn" href="${API}/export/transactions.csv">⇩ Export CSV</a></div>${table(report.items)}</section>`;
}
async function settingsPage() {
  const data = await api('/settings'); state.currency = data.default_currency || 'USD';
  return `${heading('Settings', 'Manage display preferences and local assistant configuration.') }<div class="two-col"><section class="panel"><div class="panel-header"><div><h2>Workspace preferences</h2><p>Settings are stored in your local SQLite database.</p></div></div><form id="settings-form"><div class="field"><label for="currency">Default display currency</label><select id="currency"><option ${data.default_currency === 'USD' ? 'selected' : ''}>USD</option><option ${data.default_currency === 'EUR' ? 'selected' : ''}>EUR</option><option ${data.default_currency === 'GBP' ? 'selected' : ''}>GBP</option><option ${data.default_currency === 'INR' ? 'selected' : ''}>INR</option></select></div><button class="btn btn-primary">Save preferences</button></form></section><section class="panel"><div class="panel-header"><div><h2>AI assistant</h2><p>Local Ollama connection · no paid keys required</p></div><span class="metric-icon violet">✧</span></div><div class="detail-grid"><div class="detail-item"><small>Configured model</small><b>${escapeHtml(data.assistant_model)}</b></div><div class="detail-item"><small>Inference service</small><b>Configured on server · endpoint kept private</b></div><div class="detail-item"><small>Fallback mode</small><b>Limited and labeled; not equivalent to the model</b></div><div class="detail-item"><small>Payment network</small><b>${data.paypal_sandbox_enabled ? 'PayPal Sandbox configured' : 'Not configured · local simulation available'}</b></div></div></section></div><section class="panel activity-panel"><h2 class="subheading">PayPal Sandbox integration</h2><p style="color:#8996aa;font-size:11px;line-height:1.8">${data.paypal_sandbox_enabled ? 'PayPal Sandbox credentials are configured on this server. Add products to your shopping cart and use the Marketplace checkout to test approval and capture.' : 'To enable Marketplace checkout, create a Sandbox REST app in the PayPal Developer Dashboard and add PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET to your server-side .env file. Prices are read from the database; the checkout request does not accept an amount. Never put the secret in frontend code or commit it.'}</p></section><section class="panel activity-panel"><h2 class="subheading">About this workspace</h2><p style="color:#8996aa;font-size:11px;line-height:1.8">PayPilot AI is an integrated marketplace and payment-intelligence workspace created by H B Veeresha. Local transaction records may be simulations; PayPal checkout is a separate Sandbox integration. Never enter real card details or credentials.</p></section>`;
}
async function assistantPage() {
  let messages = [];
  if (state.conversationId) {
    try {
      const conversation = await api(`/assistant/conversations/${encodeURIComponent(state.conversationId)}`, { headers: { 'X-Conversation-Session': state.conversationSessionKey } });
      messages = conversation.messages;
    } catch (err) {
      if (!err.message.includes('Conversation not found')) throw err;
      state.conversationId = null;
      try { sessionStorage.removeItem('paypilotConversationId'); } catch {}
    }
  }
  const initial = messages.length ? messages.map(chatMessageHtml).join('') : chatMessageHtml({ role: 'assistant', content: 'Hi, I’m PayPilot AI. We can talk about general questions, learning, technology, or your simulated payment activity. What’s on your mind?', created_at: new Date().toISOString(), model: 'PayPilot AI' });
  return `${heading('AI assistant', 'General conversation, multilingual help, and payment intelligence grounded in your local records.') }<div class="assistant-layout"><section class="panel chat-panel"><div class="panel-header"><div><h2>PayPilot intelligence</h2><p>Local Ollama model · streaming replies · privacy-aware payment context</p></div><div class="chat-header-actions"><span class="metric-icon violet">✧</span><button class="btn" id="new-conversation" type="button">Clear conversation</button></div></div><div class="chat-messages" id="chat-messages">${initial}</div><form class="chat-composer" id="chat-form"><textarea id="chat-input" placeholder="Ask anything, in your language…" maxlength="2000" rows="1" aria-label="Message PayPilot AI" required></textarea><button class="btn btn-primary" type="submit">Send ↗</button></form><div class="composer-hint">Enter to send · Shift+Enter for a new line</div></section><aside class="assistant-side"><section class="panel"><h2 class="subheading">Try asking</h2><button class="suggestion">What is AI?</button><button class="suggestion">Explain a technical idea simply</button><button class="suggestion">Ninna hesaru enu?</button><button class="suggestion">Summarize my transactions</button><button class="suggestion">Show failed payments</button><button class="suggestion">Find suspicious transactions</button><button class="suggestion">Explain payment trends</button></section><section class="panel"><h2 class="subheading">Grounded in your data</h2><p style="color:#8290a7;font-size:10px;line-height:1.7">Payment counts, totals, and risk signals come from SQLite. General model context excludes customer names, contact details, and payment credentials; explicit customer-name lookup is handled directly by SQLite and omits email addresses.</p></section></aside></div>`;
}
function chatMessageHtml(item) {
  const isUser = item.role === 'user';
  const metadata = item.metadata || {};
  const source = isUser ? 'You' : metadata.source_label || (item.model === 'deterministic-fallback' ? 'Local fallback · limited understanding' : item.model === 'sqlite-grounded' ? 'SQLite · database-grounded' : item.model && item.model !== 'PayPilot AI' ? `Ollama · ${escapeHtml(item.model)}` : 'PayPilot AI');
  return `<article class="chat-entry ${isUser ? 'user-entry' : 'assistant-entry'}"><div class="chat-message ${isUser ? 'user' : ''}">${escapeHtml(item.content).replace(/\n/g, '<br>')}</div><div class="chat-artifacts">${isUser ? '' : assistantArtifactsHtml(metadata.artifacts || [])}</div><div class="chat-meta"><span class="chat-source">${escapeHtml(source)}</span><time>${dateTime(item.created_at)}</time></div></article>`;
}
function humanizeKey(key) { return String(key).replace(/_/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase()); }
function formatScalar(key, value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'number') {
    const k = String(key).toLowerCase();
    if (k.includes('rate') || k.includes('percent')) return `${value.toFixed(2)}%`;
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return escapeHtml(String(value));
}
// Renders a tool-result value (object/array/scalar) as readable HTML instead
// of ever dumping raw JSON to the user. Handles currency-code maps (e.g.
// {"USD": 120.5}), nested breakdowns, lists of records, and plain scalars.
function renderStructuredValue(key, value) {
  if (value === null || value === undefined) return '<span class="artifact-empty-value">Not recorded</span>';
  if (Array.isArray(value)) {
    if (!value.length) return '<span class="artifact-empty-value">None recorded</span>';
    if (value[0] && typeof value[0] === 'object') {
      const columns = [...new Set(value.flatMap(row => Object.keys(row || {})))];
      return `<table class="artifact-mini-table"><thead><tr>${columns.map(col => `<th>${escapeHtml(humanizeKey(col))}</th>`).join('')}</tr></thead><tbody>${value.map(row => `<tr>${columns.map(col => `<td>${row[col] && typeof row[col] === 'object' ? renderStructuredValue(col, row[col]) : formatScalar(col, row[col])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    }
    return escapeHtml(value.join(', '));
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value);
    if (!entries.length) return '<span class="artifact-empty-value">None recorded</span>';
    const looksLikeCurrencyMap = entries.every(([code]) => /^[A-Z]{3}$/.test(code));
    return `<ul class="artifact-kv-list">${entries.map(([entryKey, entryValue]) => {
      const label = looksLikeCurrencyMap ? escapeHtml(entryKey) : escapeHtml(humanizeKey(entryKey));
      const rendered = entryValue && typeof entryValue === 'object' ? renderStructuredValue(entryKey, entryValue) : formatScalar(entryKey, entryValue);
      return `<li><b>${label}:</b> ${rendered}</li>`;
    }).join('')}</ul>`;
  }
  return formatScalar(key, value);
}
function assistantArtifactsHtml(artifacts) {
  if (!Array.isArray(artifacts) || !artifacts.length) return '';
  return artifacts.map(artifact => {
    const rows = Array.isArray(artifact.rows) ? artifact.rows : [];
    const title = escapeHtml(artifact.title || artifact.source || 'Workspace results');
    const summary = artifact.summary ? `<p class="artifact-summary">${escapeHtml(artifact.summary)}</p>` : '';
    if (artifact.kind === 'products') {
      return `<section class="assistant-artifact"><h3>${title}</h3>${summary}<div class="artifact-products">${rows.map(row => { const image = typeof row.image_url === 'string' && /^\/(?:static\/images\/|media\/)/.test(row.image_url) ? row.image_url : '/static/images/product-fallback.svg'; return `<article class="artifact-product"><img src="${escapeHtml(image)}" alt="${escapeHtml(row.name)}" loading="lazy" onerror="this.onerror=null;this.src='/static/images/product-fallback.svg'"><div><b>${escapeHtml(row.name)}</b><small>${escapeHtml(row.category || '')} · ${escapeHtml(row.stock_quantity)} in stock</small><strong>${escapeHtml(row.currency || 'USD')} ${Number(row.price || 0).toFixed(2)}</strong></div></article>`; }).join('') || '<p class="artifact-empty">No matching products were found.</p>'}</div></section>`;
    }
    if (artifact.kind === 'orders') {
      const imagePath = value => typeof value === 'string' && /^\/(?:static\/images\/|media\/)/.test(value) ? value : '/static/images/product-fallback.svg';
      return `<section class="assistant-artifact"><h3>${title}</h3>${summary}<div class="artifact-orders">${rows.map(order => `<article><header><b>${escapeHtml(order.order_number)}</b><span>${escapeHtml(order.status)}</span><strong>${escapeHtml(order.currency || 'USD')} ${Number(order.total ?? order.subtotal ?? 0).toFixed(2)}</strong></header><div class="artifact-order-items">${(order.items || []).map(item => `<div><img src="${escapeHtml(imagePath(item.image_url))}" alt="${escapeHtml(item.product_name)}" onerror="this.onerror=null;this.src='/static/images/product-fallback.svg'"><span>${escapeHtml(item.product_name)} · ${escapeHtml(item.quantity)} × ${escapeHtml(order.currency || 'USD')} ${Number(item.unit_price || 0).toFixed(2)}</span></div>`).join('')}</div></article>`).join('') || '<p class="artifact-empty">No permitted orders were found.</p>'}</div></section>`;
    }
    if (artifact.kind === 'payment_link') {
      const row = rows[0] || {};
      const url = typeof row.approval_url === 'string' && /^https:\/\/www\.sandbox\.paypal\.com\//.test(row.approval_url) ? row.approval_url : '';
      return `<section class="assistant-artifact"><h3>${title}</h3>${summary}<div class="artifact-payment"><b>${escapeHtml(row.currency || 'USD')} ${Number(row.total || 0).toFixed(2)}</b><span>${escapeHtml(row.order_number || '')} · Sandbox order</span>${url ? `<a class="btn btn-primary" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Approve on PayPal Sandbox ↗</a>` : '<p class="artifact-empty">No approved PayPal Sandbox link was returned.</p>'}</div></section>`;
    }
    if (artifact.kind === 'sections') {
      return `<section class="assistant-artifact"><h3>${title}</h3>${summary}<div class="artifact-sections">${rows.map(row => `<div class="artifact-section"><h4>${escapeHtml(row.section || 'Summary')}</h4>${renderStructuredValue(row.section, row.data ?? row)}</div>`).join('')}</div></section>`;
    }
    const columns = [...new Set(rows.flatMap(row => Object.keys(row || {})))].filter(key => key !== 'image_url');
    const table = rows.length && columns.length ? `<div class="artifact-table-wrap"><table><thead><tr>${columns.map(key => `<th>${escapeHtml(humanizeKey(key))}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${columns.map(key => { const value = row[key]; return `<td>${value && typeof value === 'object' ? renderStructuredValue(key, value) : formatScalar(key, value)}</td>`; }).join('')}</tr>`).join('')}</tbody></table></div>` : '<p class="artifact-empty">No matching records were found.</p>';
    return `<section class="assistant-artifact"><h3>${title}</h3>${summary}${table}</section>`;
  }).join('');
}
function showModal(content) { $('#modal-body').innerHTML = content; $('#modal-backdrop').classList.add('open'); $('#modal-backdrop').setAttribute('aria-hidden', 'false'); }
function closeModal() { $('#modal-backdrop').classList.remove('open'); $('#modal-backdrop').setAttribute('aria-hidden', 'true'); }
async function refreshAuthState() {
  try {
    const result = await api('/auth/me'); state.marketUser = result.user;
    const button = $('#auth-action');
    if (button) button.textContent = state.marketUser ? `${state.marketUser.name.split(' ')[0]} · Account` : 'Sign in';
  } catch { state.marketUser = null; }
}
function showAuthModal(mode = 'login', role = 'customer') {
  if (state.marketUser) {
    showModal(`<h2 id="modal-title">Your account</h2><p>Signed in as <b>${escapeHtml(state.marketUser.name)}</b> · ${escapeHtml(state.marketUser.role)}.</p><p>Your account session is stored in an HttpOnly cookie.</p><button class="btn btn-primary" id="market-logout">Sign out</button>`);
    $('#market-logout').addEventListener('click', async e => { e.currentTarget.disabled = true; try { await api('/auth/logout', { method: 'POST', body: '{}' }); state.marketUser = null; await refreshAuthState(); closeModal(); toast('You are signed out.'); loadPage(); } catch (err) { toast(err.message, true); e.currentTarget.disabled = false; } });
    return;
  }
  const registration = mode === 'register';
  showModal(`<div class="auth-modal-heading"><span class="eyebrow">PAYPILOT MARKETPLACE</span><h2 id="modal-title">${registration ? 'Create your account' : 'Welcome back'}</h2><p>${registration ? 'Join as a customer or start selling as a merchant.' : 'Sign in to save your cart and manage your orders.'}</p></div><div class="auth-mode"><button class="${registration ? '' : 'active'}" data-auth-mode="login">Sign in</button><button class="${registration ? 'active' : ''}" data-auth-mode="register">Create account</button></div>${registration ? `<form id="market-auth-form" data-mode="register"><div class="field"><label for="auth-name">Full name</label><input id="auth-name" name="name" minlength="2" maxlength="120" autocomplete="name" required></div><div class="field"><label for="auth-email">Email address</label><input id="auth-email" name="email" type="email" maxlength="160" autocomplete="email" required></div><div class="field"><label for="auth-password">Password · at least 10 characters</label><input id="auth-password" name="password" type="password" minlength="10" maxlength="128" autocomplete="new-password" required></div><div class="field"><label for="auth-role">Account type</label><select id="auth-role" name="role"><option value="customer" ${role === 'customer' ? 'selected' : ''}>Customer</option><option value="merchant" ${role === 'merchant' ? 'selected' : ''}>Merchant / seller</option></select></div><button class="btn btn-primary" type="submit">Create secure account</button></form>` : `<form id="market-auth-form" data-mode="login"><div class="field"><label for="auth-email">Email address</label><input id="auth-email" name="email" type="email" maxlength="160" autocomplete="email" required></div><div class="field"><label for="auth-password">Password</label><input id="auth-password" name="password" type="password" minlength="1" maxlength="128" autocomplete="current-password" required></div><button class="btn btn-primary" type="submit">Sign in securely</button></form>`}<p class="auth-error" id="auth-error" role="alert"></p>`);
  $$('[data-auth-mode]', $('#modal-body')).forEach(button => button.addEventListener('click', () => showAuthModal(button.dataset.authMode, role)));
  $('#market-auth-form').addEventListener('submit', async e => {
    e.preventDefault(); const form = Object.fromEntries(new FormData(e.currentTarget)); const button = $('button[type="submit"]', e.currentTarget); const error = $('#auth-error');
    button.disabled = true; button.innerHTML = '<span class="spinner"></span> Working…'; error.textContent = '';
    try {
      const endpoint = e.currentTarget.dataset.mode === 'register' ? '/auth/register' : '/auth/login';
      const result = await api(endpoint, { method: 'POST', body: JSON.stringify(form) });
      state.marketUser = result.user; await refreshAuthState(); closeModal(); toast(`Welcome${state.marketUser ? `, ${state.marketUser.name.split(' ')[0]}` : ''}.`); loadPage();
    } catch (err) { error.textContent = err.message; button.disabled = false; button.textContent = e.currentTarget.dataset.mode === 'register' ? 'Create secure account' : 'Sign in securely'; }
  });
}
function simulationModal() {
  showModal(`<h2 id="modal-title">Simulate a payment</h2><p>Creates a local demo order and transaction. No card or payment credentials are collected.</p><form id="simulation-form"><div class="field-grid"><div class="field"><label>Customer name</label><input name="customer_name" required minlength="2" maxlength="120" placeholder="Alex Morgan"></div><div class="field"><label>Customer email</label><input name="customer_email" type="email" required placeholder="alex@example.test"></div><div class="field"><label>Amount</label><input name="amount" type="number" min="0.01" max="1000000" step="0.01" required placeholder="125.00"></div><div class="field"><label>Currency</label><select name="currency"><option>USD</option><option>EUR</option><option>GBP</option><option>INR</option></select></div></div><div class="field"><label>Description</label><input name="description" maxlength="240" value="Simulated payment"></div><div class="field"><label>Simulated outcome</label><select name="outcome"><option value="approved">Approved</option><option value="pending">Pending</option><option value="failed">Failed</option></select></div><div class="notice">This is a simulation. No real payment will be made.</div><button class="btn btn-primary">Create simulated transaction</button></form>`);
  // The form is injected dynamically, so its submit handler must be attached after insertion.
  $('#simulation-form').addEventListener('submit', async e => {
    e.preventDefault();
    const form = Object.fromEntries(new FormData(e.currentTarget));
    form.amount = Number(form.amount);
    const submit = e.submitter || $('button[type="submit"]', e.currentTarget);
    submit.disabled = true;
    submit.innerHTML = '<span class="spinner"></span> Creating…';
    try {
      const result = await api('/simulations', { method: 'POST', body: JSON.stringify(form) });
      closeModal();
      state.search = state.status = state.risk = '';
      state.dateFrom = state.dateTo = '';
      if ($('#global-search')) $('#global-search').value = '';
      state.pageNumber = 1;
      toast(`Simulation saved · ${result.transaction.transaction_id}`);
      navigate('transactions');
    } catch (err) {
      toast(err.message, true);
      submit.disabled = false;
      submit.textContent = 'Create simulated transaction';
    }
  });
}
async function handlePayPalReturn(params = new URLSearchParams(location.search)) {
  if (params.get('paypal_order_return') === '1') {
    const orderId = params.get('token');
    state.page = 'marketplace'; state.storeView = 'orders';
    history.replaceState(null, '', `${location.pathname}#marketplace`);
    if (!orderId) { toast('PayPal returned without an order ID.', true); return; }
    try {
      const result = await api(`/paypal/sandbox/orders/${encodeURIComponent(orderId)}/capture`, { method: 'POST', body: '{}' });
      toast(`Sandbox order ${result.order_number} paid · transaction ${result.transaction_id}`);
      state.page = 'marketplace'; state.storeView = 'orders';
    } catch (err) { toast(err.message, true); }
  } else if (params.get('paypal_order_cancel') === '1') {
    const orderId = params.get('token');
    state.page = 'marketplace'; state.storeView = 'cart';
    history.replaceState(null, '', `${location.pathname}#marketplace`);
    if (orderId) {
      try { const result = await api(`/paypal/sandbox/orders/${encodeURIComponent(orderId)}/cancel`, { method: 'POST', body: '{}' }); toast(`Checkout cancelled · ${result.order_number}`); }
      catch (err) { toast(err.message, true); }
    } else toast('PayPal Sandbox checkout was cancelled.');
    state.page = 'marketplace'; state.storeView = 'cart';
  }
}
async function showDetail(id) {
  try { const tx = await api(`/transactions/${encodeURIComponent(id)}`), risk = tx.risk; showModal(`<h2 id="modal-title">Transaction details</h2><p>Simulated record · ${escapeHtml(tx.transaction_id)}</p><div class="detail-grid"><div class="detail-item"><small>Customer</small><b>${escapeHtml(tx.customer_name)} · ${escapeHtml(tx.customer_email)}</b></div><div class="detail-item"><small>Amount</small><b>${money(tx.amount, tx.currency)}</b></div><div class="detail-item"><small>Status</small><span class="status ${escapeHtml(tx.status)}">${escapeHtml(tx.status)}</span></div><div class="detail-item"><small>Created</small><b>${dateTime(tx.created_at)}</b></div><div class="detail-item"><small>Order ID</small><b>${escapeHtml(tx.order_id || '—')}</b></div><div class="detail-item"><small>Risk score</small><span class="risk-badge ${escapeHtml(risk?.level || 'low')}">${risk?.score ?? 0}/100 · ${escapeHtml(risk?.level || 'low')}</span></div></div><section class="panel" style="margin-top:17px"><h3 class="subheading">Assessment reasons</h3><p>${escapeHtml(risk?.reasons?.join(' ') || 'No assessment reasons available.')}</p><p><b style="color:#d7dfec">Recommendation:</b> ${escapeHtml(risk?.recommendation || 'No action suggested.')}</p></section>`); } catch (err) { toast(err.message, true); }
}
async function showProductDetail(slug) {
  try {
    const product = await api(`/marketplace/products/${encodeURIComponent(slug)}`);
    showModal(`<div class="product-detail-layout"><div class="product-detail-art">${productImage(product.image_url, product.name, 'detail-photo')}</div><div class="product-detail-copy"><span class="product-category">${escapeHtml(product.category.name)} · ★ ${Number(product.rating).toFixed(1)} (${product.review_count} reviews)</span><h2 id="modal-title">${escapeHtml(product.name)}</h2><p>${escapeHtml(product.description)}</p><div class="product-detail-price">${money(product.price, product.currency)}</div><p>${product.in_stock ? `${product.stock_quantity} available · Sold by ${escapeHtml(product.merchant_name)}` : 'Currently out of stock'}</p><div class="detail-actions"><button class="btn" id="detail-add-product" ${product.in_stock ? '' : 'disabled'}>${product.in_stock ? 'Add to cart' : 'Out of stock'}</button><button class="btn btn-primary" id="detail-buy-product" ${product.in_stock ? '' : 'disabled'}>Buy now</button></div><small class="representative-note">Representative demonstration photo; exact item appearance may vary.</small></div></div>`);
    $('#detail-add-product').addEventListener('click', () => addProductToCart(product.id));
    $('#detail-buy-product').addEventListener('click', () => addProductToCart(product.id, true));
  } catch (err) { toast(err.message, true); }
}
async function addProductToCart(productId, buyNow = false) {
  if (!state.marketUser) { showAuthModal('login'); return; }
  if (state.marketUser.role !== 'customer') { toast('A customer account is required for a shopping cart. Sign out to switch accounts.', true); return; }
  try {
    const cart = await api('/marketplace/cart/items', { method: 'POST', body: JSON.stringify({ product_id: Number(productId), quantity: 1 }) });
    state.cartCount = cart.item_count;
    closeModal();
    state.storeView = buyNow ? 'cart' : state.storeView;
    toast(buyNow ? 'Added to your cart. Review your total before checkout.' : 'Added to your cart.');
    loadPage();
  } catch (err) { toast(err.message, true); }
}
async function uploadProductImage(productId, file) {
  const form = new FormData(); form.append('file', file);
  const response = await fetch(`${API}/merchant/products/${encodeURIComponent(productId)}/image`, { method: 'POST', body: form });
  if (!response.ok) {
    let message = `Image upload failed (${response.status})`;
    try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; } catch {}
    throw new Error(message);
  }
  return response.json();
}
function previewImageFile(input, image) {
  const file = input.files?.[0];
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) { input.value = ''; toast('Choose an image smaller than 5 MB.', true); return; }
  const previous = input.dataset.previewUrl;
  if (previous) { URL.revokeObjectURL(previous); state.imagePreviewUrls = state.imagePreviewUrls.filter(url => url !== previous); }
  const previewUrl = URL.createObjectURL(file);
  input.dataset.previewUrl = previewUrl; state.imagePreviewUrls.push(previewUrl); image.src = previewUrl;
}
async function startMarketplaceCheckout(button) {
  button.disabled = true; const priorText = button.textContent; button.innerHTML = '<span class="spinner"></span> Checking cart…';
  try {
    await api('/marketplace/checkout/quote', { method: 'POST', body: '{}' });
    const result = await api('/marketplace/checkout/paypal', { method: 'POST', body: '{}' });
    if (!result.approval_url) throw new Error('PayPal Sandbox did not return an approval link.');
    window.location.assign(result.approval_url);
  } catch (err) { toast(err.message, true); button.disabled = false; button.textContent = priorText; }
}
function wirePage() {
  const chatHistory = $('#chat-messages');
  if (chatHistory) requestAnimationFrame(() => { chatHistory.scrollTop = chatHistory.scrollHeight; });
  $$('[data-simulate]').forEach(el => el.addEventListener('click', simulationModal));
  $$('[data-page-link]').forEach(el => el.addEventListener('click', () => navigate(el.dataset.pageLink)));
  $$('[data-detail]').forEach(el => el.addEventListener('click', () => showDetail(el.dataset.detail)));
  $('[data-reload]')?.addEventListener('click', loadPage);
  $$('[data-store-view]').forEach(el => el.addEventListener('click', () => { state.storeView = el.dataset.storeView; loadPage(); }));
  $('[data-open-auth]')?.addEventListener('click', () => showAuthModal('login'));
  $$('[data-add-product]').forEach(el => el.addEventListener('click', () => addProductToCart(el.dataset.addProduct)));
  $$('[data-buy-product]').forEach(el => el.addEventListener('click', () => addProductToCart(el.dataset.buyProduct, true)));
  $$('[data-product-detail]').forEach(el => el.addEventListener('click', () => showProductDetail(el.dataset.productDetail)));
  $$('[data-category-link]').forEach(el => el.addEventListener('click', () => { state.marketCategory = el.dataset.categoryLink; state.storeView = 'shop'; loadPage(); }));
  $('#market-search-form')?.addEventListener('submit', e => { e.preventDefault(); state.marketSearch = $('#market-search').value.trim(); loadPage(); });
  $('#market-category')?.addEventListener('change', e => { state.marketCategory = e.target.value; loadPage(); });
  $('#market-sort')?.addEventListener('change', e => { state.marketSort = e.target.value; loadPage(); });
  $('#market-apply-filter')?.addEventListener('click', () => { state.marketMin = $('#market-min').value; state.marketMax = $('#market-max').value; loadPage(); });
  const clearMarketFilters = () => { state.marketSearch = ''; state.marketCategory = ''; state.marketSort = 'featured'; state.marketMin = ''; state.marketMax = ''; loadPage(); };
  $('#market-clear-filter')?.addEventListener('click', clearMarketFilters);
  $$('[data-market-clear-filter]').forEach(el => el.addEventListener('click', clearMarketFilters));
  $$('[data-cart-qty]').forEach(el => el.addEventListener('change', async e => { const input = e.currentTarget; input.disabled = true; try { await api(`/marketplace/cart/items/${input.dataset.cartQty}`, { method: 'PUT', body: JSON.stringify({ quantity: Number(input.value) }) }); loadPage(); } catch (err) { toast(err.message, true); loadPage(); } }));
  $$('[data-cart-remove]').forEach(el => el.addEventListener('click', async () => { el.disabled = true; try { await api(`/marketplace/cart/items/${el.dataset.cartRemove}`, { method: 'DELETE' }); toast('Removed from your cart.'); loadPage(); } catch (err) { toast(err.message, true); el.disabled = false; } }));
  $('#paypal-cart-checkout')?.addEventListener('click', e => startMarketplaceCheckout(e.currentTarget));
  $('#product-image')?.addEventListener('change', e => previewImageFile(e.currentTarget, $('.merchant-create-preview')));
  $$('[data-image-file]').forEach(input => input.addEventListener('change', () => { const image = $(`[data-image-preview="${input.dataset.imageFile}"]`); if (image) previewImageFile(input, image); const button = $(`[data-image-save="${input.dataset.imageFile}"]`); if (button) button.disabled = !input.files?.length; }));
  $$('[data-image-save]').forEach(button => button.addEventListener('click', async () => { const input = $(`[data-image-file="${button.dataset.imageSave}"]`), priorText = button.textContent; if (!input?.files?.[0]) return; button.disabled = true; button.textContent = 'Uploading…'; try { await uploadProductImage(button.dataset.imageSave, input.files[0]); toast('Product photo updated.'); loadPage(); } catch (err) { toast(err.message, true); button.disabled = false; button.textContent = priorText; } }));
  $('#merchant-product-form')?.addEventListener('submit', async e => { e.preventDefault(); const form = e.currentTarget, button = $('button[type="submit"]', form), image = $('#product-image')?.files?.[0] || null, data = Object.fromEntries(new FormData(form)); delete data.image; data.price = Number(data.price); data.stock_quantity = Number(data.stock_quantity); button.disabled = true; button.textContent = 'Publishing…'; try { const created = await api('/merchant/products', { method: 'POST', body: JSON.stringify(data) }); if (image) await uploadProductImage(created.product.id, image); toast(image ? 'Product and photo published.' : 'Product published. Add a photo at any time from your catalogue.'); loadPage(); } catch (err) { toast(err.message, true); button.disabled = false; button.textContent = 'Publish product'; } });
  $$('[data-stock-save]').forEach(el => el.addEventListener('click', async () => { const stock = $(`[data-stock-input="${el.dataset.stockSave}"]`), price = $(`[data-price-input="${el.dataset.stockSave}"]`); el.disabled = true; try { await api(`/merchant/products/${el.dataset.stockSave}`, { method: 'PUT', body: JSON.stringify({ stock_quantity: Number(stock.value), price: Number(price.value) }) }); toast('Product price and inventory updated.'); loadPage(); } catch (err) { toast(err.message, true); el.disabled = false; } }));
  $('#tx-search')?.addEventListener('input', debounce(e => { state.search = e.target.value; state.pageNumber = 1; loadPage(); }, 320));
  $('#tx-status')?.addEventListener('change', e => { state.status = e.target.value; state.pageNumber = 1; loadPage(); });
  $('#tx-risk')?.addEventListener('change', e => { state.risk = e.target.value; state.pageNumber = 1; loadPage(); });
  $('#tx-date-from')?.addEventListener('change', e => { state.dateFrom = e.target.value; state.pageNumber = 1; loadPage(); });
  $('#tx-date-to')?.addEventListener('change', e => { state.dateTo = e.target.value; state.pageNumber = 1; loadPage(); });
  $('#clear-filters')?.addEventListener('click', () => { state.search = state.status = state.risk = ''; state.dateFrom = state.dateTo = ''; state.pageNumber = 1; if ($('#global-search')) $('#global-search').value = ''; loadPage(); });
  $('[data-prev]')?.addEventListener('click', () => { state.pageNumber = Math.max(1, state.pageNumber - 1); loadPage(); });
  $('[data-next]')?.addEventListener('click', () => { state.pageNumber++; loadPage(); });
  $('#chat-form')?.addEventListener('submit', e => { e.preventDefault(); sendMessage($('#chat-input').value); });
  $('#chat-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (e.target.value.trim()) sendMessage(e.target.value); }
  });
  $('#chat-input')?.addEventListener('input', e => { e.target.style.height = 'auto'; e.target.style.height = `${Math.min(e.target.scrollHeight, 132)}px`; });
  $('#new-conversation')?.addEventListener('click', async e => {
    if (state.chatBusy) return;
    const button = e.currentTarget;
    button.disabled = true;
    if (state.conversationId) {
      try {
        await api(`/assistant/conversations/${encodeURIComponent(state.conversationId)}`, { method: 'DELETE', headers: { 'X-Conversation-Session': state.conversationSessionKey } });
      } catch (err) { toast(err.message, true); button.disabled = false; return; }
    }
    state.conversationId = null;
    try { sessionStorage.removeItem('paypilotConversationId'); } catch {}
    $('#chat-messages').innerHTML = chatMessageHtml({ role: 'assistant', content: 'New conversation started. I’ll use the same local data and keep this chat separate from your previous session.', created_at: new Date().toISOString(), model: 'PayPilot AI' });
    button.disabled = false;
    $('#chat-input').focus();
  });
  $$('.suggestion').forEach(el => el.addEventListener('click', () => sendMessage(el.textContent)));
  $('#settings-form')?.addEventListener('submit', async e => { e.preventDefault(); try { const result = await api('/settings', { method: 'PUT', body: JSON.stringify({ default_currency: $('#currency').value }) }); state.currency = result.default_currency; toast('Workspace preference saved.'); } catch (err) { toast(err.message, true); } });
  $$('[data-report]').forEach(el => el.addEventListener('click', async () => { try { const report = await api(`/reports?period=${el.dataset.report}`); toast(`${el.dataset.report === 'daily' ? 'Daily' : 'Monthly'} summary: ${report.transaction_count} transactions · ${money(report.volume)}`); } catch (err) { toast(err.message, true); } }));
  $('#risk-filter')?.addEventListener('change', e => { const items = window.currentRiskItems || []; const filtered = e.target.value ? items.filter(x => x.level === e.target.value) : items; const target = $('#risk-feed-table'); if (target) target.innerHTML = filtered.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Transaction</th><th>Score</th><th>Level</th><th>Assessment reasons</th><th>Recommended action</th></tr></thead><tbody>${filtered.map(r => `<tr><td><button class="row-link txid" data-detail="${escapeHtml(r.transaction_id)}">${escapeHtml(r.transaction_id)}</button></td><td><b>${r.score}/100</b></td><td><span class="risk-badge ${r.level}">${r.level}</span></td><td>${escapeHtml(r.reasons.join(' · '))}</td><td>${escapeHtml(r.recommendation)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty-state">No assessments in this category yet.</div>'; $$('[data-detail]', target || document).forEach(el => el.addEventListener('click', () => showDetail(el.dataset.detail))); });
}
function debounce(fn, delay) { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); }; }
function navigate(page) { state.page = page; state.pageNumber = 1; $$('.nav-link[data-page]').forEach(el => el.classList.toggle('active', el.dataset.page === page)); history.replaceState(null, '', `${location.pathname}#${page}`); $('#sidebar').classList.remove('open'); loadPage(); }
async function sendMessage(text) {
  if (state.chatBusy) return;
  const value = text.trim(); if (!value) return; const messages = $('#chat-messages'); if (!messages) return;
  state.chatBusy = true;
  const input = $('#chat-input'), submit = $('#chat-form button[type="submit"]'), clearButton = $('#new-conversation');
  input.disabled = true; submit.disabled = true; if (clearButton) clearButton.disabled = true;
  messages.insertAdjacentHTML('beforeend', chatMessageHtml({ role: 'user', content: value, created_at: new Date().toISOString(), model: 'user' }));
  messages.insertAdjacentHTML('beforeend', '<article class="chat-entry assistant-entry" data-typing="true"><div class="chat-message typing-indicator" aria-label="PayPilot AI is typing"><span></span><span></span><span></span></div><div class="chat-meta"><span>PayPilot AI</span><time>Thinking…</time></div></article>');
  const waitNode = $('[data-typing="true"]', messages); $('#chat-input').value = ''; $('#chat-input').style.height = 'auto'; messages.scrollTop = messages.scrollHeight;
  let streamMessageNode = null;
  try {
    const response = await fetch(`${API}/assistant/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Conversation-Session': state.conversationSessionKey },
      body: JSON.stringify({ message: value, conversation_id: state.conversationId }),
    });
    if (!response.ok) { let detail = `Request failed (${response.status})`; try { const body = await response.json(); if (typeof body.detail === 'string') detail = body.detail; } catch {} throw new Error(detail); }
    if (!response.body) throw new Error('Streaming responses are not available in this browser.');
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '', reply = '', assistantNode = null, completion = null;
    const renderChunk = chunk => {
      reply += chunk;
      if (!assistantNode) {
        waitNode.outerHTML = chatMessageHtml({ role: 'assistant', content: reply, created_at: new Date().toISOString(), model: 'PayPilot AI' });
        assistantNode = messages.lastElementChild;
        streamMessageNode = assistantNode;
      } else {
        $('.chat-message', assistantNode).innerHTML = escapeHtml(reply).replace(/\n/g, '<br>');
      }
      messages.scrollTop = messages.scrollHeight;
    };
    const consumeLine = line => {
      if (!line.trim()) return;
      const frame = JSON.parse(line);
      if (frame.type === 'chunk') renderChunk(frame.text || '');
      if (frame.type === 'warning') toast(frame.text || 'The local model stream was interrupted.', true);
      if (frame.type === 'done') completion = frame;
    };
    while (true) {
      const { value: bytes, done } = await reader.read();
      buffer += decoder.decode(bytes || new Uint8Array(), { stream: !done });
      const lines = buffer.split('\n'); buffer = lines.pop() || '';
      lines.forEach(consumeLine);
      if (done) break;
    }
    if (buffer.trim()) consumeLine(buffer);
    if (!completion) throw new Error('The assistant response ended unexpectedly. Please try again.');
    state.conversationId = completion.conversation_id;
    try { sessionStorage.setItem('paypilotConversationId', state.conversationId); } catch {}
    if (!assistantNode) {
      waitNode.outerHTML = chatMessageHtml({ role: 'assistant', content: reply, created_at: new Date().toISOString(), model: completion.model });
      assistantNode = messages.lastElementChild;
      streamMessageNode = assistantNode;
    }
    const source = $('.chat-meta .chat-source', assistantNode);
    if (source) source.textContent = completion.source_label || (completion.source === 'database' ? 'SQLite · database-grounded' : completion.used_fallback ? 'Local fallback · limited understanding' : `Ollama · ${completion.model}`);
    const artifacts = $('.chat-artifacts', assistantNode);
    if (artifacts) artifacts.innerHTML = assistantArtifactsHtml(completion.artifacts || []);
  }
  catch (err) {
    const errorMessage = `I couldn’t complete that request: ${err.message}`;
    if (waitNode.isConnected) waitNode.outerHTML = chatMessageHtml({ role: 'assistant', content: errorMessage, created_at: new Date().toISOString(), model: 'Error' });
    else if (streamMessageNode) messages.insertAdjacentHTML('beforeend', chatMessageHtml({ role: 'assistant', content: errorMessage, created_at: new Date().toISOString(), model: 'Error' }));
  }
  finally { state.chatBusy = false; input.disabled = false; submit.disabled = false; if (clearButton) clearButton.disabled = false; input.focus(); }
  messages.scrollTop = messages.scrollHeight;
}
function boot() {
  const paypalParams = new URLSearchParams(location.search);
  if (location.search) history.replaceState(null, '', `${location.pathname}${location.hash}`);
  try { state.conversationId = sessionStorage.getItem('paypilotConversationId') || null; } catch {}
  state.conversationSessionKey = getConversationSessionKey();
  $('#mobile-menu').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
  $('#modal-close').addEventListener('click', closeModal); $('#modal-backdrop').addEventListener('click', e => { if (e.target.id === 'modal-backdrop') closeModal(); });
  $$('.nav-link[data-page]').forEach(el => el.addEventListener('click', () => navigate(el.dataset.page)));
  $('#global-search').addEventListener('keydown', e => { if (e.key === 'Enter') { state.search = e.target.value; state.status = state.risk = ''; navigate('transactions'); } });
  $('#notifications').addEventListener('click', () => toast('You’re all caught up on local simulation activity.'));
  $('#auth-action').addEventListener('click', () => showAuthModal());
  document.addEventListener('keydown', e => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#global-search').focus(); } if (e.key === 'Escape') closeModal(); });
  const initial = location.hash.slice(1); if (pageNames[initial]) { state.page = initial; $$('.nav-link[data-page]').forEach(el => el.classList.toggle('active', el.dataset.page === initial)); }
  Promise.all([refreshAuthState(), handlePayPalReturn(paypalParams)]).finally(() => loadPage());
}
document.addEventListener('DOMContentLoaded', boot);
