// Entry is shown only when the isolated local simulator is actually available.
fetch('/api/v1/simulation/marketplace').then((r) => {
  if (!r.ok) return;
  const bar = document.createElement('div');
  bar.style.cssText = 'position:relative;z-index:30;background:#d6f5ff;color:#09283b;padding:13px 20px;text-align:center;font:600 14px system-ui;line-height:1.5';
  bar.innerHTML = 'ЛОКАЛЬНАЯ ДЕМОНСТРАЦИЯ: новая модель заказа, комиссии и оплаты · <a href="/simulator/marketplace" style="color:#075b85;text-decoration:underline;font-weight:800">Открыть новый сценарий →</a>';
  document.body.insertBefore(bar, document.body.firstChild);
}).catch(() => {});
