let currentAdmin = null;
let currentTab = 'dashboard';
let cachedExecutors = [];
let cachedOrders = [];

function toast(msg) {
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtRub(val) {
  const num = Number(val || 0);
  if (Number.isNaN(num)) return String(val || '0') + ' ₽';
  return num.toLocaleString('ru-RU', { maximumFractionDigits: 0 }) + ' ₽';
}

function statusPillClass(status) {
  if (['completed', 'active', 'paid', 'resolved_rework'].includes(status)) return 'pill-green';
  if (['disputed', 'canceled_by_client', 'canceled_by_service', 'canceled_timeout', 'blocked', 'open'].includes(status))
    return 'pill-red';
  if (['price_adjustment_pending', 'awaiting_advance', 'awaiting_final_payment', 'pending_verification', 'draft'].includes(status))
    return 'pill-warn';
  return 'pill-blue';
}

function fillCreds(email, password) {
  document.getElementById('login-email').value = email;
  document.getElementById('login-pass').value = password;
}

async function api(url, opts = {}) {
  const res = await fetch(url, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_) {
    data = { raw: text };
  }
  if (!res.ok) {
    const msg = (data.error && data.error.message) || data.detail || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

async function doLogin() {
  try {
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-pass').value;
    const data = await api('/api/v1/admin/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
    currentAdmin = data.admin;
    enterAdmin();
  } catch (e) {
    toast('Ошибка входа: ' + e.message);
  }
}

async function logout() {
  await api('/api/v1/admin/auth/logout', { method: 'POST' });
  currentAdmin = null;
  document.getElementById('admin-view').style.display = 'none';
  document.getElementById('login-view').style.display = 'flex';
  document.getElementById('logout-btn').style.display = 'none';
  document.getElementById('role-badge').textContent = 'ВХОД';
}

function enterAdmin() {
  document.getElementById('login-view').style.display = 'none';
  document.getElementById('admin-view').style.display = 'grid';
  document.getElementById('logout-btn').style.display = 'inline-flex';
  document.getElementById('role-badge').textContent = `${currentAdmin.role.toUpperCase()} · ${currentAdmin.email}`;
  loadTab(currentTab);
}

function showTab(tab, btn) {
  currentTab = tab;
  document.querySelectorAll('.nav-item').forEach((b) => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  loadTab(tab);
}

async function loadTab(tab) {
  const root = document.getElementById('admin-content');
  root.innerHTML = '<div class="ice-panel">Загрузка данных узла…</div>';
  try {
    if (tab === 'dashboard') await renderDashboard(root);
    else if (tab === 'orders') await renderOrders(root);
    else if (tab === 'tariffs') await renderTariffs(root);
    else if (tab === 'zones') await renderZones(root);
    else if (tab === 'executors') await renderExecutors(root);
    else if (tab === 'disputes') await renderDisputes(root);
    else if (tab === 'payouts') await renderPayouts(root);
    else if (tab === 'marketplace') await renderMarketplacePreview(root);
    else if (tab === 'audit') await renderAudit(root);
    else if (tab === 'privacy') await renderPrivacyRequests(root);
  } catch (e) {
    root.innerHTML = `<div class="ice-panel"><span class="pill pill-red">ОШИБКА</span> <span style="margin-left:8px">${esc(e.message)}</span></div>`;
  }
}

async function renderDashboard(root) {
  const d = await api('/api/v1/admin/dashboard/summary');
  const attentionRows = d.attention_orders.length
    ? d.attention_orders
        .map(
          (o) => `<tr>
        <td class="mono"><b>#${o.public_number}</b></td>
        <td>${esc(o.public_area_hint)}</td>
        <td><span class="pill pill-warn">${esc(o.status_label)}</span></td>
        <td class="mono"><b>${fmtRub(o.final_total)}</b></td>
        <td style="text-align:right"><button class="btn btn-sm btn-primary" onclick="openOrder('${o.id}')">Досье</button></td>
      </tr>`
        )
        .join('')
    : '<tr><td colspan="5" style="color:var(--text-muted);text-align:center;padding:22px">Критических инцидентов нет — всё работает в штатном зимнем режиме.</td></tr>';

  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="section-kicker">СВОДКА ДИСПЕТЧЕРСКОЙ</div>
        <h2>Операционный дашборд</h2>
      </div>
      <button class="btn btn-sm" onclick="loadTab('dashboard')">↻ Обновить телеметрию</button>
    </div>

    <div class="kpi-grid">
      <div class="ice-panel ice-interactive kpi-tile">
        <div class="kpi-label">ВСЕГО ЗАКАЗОВ</div>
        <div class="kpi-val cryo">${d.total_orders}</div>
      </div>
      <div class="ice-panel ice-interactive kpi-tile">
        <div class="kpi-label">ОБОРОТ (GMV)</div>
        <div class="kpi-val">${fmtRub(d.gmv_rub)}</div>
      </div>
      <div class="ice-panel ice-interactive kpi-tile">
        <div class="kpi-label">ОПЛАЧЕНО СБП</div>
        <div class="kpi-val mint">${fmtRub(d.paid_rub)}</div>
      </div>
      <div class="ice-panel ice-interactive kpi-tile">
        <div class="kpi-label">К ВЫПЛАТЕ БРИГАДЕ</div>
        <div class="kpi-val">${fmtRub(d.accrued_payouts_rub)}</div>
      </div>
      <div class="ice-panel ice-interactive kpi-tile">
        <div class="kpi-label">ОТКРЫТЫЕ СПОРЫ</div>
        <div class="kpi-val amber">${d.open_disputes} <span class="mono" style="font-size:13px;color:var(--text-muted)">(${d.dispute_share_pct}%)</span></div>
      </div>
      <div class="ice-panel ice-interactive kpi-tile">
        <div class="kpi-label">НА ВЕРИФИКАЦИИ</div>
        <div class="kpi-val">${d.pending_executors}</div>
      </div>
    </div>

    <div class="ice-panel">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <div>
          <div class="section-kicker">РАДАР ЭСКАЛАЦИЙ</div>
          <h3 style="margin:4px 0 0;font-size:16px">Заказы, требующие внимания диспетчера</h3>
        </div>
        <span class="pill pill-warn">${d.attention_orders.length} шт.</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>№</th><th>Ориентир</th><th>Статус</th><th>Сумма</th><th></th></tr></thead>
          <tbody>${attentionRows}</tbody>
        </table>
      </div>
    </div>
  `;
}

function renderOrderRows(orders) {
  if (!orders.length) {
    return '<tr><td colspan="7" style="color:var(--text-muted);text-align:center;padding:28px">Заказов по фильтру пока нет — новые заявки с сайта и из MAX-бота появятся здесь автоматически.</td></tr>';
  }
  return orders
    .map(
      (o) => `
    <tr>
      <td class="mono">
        <b>#${o.public_number}</b>
        <div style="font-size:10px;color:var(--text-muted);margin-top:2px">${o.source === 'web_site' ? 'САЙТ' : 'MAX'}</div>
      </td>
      <td>${esc(o.service_label)}</td>
      <td>
        <b>${esc(o.zone_name || '—')}</b>
        <div style="font-size:11.5px;color:var(--text-muted)">${esc(o.address_text)}</div>
      </td>
      <td>
        <div>${esc(o.client_name)}</div>
        ${o.client_phone ? `<div class="mono" style="font-size:11px;color:var(--cryo)">${esc(o.client_phone)}</div>` : ''}
      </td>
      <td><span class="pill ${statusPillClass(o.status)}">${esc(o.status_label)}</span></td>
      <td class="mono">
        <b>${fmtRub(o.final_total)}</b>
        <div style="font-size:11px;color:var(--text-muted)">Опл: ${fmtRub(o.paid_amount)}</div>
      </td>
      <td style="text-align:right"><button class="btn btn-sm btn-primary" onclick="openOrder('${o.id}')">Досье</button></td>
    </tr>`
    )
    .join('');
}

function filterOrdersTable() {
  const q = (document.getElementById('ord-search')?.value || '').toLowerCase().trim();
  const st = document.getElementById('ord-status-filter')?.value || '';
  const filtered = cachedOrders.filter((o) => {
    if (st && o.status !== st) return false;
    if (q) {
      const hay = `${o.public_number} ${o.address_text} ${o.client_name} ${o.client_phone || ''} ${o.zone_name} ${o.service_label}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const tbody = document.getElementById('orders-tbody');
  if (tbody) tbody.innerHTML = renderOrderRows(filtered);
}

async function renderOrders(root) {
  const [orders, execs] = await Promise.all([
    api('/api/v1/admin/orders'),
    api('/api/v1/admin/executors'),
  ]);
  cachedExecutors = execs;
  cachedOrders = orders;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="section-kicker">ОПЕРАТИВНЫЙ УЧЁТ</div>
        <h2>Реестр заказов (${orders.length})</h2>
      </div>
      <button class="btn btn-sm" onclick="loadTab('orders')">↻ Обновить</button>
    </div>

    <div class="filter-bar">
      <input class="input" id="ord-search" style="max-width:320px" placeholder="Поиск по №, адресу, клиенту, зоне..." oninput="filterOrdersTable()" />
      <select id="ord-status-filter" style="max-width:240px" onchange="filterOrdersTable()">
        <option value="">Все статусы</option>
        <option value="awaiting_advance">Ожидает аванс</option>
        <option value="searching_executor">Поиск исполнителя</option>
        <option value="assigned">Назначен</option>
        <option value="en_route">В пути</option>
        <option value="arrived">На месте</option>
        <option value="price_adjustment_pending">Согласование цены</option>
        <option value="in_progress">В работе</option>
        <option value="awaiting_acceptance">Ожидает приёмки</option>
        <option value="awaiting_final_payment">Ожидает доплаты</option>
        <option value="completed">Завершён</option>
        <option value="disputed">Спор</option>
      </select>
    </div>

    <div class="ice-panel table-wrap">
      <table>
        <thead><tr><th>№</th><th>Услуга</th><th>Локация</th><th>Клиент</th><th>Статус</th><th>Финансы</th><th></th></tr></thead>
        <tbody id="orders-tbody">${renderOrderRows(orders)}</tbody>
      </table>
    </div>
  `;
}

async function openOrder(orderId) {
  const o = await api(`/api/v1/admin/orders/${orderId}`);
  const mediaHtml = o.media.length
    ? `<div class="photo-grid">${o.media
        .map(
          (m) => `
        <div class="photo-card" onclick="IceDialog.lightbox('${esc(m.url)}', 'Заказ #${o.public_number} · ${esc(m.stage.toUpperCase())}')">
          <div class="mono" style="font-size:10.5px;font-weight:700;color:var(--cryo);margin-bottom:5px">${esc(m.stage.toUpperCase())}</div>
          <img src="${esc(m.url)}" alt="${esc(m.stage)}" />
        </div>`
        )
        .join('')}</div>`
    : '<div style="color:var(--text-muted);font-size:13px">Фотографий пока нет — исполнитель загрузит ДО и ПОСЛЕ в боте.</div>';

  const execOptions = cachedExecutors
    .filter((e) => e.status === 'active')
    .map((e) => `<option value="${e.user_id}">${esc(e.display_name)} (рейтинг ${e.rating})</option>`)
    .join('');

  document.getElementById('order-modal-body').innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:16px">
      <div>
        <div class="section-kicker">ДОСЬЕ ЗАКАЗА · ${esc(o.payment_status.toUpperCase())}</div>
        <h2 style="margin:5px 0 0;font-size:20px">Заказ <span class="mono">#${o.public_number}</span> · ${esc(o.service_label)}</h2>
      </div>
      <button class="btn btn-sm btn-ghost" onclick="closeModal()">Закрыть ✕</button>
    </div>

    <div class="split-2" style="margin-bottom:14px">
      <div class="ice-panel" style="padding:14px">
        <div class="field-label">МАРШРУТ И КЛИЕНТ</div>
        <div style="display:flex;flex-direction:column;gap:5px;font-size:13px">
          <div>Статус: <span class="pill ${statusPillClass(o.status)}">${esc(o.status_label)}</span> · <span class="pill">${o.source === 'web_site' ? 'Заявка с сайта' : 'MAX-бот'}</span></div>
          <div>Клиент: <b>${esc(o.client.display_name)}</b></div>
          <div>Точный адрес: <b>${esc(o.address_text)}</b></div>
          <div style="color:var(--text-muted)">В общем чате: ${esc(o.public_area_hint)}</div>
          <div>Связь: <code>${esc(o.contact_mode)}</code> ${o.client.shared_contact_phone ? `· <a href="tel:${esc(o.client.shared_contact_phone)}">${esc(o.client.shared_contact_phone)}</a>` : ''}${o.client.email ? ` · ${esc(o.client.email)}` : ''}</div>
          ${o.client_comment ? `<div style="margin-top:4px;padding:7px 9px;border-radius:7px;background:rgba(140,228,255,0.07);color:var(--text-frost)"><b>Комментарий:</b> ${esc(o.client_comment)}</div>` : ''}
        </div>
      </div>

      <div class="ice-panel" style="padding:14px">
        <div class="field-label">РАСЧЁТ, СБП И МАРЖА ИП</div>
        <div class="receipt-row"><span>Ориентировочно</span><b class="mono">${fmtRub(o.estimated_total)}</b></div>
        <div class="receipt-row"><span>Итоговая сумма</span><b class="mono" style="color:var(--cryo)">${fmtRub(o.final_total)}</b></div>
        <div class="receipt-row"><span>Аванс / Оплачено</span><b class="mono">${fmtRub(o.advance_amount)} / ${fmtRub(o.paid_amount)}</b></div>
        <div class="receipt-row"><span>Скидка / Бонус бригаде</span><span class="mono">${fmtRub(o.group_discount_amount)} / ${fmtRub(o.executor_bonus_amount)}</span></div>
        ${
          o.pricing_snapshot?.unit_economics
            ? `
          <div style="margin-top:8px;padding-top:8px;border-top:1px dashed rgba(145,224,255,0.2)">
            ${Number(o.pricing_snapshot.unit_economics.equipment_rental_rub) > 0 ? `<div class="receipt-row"><span>Бюджет аренды техники</span><b class="mono" style="color:var(--beacon-amber)">${fmtRub(o.pricing_snapshot.unit_economics.equipment_rental_rub)}</b></div>` : ''}
            <div class="receipt-row"><span>Прогноз чистой прибыли ИП</span><b class="mono" style="color:var(--aurora-mint)">+${fmtRub(o.pricing_snapshot.unit_economics.owner_net_profit_rub)} (${esc(o.pricing_snapshot.unit_economics.owner_margin_pct)}%)</b></div>
          </div>`
            : ''
        }
      </div>
    </div>

    <div class="ice-panel" style="margin-bottom:14px;padding:14px">
      <div class="field-label" style="margin-bottom:10px">ФОТООТЧЁТ (КЛИК ДЛЯ УВЕЛИЧЕНИЯ)</div>
      ${mediaHtml}
    </div>

    <div class="ice-panel" style="margin-bottom:14px;padding:14px">
      <div class="field-label" style="margin-bottom:10px">ДИСПЕТЧЕРСКИЕ КОМАНДЫ</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
        ${
          ['awaiting_advance', 'searching_executor', 'partially_assigned'].includes(o.status)
            ? `<button class="btn btn-success btn-sm" onclick="dispatchToWorkers('${o.id}')">🚀 Отправить в чат рабочих</button>`
            : ''
        }
        <select id="assign-exec-sel" style="max-width:250px">${execOptions}</select>
        <button class="btn btn-primary btn-sm" onclick="assignExec('${o.id}')">Назначить исполнителя</button>
        <button class="btn btn-warn btn-sm" onclick="proposeAdj('${o.id}', '${o.final_total}')">Корректировка цены &gt;20%</button>
        <button class="btn btn-danger btn-sm" onclick="cancelOrd('${o.id}')">Отменить заказ</button>
      </div>
    </div>

    <div class="split-2">
      <div class="ice-panel" style="padding:14px">
        <div class="field-label" style="margin-bottom:8px">ПЛАТЕЖИ СБП И ЧЕКИ 54-ФЗ</div>
        ${
          o.payments
            .map(
              (p) => `<div class="receipt-row">
            <span><b class="mono">${esc(p.purpose)}</b> · чек: <code>${esc(p.receipt_status || '—')}</code></span>
            <span><b class="mono">${fmtRub(p.amount)}</b> <span class="pill ${statusPillClass(p.status)}">${esc(p.status)}</span></span>
          </div>`
            )
            .join('') || '<div style="color:var(--text-muted)">Платежей нет</div>'
        }
      </div>
      <div class="ice-panel" style="padding:14px">
        <div class="field-label" style="margin-bottom:10px">ХРОНОЛОГИЯ СТАТУСОВ</div>
        <div class="timeline">
          ${o.history
            .map(
              (h) => `<div class="tl-item">
            <span class="mono" style="color:var(--text-muted);font-size:11px">${(h.created_at || '').slice(11, 19)}</span>
            <b style="margin-left:6px">${esc(h.to_status)}</b>
            <div style="color:var(--text-frost);font-size:12px">${esc(h.reason || '')}</div>
          </div>`
            )
            .join('')}
        </div>
      </div>
    </div>
  `;
  document.getElementById('order-modal').classList.add('open');
}

function closeModal() {
  document.getElementById('order-modal').classList.remove('open');
}

async function dispatchToWorkers(orderId) {
  try {
    await api(`/api/v1/admin/orders/${orderId}/dispatch`, { method: 'POST' });
    toast('Карточка заказа отправлена рабочим в чат бригады MAX!');
    await openOrder(orderId);
  } catch (e) {
    toast('Ошибка: ' + e.message);
  }
}

async function assignExec(orderId) {
  try {
    const executor_user_id = document.getElementById('assign-exec-sel').value;
    await api(`/api/v1/admin/orders/${orderId}/assign`, {
      method: 'POST',
      body: JSON.stringify({ executor_user_id }),
    });
    toast('Исполнитель назначен, уведомление отправлено в MAX');
    await openOrder(orderId);
  } catch (e) {
    toast('Ошибка: ' + e.message);
  }
}

async function proposeAdj(orderId, currentTotal) {
  const vals = await IceDialog.form({
    title: 'Корректировка цены (>20%)',
    subtitle: 'Новая сумма вступит в силу только после подтверждения клиентом в MAX-боте.',
    submitText: 'Отправить клиенту',
    submitClass: 'btn-warn',
    fields: [
      { name: 'new_total', label: 'Новая итоговая сумма (₽)', type: 'number', value: currentTotal, required: true },
      { name: 'reason', label: 'Обоснование расхождения', type: 'textarea', value: 'Фактический объём и плотный наст выше заявленного', required: true },
    ],
  });
  if (!vals) return;
  try {
    await api(`/api/v1/admin/orders/${orderId}/price-adjustment`, {
      method: 'POST',
      body: JSON.stringify({ new_total: vals.new_total, reason: vals.reason }),
    });
    toast('Запрос на корректировку цены отправлен клиенту в MAX');
    await openOrder(orderId);
  } catch (e) {
    toast('Ошибка: ' + e.message);
  }
}

async function cancelOrd(orderId) {
  const vals = await IceDialog.form({
    title: 'Отмена заказа диспетчером',
    subtitle: 'При отмене со стороны сервиса клиенту возвращается 100% оплаченного аванса.',
    submitText: 'Отменить заказ',
    submitClass: 'btn-danger',
    fields: [
      { name: 'reason', label: 'Причина отмены', type: 'textarea', value: 'Отменено диспетчером', required: true },
    ],
  });
  if (!vals) return;
  try {
    await api(`/api/v1/admin/orders/${orderId}/cancel`, {
      method: 'POST',
      body: JSON.stringify({ reason: vals.reason, by_service: true }),
    });
    toast('Заказ отменён, возврат инициирован');
    closeModal();
    loadTab('orders');
  } catch (e) {
    toast('Ошибка: ' + e.message);
  }
}

async function renderMarketplacePreview(root) {
  if (!currentAdmin || !['admin', 'owner'].includes(currentAdmin.role)) {
    root.innerHTML = '<div class="ice-panel">Раздел доступен владельцу и администратору.</div>';
    return;
  }
  root.innerHTML = `
    <div class="page-head"><div>
      <div class="section-kicker">ВНУТРЕННИЙ ПРОЕКТ · НЕ ДЕЙСТВУЮЩИЕ УСЛОВИЯ</div>
      <h2>Новая модель: расчёт и макеты</h2>
    </div><span class="pill pill-warn">ВНУТРЕННИЙ ПРОЕКТ</span></div>
    <div class="ice-panel" style="display:grid;gap:14px;margin-bottom:16px">
      <p>Это предварительная модель для одного исполнителя. Новые условия ещё не включены:
      заказы, платежи и сообщения в MAX не создаются. Расчёты и документы требуют согласования.</p>
      <form id="marketplace-form" style="display:grid;gap:12px">
        <div class="split-2">
          <div><label class="field-label" for="mp-total">Полная стоимость заказа, ₽</label>
            <input class="input" id="mp-total" type="number" min="0.01" max="9999999999.99" step="0.01" value="10000" required /></div>
          <div><label class="field-label" for="mp-schedule">График оплаты</label>
            <select id="mp-schedule">
              <option value="advance_27">Аванс 27%, остаток после работ</option>
              <option value="full_upfront">Полная предоплата</option>
              <option value="after_completion">Полностью после работ</option>
            </select></div>
        </div>
        <div><label class="field-label" for="mp-route">Способ оплаты после работ</label>
          <select id="mp-route">
            <option value="payment_provider">Через платёжного партнёра</option>
            <option value="direct_to_executor">Напрямую исполнителю</option>
          </select></div>
        <div><label class="field-label" for="mp-rental">Ориентировочная аренда техники для исполнителя, ₽ (необязательно)</label>
          <input class="input" id="mp-rental" type="number" min="0" max="9999999999.99" step="0.01" placeholder="Не указана" />
          <small>Аренда входит в полную цену. Комиссия считается от всей суммы. Это только проверка экономики, не утверждённый бюджет.</small></div>
        <button class="btn btn-primary" type="submit">Рассчитать и показать макеты</button>
      </form>
      <div id="mp-result" aria-live="polite" style="font-size:14px"></div>
    </div>
    <div class="ice-panel" style="display:grid;gap:12px">
      <h3>Внутренние черновики условий</h3>
      <p>Черновик не является заказом, договором или согласием сторон. Не вносите персональные данные: здесь сохраняются только вид услуги и параметры расчёта. Версии не изменяют действующие заказы.</p>
      <form id="draft-form" style="display:grid;gap:10px">
        <div id="draft-edit-label">Новый черновик</div>
        <div class="split-2"><div><label class="field-label" for="draft-service">Услуга</label><select id="draft-service">
          <option value="snow_yard">Уборка снега во дворе</option><option value="snow_road">Расчистка дороги</option>
          <option value="car_rescue">Машина в снегу</option><option value="yard_help">Помощь по двору</option>
          <option value="movers">Грузчики</option><option value="roof_cleaning">Расчистка крыши</option><option value="reagents">Посыпка реагентами</option>
        </select></div><div><label class="field-label" for="draft-total">Стоимость, ₽</label><input class="input" id="draft-total" type="number" min="0.01" max="9999999999.99" step="0.01" value="10000" required /></div></div>
        <div class="split-2"><div><label class="field-label" for="draft-schedule">График оплаты</label><select id="draft-schedule"><option value="advance_27">Аванс 27%</option><option value="full_upfront">Полная предоплата</option><option value="after_completion">После работ</option></select></div>
        <div><label class="field-label" for="draft-route">Оплата после работ</label><select id="draft-route"><option value="payment_provider">Платёжный партнёр</option><option value="direct_to_executor">Напрямую исполнителю</option></select></div></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap"><button type="submit" class="btn btn-primary" id="draft-save">Сохранить черновик</button><button type="button" class="btn" id="draft-reset">Новый черновик</button></div>
      </form>
      <div id="draft-list" aria-live="polite">Загрузка…</div><div id="draft-detail"></div>
    </div>
  `;
  const form = document.getElementById('marketplace-form');
  const schedule = document.getElementById('mp-schedule');
  const route = document.getElementById('mp-route');
  const result = document.getElementById('mp-result');
  let revision = 0;
  form.addEventListener('input', () => {
    revision++;
    route.disabled = schedule.value === 'full_upfront';
    result.textContent = '';
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const currentRevision = ++revision;
    const request = {
      total: document.getElementById('mp-total').value,
      schedule: schedule.value,
      final_payment_route: schedule.value === 'full_upfront' ? null : route.value,
      rental_estimate: document.getElementById('mp-rental').value || null,
    };
    result.textContent = 'Расчёт…';
    try {
      const data = await api('/api/v1/admin/marketplace/preview', {
        method: 'POST', body: JSON.stringify(request),
      });
      if (!result.isConnected || currentRevision !== revision) return;
      const rub = (amount) => `${esc(amount)} ₽`;
      result.innerHTML = `
        <div class="receipt-box" style="margin-top:8px">
          <b>Предварительный расчёт</b><br>
          Стоимость: ${rub(data.total)} · Комиссия 27%: ${rub(data.commission)}<br>
          Исполнителю после комиссии: ${rub(data.executor_after_commission)} до налогов и расходов.<br>
          Аванс: ${rub(data.upfront)} · После работ: ${rub(data.after_completion)}
          ${data.rental_estimate !== null ? `<br>Аренда: ${rub(data.rental_estimate)} · После комиссии и аренды: ${rub(data.after_commission_and_rental)} до налогов и прочих расходов.${Number(data.after_commission_and_rental) <= 0 ? ' Внимание: на вознаграждение и прочие расходы ничего не остаётся.' : ''}` : '<br>Аренда не указана: рентабельность не оценена.'}
        </div>
        ${[
          ['Пример для сайта', data.site_example],
          ['Пример для MAX-бота', data.bot_example],
          ['Пример для группы MAX', data.group_example],
        ].map(([label, text]) => `<div class="receipt-box" style="margin-top:10px"><b>${esc(label)}</b><div style="white-space:pre-wrap;margin-top:8px">${esc(text)}</div></div>`).join('')}
        <p>Пример для группы не отправляется. Действующие заказы остаются на прежних условиях.</p>
      `;
    } catch (e) {
      if (result.isConnected && currentRevision === revision) result.textContent = 'Ошибка расчёта: ' + e.message;
    }
  });
  initMarketplaceDrafts();
}

function initMarketplaceDrafts() {
  const form = document.getElementById('draft-form');
  const list = document.getElementById('draft-list');
  const detail = document.getElementById('draft-detail');
  const schedule = document.getElementById('draft-schedule');
  const route = document.getElementById('draft-route');
  const save = document.getElementById('draft-save');
  let selected = null;
  let detailRevision = 0;
  const reset = () => {
    selected = null;
    document.getElementById('draft-edit-label').textContent = 'Новый черновик';
    save.textContent = 'Сохранить черновик';
  };
  const loadList = async () => {
    try {
      const drafts = await api('/api/v1/admin/marketplace/drafts');
      if (!list.isConnected) return;
      list.innerHTML = drafts.length ? `<b>Последние черновики</b><div style="display:grid;gap:6px;margin-top:8px">${drafts.map((d) =>
        `<button type="button" class="btn btn-sm" data-draft-id="${esc(d.id)}">${esc(d.id.slice(0, 8))} · версия ${esc(d.revision)} · ${d.status === 'archived' ? 'архив' : 'черновик'}</button>`
      ).join('')}</div>` : 'Черновиков пока нет.';
    } catch (e) { if (list.isConnected) list.textContent = 'Не удалось загрузить черновики: ' + e.message; }
  };
  const openDraft = async (id) => {
    const token = ++detailRevision;
    detail.textContent = 'Загрузка версий…';
    try {
      const draft = await api(`/api/v1/admin/marketplace/drafts/${encodeURIComponent(id)}`);
      if (!detail.isConnected || token !== detailRevision) return;
      const last = draft.versions[draft.versions.length - 1];
      detail.innerHTML = `<div class="receipt-box" style="margin-top:10px"><b>Черновик ${esc(draft.id.slice(0, 8))} · ${draft.status === 'archived' ? 'архив' : 'активен'}</b>
        <div style="margin:8px 0">${draft.versions.map((v) => `Версия ${esc(v.revision)}: ${esc(v.service_type)} · ${esc(v.total)} ₽ · комиссия ${esc(v.commission)} ₽ · ${esc(v.schedule)} · ${esc(v.final_payment_route || 'без остатка')} · до работ ${esc(v.upfront)} ₽ · после ${esc(v.after_completion)} ₽<br>`).join('')}</div>
        ${draft.status === 'draft' ? '<button type="button" class="btn btn-sm" id="draft-edit">Изменить: создать новую версию</button> <button type="button" class="btn btn-sm" id="draft-archive">В архив</button>' : ''}</div>`;
      const edit = document.getElementById('draft-edit');
      if (edit) edit.addEventListener('click', () => {
        selected = { id: draft.id, revision: draft.revision };
        document.getElementById('draft-edit-label').textContent = `Новая версия черновика ${draft.id.slice(0, 8)} (текущая: ${draft.revision})`;
        document.getElementById('draft-service').value = last.service_type;
        document.getElementById('draft-total').value = last.total;
        schedule.value = last.schedule;
        if (last.final_payment_route) route.value = last.final_payment_route;
        route.disabled = schedule.value === 'full_upfront';
        save.textContent = 'Создать новую версию';
      });
      const archive = document.getElementById('draft-archive');
      if (archive) archive.addEventListener('click', async () => {
        if (!window.confirm('Архивировать черновик? Его историю можно будет просмотреть, но изменить больше нельзя.')) return;
        archive.disabled = true;
        try {
          await api(`/api/v1/admin/marketplace/drafts/${encodeURIComponent(id)}/archive`, { method: 'POST', body: JSON.stringify({ expected_revision: draft.revision }) });
          if (selected && selected.id === id) reset();
          await loadList(); await openDraft(id);
        } catch (e) { toast('Ошибка: ' + e.message); archive.disabled = false; }
      });
    } catch (e) { if (detail.isConnected && token === detailRevision) detail.textContent = 'Ошибка: ' + e.message; }
  };
  list.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-draft-id]');
    if (btn) openDraft(btn.dataset.draftId);
  });
  document.getElementById('draft-reset').addEventListener('click', reset);
  schedule.addEventListener('change', () => { route.disabled = schedule.value === 'full_upfront'; });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    save.disabled = true;
    const terms = {
      service_type: document.getElementById('draft-service').value,
      total: document.getElementById('draft-total').value,
      schedule: schedule.value,
      final_payment_route: schedule.value === 'full_upfront' ? null : route.value,
    };
    const editing = selected;
    const url = editing ? `/api/v1/admin/marketplace/drafts/${encodeURIComponent(editing.id)}/versions` : '/api/v1/admin/marketplace/drafts';
    if (editing) terms.expected_revision = editing.revision;
    try {
      const saved = await api(url, { method: 'POST', body: JSON.stringify(terms) });
      const id = editing ? editing.id : saved.id;
      reset(); await loadList(); await openDraft(id);
      toast('Внутренний черновик сохранён');
    } catch (e) { toast('Ошибка: ' + e.message); }
    finally { save.disabled = false; }
  });
  loadList();
}

