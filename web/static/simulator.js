let simState = null;
let activeClient = 'client_1';
let activeWorker = 'executor_1';
let activeTaker = 'executor_1';

function showToast(text) {
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = text;
  box.appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

function escapeHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function formatMd(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/`(.+?)`/g, '<code>$1</code>')
    .replace(/\*(.+?)\*/g, '<i>$1</i>');
}

function switchClient(p) {
  activeClient = p;
  document.querySelectorAll('#tab-client_1,#tab-client_2').forEach((b) => b.classList.remove('active'));
  document.getElementById('tab-' + p).classList.add('active');
  renderAll();
}

function switchWorker(p) {
  activeWorker = p;
  document.querySelectorAll('#tab-executor_1,#tab-executor_2,#tab-foreman_1').forEach((b) => b.classList.remove('active'));
  document.getElementById('tab-' + p).classList.add('active');
  renderAll();
}

function switchTaker(p) {
  activeTaker = p;
  document.querySelectorAll('[id^="tab-taker-"]').forEach((b) => b.classList.remove('active'));
  document.getElementById('tab-taker-' + p).classList.add('active');
}

function renderChat(containerId, chatId, clickPersona, isDispatch = false) {
  const el = document.getElementById(containerId);
  if (!simState) return;
  const list = simState.messages_by_chat[String(chatId)] || [];
  if (!list.length) {
    el.innerHTML = `<div style="color:var(--text-muted);font-size:12.5px;text-align:center;margin:auto;padding:24px;border:1px dashed var(--ice-edge-soft);border-radius:12px">
      ${isDispatch
        ? 'Очередь биржи пуста.<br/>Оформите и оплатите аванс слева — карточка появится здесь.'
        : 'Диалог пуст.<br/>Нажмите <b>/start</b> в шапке окна, чтобы начать сценарий.'}
    </div>`;
    return;
  }

  el.innerHTML = list
    .map((m) => {
      const isBot = m.sender_role === 'bot';
      const editedTag = m.is_edited ? ' · <span class="edited-tag">ОБНОВЛЕНО</span>' : '';
      let attHtml = '';
      for (const att of m.attachments || []) {
        if (att.type === 'image' && att.payload && att.payload.url) {
          const safeUrl = escapeHtml(att.payload.url);
          attHtml += `<img class="msg-img" src="${safeUrl}" alt="Фотоотчёт" onclick="IceDialog.lightbox('${safeUrl}', 'Фото из чата MAX')" />`;
        } else if (att.type === 'inline_keyboard' && att.payload && att.payload.buttons) {
          const rowsHtml = att.payload.buttons
            .map((row) => {
              const btns = row
                .map((b) => {
                  if (b.type === 'link') {
                    return `<a class="kb-btn kb-btn-link" href="${escapeHtml(b.url)}" target="_blank">${escapeHtml(b.text)} ↗</a>`;
                  }
                  if (b.type === 'request_geo_location') {
                    return `<button class="kb-btn kb-btn-geo" onclick="sendGeoPreset(55.6435, 38.0068)">${escapeHtml(b.text)}</button>`;
                  }
                  if (b.type === 'request_contact') {
                    return `<button class="kb-btn kb-btn-geo" onclick="sendContact('${clickPersona}')">${escapeHtml(b.text)}</button>`;
                  }
                  const pl = encodeURIComponent(b.payload || '');
                  const actor = isDispatch ? activeTaker : clickPersona;
                  return `<button class="kb-btn" onclick="clickCallback('${actor}', ${chatId}, decodeURIComponent('${pl}'))">${escapeHtml(b.text)}</button>`;
                })
                .join('');
              return `<div class="kb-row">${btns}</div>`;
            })
            .join('');
          attHtml += `<div class="kb">${rowsHtml}</div>`;
        }
      }
      const timeStr = (m.created_at || '').slice(11, 19);
      return `<div class="msg ${isBot ? 'msg-bot' : 'msg-user'}">
        <div class="msg-meta">
          <span>${isBot ? 'BOT · MAX' : 'USER · ' + escapeHtml(m.sender_role).toUpperCase()}</span>
          <span>${timeStr}${editedTag}</span>
        </div>
        <div>${formatMd(m.text)}</div>
        ${attHtml}
      </div>`;
    })
    .join('');
  el.scrollTop = el.scrollHeight;
}

function renderSidebar() {
  if (!simState) return;
  const pBox = document.getElementById('pending-payments-list');
  const pCnt = document.getElementById('pending-count');
  if (pCnt) pCnt.textContent = String(simState.pending_payments.length);

  if (!simState.pending_payments.length) {
    pBox.innerHTML = '<span style="color:var(--text-muted)">Нет ожидающих счетов</span>';
  } else {
    pBox.innerHTML = simState.pending_payments
      .map(
        (p) => `
      <div class="mini-item">
        <div>
          <b class="mono" style="color:var(--cryo)">${p.amount} ₽</b>
          <div class="mono" style="color:var(--text-muted);font-size:10px;text-transform:uppercase">${escapeHtml(p.purpose)}</div>
        </div>
        <a class="btn btn-success btn-sm" href="${escapeHtml(p.confirmation_url)}" target="_blank">СБП ↗</a>
      </div>`
      )
      .join('');
  }

  const oBox = document.getElementById('recent-orders-list');
  const oCnt = document.getElementById('orders-count');
  if (oCnt) oCnt.textContent = String(simState.recent_orders.length);

  if (!simState.recent_orders.length) {
    oBox.innerHTML = '<span style="color:var(--text-muted)">Пока нет заказов</span>';
  } else {
    oBox.innerHTML = simState.recent_orders
      .slice(0, 6)
      .map(
        (o) => `
      <div class="mini-item">
        <div class="mono"><b>#${o.public_number}</b> · ${o.final_total} ₽</div>
        <span class="pill pill-blue">${escapeHtml(o.status)}</span>
      </div>`
      )
      .join('');
  }
}

