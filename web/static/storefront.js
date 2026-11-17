/* ============================================================================
   ЧИСТОДВОР · Витрина, живой калькулятор тарифов и онлайн-заказ
   Работает поверх /api/v1/public/catalog, /orders/quote, /orders и /status
   ============================================================================ */

(function () {
  const SERVICE_ICONS = {
    snow_yard:
      '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2v20M2 12h20M4.9 4.9l14.2 14.2M19.1 4.9L4.9 19.1"/><path d="M9.5 4.5L12 7l2.5-2.5M9.5 19.5L12 17l2.5 2.5"/></svg>',
    snow_road:
      '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 20L9 4h6l5 16"/><path d="M12 7v3M12 14v3"/></svg>',
    car_rescue:
      '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M5 17h14M6 17l-1-5 2-5h10l2 5-1 5"/><circle cx="7.5" cy="17" r="1.5"/><circle cx="16.5" cy="17" r="1.5"/><path d="M12 2v3"/></svg>',
    roof_cleaning:
      '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 11l9-7 9 7"/><path d="M6 10v9h12v-9"/></svg>',
    reagents:
      '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3l6 9a6 6 0 1 1-12 0l6-9z"/></svg>',
    yard_help:
      '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
    movers:
      '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 8l-9-5-9 5v8l9 5 9-5V8z"/><path d="M3 8l9 5 9-5M12 13v9"/></svg>',
  };

  const state = {
    catalog: null,
    service: 'snow_yard',
    zone_code: 'malakhovka',
    area_m2: 100,
    length_m: 60,
    width_m: 4,
    depth_bucket: 'from_10_to_30',
    access_constraint: 'standard',
    urgency: 'today',
    snow_removal: false,
    vehicle_type: 'sedan',
    distance_km: 3,
    is_urgent_rescue: true,
    hours: 2,
    workers_count: 2,
    floors_without_lift: 0,
    needs_equipment: false,
    as_addon: false,
    lastQuote: null,
    lastOrder: null,
  };

  let quoteTimer = null;

  function esc(val) {
    return String(val ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmtRub(val) {
    const n = Math.round(Number(val || 0));
    return `${n.toLocaleString('ru-RU')} ₽`;
  }

  function showFormError(msg) {
    const box = document.getElementById('order-form-error');
    if (!box) return;
    if (!msg) {
      box.hidden = true;
      box.textContent = '';
      return;
    }
    box.hidden = false;
    box.textContent = msg;
  }

  function buildQuotePayload() {
    const base = {
      service_type: state.service,
      zone_code: state.zone_code,
      urgency: state.urgency,
    };
    if (state.service === 'snow_yard') {
      return {
        ...base,
        area_m2: Number(state.area_m2) || 80,
        depth_bucket: state.depth_bucket,
        access_constraint: state.access_constraint,
        snow_removal: Boolean(state.snow_removal),
      };
    }
    if (state.service === 'snow_road') {
      return {
        ...base,
        length_m: Number(state.length_m) || 60,
        width_m: Number(state.width_m) || 4,
        depth_bucket: state.depth_bucket,
        access_constraint: state.access_constraint,
        snow_removal: Boolean(state.snow_removal),
      };
    }
    if (state.service === 'car_rescue') {
      return {
        ...base,
        vehicle_type: state.vehicle_type,
        distance_km: Number(state.distance_km) || 0,
        is_urgent_rescue: Boolean(state.is_urgent_rescue),
      };
    }
    if (state.service === 'yard_help' || state.service === 'movers') {
      return {
        ...base,
        hours: Number(state.hours) || 2,
        workers_count: Number(state.workers_count) || 1,
        floors_without_lift: state.service === 'movers' ? Number(state.floors_without_lift) || 0 : 0,
        needs_equipment: Boolean(state.needs_equipment),
      };
    }
    if (state.service === 'roof_cleaning') {
      return {
        ...base,
        area_m2: Number(state.area_m2) || 80,
      };
    }
    if (state.service === 'reagents') {
      return {
        ...base,
        area_m2: Number(state.area_m2) || 100,
        as_addon: Boolean(state.as_addon),
      };
    }
    return base;
  }

  function renderServiceTabs() {
    const wrap = document.getElementById('service-selector-grid');
    if (!wrap || !state.catalog) return;
    wrap.innerHTML = state.catalog.services
      .map((s) => {
        const active = s.code === state.service ? 'active' : '';
        return `
          <button type="button" class="svc-tab ${active}" data-svc="${esc(s.code)}">
            <span class="svc-tab-ico">${SERVICE_ICONS[s.code] || ''}</span>
            <span class="svc-tab-text">
              <strong>${esc(s.short_title)}</strong>
              <small>${esc(s.price_from_label)}</small>
            </span>
          </button>
        `;
      })
      .join('');

    wrap.querySelectorAll('[data-svc]').forEach((btn) => {
      btn.addEventListener('click', () => {
        selectService(btn.getAttribute('data-svc'), false);
      });
    });
  }

  function renderCatalogCards() {
    const grid = document.getElementById('catalog-services-grid');
    if (!grid || !state.catalog) return;
    grid.innerHTML = state.catalog.services
      .map(
        (s) => `
      <article class="ice-panel ice-interactive svc-card">
        <div>
          <div class="svc-card-top">
            <span class="svc-card-icon">${SERVICE_ICONS[s.code] || ''}</span>
            <span class="pill pill-blue">${esc(s.category)}</span>
          </div>
          <h3>${esc(s.title)}</h3>
          <p>${esc(s.summary)}</p>
        </div>
        <div class="svc-card-foot">
          <div>
            <div class="svc-price">${esc(s.price_from_label)}</div>
            <div class="svc-min">мин. ${fmtRub(s.min_price_rub)} · аванс ${s.advance_pct}%</div>
          </div>
          <button type="button" class="btn btn-sm btn-primary" data-pick-svc="${esc(s.code)}">
            Рассчитать →
          </button>
        </div>
      </article>
    `
      )
      .join('');

    grid.querySelectorAll('[data-pick-svc]').forEach((btn) => {
      btn.addEventListener('click', () => {
        selectService(btn.getAttribute('data-pick-svc'), true);
      });
    });
  }

  function renderZones() {
    if (!state.catalog) return;
    const selectEl = document.getElementById('order-zone');
    if (selectEl) {
      selectEl.innerHTML = state.catalog.zones
        .map(
          (z) =>
            `<option value="${esc(z.code)}" ${z.code === state.zone_code ? 'selected' : ''}>${esc(z.name)} (выезд ${fmtRub(z.dispatch_fee)})</option>`
        )
        .join('');
    }

    const heroZones = document.getElementById('hero-zones-list');
    if (heroZones) {
      const activeHtml = state.catalog.zones
        .map((z) => `<span class="pill pill-blue">${esc(z.name)}</span>`)
        .join('');
      const plannedHtml = (state.catalog.planned_zones || [])
        .map((z) => `<span class="pill" title="Скоро запуск">${esc(z.name)} · скоро</span>`)
        .join('');
      heroZones.innerHTML = activeHtml + plannedHtml;
    }
  }

  function renderPillGroup(name, currentValue, options) {
    return `
      <div class="choice-pills" data-pill-group="${esc(name)}">
        ${options
          .map(
            (o) => `
          <button type="button" class="choice-pill ${o.value === currentValue ? 'active' : ''}" data-val="${esc(o.value)}">
            <span>${esc(o.label)}</span>
            ${o.hint ? `<small>${esc(o.hint)}</small>` : ''}
          </button>
        `
          )
          .join('')}
      </div>
    `;
  }

  function renderDynamicParams() {
    const box = document.getElementById('order-dynamic-params');
    if (!box) return;

    const svc = state.service;
    let html = '';

    if (svc === 'snow_yard') {
      html = `
        <div class="param-block">
          <div class="param-row-head">
            <label class="field-label" for="p-area">Площадь уборки (м²)</label>
            <div class="preset-chips">
              ${[50, 100, 200, 400]
                .map(
                  (v) =>
                    `<button type="button" class="preset-chip ${Number(state.area_m2) === v ? 'active' : ''}" data-preset-area="${v}">${v} м²</button>`
                )
                .join('')}
            </div>
          </div>
          <div class="range-input-row">
            <input type="range" id="p-area-range" min="10" max="1000" step="10" value="${Math.min(1000, Number(state.area_m2) || 100)}" />
            <input type="number" class="input mono" id="p-area" min="10" max="10000" step="5" value="${Number(state.area_m2) || 100}" style="width:110px" />
          </div>
        </div>

        <div class="param-block">
          <label class="field-label">Глубина снега</label>
          ${renderPillGroup('depth_bucket', state.depth_bucket, [
            { value: 'up_to_10', label: 'До 10 см', hint: '×1.0' },
            { value: 'from_10_to_30', label: '10–30 см', hint: '×1.5' },
            { value: 'over_30', label: 'Более 30 см', hint: '×2.0' },
          ])}
        </div>

        <div class="param-block">
          <label class="field-label">Ширина заезда на участок</label>
          ${renderPillGroup('access_constraint', state.access_constraint, [
            { value: 'narrow_gate', label: 'Узкая калитка', hint: 'Вручную / мини' },
            { value: 'standard', label: 'Стандартные ворота', hint: 'Техника пройдёт' },
            { value: 'wide_road', label: 'Широкий проезд', hint: 'Можно трактор' },
          ])}
        </div>

        <div class="param-block">
          <label class="field-label">Срочность выезда</label>
          ${renderPillGroup('urgency', state.urgency, [
            { value: 'today', label: 'Сегодня', hint: 'Базовый тариф' },
            { value: 'within_3h', label: 'За 3 часа', hint: '×1.3' },
            { value: 'now_or_night', label: 'Срочно / ночь', hint: '×1.5' },
          ])}
        </div>

        <label class="ice-check">
          <input type="checkbox" id="p-snow-removal" ${state.snow_removal ? 'checked' : ''} />
          <span><strong>Добавить вывоз снега с участка</strong> — расчёт объёма автоматически (450 ₽/м³, мин. 3 000 ₽)</span>
        </label>
      `;
    } else if (svc === 'snow_road') {
      html = `
        <div class="form-grid-2">
          <div>
            <label class="field-label" for="p-length">Длина проезда (м)</label>
            <input type="number" class="input mono" id="p-length" min="5" max="2000" step="5" value="${Number(state.length_m) || 60}" />
          </div>
          <div>
            <label class="field-label" for="p-width">Ширина проезда (м)</label>
            <input type="number" class="input mono" id="p-width" min="2" max="15" step="0.5" value="${Number(state.width_m) || 4}" />
          </div>
        </div>

        <div class="param-block">
          <label class="field-label">Глубина снега</label>
          ${renderPillGroup('depth_bucket', state.depth_bucket, [
            { value: 'up_to_10', label: 'До 10 см', hint: '×1.0' },
            { value: 'from_10_to_30', label: '10–30 см', hint: '×1.5' },
            { value: 'over_30', label: 'Более 30 см', hint: '×2.0' },
          ])}
        </div>

        <div class="param-block">
          <label class="field-label">Условия проезда</label>
          ${renderPillGroup('access_constraint', state.access_constraint, [
            { value: 'narrow_gate', label: 'Стеснённый проезд', hint: 'Мини-техника' },
            { value: 'standard', label: 'Стандартная улица СНТ', hint: 'Мини-погрузчик' },
            { value: 'wide_road', label: 'Широкая дорога', hint: 'Трактор' },
          ])}
        </div>

        <div class="param-block">
          <label class="field-label">Срочность выезда</label>
          ${renderPillGroup('urgency', state.urgency, [
            { value: 'today', label: 'Сегодня', hint: 'Базовый тариф' },
            { value: 'within_3h', label: 'За 3 часа', hint: '×1.3' },
            { value: 'now_or_night', label: 'Срочно / ночь', hint: '×1.5' },
          ])}
        </div>

        <label class="ice-check">
          <input type="checkbox" id="p-snow-removal" ${state.snow_removal ? 'checked' : ''} />
          <span><strong>Добавить вывоз снега</strong> (450 ₽/м³, мин. 3 000 ₽)</span>
        </label>
      `;
    } else if (svc === 'car_rescue') {
      html = `
        <div class="param-block">
          <label class="field-label">Тип автомобиля</label>
          ${renderPillGroup('vehicle_type', state.vehicle_type, [
            { value: 'sedan', label: 'Легковой / седан', hint: 'Базовая цена' },
            { value: 'suv', label: 'Внедорожник', hint: '+500 ₽' },
            { value: 'commercial', label: 'Фургон / коммерч.', hint: '+1 500 ₽' },
          ])}
        </div>

        <div class="param-block">
          <div class="param-row-head">
            <label class="field-label" for="p-dist">Удалённость от Малаховки (км)</label>
            <span class="mono" style="font-size:12px;color:var(--cryo)">до 5 км — бесплатно</span>
          </div>
          <div class="range-input-row">
            <input type="range" id="p-dist-range" min="1" max="30" step="1" value="${Math.min(30, Number(state.distance_km) || 3)}" />
            <input type="number" class="input mono" id="p-dist" min="0" max="100" step="1" value="${Number(state.distance_km) || 3}" style="width:100px" />
          </div>
        </div>

        <label class="ice-check">
          <input type="checkbox" id="p-urgent-rescue" ${state.is_urgent_rescue ? 'checked' : ''} />
          <span><strong>Срочный приоритетный выезд</strong> (+500 ₽, ближайший свободный экипаж)</span>
        </label>
      `;
    } else if (svc === 'yard_help' || svc === 'movers') {
      html = `
        <div class="form-grid-2">
          <div>
            <label class="field-label" for="p-workers">Количество исполнителей</label>
            <input type="number" class="input mono" id="p-workers" min="1" max="10" value="${Number(state.workers_count) || 2}" />
          </div>
          <div>
            <label class="field-label" for="p-hours">Время работы (часов, мин. 2)</label>
            <input type="number" class="input mono" id="p-hours" min="2" max="24" value="${Math.max(2, Number(state.hours) || 2)}" />
          </div>
        </div>

        ${
          svc === 'movers'
            ? `
          <div class="param-block">
            <label class="field-label" for="p-floors">Этажей без лифта (150 ₽ / этаж / грузчик)</label>
            <input type="number" class="input mono" id="p-floors" min="0" max="25" value="${Number(state.floors_without_lift) || 0}" style="max-width:160px" />
          </div>
        `
            : ''
        }

        <label class="ice-check">
          <input type="checkbox" id="p-equip" ${state.needs_equipment ? 'checked' : ''} />
          <span><strong>Нужен наш инструмент / спец. инвентарь</strong> (+1 500 ₽)</span>
        </label>
      `;
    } else if (svc === 'roof_cleaning') {
      html = `
        <div class="param-block">
          <div class="param-row-head">
            <label class="field-label" for="p-area">Ориентировочная площадь крыши (м²)</label>
            <div class="preset-chips">
              ${[60, 100, 160, 250]
                .map(
                  (v) =>
                    `<button type="button" class="preset-chip ${Number(state.area_m2) === v ? 'active' : ''}" data-preset-area="${v}">${v} м²</button>`
                )
                .join('')}
            </div>
          </div>
          <div class="range-input-row">
            <input type="range" id="p-area-range" min="20" max="600" step="10" value="${Math.min(600, Number(state.area_m2) || 100)}" />
            <input type="number" class="input mono" id="p-area" min="10" max="5000" step="5" value="${Number(state.area_m2) || 100}" style="width:110px" />
          </div>
          <div style="font-size:12px;color:var(--text-muted);margin-top:6px">
            На крышу выезжают только сертифицированные исполнители со страховкой. Минимальный выезд — 5 000 ₽.
          </div>
        </div>
      `;
    } else if (svc === 'reagents') {
      html = `
        <div class="param-block">
          <div class="param-row-head">
            <label class="field-label" for="p-area">Площадь обработки реагентами (м²)</label>
            <div class="preset-chips">
              ${[50, 100, 200, 400]
                .map(
                  (v) =>
                    `<button type="button" class="preset-chip ${Number(state.area_m2) === v ? 'active' : ''}" data-preset-area="${v}">${v} м²</button>`
                )
                .join('')}
            </div>
          </div>
          <div class="range-input-row">
            <input type="range" id="p-area-range" min="10" max="1000" step="10" value="${Math.min(1000, Number(state.area_m2) || 100)}" />
            <input type="number" class="input mono" id="p-area" min="10" max="10000" step="5" value="${Number(state.area_m2) || 100}" style="width:110px" />
          </div>
        </div>

        <label class="ice-check">
          <input type="checkbox" id="p-addon" ${state.as_addon ? 'checked' : ''} />
          <span><strong>Вместе с уборкой снега</strong> (выезд 0 ₽, оплачивается только обработка)</span>
        </label>
      `;
    }

    box.innerHTML = html;
    bindDynamicEvents(box);
  }

  function bindDynamicEvents(box) {
    box.querySelectorAll('[data-pill-group]').forEach((group) => {
      const key = group.getAttribute('data-pill-group');
      group.querySelectorAll('[data-val]').forEach((btn) => {
        btn.addEventListener('click', () => {
          state[key] = btn.getAttribute('data-val');
          group.querySelectorAll('[data-val]').forEach((b) => b.classList.remove('active'));
          btn.classList.add('active');
          scheduleQuote();
        });
      });
    });

    box.querySelectorAll('[data-preset-area]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const val = Number(btn.getAttribute('data-preset-area'));
        state.area_m2 = val;
        const num = document.getElementById('p-area');
        const rng = document.getElementById('p-area-range');
        if (num) num.value = String(val);
        if (rng) rng.value = String(Math.min(Number(rng.max), val));
        box.querySelectorAll('[data-preset-area]').forEach((c) => c.classList.remove('active'));
        btn.classList.add('active');
        scheduleQuote();
      });
    });

    const areaNum = document.getElementById('p-area');
    const areaRange = document.getElementById('p-area-range');
    if (areaNum) {
      areaNum.addEventListener('input', () => {
        state.area_m2 = Number(areaNum.value) || 10;
        if (areaRange) areaRange.value = String(Math.min(Number(areaRange.max), state.area_m2));
        scheduleQuote();
      });
    }
    if (areaRange) {
      areaRange.addEventListener('input', () => {
        state.area_m2 = Number(areaRange.value) || 10;
        if (areaNum) areaNum.value = String(state.area_m2);
        scheduleQuote();
      });
    }

    const bindNum = (id, key, minVal) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('input', () => {
        state[key] = Math.max(minVal, Number(el.value) || minVal);
        scheduleQuote();
      });
    };
    bindNum('p-length', 'length_m', 5);
    bindNum('p-width', 'width_m', 2);
    bindNum('p-workers', 'workers_count', 1);
    bindNum('p-hours', 'hours', 2);
    bindNum('p-floors', 'floors_without_lift', 0);

    const distNum = document.getElementById('p-dist');
    const distRange = document.getElementById('p-dist-range');
    if (distNum) {
      distNum.addEventListener('input', () => {
        state.distance_km = Math.max(0, Number(distNum.value) || 0);
        if (distRange) distRange.value = String(Math.min(30, state.distance_km));
        scheduleQuote();
      });
    }
    if (distRange) {
      distRange.addEventListener('input', () => {
        state.distance_km = Number(distRange.value) || 0;
        if (distNum) distNum.value = String(state.distance_km);
        scheduleQuote();
      });
    }

    const bindCheck = (id, key) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('change', () => {
        state[key] = Boolean(el.checked);
        scheduleQuote();
      });
    };
    bindCheck('p-snow-removal', 'snow_removal');
    bindCheck('p-urgent-rescue', 'is_urgent_rescue');
    bindCheck('p-equip', 'needs_equipment');
    bindCheck('p-addon', 'as_addon');
  }

  function scheduleQuote() {
    if (quoteTimer) clearTimeout(quoteTimer);
    quoteTimer = setTimeout(fetchQuote, 140);
  }

  async function fetchQuote() {
    const statusPill = document.getElementById('quote-status-pill');
    if (statusPill) statusPill.textContent = 'РАСЧЁТ…';
    try {
      const resp = await fetch('/api/v1/public/orders/quote', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildQuotePayload()),
      });
      const data = await resp.json();
      if (!resp.ok) {
        const msg = data?.error?.message || data?.detail || 'Проверьте параметры расчёта';
        if (statusPill) statusPill.textContent = 'ПРОВЕРЬТЕ ПАРАМЕТРЫ';
        showFormError(msg);
        return;
      }
      showFormError('');
      state.lastQuote = data;
      renderQuoteReceipt(data);
    } catch (_) {
      if (statusPill) statusPill.textContent = 'ОШИБКА СЕТИ';
    }
  }

  function renderQuoteReceipt(q) {
    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    };

    set('quote-status-pill', q.is_night ? 'НОЧНОЙ ТАРИФ МСК' : 'ТОЧНЫЙ РАСЧЁТ');
    set('q-service-name', q.service_label);
    set('q-zone-name', q.zone_name);

    const methodRow = document.getElementById('q-method-row');
    if (methodRow) {
      if (q.cleaning_method_label) {
        methodRow.hidden = false;
        set('q-method-val', q.cleaning_method_label);
      } else {
        methodRow.hidden = true;
      }
    }

    const extrasRow = document.getElementById('q-extras-row');
    if (extrasRow) {
      if (Number(q.extras_cost) > 0) {
        extrasRow.hidden = false;
        set('q-extras-val', fmtRub(q.extras_cost));
      } else {
        extrasRow.hidden = true;
      }
    }

    set('q-work-val', fmtRub(q.work_cost));
    set('q-dispatch-val', Number(q.dispatch_fee) > 0 ? fmtRub(q.dispatch_fee) : '0 ₽ (включён)');
    set('q-total-val', fmtRub(q.estimated_total));
    set('q-advance-label', `Аванс (${q.advance_pct}%)`);
    set('q-advance-val', fmtRub(q.advance_amount));
    set('q-remainder-val', fmtRub(q.remainder_amount));

    const submitBtn = document.getElementById('order-submit-btn');
    if (submitBtn) {
      submitBtn.textContent = `Оформить заказ · ${fmtRub(q.estimated_total)}`;
    }
  }

  function selectService(code, shouldScroll) {
    if (!code) return;
    state.service = code;
    // Возвращаем форму заказа, если до этого был показан экран подтверждения
    const formWrap = document.getElementById('order-form-panel');
    const doneWrap = document.getElementById('order-success-panel');
    if (formWrap) formWrap.hidden = false;
    if (doneWrap) doneWrap.hidden = true;

    renderServiceTabs();
    renderDynamicParams();
    scheduleQuote();

    if (shouldScroll) {
      const target = document.getElementById('order');
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  async function submitOrder(ev) {
    ev.preventDefault();
    showFormError('');

    const address = (document.getElementById('order-address')?.value || '').trim();
    const name = (document.getElementById('order-name')?.value || '').trim();
    const phone = (document.getElementById('order-phone')?.value || '').trim();
    const email = (document.getElementById('order-email')?.value || '').trim();
    const comment = (document.getElementById('order-comment')?.value || '').trim();
    const consentPdn = Boolean(document.getElementById('order-consent-pdn')?.checked);
    const consentOffer = Boolean(document.getElementById('order-consent-offer')?.checked);

    if (address.length < 5) {
      showFormError('Укажите точный адрес: улицу, номер дома или СНТ.');
      document.getElementById('order-address')?.focus();
      return;
    }
    if (name.length < 2) {
      showFormError('Укажите ваше имя.');
      document.getElementById('order-name')?.focus();
      return;
    }
    if (phone.replace(/\D/g, '').length < 10) {
      showFormError('Укажите корректный номер телефона (+7 …).');
      document.getElementById('order-phone')?.focus();
      return;
    }
    if (!consentPdn || !consentOffer) {
      showFormError('Чтобы отправить заказ, отметьте согласие на обработку ПДн и принятие публичной оферты.');
      return;
    }

    const btn = document.getElementById('order-submit-btn');
    const prevText = btn ? btn.textContent : '';
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Оформляем заявку…';
    }

    try {
      const payload = {
        ...buildQuotePayload(),
        address_text: address,
        client_name: name,
        client_phone: phone,
        client_email: email || null,
        client_comment: comment || null,
        consent_pdn: consentPdn,
        consent_offer: consentOffer,
      };
      const resp = await fetch('/api/v1/public/orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok) {
        const msg =
          data?.error?.message ||
          (typeof data?.detail === 'string' ? data.detail : null) ||
          'Не удалось оформить заказ. Проверьте поля формы.';
        showFormError(msg);
        return;
      }

      state.lastOrder = data;
      renderOrderSuccess(data);
    } catch (_) {
      showFormError('Ошибка соединения с сервером. Попробуйте ещё раз или позвоните диспетчеру.');
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = prevText || 'Оформить заказ';
      }
    }
  }

  function renderOrderSuccess(o) {
    const formWrap = document.getElementById('order-form-panel');
    const doneWrap = document.getElementById('order-success-panel');
    if (!doneWrap) return;

    const phoneDigits = String(o.dispatcher_phone || '').replace(/\D/g, '');
    let nextStepHtml = '';

    if (o.payment_mode === 'yookassa' && o.payment_url) {
      nextStepHtml = `
        <div class="success-action-box">
          <div style="font-size:13.5px;color:var(--text-frost);line-height:1.55;margin-bottom:14px">
            Для закрепления бригады оплатите аванс <strong>${fmtRub(o.advance_amount)}</strong> через СБП (ЮKassa).
            Кассовый чек 54-ФЗ придёт автоматически.
          </div>
          <a class="btn btn-primary" href="${esc(o.payment_url)}" target="_blank" rel="noopener">
            Оплатить аванс ${fmtRub(o.advance_amount)} по СБП →
          </a>
        </div>
      `;
    } else if (o.payment_mode === 'dev_mock' && o.payment_url) {
      nextStepHtml = `
        <div class="success-action-box">
          <div style="font-size:13.5px;color:var(--text-frost);line-height:1.55;margin-bottom:14px">
            Заявка создана в локальном режиме разработки. Можно проверить оплату на тестовой странице СБП:
          </div>
          <a class="btn btn-primary" href="${esc(o.payment_url)}">
            Перейти к тестовой оплате СБП (DEV) →
          </a>
        </div>
      `;
    } else {
      nextStepHtml = `
        <div class="success-action-box">
          <div style="font-size:13.5px;color:var(--text-frost);line-height:1.6;margin-bottom:14px">
            Заявка <strong>#${o.public_number}</strong> уже поступила диспетчеру.
            Мы перезвоним вам в течение <strong>10 минут</strong>, подтвердим время приезда бригады
            и направим ссылку на оплату аванса по СБП.
          </div>
          <a class="btn btn-primary" href="tel:+${esc(phoneDigits)}">
            Позвонить диспетчеру ${esc(o.dispatcher_phone)}
          </a>
        </div>
      `;
    }

    doneWrap.innerHTML = `
      <div class="section-kicker" style="margin-bottom:10px">ЗАЯВКА ОФОРМЛЕНА</div>
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px">
        <h3 style="margin:0;font-size:24px">Заказ <span class="mono" style="color:var(--cryo)">#${o.public_number}</span></h3>
        <span class="pill pill-green" id="created-order-status">${esc(o.status_label)}</span>
      </div>

      <div class="success-summary-grid">
        <div><span>Услуга</span><strong>${esc(o.service_label)}</strong></div>
        <div><span>Район</span><strong>${esc(o.zone_name)}</strong></div>
        <div><span>Адрес</span><strong>${esc(o.address_text)}</strong></div>
        <div><span>Итоговая оценка</span><strong class="mono" style="color:var(--cryo)">${fmtRub(o.estimated_total)}</strong></div>
        <div><span>Аванс по СБП</span><strong class="mono">${fmtRub(o.advance_amount)}</strong></div>
        <div><span>Остаток после фото «ПОСЛЕ»</span><strong class="mono">${fmtRub(o.remainder_amount)}</strong></div>
      </div>

      ${nextStepHtml}

      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:18px">
        <button type="button" class="btn btn-sm" id="btn-refresh-created-status">↻ Обновить статус</button>
        <button type="button" class="btn btn-sm btn-ghost" id="btn-new-order">Оформить ещё один заказ</button>
      </div>
    `;

    if (formWrap) formWrap.hidden = true;
    doneWrap.hidden = false;
    doneWrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    document.getElementById('btn-refresh-created-status')?.addEventListener('click', async () => {
      try {
        const r = await fetch(
          `/api/v1/public/orders/${o.public_number}/status?phone_last4=${encodeURIComponent(o.phone_last4)}`
        );
        if (r.ok) {
          const st = await r.json();
          const pill = document.getElementById('created-order-status');
          if (pill) pill.textContent = st.status_label;
        }
      } catch (_) {}
    });

    document.getElementById('btn-new-order')?.addEventListener('click', () => {
      document.getElementById('public-order-form')?.reset();
      if (formWrap) formWrap.hidden = false;
      doneWrap.hidden = true;
      renderZones();
      scheduleQuote();
    });
  }

  async function checkOrderStatus(ev) {
    ev.preventDefault();
    const numEl = document.getElementById('track-order-num');
    const last4El = document.getElementById('track-phone-last4');
    const outEl = document.getElementById('track-result');
    if (!outEl) return;

    const num = Number((numEl?.value || '').replace(/\D/g, ''));
    const last4 = (last4El?.value || '').replace(/\D/g, '');
    if (!num || last4.length !== 4) {
      outEl.hidden = false;
      outEl.innerHTML =
        '<div style="color:#ffb8c5;font-size:13px">Введите номер заказа и последние 4 цифры вашего телефона.</div>';
      return;
    }

    outEl.hidden = false;
    outEl.innerHTML = '<div style="color:var(--text-muted);font-size:13px">Проверяем статус…</div>';
    try {
      const r = await fetch(
        `/api/v1/public/orders/${num}/status?phone_last4=${encodeURIComponent(last4)}`
      );
      const d = await r.json();
      if (!r.ok) {
        outEl.innerHTML = `<div style="color:#ffb8c5;font-size:13px">${esc(d?.error?.message || 'Заказ не найден.')}</div>`;
        return;
      }
      outEl.innerHTML = `
        <div class="track-card">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
            <strong>Заказ <span class="mono">#${d.public_number}</span> · ${esc(d.service_label)}</strong>
            <span class="pill pill-green">${esc(d.status_label)}</span>
          </div>
          <div style="margin-top:8px;font-size:13px;color:var(--text-frost);display:flex;gap:16px;flex-wrap:wrap">
            <span>Район: <b>${esc(d.zone_name || '—')}</b></span>
            <span>Итого: <b class="mono">${fmtRub(d.final_total)}</b></span>
            <span>Оплачено: <b class="mono">${fmtRub(d.paid_amount)}</b></span>
          </div>
          ${
            d.payment_url
              ? `<div style="margin-top:10px"><a class="btn btn-sm btn-primary" href="${esc(d.payment_url)}" target="_blank" rel="noopener">Перейти к оплате аванса →</a></div>`
              : ''
          }
        </div>
      `;
    } catch (_) {
      outEl.innerHTML = '<div style="color:#ffb8c5;font-size:13px">Ошибка соединения.</div>';
    }
  }

  async function initStorefront() {
    // Быстрые кнопки выбора в hero
    document.querySelectorAll('[data-quick-svc]').forEach((btn) => {
      btn.addEventListener('click', () => {
        selectService(btn.getAttribute('data-quick-svc'), true);
      });
    });

    const zoneSelect = document.getElementById('order-zone');
    if (zoneSelect) {
      zoneSelect.addEventListener('change', () => {
        state.zone_code = zoneSelect.value;
        scheduleQuote();
      });
    }

    document.getElementById('public-order-form')?.addEventListener('submit', submitOrder);
    document.getElementById('track-order-form')?.addEventListener('submit', checkOrderStatus);

    const trackToggle = document.getElementById('toggle-track-btn');
    const trackBox = document.getElementById('track-drawer');
    if (trackToggle && trackBox) {
      trackToggle.addEventListener('click', () => {
        trackBox.hidden = !trackBox.hidden;
        if (!trackBox.hidden) document.getElementById('track-order-num')?.focus();
      });
    }

    try {
      const r = await fetch('/api/v1/public/catalog');
      if (r.ok) {
        const cat = await r.json();
        state.catalog = cat;
        if (cat.zones && cat.zones.length) {
          state.zone_code = cat.zones[0].code;
        }
        // Показываем кнопку симулятора только в локальном dev-режиме
        if (cat.simulator_enabled) {
          document.querySelectorAll('[data-sim-only]').forEach((el) => {
            el.style.display = el.classList.contains('btn') ? 'inline-flex' : 'inline';
          });
        }
        // Если задана боевая ссылка на MAX-бот, показываем её
        if (cat.max_bot_url) {
          document.querySelectorAll('[data-max-bot-link]').forEach((el) => {
            el.href = cat.max_bot_url;
            el.style.display = 'inline-flex';
          });
        }
        renderZones();
        renderServiceTabs();
        renderCatalogCards();
      }
    } catch (_) {}

    renderDynamicParams();
    fetchQuote();
  }

  document.addEventListener('DOMContentLoaded', initStorefront);
})();