async function renderTariffs(root) {
  const cur = await api('/api/v1/admin/tariffs/current');
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="section-kicker">ТАРИФНАЯ МАТРИЦА · ВЕРСИЯ V${cur.version}</div>
        <h2>Тарифы и интерактивная песочница</h2>
      </div>
      <span class="pill pill-blue">ОКРУГЛЕНИЕ ДО 50 ₽</span>
    </div>

    <div class="split-2">
      <div class="ice-panel">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">
          <div class="field-label" style="margin:0">КОНФИГУРАЦИЯ ТАРИФА (JSON)</div>
          <button type="button" class="btn btn-sm" onclick="applyMarket2026Tariff()" title="Подставить рыночные ставки Московской области 2026 для предпросмотра">
            ❄ Подставить прайс МО 2026
          </button>
        </div>
        <textarea id="tariff-json" rows="21" class="mono" style="font-size:11.5px;line-height:1.5">${esc(JSON.stringify(cur.config_json, null, 2))}</textarea>
        <div style="display:flex;gap:8px;margin-top:12px">
          <input class="input" id="tariff-comment" placeholder="Комментарий к версии v${cur.version + 1}..." />
          <button class="btn btn-primary" style="white-space:nowrap" onclick="saveTariff()">Сохранить v${cur.version + 1}</button>
        </div>
      </div>

      <div class="ice-panel" style="display:flex;flex-direction:column;gap:12px">
        <div>
          <div class="section-kicker">ПЕСОЧНИЦА КАЛЬКУЛЯТОРА</div>
          <h3 style="margin:5px 0 0;font-size:16px">Проверка формул до публикации</h3>
        </div>

        <div>
          <label class="field-label">Тип услуги</label>
          <select id="pv-svc">
            <option value="snow_yard">Уборка снега во дворе</option>
            <option value="snow_road">Расчистка дороги / проезда</option>
            <option value="car_rescue">Срочно застрял</option>
            <option value="yard_help">Помощь по двору</option>
            <option value="movers">Грузчики на переезд</option>
          </select>
        </div>

        <div class="split-2">
          <div>
            <label class="field-label">Площадь (м²)</label>
            <input class="input mono" id="pv-area" type="number" value="120" />
          </div>
          <div>
            <label class="field-label">Глубина снега</label>
            <select id="pv-depth">
              <option value="up_to_10">До 10 см (×1.0)</option>
              <option value="from_10_to_30" selected>10–30 см (×1.5)</option>
              <option value="over_30">Больше 30 см (×2.0)</option>
            </select>
          </div>
        </div>

        <div class="split-2">
          <div>
            <label class="field-label">Срочность</label>
            <select id="pv-urg">
              <option value="today">В течение дня (×1.0)</option>
              <option value="within_3h" selected>До 3 часов (×1.3)</option>
              <option value="now_or_night">Сейчас / Ночь (×1.5)</option>
            </select>
          </div>
          <div>
            <label class="field-label">Геозона</label>
            <select id="pv-zone">
              <option value="malakhovka">Малаховка</option>
              <option value="tomilino">Томилино</option>
              <option value="kraskovo">Красково</option>
              <option value="bykovo">Быково</option>
              <option value="lytkarino">Лыткарино</option>
            </select>
          </div>
        </div>

        <div class="split-2">
          <div>
            <label class="field-label">Заезд / техника</label>
            <select id="pv-access">
              <option value="narrow_gate">Узкая калитка (вручную)</option>
              <option value="standard" selected>Обычные ворота (снегоуб.)</option>
              <option value="wide_road">Широкий заезд (трактор &gt;350 м²)</option>
            </select>
          </div>
          <div style="display:flex;align-items:flex-end;padding-bottom:6px">
            <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer;color:var(--text-frost)">
              <input type="checkbox" id="pv-removal" onchange="runPreview()" />
              <span>Вывоз снега (самосвал)</span>
            </label>
          </div>
        </div>

        <button class="btn btn-success" onclick="runPreview()">Рассчитать смету и маржу</button>
        <div id="pv-out" class="receipt-box">
          <div style="color:var(--text-muted);font-size:12.5px">Нажмите «Рассчитать смету», чтобы увидеть детализацию.</div>
        </div>
      </div>
    </div>
  `;
  runPreview();
}

function applyMarket2026Tariff() {
  try {
    const area = document.getElementById('tariff-json');
    const cfg = JSON.parse(area.value);
    cfg.min_order_total_rub = 2000;
    cfg.min_advance_rub = 700;
    cfg.subcontract_advance_pct = 50;
    cfg.tax_and_acquiring_pct = 7.9;
    cfg.equipment_rental_estimates = {
      snowblower_rental_rub: 2200,
      tractor_subcontract_share_pct: 70,
      snow_removal_subcontract_share_pct: 72,
      car_rescue_subcontract_share_pct: 65,
      tool_rental_rub: 1200,
    };
    if (cfg.advance_pct_by_service) {
      cfg.advance_pct_by_service.snow_road = 50;
    }
    if (cfg.snow && cfg.snow.methods) {
      if (cfg.snow.methods.manual) {
        cfg.snow.methods.manual.rate_per_m2 = 40;
        cfg.snow.methods.manual.min_work_price = 2500;
      }
      if (cfg.snow.methods.snowblower_or_miniloader) {
        cfg.snow.methods.snowblower_or_miniloader.rate_per_m2 = 42;
        cfg.snow.methods.snowblower_or_miniloader.min_work_price = 5500;
      }
      if (cfg.snow.methods.tractor) {
        cfg.snow.methods.tractor.rate_per_m2 = 30;
        cfg.snow.methods.tractor.min_work_price = 14500;
      }
    }
    if (cfg.snow && cfg.snow.auto_select_thresholds_m2) {
      cfg.snow.auto_select_thresholds_m2.manual_max = 100;
      cfg.snow.auto_select_thresholds_m2.small_tech_max = 350;
    }
    if (cfg.snow && cfg.snow.snow_removal) {
      cfg.snow.snow_removal.rate_per_m3 = 850;
      cfg.snow.snow_removal.min_removal_price = 13500;
      delete cfg.snow.snow_removal.min_price;
    }
    if (cfg.car_rescue) {
      cfg.car_rescue.base_price = 3500;
      cfg.car_rescue.per_km_rate = 120;
      cfg.car_rescue.vehicle_surcharge = { sedan: 0, suv: 700, commercial: 2000 };
    }
    if (cfg.hourly) {
      cfg.hourly.yard_help_rate_per_hour = 700;
      cfg.hourly.movers_rate_per_hour = 750;
      cfg.hourly.floor_no_lift_surcharge_per_worker = 200;
      cfg.hourly.equipment_fee = 2000;
    }
    if (cfg.roof_and_reagents) {
      cfg.roof_and_reagents.roof_rate_per_m2 = 90;
      cfg.roof_and_reagents.roof_min_price = 6000;
      cfg.roof_and_reagents.reagents_rate_per_m2 = 20;
      cfg.roof_and_reagents.reagents_min_price = 1000;
    }
    area.value = JSON.stringify(cfg, null, 2);
    const commentEl = document.getElementById('tariff-comment');
    if (commentEl && !commentEl.value.trim()) {
      commentEl.value = 'Прайс МО 2026 с учётом аренды техники на субподряде';
    }
    runPreview();
    toast('Подставлены ставки МО 2026 с арендой техники — проверьте маржу справа и нажмите «Сохранить»');
  } catch (e) {
    toast('Ошибка JSON: ' + e.message);
  }
}

async function saveTariff() {
  try {
    const config_json = JSON.parse(document.getElementById('tariff-json').value);
    const comment = document.getElementById('tariff-comment').value;
    const res = await api('/api/v1/admin/tariffs', {
      method: 'PUT',
      body: JSON.stringify({ config_json, comment }),
    });
    toast(`Опубликована версия тарифа v${res.version} (старые заказы защищены)`);
    loadTab('tariffs');
  } catch (e) {
    toast('Ошибка сохранения: ' + e.message);
  }
}

async function runPreview() {
  try {
    const override_config = JSON.parse(document.getElementById('tariff-json').value);
    const areaVal = Number(document.getElementById('pv-area').value) || 100;
    const res = await api('/api/v1/admin/tariffs/preview', {
      method: 'POST',
      body: JSON.stringify({
        service_type: document.getElementById('pv-svc').value,
        geo_zone_code: document.getElementById('pv-zone').value,
        area_m2: areaVal,
        length_m: Math.max(10, Math.round(areaVal / 4)),
        width_m: 4,
        depth_bucket: document.getElementById('pv-depth').value,
        access_constraint: document.getElementById('pv-access')?.value || 'standard',
        urgency: document.getElementById('pv-urg').value,
        snow_removal: Boolean(document.getElementById('pv-removal')?.checked),
        override_config,
      }),
    });
    const ue = res.pricing_snapshot?.unit_economics || null;
    const profitNum = ue ? Number(ue.owner_net_profit_rub) : 0;
    const profitColor = profitNum > 0 ? 'var(--aurora-mint)' : '#ff6b81';
    document.getElementById('pv-out').innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px">
        <span class="field-label" style="margin:0">КЛИЕНТУ К ОПЛАТЕ</span>
        <span class="receipt-total">${fmtRub(res.estimated_total)}</span>
      </div>
      <div class="receipt-row"><span>Стоимость работ</span><b class="mono">${fmtRub(res.work_cost)}</b></div>
      ${Number(res.extras_cost) > 0 ? `<div class="receipt-row"><span>Вывоз снега / допы</span><b class="mono">${fmtRub(res.extras_cost)}</b></div>` : ''}
      <div class="receipt-row"><span>Выезд бригады</span><b class="mono">${fmtRub(res.dispatch_fee)}</b></div>
      <div class="receipt-row"><span>Аванс СБП (${esc(res.pricing_snapshot?.advance_pct || '—')}%)</span><b class="mono" style="color:var(--cryo)">${fmtRub(res.advance_amount)}</b></div>
      <div class="receipt-row"><span>Остаток после приёмки</span><b class="mono">${fmtRub(res.remainder_amount)}</b></div>
      ${res.cleaning_method ? `<div class="receipt-row"><span>Автовыбор техники</span><code>${esc(res.cleaning_method)}</code></div>` : ''}
      ${
        ue
          ? `<div style="margin-top:10px;padding-top:10px;border-top:1px dashed rgba(145,224,255,0.22)">
              <div class="field-label" style="margin-bottom:6px">ЮНИТ-ЭКОНОМИКА ИП (СУБПОДРЯД / АРЕНДА)</div>
              ${Number(ue.equipment_rental_rub) > 0 ? `<div class="receipt-row"><span>Аренда техники / субподряд</span><b class="mono" style="color:var(--beacon-amber)">−${fmtRub(ue.equipment_rental_rub)}</b></div>` : ''}
              ${Number(ue.labor_payout_rub) > 0 ? `<div class="receipt-row"><span>Выплата бригаде</span><b class="mono" style="color:var(--beacon-amber)">−${fmtRub(ue.labor_payout_rub)}</b></div>` : ''}
              <div class="receipt-row"><span>УСН 6% + ЮKassa (~${esc(ue.tax_and_acquiring_pct)}%)</span><b class="mono">−${fmtRub(ue.tax_and_acquiring_rub)}</b></div>
              <div class="receipt-row" style="margin-top:4px;font-size:14px">
                <span><b>Чистая прибыль ИП</b></span>
                <b class="mono" style="color:${profitColor}">${profitNum >= 0 ? '+' : ''}${fmtRub(ue.owner_net_profit_rub)} (${esc(ue.owner_margin_pct)}%)</b>
              </div>
            </div>`
          : `<div class="receipt-row"><span>Прогноз выплаты исполнителю</span><b class="mono" style="color:var(--beacon-amber)">${fmtRub(res.estimated_executor_payout)}</b></div>`
      }
    `;
  } catch (e) {
    toast('Ошибка расчёта: ' + e.message);
  }
}

