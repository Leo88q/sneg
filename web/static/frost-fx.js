/* ============================================================================
   ARCTIC CRYO-FX: Живой снег на Canvas, блики льда, часы МСК, кастомные окна
   ============================================================================ */

(function () {
  // 1. Многослойный зимний снегопад: дальнее боке, ледяная крупа и вращающиеся 6-лучевые снежинки
  const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let snowEnabled = localStorage.getItem('sneg_snow_fx') !== 'off' && !reduceMotion;
  let pointerWind = 0;

  window.addEventListener('pointermove', (e) => {
    if (!window.innerWidth) return;
    pointerWind = ((e.clientX / window.innerWidth) - 0.5) * 0.45;
  });

  function initFrostCanvas() {
    let canvas = document.getElementById('frost-canvas');
    if (!canvas) {
      canvas = document.createElement('canvas');
      canvas.id = 'frost-canvas';
      document.body.prepend(canvas);
    }
    const ctx = canvas.getContext('2d');
    let w = (canvas.width = window.innerWidth);
    let h = (canvas.height = window.innerHeight);

    const total = Math.min(105, Math.max(46, Math.floor(w / 15)));
    const flakes = Array.from({ length: total }, (_, idx) => {
      const layer = idx % 5 === 0 ? 2 : idx % 3 === 0 ? 0 : 1; // 0=дальнее боке, 1=средний снег, 2=крупные снежинки
      return {
        layer,
        x: Math.random() * w,
        y: Math.random() * h,
        r: layer === 2 ? 3.2 + Math.random() * 2.8 : layer === 0 ? 1.8 + Math.random() * 2.4 : 0.8 + Math.random() * 1.6,
        vy: layer === 2 ? 0.48 + Math.random() * 0.55 : layer === 0 ? 0.16 + Math.random() * 0.25 : 0.32 + Math.random() * 0.48,
        vx: -0.22 + Math.random() * 0.44,
        phase: Math.random() * Math.PI * 2,
        rot: Math.random() * Math.PI * 2,
        vrot: (Math.random() - 0.5) * 0.014,
        alpha: layer === 2 ? 0.28 + Math.random() * 0.36 : layer === 0 ? 0.08 + Math.random() * 0.14 : 0.18 + Math.random() * 0.38,
      };
    });

    window.addEventListener('resize', () => {
      w = canvas.width = window.innerWidth;
      h = canvas.height = window.innerHeight;
    });

    function drawSnowflake(x, y, r, rot, alpha) {
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(rot);
      ctx.strokeStyle = `rgba(218, 246, 255, ${alpha})`;
      ctx.lineWidth = 0.85;
      for (let i = 0; i < 6; i++) {
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(0, -r * 1.65);
        // Боковые ледяные веточки снежинки
        ctx.moveTo(0, -r * 0.95);
        ctx.lineTo(-r * 0.42, -r * 1.32);
        ctx.moveTo(0, -r * 0.95);
        ctx.lineTo(r * 0.42, -r * 1.32);
        ctx.stroke();
        ctx.rotate(Math.PI / 3);
      }
      ctx.restore();
    }

    let breezePhase = 0;
    function tick() {
      ctx.clearRect(0, 0, w, h);
      if (snowEnabled) {
        breezePhase += 0.004;
        const globalBreeze = Math.sin(breezePhase) * 0.28 + pointerWind;
        for (const f of flakes) {
          f.phase += 0.011;
          f.rot += f.vrot;
          const layerWind = f.layer === 2 ? globalBreeze * 1.2 : f.layer === 0 ? globalBreeze * 0.35 : globalBreeze * 0.75;
          f.x += f.vx + layerWind + Math.sin(f.phase) * 0.24;
          f.y += f.vy;
          if (f.y > h + 16) {
            f.y = -16;
            f.x = Math.random() * w;
          }
          if (f.x < -16) f.x = w + 16;
          if (f.x > w + 16) f.x = -16;

          if (f.layer === 2) {
            drawSnowflake(f.x, f.y, f.r, f.rot, f.alpha);
          } else if (f.layer === 0) {
            const g = ctx.createRadialGradient(f.x, f.y, 0, f.x, f.y, f.r * 2.4);
            g.addColorStop(0, `rgba(190, 240, 255, ${f.alpha})`);
            g.addColorStop(1, 'rgba(190, 240, 255, 0)');
            ctx.fillStyle = g;
            ctx.beginPath();
            ctx.arc(f.x, f.y, f.r * 2.4, 0, Math.PI * 2);
            ctx.fill();
          } else {
            ctx.beginPath();
            ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(226, 248, 255, ${f.alpha})`;
            ctx.fill();
          }
        }
      }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  window.toggleSnowFx = function (btn) {
    snowEnabled = !snowEnabled;
    localStorage.setItem('sneg_snow_fx', snowEnabled ? 'on' : 'off');
    if (btn) btn.setAttribute('data-active', snowEnabled ? '1' : '0');
  };

  // 2. Спекулярный ледяной блик за курсором на интерактивных панелях
  document.addEventListener('pointermove', (e) => {
    const el = e.target.closest('.ice-interactive');
    if (!el) return;
    const rect = el.getBoundingClientRect();
    el.style.setProperty('--mx', `${e.clientX - rect.left}px`);
    el.style.setProperty('--my', `${e.clientY - rect.top}px`);
  });

  // 3. Живое время МСК в шапке
  function updateClock() {
    const nodes = document.querySelectorAll('[data-msk-clock]');
    if (!nodes.length) return;
    const fmt = new Intl.DateTimeFormat('ru-RU', {
      timeZone: 'Europe/Moscow',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(new Date());
    nodes.forEach((n) => (n.textContent = `${fmt} МСК`));
  }

  // 4. Кастомный менеджер ледяных окон (замена стандартных prompt/alert + лайтбокс фото)
  function ensureDialogRoot() {
    let root = document.getElementById('ice-dialog-backdrop');
    if (!root) {
      root = document.createElement('div');
      root.id = 'ice-dialog-backdrop';
      root.className = 'modal-backdrop';
      root.innerHTML = `<div class="modal modal-sm" id="ice-dialog-box"></div>`;
      root.addEventListener('click', (e) => {
        if (e.target === root && window._iceDialogCancel) window._iceDialogCancel();
      });
      document.body.appendChild(root);
    }
    return root;
  }

  function escHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  window.IceDialog = {
    form({ title, subtitle = '', submitText = 'Подтвердить', submitClass = 'btn-primary', fields = [] }) {
      return new Promise((resolve) => {
        const root = ensureDialogRoot();
        const box = document.getElementById('ice-dialog-box');
        box.className = 'modal modal-sm';

        const fieldsHtml = fields
          .map((f, idx) => {
            const id = `ice-f-${idx}`;
            let ctrl = '';
            if (f.type === 'select') {
              ctrl = `<select class="input" id="${id}" name="${escHtml(f.name)}">${(f.options || [])
                .map(
                  (o) =>
                    `<option value="${escHtml(o.value)}" ${o.value === f.value ? 'selected' : ''}>${escHtml(o.label)}</option>`
                )
                .join('')}</select>`;
            } else if (f.type === 'textarea') {
              ctrl = `<textarea class="input" id="${id}" name="${escHtml(f.name)}" rows="3" placeholder="${escHtml(f.placeholder || '')}">${escHtml(f.value ?? '')}</textarea>`;
            } else {
              ctrl = `<input class="input" id="${id}" type="${escHtml(f.type || 'text')}" name="${escHtml(f.name)}" value="${escHtml(f.value ?? '')}" placeholder="${escHtml(f.placeholder || '')}" ${f.required ? 'required' : ''} />`;
            }
            return `<div><label class="field-label" for="${id}">${escHtml(f.label)}</label>${ctrl}</div>`;
          })
          .join('');

        box.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:14px">
            <div>
              <div class="section-kicker" style="margin-bottom:6px">ОПЕРАЦИЯ ДИСПЕТЧЕРА</div>
              <h3 style="margin:0;font-size:17px">${escHtml(title)}</h3>
              ${subtitle ? `<p style="margin:6px 0 0;color:var(--text-muted);font-size:13px">${escHtml(subtitle)}</p>` : ''}
            </div>
            <button type="button" class="btn btn-sm btn-ghost" id="ice-dlg-x">✕</button>
          </div>
          <form id="ice-dlg-form" style="display:flex;flex-direction:column;gap:12px">
            ${fieldsHtml}
            <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:6px">
              <button type="button" class="btn btn-ghost" id="ice-dlg-cancel">Отмена</button>
              <button type="submit" class="btn ${submitClass}">${escHtml(submitText)}</button>
            </div>
          </form>
        `;

        const close = (val) => {
          root.classList.remove('open');
          window._iceDialogCancel = null;
          resolve(val);
        };
        window._iceDialogCancel = () => close(null);
        document.getElementById('ice-dlg-x').onclick = () => close(null);
        document.getElementById('ice-dlg-cancel').onclick = () => close(null);
        document.getElementById('ice-dlg-form').onsubmit = (ev) => {
          ev.preventDefault();
          const result = {};
          const fd = new FormData(ev.target);
          for (const f of fields) {
            result[f.name] = fd.get(f.name) ?? '';
          }
          close(result);
        };

        root.classList.add('open');
        const firstInput = box.querySelector('input, textarea, select');
        if (firstInput) setTimeout(() => firstInput.focus(), 40);
      });
    },

    lightbox(url, caption = 'Фотоотчёт') {
      const root = ensureDialogRoot();
      const box = document.getElementById('ice-dialog-box');
      box.className = 'modal';
      box.style.width = 'auto';
      box.style.maxWidth = '96vw';
      box.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:12px">
          <div class="section-kicker">${escHtml(caption)}</div>
          <button type="button" class="btn btn-sm btn-ghost" id="ice-lb-close">Закрыть ✕</button>
        </div>
        <img class="lightbox-img" src="${escHtml(url)}" alt="${escHtml(caption)}" />
      `;
      const close = () => {
        root.classList.remove('open');
        box.style.width = '';
        box.style.maxWidth = '';
        window._iceDialogCancel = null;
      };
      window._iceDialogCancel = close;
      document.getElementById('ice-lb-close').onclick = close;
      root.classList.add('open');
    },
  };

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && window._iceDialogCancel) {
      window._iceDialogCancel();
    }
  });

  // -------------------------------------------------------------------------
  // Cookie-баннер (п. 4, 5, 9: без тёмных паттернов, равнозначные кнопки)
  // -------------------------------------------------------------------------
  const COOKIE_KEY = 'chistodvor_cookie_consent';

  function readCookieChoice() {
    try {
      const v = localStorage.getItem(COOKIE_KEY);
      if (v === 'all' || v === 'necessary') return v;
    } catch (_) {}
    const m = document.cookie.match(/(?:^|;\s*)chistodvor_cookie_consent=(all|necessary)/);
    return m ? m[1] : null;
  }

  function saveCookieChoice(choice) {
    try {
      localStorage.setItem(COOKIE_KEY, choice);
    } catch (_) {}
    const maxAge = 180 * 24 * 60 * 60;
    const secureFlag = location.protocol === 'https:' ? '; Secure' : '';
    document.cookie = `${COOKIE_KEY}=${choice}; path=/; max-age=${maxAge}; SameSite=Lax${secureFlag}`;
    if (choice === 'necessary') {
      try {
        localStorage.removeItem('cryo_snow_fx');
      } catch (_) {}
    }
    const el = document.getElementById('ice-cookie-banner');
    if (el) el.hidden = true;
  }

  function ensureCookieBanner() {
    let banner = document.getElementById('ice-cookie-banner');
    if (banner) return banner;
    banner = document.createElement('aside');
    banner.id = 'ice-cookie-banner';
    banner.className = 'cookie-banner';
    banner.setAttribute('role', 'dialog');
    banner.setAttribute('aria-label', 'Настройки файлов Cookie');
    banner.hidden = true;
    banner.innerHTML = `
      <div class="cookie-banner-copy">
        <strong>Файлы Cookie и защита данных (РФ, 152-ФЗ).</strong>
        Мы используем строго необходимые технические Cookie (авторизация админки и сохранение вашего выбора)
        и не подключаем зарубежные рекламные трекеры. Подробнее — в
        <a href="/legal/cookies">Политике Cookie</a> и <a href="/legal/privacy">Политике ПДн</a>.
      </div>
      <div class="cookie-banner-actions">
        <button type="button" class="btn btn-sm" id="cookie-only-necessary">Только необходимые</button>
        <button type="button" class="btn btn-sm btn-primary" id="cookie-accept-all">Принять все</button>
      </div>
    `;
    document.body.appendChild(banner);
    document.getElementById('cookie-only-necessary').addEventListener('click', () => saveCookieChoice('necessary'));
    document.getElementById('cookie-accept-all').addEventListener('click', () => saveCookieChoice('all'));
    return banner;
  }

  window.openCookieSettings = function openCookieSettings() {
    const banner = ensureCookieBanner();
    banner.hidden = false;
  };

  function hydrateLegalFooter() {
    const footerOp = document.getElementById('legal-footer-operator');
    if (!footerOp) return;
    fetch('/api/v1/legal/public-meta')
      .then((r) => (r.ok ? r.json() : null))
      .then((m) => {
        if (!m) return;
        const parts = [m.operator_name];
        if (m.inn) parts.push(`ИНН ${m.inn}`);
        if (m.ogrnip) parts.push(`ОГРНИП ${m.ogrnip}`);
        if (m.phone) parts.push(m.phone);
        if (m.rkn_reg_no) parts.push(`РКН № ${m.rkn_reg_no}`);
        parts.push('18+');
        footerOp.textContent = parts.join(' · ');
        const dcEl = document.getElementById('legal-footer-dc');
        if (dcEl && m.datacenter_info) {
          dcEl.textContent = `${m.datacenter_info} · Контакт по ПДн: ${m.email}`;
        }
      })
      .catch(() => {});
  }

  document.addEventListener('DOMContentLoaded', () => {
    initFrostCanvas();
    updateClock();
    setInterval(updateClock, 1000);
    const banner = ensureCookieBanner();
    if (!readCookieChoice()) {
      banner.hidden = false;
    }
    hydrateLegalFooter();
  });
})();
