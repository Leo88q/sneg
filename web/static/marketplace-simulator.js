(() => {
  const base = '/api/v1/simulation/marketplace';
  const form = document.getElementById('new-order');
  const list = document.getElementById('order-list');
  const details = document.getElementById('order-details');
  const contract = document.getElementById('contract');
  const events = document.getElementById('events');
  const formError = document.getElementById('form-error');
  const actionError = document.getElementById('action-error');
  let orders = [], current = null, busy = false;
  const escape = value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const money = value => `${escape(value)} ₽`;
  const statuses = {searching:'Поиск исполнителя',agreement:'Согласование условий',awaiting_advance:'Ожидает тестовой оплаты',ready:'Готов к началу работ',in_progress:'Работы выполняются',awaiting_acceptance:'Ожидает приёмки',awaiting_balance:'Ожидает остатка',completed:'Завершён',disputed:'Обращение',canceled:'Отменён'};
  const schedule = {advance_27:'Аванс 27%, остаток после работ',full_upfront:'Полная предоплата',after_completion:'Постоплата'};
  const routes = {payment_provider:'через партнёра (имитация)',direct_to_executor:'напрямую исполнителю (имитация)'};
  const labels = {client:'Клиент',group:'Группа MAX',executor:'Исполнитель',support:'Сопровождение'};
  async function api(path, body) {
    const response = await fetch(base + path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || data.detail || 'Не удалось выполнить действие');
    return data;
  }
  function buttons(o) {
    const b = [];
    const add = (id, text) => b.push(`<button type="button" data-action="${id}">${text}</button>`);
    if (o.status === 'searching') add('take','Исполнитель: принять условия');
    if (o.status === 'agreement') {
      if (!o.client_signed) add('client_sign','Клиент: подтвердить макет');
      if (!o.executor_signed) add('executor_sign','Исполнитель: подтвердить макет');
    }
    if (o.status === 'awaiting_advance') add('pay_advance','Клиент: имитировать оплату');
    if (o.status === 'ready') add('start','Исполнитель: начать работу');
    if (o.status === 'in_progress') add('finish','Исполнитель: завершить работу');
    if (o.status === 'awaiting_acceptance') { add('accept','Клиент: принять работу'); add('dispute','Клиент: открыть обращение'); }
    if (o.status === 'awaiting_balance') add('pay_balance','Клиент: имитировать оплату остатка');
    if (o.status === 'disputed') { add('rework','Согласовать доработку'); add('refund','Имитировать возврат'); }
    if (['searching','agreement','awaiting_advance','ready','in_progress'].includes(o.status)) add('cancel','Отменить тестовый заказ');
    return b.join('');
  }
  function render() {
    list.innerHTML = orders.map(o => `<button class="secondary" type="button" data-order="${escape(o.id)}">#${escape(o.id)} · ${escape(statuses[o.status])}</button>`).join('');
    list.querySelectorAll('[data-order]').forEach(btn => btn.addEventListener('click', () => {current = btn.dataset.order; actionError.textContent=''; render();}));
    const o = orders.find(x => x.id === current);
    if (!o) return;
    const fact = (label, value) => `<div class="fact"><span>${label}</span><strong>${value}</strong></div>`;
    details.innerHTML = `<p><b>#${escape(o.id)} · ${escape(statuses[o.status])}</b><br>${escape(o.service)} · ${escape(o.address)}</p>
      <div class="facts">${fact('Полная цена',money(o.total))}${fact('Комиссия 27%',money(o.commission))}${fact('После комиссии',money(o.executor_after_commission))}${fact('Расходы, включая аренду',money(o.expenses))}${fact('Остаток после расходов, до налогов',money(o.after_expenses))}${fact('Тестовый аванс / остаток',`${money(o.advance)} / ${money(o.balance)}`)}${fact('Виртуально оплачено',money(o.paid))}${fact('Виртуально возвращено',money(o.refunded))}</div>
      <p>${escape(schedule[o.schedule])}${o.route ? ` · ${escape(routes[o.route])}` : ''}. Комиссия включена в расчёт, а аванс — в счёт работ исполнителя. Фактический зачёт комиссии не реализован.</p>
      ${Number(o.after_expenses) <= 0 ? '<p class="notice">Внимание: после расходов нет положительного вознаграждения. Исполнитель не может принять этот тестовый заказ.</p>' : ''}
      <div class="actions">${buttons(o)}</div>`;
    details.querySelectorAll('[data-action]').forEach(btn => btn.addEventListener('click', () => act(btn.dataset.action)));
    if (o.status === 'searching') contract.textContent = 'Появится после выбора исполнителя.';
    else contract.textContent = `ТЕСТОВЫЙ МАКЕТ, НЕ ПОДПИСАННЫЙ ДОГОВОР\n\nКлиент: ${o.client_name}\nИсполнитель: ${o.executor_name}\nУслуга: ${o.service}\nАдрес: ${o.address}\nСрок: ${o.work_date}\nЦена работ: ${o.total} ₽\nПорядок оплаты: ${schedule[o.schedule]}; аванс ${o.advance} ₽, остаток ${o.balance} ₽${o.route ? `, ${routes[o.route]}` : ''}.\nКомиссия сервису между исполнителем и сервисом: ${o.commission} ₽ (27% от всей цены).\n\nПодтверждение клиента: ${o.client_signed ? 'тестовое нажатие' : 'нет'}; исполнителя: ${o.executor_signed ? 'тестовое нажатие' : 'нет'}.\nЮридическая форма, идентификация и подписание не реализованы.`;
    events.innerHTML = o.events.map(e => `<div class="event"><b>${escape(labels[e.role] || e.role)}</b><br>${escape(e.text)}</div>`).join('');
  }
  async function load() {
    const data = await api(''); orders = data.orders;
    if (!orders.some(o => o.id === current)) current = orders[0]?.id || null;
    render();
  }
  async function act(action) {
    if (busy) return; busy=true; actionError.textContent='';
    try { await api(`/${current}/action`, {action}); await load(); }
    catch(e) { actionError.textContent = e.message; }
    finally { busy=false; }
  }
  form.elements.schedule.addEventListener('change', () => {
    document.getElementById('route-wrap').hidden = form.elements.schedule.value === 'full_upfront';
  });
  form.addEventListener('submit', async e => {
    e.preventDefault(); if (busy) return; busy=true; formError.textContent='';
    const values = Object.fromEntries(new FormData(form));
    if (values.schedule === 'full_upfront') values.route = null;
    try { const order = await api('', values); current=order.id; await load(); }
    catch(err) { formError.textContent=err.message; }
    finally { busy=false; }
  });
  load().catch(e => {formError.textContent=e.message;});
})();