async function renderZones(root) {
  const zones = await api('/api/v1/admin/geo-zones');
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="section-kicker">ПОЛИГОНЫ И ВЫЕЗД</div>
        <h2>Геозоны обслуживания</h2>
      </div>
    </div>
    <div class="ice-panel table-wrap">
      <table>
        <thead><tr><th>Зона</th><th>Уровень</th><th>Выезд</th><th>Статус</th><th style="text-align:right">Управление</th></tr></thead>
        <tbody>${zones
          .map(
            (z) => `<tr>
          <td><b>${esc(z.name)}</b> <code style="margin-left:6px">${esc(z.code)}</code></td>
          <td><span class="pill pill-blue">${esc(z.tier)}</span></td>
          <td class="mono"><b>${fmtRub(z.dispatch_fee)}</b></td>
          <td><span class="pill ${z.is_active ? 'pill-green' : 'pill-red'}">${z.is_active ? 'АКТИВНА' : 'ВЫКЛ'}</span></td>
          <td style="text-align:right;display:flex;gap:6px;justify-content:flex-end">
            <button class="btn btn-sm" onclick="toggleZone('${z.id}', ${!z.is_active})">${z.is_active ? 'Отключить' : 'Включить'}</button>
            <button class="btn btn-sm btn-primary" onclick="editZoneFee('${z.id}', '${esc(z.name)}', '${z.dispatch_fee}')">Ставка выезда</button>
          </td>
        </tr>`
          )
          .join('')}</tbody>
      </table>
    </div>
  `;
}

async function toggleZone(id, is_active) {
  await api(`/api/v1/admin/geo-zones/${id}`, { method: 'PATCH', body: JSON.stringify({ is_active }) });
  toast('Статус геозоны обновлён');
  loadTab('zones');
}

async function editZoneFee(id, zoneName, cur) {
  const vals = await IceDialog.form({
    title: `Ставка выезда · ${zoneName}`,
    subtitle: 'Новая ставка применяется для всех последующих заказов в этой геозоне.',
    submitText: 'Сохранить ставку',
    fields: [{ name: 'dispatch_fee', label: 'Стоимость выезда (₽)', type: 'number', value: cur, required: true }],
  });
  if (!vals) return;
  await api(`/api/v1/admin/geo-zones/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ dispatch_fee: vals.dispatch_fee }),
  });
  toast('Ставка выезда обновлена');
  loadTab('zones');
}

