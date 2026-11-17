"""Интерактивная страница эмуляции СБП-оплаты (только PAYMENT_PROVIDER=mock)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.api.deps import get_container
from src.core.container import AppContainer
from src.core.exceptions import NotFoundError
from src.infra.db.models import Order, Payment
from src.services.payments.mock import MockPaymentProvider

router = APIRouter(tags=["mock-pay"])


class MockCompleteBody(BaseModel):
    result: Literal["succeeded", "canceled"] = "succeeded"


@router.get("/mock-pay/{payment_id}", response_class=HTMLResponse)
async def mock_pay_page(
    payment_id: uuid.UUID,
    container: AppContainer = Depends(get_container),
) -> HTMLResponse:
    if container.settings.payment_provider != "mock":
        raise HTTPException(status_code=404, detail="Mock payments disabled")

    async with container.session_factory() as session:
        payment = await session.get(Payment, payment_id)
        if payment is None:
            raise NotFoundError("Платёж не найден")
        order = await session.get(Order, payment.order_id) if payment.order_id else None

    order_num = f"#{order.public_number}" if order else "Подписка"
    purpose_ru = "Аванс (30–50%)" if payment.purpose == "advance" else "Окончательный расчёт"
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <meta name="theme-color" content="#04101a" />
  <title>СБП · Ледяной платёжный терминал (Mock)</title>
  <link rel="icon" href="/favicon.ico" sizes="any" />
  <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png" />
  <link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png" />
  <link rel="stylesheet" href="/static/fonts.css" />
  <link rel="stylesheet" href="/static/styles.css" />
  <style>
    .pay-stage {{
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    .pay-terminal {{
      max-width: 470px;
      width: 100%;
      padding: 30px 28px;
    }}
    .pay-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 18px;
    }}
    .pay-amount {{
      font-family: var(--font-display);
      font-size: 38px;
      font-weight: 800;
      margin: 14px 0 18px;
      background: linear-gradient(180deg, #ffffff 0%, #bae9ff 65%, #6cd4fa 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      font-variant-numeric: tabular-nums;
    }}
    .pay-receipt {{
      background: rgba(4, 16, 28, 0.72);
      border: 1px solid var(--ice-edge-soft);
      border-radius: 12px;
      padding: 14px 16px;
      margin-bottom: 20px;
    }}
    .pay-row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 6px 0;
      border-bottom: 1px dashed rgba(176, 230, 255, 0.14);
      font-size: 13px;
    }}
    .pay-row:last-child {{ border-bottom: 0; }}
    .pay-row span {{ color: var(--text-muted); }}
    .pay-actions {{ display: flex; flex-direction: column; gap: 9px; }}
    .pay-status-msg {{
      margin-top: 14px;
      min-height: 22px;
      text-align: center;
      font-weight: 600;
      font-size: 13.5px;
      color: var(--aurora-mint);
    }}
  </style>
</head>
<body>
  <main class="pay-stage">
    <section class="ice-panel ice-interactive pay-terminal">
      <div class="pay-head">
        <span class="section-kicker">ЮKASSA СБП · MOCK</span>
        <span class="pill pill-blue" id="st">{payment.status.upper()}</span>
      </div>

      <h1 style="margin:0;font-size:20px">Оплата заказа <span class="mono">{order_num}</span></h1>
      <div class="pay-amount">{payment.amount:,.2f} ₽</div>

      <div class="pay-receipt">
        <div class="pay-row"><span>Назначение</span><b>{purpose_ru}</b></div>
        <div class="pay-row"><span>Фискализация 54-ФЗ</span><b>Автоматический чек</b></div>
        <div class="pay-row"><span>Способ</span><b>СБП (одностадийно)</b></div>
        <div class="pay-row"><span>ID транзакции</span><code>{payment.provider_payment_id}</code></div>
      </div>

      <div class="pay-actions">
        <button class="btn btn-primary" style="padding:13px 16px;font-size:14px" onclick="complete('succeeded')">
          Подтвердить оплату через СБП →
        </button>
        <button class="btn btn-ghost" style="padding:11px 16px" onclick="complete('canceled')">
          Отменить платёж
        </button>
      </div>

      <div id="msg" class="pay-status-msg"></div>

      <hr class="divider-ice" />
      <div style="display:flex;justify-content:space-between;font-size:12.5px">
        <a href="/simulator">← Вернуться в симулятор MAX</a>
        <a href="/admin">Пульт админа ↗</a>
      </div>
    </section>
  </main>

  <script src="/static/frost-fx.js"></script>
  <script>
    async function complete(result) {{
      const r = await fetch('/mock-pay/{payment.id}/complete', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ result }})
      }});
      const data = await r.json();
      const stEl = document.getElementById('st');
      stEl.textContent = (data.payment_status || result).toUpperCase();
      stEl.className = 'pill ' + (result === 'succeeded' ? 'pill-green' : 'pill-red');
      const msgEl = document.getElementById('msg');
      msgEl.style.color = result === 'succeeded' ? 'var(--aurora-mint)' : 'var(--frost-crimson)';
      msgEl.textContent =
        result === 'succeeded'
          ? 'Оплата подтверждена! Вебхук обработан, карточка обновлена в MAX-боте.'
          : 'Платёж отменён.';
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.post("/mock-pay/{payment_id}/complete")
async def mock_pay_complete(
    payment_id: uuid.UUID,
    body: MockCompleteBody,
    container: AppContainer = Depends(get_container),
) -> dict[str, str]:
    if container.settings.payment_provider != "mock":
        raise HTTPException(status_code=404, detail="Mock payments disabled")

    async with container.session_factory() as session:
        payment = await session.get(Payment, payment_id)
        if payment is None:
            raise NotFoundError("Платёж не найден")
        provider_pid = payment.provider_payment_id

    if isinstance(container.payment_provider, MockPaymentProvider) and provider_pid:
        container.payment_provider.mark_status(provider_pid, body.result)

    if body.result == "succeeded":
        pay, order = await container.orchestrator.confirm_payment_succeeded(payment_id)
        return {
            "payment_id": str(pay.id),
            "payment_status": pay.status,
            "order_status": order.status if order else "",
        }
    pay = await container.orchestrator.cancel_payment(payment_id)
    return {"payment_id": str(pay.id), "payment_status": pay.status, "order_status": ""}