function renderAll() {
  if (!simState) return;
  renderChat('msgs-client', simState.personas[activeClient].chat_id, activeClient, false);
  renderChat('msgs-dispatch', simState.dispatch_chat_id, activeTaker, true);
  renderChat('msgs-worker', simState.personas[activeWorker].chat_id, activeWorker, false);
  renderSidebar();
}

async function fetchState() {
  const r = await fetch('/api/v1/simulator/state');
  simState = await r.json();
  renderAll();
}

async function botStart(persona) {
  await fetch('/api/v1/simulator/bot-start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ persona }),
  });
  await fetchState();
}

async function sendText(persona, inputId) {
  const inp = document.getElementById(inputId);
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  await fetch('/api/v1/simulator/send-message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ persona, text }),
  });
  await fetchState();
}

async function sendGeoPreset(lat, lon) {
  await fetch('/api/v1/simulator/send-message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ persona: activeClient, location: { latitude: lat, longitude: lon } }),
  });
  showToast(`Координаты ${lat.toFixed(4)}°N, ${lon.toFixed(4)}°E переданы боту`);
  await fetchState();
}

async function sendContact(persona) {
  await fetch('/api/v1/simulator/send-message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ persona, contact_phone: '+7 999 000-11-22' }),
  });
  showToast('Контакт MAX (request_contact) записан только в bot_phone');
  await fetchState();
}

async function clickCallback(persona, chatId, payload) {
  const r = await fetch('/api/v1/simulator/callback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ persona, chat_id: chatId, payload }),
  });
  const data = await r.json();
  for (const n of data.notifications || []) {
    if (n.notification) showToast(n.notification);
  }
  await fetchState();
}

async function uploadDemoPhoto(persona, preset, caption) {
  const fd = new FormData();
  fd.append('persona', persona);
  fd.append('preset', preset);
  fd.append('caption', caption);
  await fetch('/api/v1/simulator/upload-photo', { method: 'POST', body: fd });
  showToast(`Фото «${preset.toUpperCase()}» загружено в диалог`);
  await fetchState();
}

async function uploadRealPhoto(persona, inputEl) {
  if (!inputEl.files || !inputEl.files[0]) return;
  const fd = new FormData();
  fd.append('persona', persona);
  fd.append('file', inputEl.files[0]);
  inputEl.value = '';
  await fetch('/api/v1/simulator/upload-photo', { method: 'POST', body: fd });
  showToast('Файл фотографии передан боту');
  await fetchState();
}

async function payLatest(result) {
  const r = await fetch('/api/v1/simulator/trigger/pay-latest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ result }),
  });
  if (!r.ok) {
    const err = await r.json();
    showToast(err.detail || 'Нет ожидающих платежей');
    return;
  }
  const data = await r.json();
  showToast(`СБП подтверждён: ${data.amount} ₽ (${data.purpose}) · Заказ #${data.order_public_number}`);
  await fetchState();
}

async function raceTake() {
  const r = await fetch('/api/v1/simulator/race-take', { method: 'POST' });
  const data = await r.json();
  if (!r.ok) {
    showToast(data.detail || 'Нет открытого заказа на бирже');
    return;
  }
  const summary = data.results
    .map((x) => `${x.executor}: ${x.won ? 'ЗАБРАЛ' : 'ОТКАЗ (' + x.message + ')'}`)
    .join(' · ');
  showToast(`Гонка #${data.order_public_number}: ${summary}`);
  await fetchState();
}

async function runTimers() {
  const r = await fetch('/api/v1/simulator/trigger/timers', { method: 'POST' });
  const d = await r.json();
  showToast(
    `Таймеры: просрочено=${d.expired_unpaid}, эскалаций=${d.escalated_dispatch}, автоприёмка=${d.auto_accepted}`
  );
  await fetchState();
}

async function triggerWeather() {
  const r = await fetch('/api/v1/simulator/trigger/weather', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ zone_code: 'malakhovka', snow_cm: 15 }),
  });
  const d = await r.json();
  showToast(`Снегопад ${d.snow_cm} см (${d.zone_code}): оповещено подписчиков = ${d.notified}`);
  await fetchState();
}

async function resetSim() {
  await fetch('/api/v1/simulator/reset', { method: 'POST' });
  showToast('Лента симулятора очищена');
  await fetchState();
}

function connectWs() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/api/v1/simulator/ws`);
  const badge = document.getElementById('ws-badge');
  ws.onopen = () => {
    badge.textContent = 'LIVE WS';
  };
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'callback_notification' && msg.notification) {
        showToast(msg.notification);
      }
    } catch (_) {}
    fetchState();
  };
  ws.onclose = () => {
    badge.textContent = 'POLL';
    setTimeout(connectWs, 3000);
  };
}

fetchState().then(() => {
  connectWs();
  if (simState && !(simState.messages_by_chat['10001'] || []).length) {
    botStart('client_1');
  }
});