async function renderExecutors(root) {
  const list = await api('/api/v1/admin/executors');
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="section-kicker">БРИГАДА И ТЕХНИКА</div>
        <h2>Исполнители и допуски</h2>
      </div>
    </div>
    <div class="ice-panel table-wrap">
      <table>
        <thead><tr><th>Исполнитель</th><th>Статус</th><th>Рейтинг</th><th>Оснащение / Допуски</th><th>К выплате</th><th style="text-align:right">Действия</th></tr></thead>
        <tbody>${list
          .map(
            (e) => `<tr>
          <td>
            <b>${esc(e.display_name)}</b>
            <div class="mono" style="font-size:11px;color:var(--text-muted)">ИНН: ${esc(e.inn || '—')} · ${esc(e.role)}</div>
          </td>
          <td><span class="pill ${statusPillClass(e.status)}">${esc(e.status)}</span></td>
          <td class="mono"><b>${e.rating} ★</b> <span style="color:var(--text-muted)">(${e.completed_orders_count})</span></td>
          <td style="display:flex;gap:5px;flex-wrap:wrap">
            ${e.has_snowblower ? '<span class="pill pill-blue">СНЕГОУБОРЩИК</span>' : ''}
            ${e.has_tractor ? '<span class="pill pill-blue">ТРАКТОР</span>' : ''}
            ${e.roof_certified ? '<span class="pill pill-green">ДОПУСК КРЫШИ</span>' : '<span class="pill">БЕЗ КРЫШ</span>'}
          </td>
          <td class="mono"><b>${fmtRub(e.accrued_balance_rub)}</b></td>
          <td style="text-align:right">
            <div style="display:inline-flex;gap:6px">
              ${e.status !== 'active' ? `<button class="btn btn-success btn-sm" onclick="verifyExec('${e.user_id}', 'active')">Активировать</button>` : ''}
              <button class="btn btn-sm" onclick="toggleRoof('${e.user_id}', ${!e.roof_certified})">${e.roof_certified ? 'Снять крыши' : 'Допуск крыш'}</button>
              <button class="btn btn-danger btn-sm" onclick="penaltyExec('${e.user_id}', '${esc(e.display_name)}')">Штраф</button>
            </div>
          </td>
        </tr>`
          )
          .join('')}</tbody>
      </table>
    </div>
  `;
}

async function verifyExec(id, status) {
  await api(`/api/v1/admin/executors/${id}/verify`, { method: 'PATCH', body: JSON.stringify({ status }) });
  toast('Статус исполнителя обновлён');
  loadTab('executors');
}

async function toggleRoof(id, roof_certified) {
  await api(`/api/v1/admin/executors/${id}/permissions`, { method: 'PATCH', body: JSON.stringify({ roof_certified }) });
  toast('Допуск к высотным работам обновлён');
  loadTab('executors');
}

async function penaltyExec(id, name) {
  const vals = await IceDialog.form({
    title: `Удержание / штраф · ${name}`,
    subtitle: 'Сумма будет отражена отдельной отрицательной записью в леджере выплат.',
    submitText: 'Применить штраф',
    submitClass: 'btn-danger',
    fields: [
      { name: 'amount', label: 'Сумма штрафа (₽)', type: 'number', value: '500', required: true },
      { name: 'reason', label: 'Основание', type: 'textarea', value: 'Опоздание / срыв выезда', required: true },
    ],
  });
  if (!vals) return;
  await api(`/api/v1/admin/executors/${id}/penalty`, {
    method: 'POST',
    body: JSON.stringify({ amount: vals.amount, reason: vals.reason }),
  });
  toast('Штраф зафиксирован в леджере');
  loadTab('executors');
}

async function renderDisputes(root) {
  const list = await api('/api/v1/admin/disputes');
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="section-kicker">АРБИТРАЖ КАЧЕСТВА</div>
        <h2>Споры и рекламации</h2>
      </div>
    </div>
    <div class="ice-panel table-wrap">
      <table>
        <thead><tr><th>Заказ</th><th>Претензия клиента</th><th>Статус</th><th>Решение</th><th style="text-align:right">Арбитраж</th></tr></thead>
        <tbody>${
          list.length
            ? list
                .map(
                  (d) => `<tr>
            <td class="mono"><b>#${d.order_public_number}</b></td>
            <td>${esc(d.reason)}</td>
            <td><span class="pill ${statusPillClass(d.status)}">${esc(d.status)}</span></td>
            <td>${esc(d.resolution_note || '—')}</td>
            <td style="text-align:right">
              ${
                d.status === 'open'
                  ? `<div style="display:inline-flex;gap:6px">
                      <button class="btn btn-sm" onclick="resDispute('${d.id}', 'resolved_rework')">Доработка</button>
                      <button class="btn btn-warn btn-sm" onclick="resDispute('${d.id}', 'resolved_discount')">Скидка</button>
                      <button class="btn btn-danger btn-sm" onclick="resDispute('${d.id}', 'resolved_refund')">Возврат</button>
                    </div>`
                  : '<span style="color:var(--text-muted)">Закрыт</span>'
              }
            </td>
          </tr>`
                )
                .join('')
            : '<tr><td colspan="5" style="color:var(--text-muted);text-align:center;padding:24px">Открытых споров нет</td></tr>'
        }</tbody>
      </table>
    </div>
  `;
}

async function resDispute(id, resolution) {
  const fields = [
    { name: 'note', label: 'Комментарий решения', type: 'textarea', value: 'Решено диспетчером по фотоотчёту', required: true },
  ];
  if (resolution === 'resolved_discount') {
    fields.push({ name: 'discount_amount', label: 'Размер скидки клиенту (₽)', type: 'number', value: '500', required: true });
  }
  if (resolution === 'resolved_refund') {
    fields.push({ name: 'refund_amount', label: 'Сумма возврата (₽, 0 = весь оплаченный аванс)', type: 'number', value: '0', required: true });
  }
  const titles = {
    resolved_rework: 'Назначить доработку',
    resolved_discount: 'Закрыть спор со скидкой',
    resolved_refund: 'Оформить возврат средств',
  };
  const vals = await IceDialog.form({
    title: titles[resolution] || 'Разрешение спора',
    submitText: 'Применить решение',
    submitClass: resolution === 'resolved_refund' ? 'btn-danger' : 'btn-primary',
    fields,
  });
  if (!vals) return;
  try {
    await api(`/api/v1/admin/disputes/${id}/resolve`, {
      method: 'POST',
      body: JSON.stringify({
        resolution,
        note: vals.note,
        discount_amount: Number(vals.discount_amount || 0),
        refund_amount: Number(vals.refund_amount || 0),
      }),
    });
    toast('Спор урегулирован, стороны уведомлены в MAX');
    loadTab('disputes');
  } catch (e) {
    toast('Ошибка: ' + e.message);
  }
}

async function renderPayouts(root) {
  const [balances, batches] = await Promise.all([
    api('/api/v1/admin/payouts/balances'),
    api('/api/v1/admin/payouts/batches'),
  ]);
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="section-kicker">РАСЧЁТЫ С САМОЗАНЯТЫМИ</div>
        <h2>Финансы и выплаты (НПД)</h2>
      </div>
      <a class="btn btn-sm btn-primary" href="/api/v1/admin/payouts/batches/export.csv">⬇ Экспорт реестра CSV</a>
    </div>

    <div class="ice-panel table-wrap" style="margin-bottom:16px">
      <div class="field-label" style="margin-bottom:10px">ТЕКУЩИЕ НАЧИСЛЕНИЯ БРИГАДЫ</div>
      <table>
        <thead><tr><th>Исполнитель</th><th>ИНН</th><th>К выплате</th><th>Выплачено ранее</th><th style="text-align:right">Ведомость</th></tr></thead>
        <tbody>${balances
          .map(
            (b) => `<tr>
          <td><b>${esc(b.display_name)}</b></td>
          <td class="mono">${esc(b.inn || '—')}</td>
          <td class="mono"><b style="color:var(--cryo)">${fmtRub(b.accrued_rub)}</b></td>
          <td class="mono">${fmtRub(b.paid_rub)}</td>
          <td style="text-align:right">${
            Number(b.accrued_rub) > 0
              ? `<button class="btn btn-primary btn-sm" onclick="createBatch('${b.executor_id}')">Сформировать</button>`
              : '<span style="color:var(--text-muted)">—</span>'
          }</td>
        </tr>`
          )
          .join('')}</tbody>
      </table>
    </div>

    <div class="ice-panel table-wrap">
      <div class="field-label" style="margin-bottom:10px">РЕЕСТР ВЕДОМОСТЕЙ И ЧЕКОВ «МОЙ НАЛОГ»</div>
      <table>
        <thead><tr><th>Исполнитель</th><th>Сумма</th><th>Статус</th><th>Чек НПД</th><th style="text-align:right">Проводка</th></tr></thead>
        <tbody>${
          batches.length
            ? batches
                .map(
                  (b) => `<tr>
            <td><b>${esc(b.executor_name)}</b></td>
            <td class="mono"><b>${fmtRub(b.total_amount)}</b></td>
            <td><span class="pill ${statusPillClass(b.status)}">${esc(b.status)}</span></td>
            <td>${b.npd_receipt_id ? `<code>${esc(b.npd_receipt_id)}</code>` : '—'}</td>
            <td style="text-align:right">${
              b.status === 'draft'
                ? `<button class="btn btn-success btn-sm" onclick="markPaid('${b.id}', '${esc(b.executor_name)}', '${b.total_amount}')">Выплатить + НПД</button>`
                : '<span style="color:var(--aurora-mint)" class="mono">ПРОВЕДЕНО</span>'
            }</td>
          </tr>`
                )
                .join('')
            : '<tr><td colspan="5" style="color:var(--text-muted);text-align:center;padding:22px">Ведомостей пока нет</td></tr>'
        }</tbody>
      </table>
    </div>
  `;
}

async function createBatch(executor_id) {
  try {
    await api('/api/v1/admin/payouts/batches', { method: 'POST', body: JSON.stringify({ executor_id }) });
    toast('Ведомость сформирована');
    loadTab('payouts');
  } catch (e) {
    toast('Ошибка: ' + e.message);
  }
}

async function markPaid(batchId, execName, amount) {
  const vals = await IceDialog.form({
    title: `Подтверждение выплаты · ${execName}`,
    subtitle: `Сумма к перечислению: ${fmtRub(amount)}. Зафиксируйте номер чека НПД.`,
    submitText: 'Провести выплату',
    submitClass: 'btn-success',
    fields: [
      { name: 'payment_method_note', label: 'Способ перевода', value: 'Перевод СБП самозанятому', required: true },
      { name: 'npd_receipt_id', label: 'Номер чека «Мой налог»', value: 'NPD-2026-0001', required: true },
      { name: 'npd_receipt_url', label: 'Ссылка на чек НПД (опционально)', value: 'https://lknpd.nalog.ru/api/v1/receipt/demo' },
    ],
  });
  if (!vals) return;
  await api(`/api/v1/admin/payouts/batches/${batchId}/mark-paid`, {
    method: 'POST',
    body: JSON.stringify(vals),
  });
  toast('Выплата проведена, чек НПД сохранён в реестре');
  loadTab('payouts');
}

async function renderAudit(root) {
  const [logs, chats] = await Promise.all([
    api('/api/v1/admin/audit-logs'),
    api('/api/v1/admin/bot-chats'),
  ]);
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="section-kicker">КОНТРОЛЬ ДОСТУПА И СОБЫТИЙ</div>
        <h2>Чаты MAX и журнал аудита</h2>
      </div>
    </div>

    <div class="ice-panel table-wrap" style="margin-bottom:16px">
      <div class="field-label" style="margin-bottom:10px">ПРИВЯЗАННЫЕ ЧАТЫ MAX (ЗАЯВКИ И БРИГАДА)</div>
      <table>
        <thead><tr><th>chat_id</th><th>Название</th><th>Роль чата</th><th>Права админа</th></tr></thead>
        <tbody>${chats
          .map(
            (c) => {
              const roleMap = {
                dispatch_pool: ['Чат рабочих (биржа)', 'pill-green'],
                foreman_alerts: ['Диспетчерская (уведомления)', 'pill-blue'],
                other: ['Прочий чат', 'pill-warn'],
              };
              const [rLabel, rCls] = roleMap[c.chat_purpose] || [c.chat_purpose, 'pill-blue'];
              return `<tr>
          <td><code>${c.chat_id}</code></td>
          <td><b>${esc(c.title)}</b></td>
          <td><span class="pill ${rCls}">${esc(rLabel)}</span></td>
          <td><span class="pill ${c.bot_is_admin ? 'pill-green' : 'pill-red'}">${c.bot_is_admin ? 'ДА' : 'НЕТ'}</span></td>
        </tr>`;
            }
          )
          .join('') || '<tr><td colspan="4" style="color:var(--text-muted)">Чаты ещё не привязаны. Напишите <code>/bind_alerts_chat ПАРОЛЬ</code> боту в ЛС и <code>/bind_dispatch_chat</code> в группе рабочих.</td></tr>'}</tbody>
      </table>
    </div>

    <div class="ice-panel table-wrap">
      <div class="field-label" style="margin-bottom:10px">ЖУРНАЛ ДЕЙСТВИЙ АДМИНИСТРАТОРОВ</div>
      <table>
        <thead><tr><th>Время</th><th>Учётная запись</th><th>Действие</th><th>Объект</th><th>Изменения</th></tr></thead>
        <tbody>${logs
          .map(
            (l) => `<tr>
          <td class="mono" style="font-size:11.5px;color:var(--text-muted)">${(l.created_at || '').slice(0, 19)}</td>
          <td>${esc(l.admin_email)}</td>
          <td><span class="pill pill-blue">${esc(l.action)}</span></td>
          <td class="mono" style="font-size:11.5px">${esc(l.entity_type)}:${esc(l.entity_id).slice(0, 8)}</td>
          <td><code style="font-size:11px">${esc(JSON.stringify(l.diff_json))}</code></td>
        </tr>`
          )
          .join('')}</tbody>
      </table>
    </div>
  `;
}

async function renderPrivacyRequests(root) {
  const rows = await api('/api/v1/admin/data-requests');
  const typeLabels = {
    delete_data: 'Удаление ПДн (ст. 21)',
    unsubscribe_marketing: 'Отписка от рассылок',
    export_data: 'Выгрузка ПДн (ст. 14)',
    revoke_consent: 'Отзыв согласия ПДн',
  };
  const statusPill = (s) => {
    if (s === 'completed') return '<span class="pill pill-green">ВЫПОЛНЕНО</span>';
    if (s === 'rejected') return '<span class="pill pill-red">ОТКЛОНЕНО</span>';
    return '<span class="pill pill-warn">В РАБОТЕ (ДО 10 ДН.)</span>';
  };

  root.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap">
      <div>
        <div class="section-kicker">152-ФЗ СТ. 14 И СТ. 21</div>
        <h2 style="margin:4px 0 0">Запросы субъектов персональных данных</h2>
      </div>
      <a class="btn btn-sm" href="/legal/my-data" target="_blank">Открыть публичную форму ↗</a>
    </div>

    <div class="ice-panel table-wrap">
      <table>
        <thead>
          <tr>
            <th>Номер</th>
            <th>Дата</th>
            <th>Тип запроса</th>
            <th>Контакт / MAX ID</th>
            <th>Источник</th>
            <th>Статус</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          ${
            rows.length
              ? rows
                  .map(
                    (r) => `<tr>
              <td class="mono"><b>${esc(r.ticket_code)}</b></td>
              <td class="mono" style="font-size:11.5px;color:var(--text-muted)">${(r.created_at || '').slice(0, 19)}</td>
              <td>${esc(typeLabels[r.request_type] || r.request_type)}</td>
              <td>
                <div>${esc(r.contact_value)}</div>
                ${r.max_user_id ? `<div class="mono" style="font-size:11px;color:var(--text-muted)">MAX: ${r.max_user_id}</div>` : ''}
                ${r.details ? `<div style="font-size:11.5px;color:var(--text-muted);margin-top:3px">${esc(r.details)}</div>` : ''}
              </td>
              <td><span class="pill pill-blue">${esc(r.source)}</span></td>
              <td>${statusPill(r.status)}</td>
              <td>
                ${
                  r.status === 'pending'
                    ? `<div style="display:flex;gap:6px;flex-wrap:wrap">
                        <button class="btn btn-sm btn-success" onclick="resolveDsr('${r.id}', false)">Выполнить</button>
                        ${
                          r.user_id && (r.request_type === 'delete_data' || r.request_type === 'revoke_consent')
                            ? `<button class="btn btn-sm btn-danger" onclick="resolveDsr('${r.id}', true)">Обезличить ПДн</button>`
                            : ''
                        }
                      </div>`
                    : `<span style="font-size:11.5px;color:var(--text-muted)">${esc(r.resolution_note || '—')}</span>`
                }
              </td>
            </tr>`
                  )
                  .join('')
              : '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:20px">Обращений субъектов ПДн пока нет.</td></tr>'
          }
        </tbody>
      </table>
    </div>
  `;
}

async function resolveDsr(requestId, anonymizeUser) {
  const dlg = await window.IceDialog.form({
    title: anonymizeUser ? 'Выполнить и обезличить ПДн пользователя' : 'Закрыть обращение субъекта ПДн',
    subtitle: anonymizeUser
      ? 'ФИО, username, телефоны и email пользователя будут безвозвратно очищены (фискальная история 54-ФЗ сохранится в обезличенном виде).'
      : 'Укажите комментарий об исполнении обращения по 152-ФЗ.',
    submitText: anonymizeUser ? 'Обезличить и закрыть' : 'Подтвердить выполнение',
    submitClass: anonymizeUser ? 'btn-danger' : 'btn-success',
    fields: [
      {
        name: 'resolution_note',
        label: 'Комментарий исполнения',
        type: 'textarea',
        value: anonymizeUser ? 'ПДн субъекта обезличены по ст. 21 152-ФЗ' : 'Запрос исполнен в срок по 152-ФЗ',
      },
    ],
  });
  if (!dlg) return;
  try {
    await api(`/api/v1/admin/data-requests/${requestId}/resolve`, {
      method: 'POST',
      body: JSON.stringify({
        status: 'completed',
        anonymize_user: anonymizeUser,
        resolution_note: dlg.resolution_note || '',
      }),
    });
    toast(anonymizeUser ? 'ПДн обезличены, запрос закрыт' : 'Запрос ПДн выполнен');
    loadTab('privacy');
  } catch (e) {
    toast(e.message);
  }
}

fetch('/ready')
  .then((r) => (r.ok ? r.json() : null))
  .then((d) => {
    if (d && d.app_env === 'development') {
      const box = document.getElementById('dev-quick-roles');
      if (box) box.style.display = 'grid';
    }
  })
  .catch(() => {});

api('/api/v1/admin/auth/me')
  .then((me) => {
    currentAdmin = me;
    enterAdmin();
  })
  .catch(() => {});
