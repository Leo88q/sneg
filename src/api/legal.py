"""Юридический центр и соответствие требованиям законодательства РФ (20 пунктов).

Покрывает:
- 152-ФЗ «О персональных данных» (ред. 2025–2026: локализация в РФ, отдельное согласие,
  права субъекта, удаление/выгрузка ПДн, уведомление РКН, инциденты 24/72 ч, дети до 14 лет)
- Закон РФ «О защите прав потребителей» (оферта, прозрачные цены, возврат СБП)
- 54-ФЗ «О применении ККТ» (онлайн-чеки аванса, доплаты и возврата)
- 38-ФЗ «О рекламе» (отдельное согласие на рассылки, отписка, без неподтверждённых превосходных степеней)
- 406-ФЗ / 149-ФЗ (авторизация без иностранных OAuth-сервисов)
- 422-ФЗ (НПД для самозанятых исполнителей)
- Политика Cookie и равнозначный Cookie-баннер без тёмных паттернов
- Лицензии self-hosted шрифтов (SIL OFL 1.1) и графики (без внешних CDN)
"""

from __future__ import annotations

import html
import secrets
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from src.api.deps import get_container
from src.core.config import Settings
from src.core.container import AppContainer
from src.infra.db.models import DataSubjectRequest, User, UserConsent

router = APIRouter(tags=["legal"])


def _e(value: str) -> str:
    return html.escape(value, quote=True)


def _display_or_pending(value: str, placeholder: str) -> str:
    clean = value.strip()
    return _e(clean) if clean else f"<span class='legal-muted'>{_e(placeholder)}</span>"


def _legal_nav_links(active: str) -> str:
    items = [
        ("/legal", "Сводка (20 п.)", "index"),
        ("/legal/privacy", "Политика ПДн", "privacy"),
        ("/legal/consent", "Согласие на ПДн", "consent"),
        ("/legal/offer", "Оферта клиента", "offer"),
        ("/legal/refunds", "Возврат средств", "refunds"),
        ("/legal/executor-offer", "Оферта исполнителя", "executor"),
        ("/legal/cookies", "Cookie", "cookies"),
        ("/legal/requisites", "Реквизиты и РКН", "requisites"),
        ("/legal/licenses", "Лицензии", "licenses"),
        ("/legal/my-data", "Удалить мои данные", "my-data"),
    ]
    out: list[str] = []
    for href, label, key in items:
        cls = "legal-pill active" if key == active else "legal-pill"
        if key == "my-data":
            cls += " legal-pill-danger"
        out.append(f'<a class="{cls}" href="{href}">{label}</a>')
    return "\n".join(out)


def _render_legal_page(
    *,
    cfg: Settings,
    title: str,
    eyebrow: str,
    subtitle: str,
    active_key: str,
    body_html: str,
    extra_script: str = "",
) -> HTMLResponse:
    brand = _e(cfg.brand_name)
    op_name = _e(cfg.legal_operator_name)
    inn_text = f"ИНН {_e(cfg.legal_inn)}" if cfg.legal_inn.strip() else "ИНН: не указан"
    ogrn_text = f"ОГРНИП {_e(cfg.legal_ogrnip)}" if cfg.legal_ogrnip.strip() else "ОГРНИП: не указан"
    phone_display = _e(cfg.legal_phone)
    phone_digits = "".join(ch for ch in cfg.legal_phone if ch.isdigit())
    phone_href = f"+{phone_digits}" if phone_digits else ""
    email = _e(cfg.legal_email)
    doc_ver = _e(cfg.legal_doc_version)
    doc_date = _e(cfg.legal_doc_updated_date)

    page = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_e(title)} — {brand}</title>
  <link rel="stylesheet" href="/static/fonts.css" />
  <link rel="stylesheet" href="/static/styles.css" />
  <style>
    .legal-shell {{
      max-width: 1080px;
      margin: 0 auto;
      padding: 24px 20px 72px;
      display: grid;
      gap: 18px;
    }}
    .legal-head {{
      padding: 24px 28px;
    }}
    .legal-head h1 {{
      margin: 10px 0 8px;
      font-size: clamp(22px, 3vw, 32px);
      line-height: 1.18;
    }}
    .legal-head p {{
      margin: 0;
      color: var(--text-frost);
      font-size: 14px;
      line-height: 1.6;
      max-width: 78ch;
    }}
    .legal-meta-bar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 14px;
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--text-muted);
    }}
    .legal-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 14px 16px;
    }}
    .legal-pill {{
      display: inline-flex;
      align-items: center;
      padding: 7px 12px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      color: var(--text-frost);
      background: rgba(8, 28, 44, 0.72);
      border: 1px solid var(--ice-edge);
      transition: all 140ms ease;
    }}
    .legal-pill:hover {{
      color: var(--text-crystal);
      border-color: var(--ice-edge-strong);
      background: rgba(18, 56, 86, 0.82);
    }}
    .legal-pill.active {{
      color: #03131f;
      background: linear-gradient(135deg, #d7f4ff 0%, #86dfff 100%);
      border-color: rgba(255, 255, 255, 0.8);
    }}
    .legal-pill-danger {{
      border-color: rgba(255, 115, 138, 0.45);
      color: #ffc2cd;
    }}
    .legal-pill-danger.active {{
      background: linear-gradient(135deg, #ffd5dd 0%, #ff859a 100%);
      color: #24040a;
    }}
    .legal-article {{
      padding: 28px 30px;
      line-height: 1.68;
      font-size: 14.5px;
      color: var(--text-crystal);
    }}
    .legal-article h2 {{
      font-size: 17px;
      margin: 26px 0 10px;
      color: var(--text-crystal);
      padding-bottom: 6px;
      border-bottom: 1px solid var(--ice-edge-soft);
    }}
    .legal-article h2:first-child {{
      margin-top: 0;
    }}
    .legal-article h3 {{
      font-size: 14px;
      margin: 16px 0 6px;
      color: var(--cryo);
    }}
    .legal-article p {{
      margin: 0 0 12px;
      color: var(--text-frost);
    }}
    .legal-article ul, .legal-article ol {{
      margin: 0 0 14px;
      padding-left: 22px;
      color: var(--text-frost);
    }}
    .legal-article li {{
      margin-bottom: 6px;
    }}
    .legal-article strong {{
      color: var(--text-crystal);
    }}
    .legal-muted {{
      color: var(--text-muted);
      font-style: italic;
    }}
    .legal-callout {{
      margin: 16px 0;
      padding: 14px 16px;
      border-radius: 8px;
      background: rgba(14, 48, 74, 0.56);
      border: 1px solid rgba(114, 214, 255, 0.34);
      color: var(--text-crystal);
      font-size: 13.5px;
    }}
    .legal-callout-warn {{
      background: rgba(74, 44, 12, 0.44);
      border-color: rgba(255, 184, 77, 0.42);
    }}
    .legal-table-wrap {{
      overflow-x: auto;
      margin: 14px 0 18px;
      border: 1px solid var(--ice-edge);
      border-radius: 8px;
    }}
    .legal-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    .legal-table th, .legal-table td {{
      padding: 10px 12px;
      text-align: left;
      border-bottom: 1px solid var(--ice-edge-soft);
      vertical-align: top;
    }}
    .legal-table th {{
      font-family: var(--font-mono);
      font-size: 11px;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: var(--cryo);
      background: rgba(7, 24, 39, 0.85);
    }}
    .legal-table tr:last-child td {{
      border-bottom: none;
    }}
    .dsr-form {{
      display: grid;
      gap: 14px;
      margin-top: 16px;
    }}
    .dsr-types {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 10px;
    }}
    .dsr-type-card {{
      display: flex;
      gap: 10px;
      align-items: flex-start;
      padding: 12px 14px;
      border-radius: 8px;
      border: 1px solid var(--ice-edge);
      background: rgba(6, 22, 36, 0.72);
      cursor: pointer;
    }}
    .dsr-type-card:has(input:checked) {{
      border-color: var(--cryo);
      background: rgba(18, 62, 94, 0.62);
    }}
    .dsr-type-card input {{
      margin-top: 3px;
      accent-color: #79d8ff;
    }}
    .dsr-check {{
      display: flex;
      gap: 10px;
      align-items: flex-start;
      padding: 10px 12px;
      border-radius: 8px;
      border: 1px solid var(--ice-edge);
      background: rgba(5, 19, 31, 0.65);
      font-size: 13px;
      color: var(--text-frost);
      cursor: pointer;
    }}
    .dsr-check input {{
      width: 18px;
      height: 18px;
      margin-top: 2px;
      flex-shrink: 0;
      accent-color: #79d8ff;
    }}
    .dsr-result {{
      padding: 14px 16px;
      border-radius: 8px;
      border: 1px solid rgba(72, 232, 178, 0.45);
      background: rgba(10, 62, 48, 0.48);
      color: #d9fff0;
      display: none;
    }}
    .dsr-result.error {{
      border-color: rgba(255, 112, 136, 0.5);
      background: rgba(76, 16, 28, 0.5);
      color: #ffd9e0;
    }}
    .legal-footer-bar {{
      padding: 18px 22px;
      font-size: 12px;
      color: var(--text-muted);
      display: flex;
      flex-wrap: wrap;
      gap: 12px 18px;
      justify-content: space-between;
      align-items: center;
    }}
    .legal-footer-links {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px 14px;
    }}
    .legal-footer-links a, .legal-footer-links button {{
      color: var(--text-frost);
      font-size: 12px;
      background: none;
      border: none;
      padding: 0;
      cursor: pointer;
      font-family: inherit;
      text-decoration: underline;
      text-underline-offset: 3px;
    }}
    .legal-footer-links a:hover, .legal-footer-links button:hover {{
      color: var(--cryo);
    }}
    @media (max-width: 720px) {{
      .legal-head, .legal-article {{ padding: 18px; }}
    }}
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8">
          <path d="M12 2v20M2 12h20M4.93 4.93l14.14 14.14M19.07 4.93L4.93 19.07"/>
          <circle cx="12" cy="12" r="3"/>
        </svg>
      </div>
      <div class="brand-sub">
        <span>{brand} · ПРАВОВОЙ ЦЕНТР</span>
        <small>152-ФЗ · 54-ФЗ · 38-ФЗ · ЗоЗПП · ХРАНЕНИЕ ДАННЫХ В РФ</small>
      </div>
    </div>
    <div class="topbar-meta">
      <nav class="nav-links">
        <a class="btn btn-sm" href="/">На главную</a>
        <a class="btn btn-sm btn-danger" href="/legal/my-data">Удалить мои данные</a>
        <button class="btn btn-sm btn-ghost" type="button" onclick="toggleSnowFx(this)">Снег</button>
      </nav>
    </div>
  </header>

  <main class="legal-shell">
    <section class="ice-panel legal-head">
      <span class="pill pill-blue">{_e(eyebrow)}</span>
      <h1>{_e(title)}</h1>
      <p>{_e(subtitle)}</p>
      <div class="legal-meta-bar">
        <span>ОПЕРАТОР: {op_name} ({inn_text}, {ogrn_text})</span>
        <span>·</span>
        <span>ТЕЛ: <a href="tel:{phone_href}">{phone_display}</a></span>
        <span>·</span>
        <span>E-MAIL: <a href="mailto:{email}">{email}</a></span>
        <span>·</span>
        <span>ВЕРСИЯ: v{doc_ver} от {doc_date}</span>
      </div>
    </section>

    <nav class="ice-panel legal-nav" aria-label="Юридические документы">
      {_legal_nav_links(active_key)}
    </nav>

    <article class="ice-panel legal-article">
      {body_html}
    </article>

    <footer class="ice-panel legal-footer-bar">
      <div>
        <strong>{op_name}</strong> · {inn_text} · {ogrn_text} · <a href="tel:{phone_href}">{phone_display}</a> · <a href="mailto:{email}">{email}</a> · 18+
      </div>
      <div class="legal-footer-links">
        <a href="/legal/privacy">Политика ПДн</a>
        <a href="/legal/consent">Согласие на ПДн</a>
        <a href="/legal/offer">Оферта</a>
        <a href="/legal/refunds">Возврат</a>
        <a href="/legal/cookies">Cookie</a>
        <a href="/legal/requisites">Реквизиты</a>
        <a href="/legal/licenses">Лицензии</a>
        <a href="/legal/my-data">Удалить мои данные</a>
        <button type="button" onclick="window.openCookieSettings && window.openCookieSettings()">Настройки Cookie</button>
      </div>
    </footer>
  </main>

  <script src="/static/frost-fx.js"></script>
  {extra_script}
</body>
</html>"""
    return HTMLResponse(page)


@router.get("/legal", response_class=HTMLResponse, include_in_schema=False)
async def legal_hub(container: AppContainer = Depends(get_container)) -> HTMLResponse:
    cfg = container.settings
    rows = [
        ("1", "Политика конфиденциальности", "152-ФЗ (ред. 2025–2026)", "Опубликована", "/legal/privacy"),
        ("2", "Публичная оферта (клиент и исполнитель)", "ГК РФ ст. 437, ЗоЗПП, 422-ФЗ", "Опубликованы", "/legal/offer"),
        ("3", "Условия отмены и возврата (СБП)", "ЗоЗПП ст. 32, 54-ФЗ", "Опубликованы", "/legal/refunds"),
        ("4", "Политика и согласие на Cookie", "152-ФЗ, прозрачный перечень", "Опубликована", "/legal/cookies"),
        ("5", "Баннер Cookie без тёмных паттернов", "Равнозначные кнопки выбора", "Активен на всех страницах", "/legal/cookies"),
        ("6", "Непредзаполненные галочки в формах", "Раздельное согласие ПДн (с 01.09.2025)", "Внедрено в формах и боте", "/legal/consent"),
        ("7", "Минимизация собираемых данных", "152-ФЗ ст. 5 (без лишних полей)", "Соблюдено", "/legal/privacy"),
        ("8", "Отсутствие зарубежных трекеров и CDN", "152-ФЗ ч. 5 ст. 18 (0 внешних скриптов)", "Шрифты и код локально в РФ", "/legal/licenses"),
        ("9", "Отсутствие тёмных паттернов", "Честный интерфейс без манипуляций", "Проверено", "/legal/my-data"),
        ("10", "Отсутствие скрытых платежей", "Прозрачный расчёт, доплата >20% только по согласию", "Закреплено в оферте и коде", "/legal/offer"),
        ("11", "Отсутствие фейковых отзывов", "Только реальные оценки завершённых заказов", "Соблюдено", "/legal/offer"),
        ("12", "Без неподтверждённых «лучший / №1»", "38-ФЗ «О рекламе» ст. 5", "Тексты очищены", "/legal/offer"),
        ("13", "Первичные БД и файлы в России", "152-ФЗ ч. 5 ст. 18 (ЦОД в РФ)", _e(cfg.legal_datacenter_info), "/legal/requisites"),
        ("14", "Уведомление в реестр операторов ПДн РКН", "152-ФЗ ст. 22 (pd.rkn.gov.ru)", _e(cfg.legal_rkn_reg_no) if cfg.legal_rkn_reg_no.strip() else "Подаётся до старта приёма заявок", "/legal/requisites"),
        ("15", "Вход без иностранных OAuth-сервисов", "406-ФЗ / 149-ФЗ", "Только MAX (РФ) и локальный вход", "/legal/requisites"),
        ("16", "Открытые реквизиты оператора", "ЗоЗПП ст. 9, 149-ФЗ", "Страница и футер на каждом экране", "/legal/requisites"),
        ("17", "Ограничение 18+ / дети до 14 лет", "ГК РФ ст. 28, 152-ФЗ", "Закреплено в документах и формах", "/legal/privacy"),
        ("18", "Отдельная отписка от рассылок", "38-ФЗ ст. 18, 152-ФЗ ст. 15", "На сайте (/legal/my-data) и /unsubscribe в боте", "/legal/my-data"),
        ("19", "Лицензии шрифтов и графики", "ГК РФ ч. IV, SIL OFL 1.1", "Self-hosted OFL + авторские SVG", "/legal/licenses"),
        ("20", "Способ удалить свои данные", "152-ФЗ ст. 14, 21 (до 10 раб. дней)", "Форма на сайте + /delete_my_data в боте", "/legal/my-data"),
    ]
    trs = "".join(
        f"<tr><td class='mono'>{num}</td><td><strong>{name}</strong></td>"
        f"<td>{law}</td><td>{status_txt}</td>"
        f"<td><a href='{href}'>Открыть →</a></td></tr>"
        for num, name, law, status_txt, href in rows
    )
    body = f"""
      <h2>Матрица соответствия требованиям законодательства РФ</h2>
      <p>
        Сервис <strong>{_e(cfg.brand_name)}</strong> работает в российской юрисдикции.
        Все обязательные правовые документы, реквизиты оператора, правила возврата,
        лицензии и инструменты управления персональными данными размещены открыто
        на этой платформе.
      </p>
      <div class="legal-table-wrap">
        <table class="legal-table">
          <thead>
            <tr>
              <th>№</th>
              <th>Требование</th>
              <th>Норма / основание</th>
              <th>Статус реализации</th>
              <th>Документ</th>
            </tr>
          </thead>
          <tbody>{trs}</tbody>
        </table>
      </div>
    """
    return _render_legal_page(
        cfg=cfg,
        title="Правовая документация и соответствие 20 требованиям РФ",
        eyebrow="ЮРИДИЧЕСКИЙ ЦЕНТР",
        subtitle="Полный комплект документов 152-ФЗ, 54-ФЗ, 38-ФЗ, ЗоЗПП и инструменты управления персональными данными.",
        active_key="index",
        body_html=body,
    )


@router.get("/legal/privacy", response_class=HTMLResponse, include_in_schema=False)
async def legal_privacy(container: AppContainer = Depends(get_container)) -> HTMLResponse:
    cfg = container.settings
    op = _e(cfg.legal_operator_name)
    brand = _e(cfg.brand_name)
    email = _e(cfg.legal_email)
    dc = _e(cfg.legal_datacenter_info)
    rkn = _display_or_pending(
        cfg.legal_rkn_reg_no,
        "регистрационный номер вносится после обработки уведомления на pd.rkn.gov.ru",
    )
    body = f"""
      <h2>1. Общие положения и сведения об Операторе</h2>
      <p>
        Настоящая Политика обработки персональных данных (далее — «Политика») разработана в
        соответствии с Федеральным законом от 27.07.2006 № 152-ФЗ «О персональных данных»
        (в редакции, действующей в 2025–2026 гг.) и определяет порядок обработки и меры по
        обеспечению безопасности персональных данных в сервисе <strong>{brand}</strong>
        (веб-сайт и чат-бот в российском мессенджере MAX).
      </p>
      <p>
        <strong>Оператор персональных данных:</strong> {op} (ИНН {_e(cfg.legal_inn)}, ОГРНИП {_e(cfg.legal_ogrnip)}).<br />
        <strong>Контактный телефон:</strong> {_e(cfg.legal_phone)} ·
        <strong>E-mail для обращений субъектов ПДн:</strong> <a href="mailto:{email}">{email}</a>.<br />
        <strong>Статус в Реестре операторов ПДн Роскомнадзора (ст. 22 152-ФЗ):</strong> {rkn}.
      </p>

      <h2>2. Принцип минимизации и перечень обрабатываемых данных</h2>
      <p>
        Оператор собирает исключительно минимальный объём сведений, необходимый для расчёта
        стоимости, исполнения заявок, фискализации платежей по 54-ФЗ и расчётов с исполнителями
        (ч. 4–5 ст. 5 152-ФЗ). Избыточные сведения (паспортные данные клиентов, дата рождения,
        биометрические и специальные категории ПДн) <strong>не запрашиваются и не обрабатываются</strong>.
      </p>
      <div class="legal-table-wrap">
        <table class="legal-table">
          <thead>
            <tr>
              <th>Категория субъектов</th>
              <th>Состав данных</th>
              <th>Цель обработки</th>
              <th>Срок хранения</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Клиенты (заказчики услуг)</td>
              <td>Идентификатор в MAX, имя, адрес/геокоординаты объекта работ, контактный телефон (при добровольном предоставлении), email для чека 54-ФЗ, история заказов и фото объекта ДО/ПОСЛЕ (без лиц людей)</td>
              <td>Заключение и исполнение договора (оферты), расчёт стоимости, направление бригады, приёмка работ, выставление чеков 54-ФЗ и рассмотрение споров</td>
              <td>До достижения цели или отзыва согласия; фискальные и расчётные записи — 5 лет в силу НК РФ и 402-ФЗ</td>
            </tr>
            <tr>
              <td>Исполнители (самозанятые НПД)</td>
              <td>Идентификатор в MAX, ФИО, контактный телефон, ИНН, статус плательщика НПД, сведения о наличии техники/допусков, ссылки на чеки «Мой налог»</td>
              <td>Заключение и исполнение договора ГПХ, распределение заявок, контроль качества, учёт выплат по 422-ФЗ</td>
              <td>В период сотрудничества и 5 лет после окончания расчётов (НК РФ, 402-ФЗ)</td>
            </tr>
            <tr>
              <td>Посетители сайта</td>
              <td>Технические логи сервера (IP, дата, URL) и строго необходимые файлы Cookie (выбор cookie-баннера, сессия админ-панели)</td>
              <td>Обеспечение работоспособности и информационной безопасности сервиса</td>
              <td>Технические логи — до 90 дней; cookie выбора — 180 дней</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>3. Локализация баз данных на территории Российской Федерации</h2>
      <div class="legal-callout">
        В строгом соответствии с <strong>ч. 5 ст. 18 152-ФЗ</strong> первичный сбор, запись,
        систематизация, накопление, хранение, уточнение и извлечение персональных данных граждан РФ
        осуществляются исключительно с использованием баз данных и серверов, физически
        расположенных на территории Российской Федерации: <strong>{dc}</strong>.
      </div>
      <ul>
        <li><strong>Трансграничная передача ПДн не осуществляется.</strong></li>
        <li><strong>Сторонние зарубежные сервисы не используются:</strong> на сайте отключены зарубежные шрифты (Google Fonts), зарубежная аналитика, рекламные пиксели и иностранные системы авторизации (OAuth Google/Apple/Meta). Все шрифты и скрипты размещены локально на сервере в РФ.</li>
        <li>Приём платежей и фискализация 54-ФЗ осуществляются через российского платёжного провайдера (ЮKassa / СБП, серверы в РФ).</li>
      </ul>

      <h2>4. Отдельное согласие и раздельная обработка</h2>
      <p>
        В соответствии с требованиями 152-ФЗ (вступившими в силу с 01.09.2025) согласие субъекта
        на обработку персональных данных оформляется <strong>отдельным документом</strong>
        (<a href="/legal/consent">Согласие на обработку ПДн</a>) и не объединяется с текстом
        публичной оферты. Передача контактного номера телефона клиента назначенному исполнителю
        происходит только при выборе клиентом соответствующего режима связи (либо взаимодействие
        ведётся анонимно через прокси-чат бота).
      </p>

      <h2>5. Несовершеннолетние и дети до 14 лет</h2>
      <p>
        Сервис предназначен для полностью дееспособных лиц, достигших <strong>18 лет</strong>.
        Оператор не осуществляет целенаправленный сбор персональных данных детей в возрасте до
        14 лет. Лица младше 18 лет вправе использовать сервис исключительно с письменного
        согласия родителей или иных законных представителей (ст. 26, 28 ГК РФ). Если Оператору
        станет известно о передаче ПДн ребёнка до 14 лет без согласия законного представителя,
        такие данные подлежат немедленному удалению по обращению на
        <a href="/legal/my-data">/legal/my-data</a> или <a href="mailto:{email}">{email}</a>.
      </p>

      <h2>6. Информационные рассылки и право на отписку</h2>
      <p>
        Сервисные уведомления (статус заказа, приезд бригады, фотоотчёт, ссылка на чек) являются
        частью исполнения договора. Направление рекламных и сезонных информационных сообщений
        осуществляется только при наличии <strong>отдельного добровольного согласия</strong>
        (ст. 18 38-ФЗ «О рекламе»). Отписаться можно в любой момент:
      </p>
      <ul>
        <li>через страницу <a href="/legal/my-data">Мои данные → Отписаться от рассылок</a>;</li>
        <li>отправив команду <code>/unsubscribe</code> в чат-боте MAX;</li>
        <li>написав письмо на <a href="mailto:{email}">{email}</a>.</li>
      </ul>

      <h2>7. Защита данных и реагирование на инциденты (ст. 19, 21 152-ФЗ)</h2>
      <ul>
        <li>Передача данных защищена шифрованием TLS 1.2/1.3 (HTTPS), пароли администраторов хранятся в виде стойкого криптографического хеша, доступ к базе данных закрыт от внешней сети.</li>
        <li>Ведётся журнал аудита обращений и административных действий.</li>
        <li>В случае выявления инцидента неправомерной передачи (утечки) ПДн Оператор уведомляет Роскомнадзор в установленные ст. 21 152-ФЗ сроки: в течение <strong>24 часов</strong> (первичное уведомление) и <strong>72 часов</strong> (результаты внутреннего расследования).</li>
      </ul>

      <h2>8. Права субъекта и удаление персональных данных</h2>
      <p>
        Вы вправе в любой момент запросить сведения об обрабатываемых данных (ст. 14 152-ФЗ),
        потребовать их уточнения, блокирования, отзыва согласия или <strong>полного удаления /
        обезличивания</strong> (ст. 21 152-ФЗ):
      </p>
      <ul>
        <li>через интерактивную форму <a href="/legal/my-data"><strong>Мои данные → Удалить мои данные</strong></a>;</li>
        <li>через команду <code>/delete_my_data</code> в чат-боте MAX;</li>
        <li>по электронной почте <a href="mailto:{email}">{email}</a>.</li>
      </ul>
      <p>
        Срок исполнения запроса — <strong>до 10 рабочих дней</strong> (ст. 21 152-ФЗ). Данные,
        хранение которых прямо предписано федеральными законами (фискальные чеки 54-ФЗ,
        бухгалтерские и налоговые документы), обезличиваются в части не обязательных для учёта
        полей и хранятся изолированно до истечения установленного законом срока.
      </p>
    """
    return _render_legal_page(
        cfg=cfg,
        title="Политика обработки персональных данных",
        eyebrow="152-ФЗ · РЕДАКЦИЯ 2025–2026",
        subtitle="Порядок сбора, локализованного хранения в РФ, защиты и удаления персональных данных.",
        active_key="privacy",
        body_html=body,
    )


@router.get("/legal/consent", response_class=HTMLResponse, include_in_schema=False)
async def legal_consent(container: AppContainer = Depends(get_container)) -> HTMLResponse:
    cfg = container.settings
    op = _e(cfg.legal_operator_name)
    brand = _e(cfg.brand_name)
    email = _e(cfg.legal_email)
    addr = _e(cfg.legal_address)
    dc = _e(cfg.legal_datacenter_info)
    body = f"""
      <div class="legal-callout">
        Данный документ является <strong>самостоятельным Согласием на обработку персональных данных</strong>,
        отделённым от Публичной оферты и иных пользовательских соглашений в соответствии с
        требованиями Федерального закона № 152-ФЗ (действуют с 01.09.2025).
      </div>

      <h2>1. Согласие на обработку персональных данных</h2>
      <p>
        Я, субъект персональных данных (достигший 18 лет либо действующий через законного
        представителя), свободно, своей волей и в своём интересе даю конкретное, предметное,
        информированное, сознательное и однозначное согласие Оператору —
        <strong>{op}</strong> (ИНН {_e(cfg.legal_inn)}, ОГРНИП {_e(cfg.legal_ogrnip)},
        {addr}, тел. {_e(cfg.legal_phone)}, e-mail: <a href="mailto:{email}">{email}</a>) —
        на обработку моих персональных данных в сервисе <strong>{brand}</strong>.
      </p>
      <h3>1.1. Конкретные цели обработки</h3>
      <ul>
        <li>Регистрация и идентификация пользователя в чат-боте MAX и/или обработка моего обращения на веб-сайте;</li>
        <li>Расчёт ориентировочной и итоговой стоимости услуг по благоустройству, уборке снега и смежным работам;</li>
        <li>Исполнение заявок, направление исполнителей на объект, фотофиксация результата работ (ДО/ПОСЛЕ);</li>
        <li>Приём оплаты, возврат средств и направление кассовых чеков в соответствии с 54-ФЗ;</li>
        <li>Исполнение запросов субъекта ПДн (выгрузка, уточнение, отзыв согласия, удаление данных).</li>
      </ul>
      <h3>1.2. Перечень данных и действия</h3>
      <p>
        Перечень данных: идентификатор в мессенджере MAX, имя, адрес и/или координаты объекта
        выполнения работ, контактный номер телефона (при добровольном вводе), адрес электронной
        почты (при предоставлении), текст обращений и сведения о заказах.
      </p>
      <p>
        Разрешаемые действия (с использованием средств автоматизации и без них): сбор, запись,
        систематизация, накопление, хранение, уточнение (обновление, изменение), извлечение,
        использование, обезличивание, блокирование, удаление и уничтожение. Все базы данных
        размещены на территории РФ ({dc}).
      </p>
      <h3>1.3. Срок действия и отзыв согласия</h3>
      <p>
        Согласие действует до достижения целей обработки либо до его отзыва субъектом.
        Согласие может быть отозвано в любой момент через страницу
        <a href="/legal/my-data"><strong>/legal/my-data</strong></a>, команду
        <code>/delete_my_data</code> в боте MAX или письмо на <a href="mailto:{email}">{email}</a>.
      </p>

      <h2>2. Отдельное согласие на информационные и маркетинговые сообщения (38-ФЗ)</h2>
      <p>
        Предоставляется <strong>строго опционально</strong> путём отдельной непредзаполненной
        отметки (галочки) и <strong>не является условием</strong> заказа услуг или работы с сайтом.
        Отказ от рассылок не ограничивает доступ к сервису. Отписаться от любых информационных
        сообщений можно в любой момент через <a href="/legal/my-data">/legal/my-data</a> или
        командой <code>/unsubscribe</code> в боте.
      </p>
    """
    return _render_legal_page(
        cfg=cfg,
        title="Согласие на обработку персональных данных",
        eyebrow="ОТДЕЛЬНЫЙ ДОКУМЕНТ · 152-ФЗ (С 01.09.2025)",
        subtitle="Отдельное согласие на обработку ПДн и независимое опциональное согласие на рассылки.",
        active_key="consent",
        body_html=body,
    )


@router.get("/legal/offer", response_class=HTMLResponse, include_in_schema=False)
async def legal_offer(container: AppContainer = Depends(get_container)) -> HTMLResponse:
    cfg = container.settings
    op = _e(cfg.legal_operator_name)
    brand = _e(cfg.brand_name)
    email = _e(cfg.legal_email)
    body = f"""
      <h2>1. Предмет Публичной оферты</h2>
      <p>
        Настоящий документ является официальным предложением (публичной офертой в смысле
        ст. 437 ГК РФ) <strong>{op}</strong> (ИНН {_e(cfg.legal_inn)}, ОГРНИП {_e(cfg.legal_ogrnip)},
        тел. {_e(cfg.legal_phone)}, e-mail <a href="mailto:{email}">{email}</a>, система
        налогообложения: {_e(cfg.legal_tax_regime)}; далее — «Исполнитель» / «Сервис {brand}») заключить
        договор возмездного оказания услуг по уборке снега, наледи, сезонной уборке и
        благоустройству придомовых территорий, расчистке крыш, погрузке/разгрузке и смежным
        работам (далее — «Услуги») с любым дееспособным физическим лицом в возрасте от 18 лет
        или юридическим лицом (далее — «Заказчик»).
      </p>
      <p>
        Акцептом оферты является явное подтверждение заказа в чат-боте MAX и внесение аванса
        (предоплаты) по ссылке на оплату.
      </p>

      <h2>2. Прозрачное ценообразование и отсутствие скрытых платежей</h2>
      <ul>
        <li>Стоимость Услуг рассчитывается автоматически до оплаты на основании введённых Заказчиком параметров (зона выезда, площадь/объём, срочность, сложность доступа, дополнительные опции) по действующему тарифному конфигу, который фиксируется в заказе на момент создания.</li>
        <li><strong>Никаких скрытых платежей и скрытых автосписаний:</strong> итоговая сумма, размер аванса (30–50%) и сумма доплаты показываются Заказчику до перехода к оплате.</li>
        <li>Рекуррентные (периодические) списания без отдельного осознанного подключения подписки не производятся.</li>
        <li><strong>Корректировка объёма на объекте:</strong> если фактический объём работ отличается от заявленного Заказчиком более чем на 20%, бригада направляет фотоподтверждение и предложение скорректированной цены. Новая цена применяется <strong>только после явного подтверждения Заказчика</strong> в боте; Заказчик вправе отказаться от увеличения объёма и оплатить только согласованный объём либо отменить заказ на условиях раздела «Возврат».</li>
      </ul>

      <h2>3. Порядок оплаты и кассовые чеки (54-ФЗ)</h2>
      <ul>
        <li>Оплата производится в безналичной форме (включая Систему быстрых платежей — СБП) через российского платёжного провайдера ЮKassa двумя одностадийными платежами: <strong>аванс</strong> до начала работ и <strong>окончательная доплата</strong> после приёмки.</li>
        <li>На каждый платёж (аванс, доплата, возврат) формируется электронный кассовый чек в соответствии с Федеральным законом № 54-ФЗ.</li>
      </ul>

      <h2>4. Приёмка работ по фотоотчёту и разрешение споров</h2>
      <ul>
        <li>Исполнитель фиксирует состояние объекта фотографиями <strong>ДО</strong> начала работ и <strong>ПОСЛЕ</strong> завершения и отправляет фотоотчёт Заказчику в чат-бот MAX.</li>
        <li>Заказчик в течение <strong>24 часов</strong> с момента получения фотоотчёта подтверждает приёмку либо открывает спор с указанием замечаний. При отсутствии замечаний в течение 24 часов работы считаются принятыми.</li>
        <li>Отзывы и оценки Заказчиков формируются исключительно по фактически завершённым заказам; публикация вымышленных отзывов не допускается.</li>
      </ul>

      <h2>5. Отмена заказа и возврат средств</h2>
      <p>
        Подробные правила отмены и возврата денежных средств по СБП изложены на отдельной
        странице <a href="/legal/refunds"><strong>Условия возврата средств</strong></a>,
        являющейся неотъемлемой частью настоящей Оферты.
      </p>

      <h2>6. Возрастное ограничение и ответственность</h2>
      <ul>
        <li>Заказчик подтверждает достижение 18 лет (для несовершеннолетних договор заключается с согласия законных представителей в порядке ст. 26 ГК РФ).</li>
        <li>Заказчик обеспечивает безопасный доступ на участок и предупреждает о скрытых коммуникациях, декоративных элементах, кабелях и хрупких покрытиях под снегом/листвой.</li>
        <li>Претензии направляются через чат-бот или на <a href="mailto:{email}">{email}</a> и рассматриваются в срок до 10 календарных дней в соответствии с Законом РФ «О защите прав потребителей».</li>
      </ul>
    """
    return _render_legal_page(
        cfg=cfg,
        title="Публичная оферта на оказание услуг",
        eyebrow="ГК РФ СТ. 437 · ЗоЗПП · 54-ФЗ",
        subtitle="Условия заказа, прозрачного расчёта стоимости, приёмки по фотоотчёту и расчётов.",
        active_key="offer",
        body_html=body,
    )


@router.get("/legal/refunds", response_class=HTMLResponse, include_in_schema=False)
async def legal_refunds(container: AppContainer = Depends(get_container)) -> HTMLResponse:
    cfg = container.settings
    email = _e(cfg.legal_email)
    body = f"""
      <h2>1. Общие правила возврата денежных средств</h2>
      <p>
        Настоящие условия действуют в соответствии со ст. 32 Закона РФ от 07.02.1992 № 2300-1
        «О защите прав потребителей» и ст. 782 ГК РФ. Заказчик вправе отказаться от исполнения
        договора в любое время при условии оплаты фактически понесённых расходов.
      </p>

      <div class="legal-table-wrap">
        <table class="legal-table">
          <thead>
            <tr>
              <th>Момент отмены / ситуация</th>
              <th>Размер возврата</th>
              <th>Удержание</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Отмена Заказчиком <strong>до выезда</strong> бригады (статусы «Ожидает исполнителя» / «Исполнитель назначен»)</td>
              <td><strong>100% оплаченного аванса</strong></td>
              <td>0 ₽</td>
            </tr>
            <tr>
              <td>Отмена по инициативе Сервиса (не найден исполнитель, срыв со стороны бригады)</td>
              <td><strong>100% оплаченного аванса</strong></td>
              <td>0 ₽</td>
            </tr>
            <tr>
              <td>Отмена Заказчиком <strong>после выезда</strong> бригады на адрес (статус «В пути» или «Прибыл на объект»), либо ложный вызов / отсутствие доступа на объект</td>
              <td>Оплаченный аванс за вычетом стоимости фактического выезда по зоне (подтверждённые расходы)</td>
              <td>Фиксированная стоимость выезда зоны, показанная Заказчику до оплаты</td>
            </tr>
            <tr>
              <td>Отказ Заказчика от предложенной корректировки цены &gt;20% на объекте</td>
              <td><strong>100% аванса</strong> (если работы не начаты) либо перерасчёт по фактически выполненному объёму</td>
              <td>Без штрафов</td>
            </tr>
            <tr>
              <td>Подтверждённое нарушение качества работ (спор по фотоотчёту ДО/ПОСЛЕ)</td>
              <td>Соразмерное уменьшение цены (частичный возврат) или <strong>полный возврат</strong> по результатам рассмотрения спора</td>
              <td>По решению спора</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>2. Сроки и способ возврата</h2>
      <ul>
        <li>Возврат производится <strong>тем же способом оплаты (СБП / банковский счёт плательщика)</strong>, с которого был совершён платёж, через платёжный шлюз ЮKassa.</li>
        <li>Инициация возврата сервисом — в течение <strong>1 рабочего дня</strong> с момента отмены заказа или завершения рассмотрения спора.</li>
        <li>Фактическое зачисление банком по СБП обычно занимает от нескольких минут до <strong>1–3 рабочих дней</strong> (максимальный банковский срок — до 10 рабочих дней).</li>
        <li>При каждом возврате автоматически формируется кассовый чек возврата прихода по 54-ФЗ.</li>
      </ul>

      <h2>3. Как оформить возврат</h2>
      <p>
        Отменить заказ можно прямо в чат-боте MAX кнопкой отмены в карточке заказа. По спорным
        вопросам и ручным возвратам напишите на <a href="mailto:{email}">{email}</a> либо
        позвоните по телефону <strong>{_e(cfg.legal_phone)}</strong>, указав номер заказа —
        обращение регистрируется и рассматривается в срок до 10 календарных дней.
      </p>
    """
    return _render_legal_page(
        cfg=cfg,
        title="Условия отмены заказа и возврата средств",
        eyebrow="ЗоЗПП СТ. 32 · ВОЗВРАТЫ СБП",
        subtitle="Прозрачные правила полного и частичного возврата предоплаты и сроки зачисления.",
        active_key="refunds",
        body_html=body,
    )


@router.get("/legal/executor-offer", response_class=HTMLResponse, include_in_schema=False)
async def legal_executor_offer(container: AppContainer = Depends(get_container)) -> HTMLResponse:
    cfg = container.settings
    op = _e(cfg.legal_operator_name)
    brand = _e(cfg.brand_name)
    body = f"""
      <h2>1. Статус сторон (ГПХ с плательщиками НПД)</h2>
      <p>
        Настоящая оферта регулирует взаимодействие <strong>{op}</strong> (ИНН {_e(cfg.legal_inn)},
        ОГРНИП {_e(cfg.legal_ogrnip)}, тел. {_e(cfg.legal_phone)}, e-mail
        <a href="mailto:{_e(cfg.legal_email)}">{_e(cfg.legal_email)}</a>; далее —
        «Заказчик-агрегатор», сервис <strong>{brand}</strong>) с физическими лицами, применяющими специальный налоговый
        режим «Налог на профессиональный доход» (самозанятыми по Федеральному закону от 27.11.2018
        № 422-ФЗ), либо индивидуальными предпринимателями (далее — «Подрядчик»).
      </p>
      <p>
        Настоящий договор является гражданско-правовым договором подряда (гл. 37 ГК РФ) и не
        создаёт трудовых отношений. Подрядчик самостоятельно определяет график занятости и
        добровольно выбирает заявки в чат-боте MAX.
      </p>

      <h2>2. Обязательные требования к Подрядчику</h2>
      <ul>
        <li>Возраст от 18 лет, полная дееспособность, действующий статус плательщика НПД (подтверждается по ИНН перед активацией профиля).</li>
        <li>Соблюдение техники безопасности при работе с ручным инструментом, снегоуборочной и иной техникой; для высотных и кровельных работ — обязательное наличие профильного допуска и страхования ответственности.</li>
        <li>Обязательная фотофиксация объекта <strong>ДО</strong> начала и <strong>ПОСЛЕ</strong> завершения работ.</li>
        <li>Бережное отношение к имуществу клиентов и неразглашение полученных адресов и контактов (152-ФЗ).</li>
      </ul>

      <h2>3. Вознаграждение, расчёты и чеки «Мой налог»</h2>
      <ul>
        <li>Размер вознаграждения Подрядчика (доля от стоимости заказа, по умолчанию 65–70% либо индивидуальная ставка) показывается в карточке заявки до её принятия.</li>
        <li>Выплата производится по ведомости на банковский счёт Подрядчика после приёмки работ клиентом.</li>
        <li>Подрядчик обязан сформировать и передать Заказчику-агрегатору чек из приложения ФНС «Мой налог» на каждую полученную сумму (ст. 14 422-ФЗ).</li>
        <li>При срыве принятого заказа без уважительной причины (неявка после подтверждения) применяются штрафные санкции, заранее указанные в тарифах и карточке профиля.</li>
      </ul>
    """
    return _render_legal_page(
        cfg=cfg,
        title="Оферта для исполнителей (самозанятые НПД)",
        eyebrow="ГК РФ ГЛ. 37 · 422-ФЗ (НПД)",
        subtitle="Условия подключения бригад и самозанятых мастеров, правила приёмки и выплат.",
        active_key="executor",
        body_html=body,
    )


@router.get("/legal/cookies", response_class=HTMLResponse, include_in_schema=False)
async def legal_cookies(container: AppContainer = Depends(get_container)) -> HTMLResponse:
    cfg = container.settings
    brand = _e(cfg.brand_name)
    body = f"""
      <h2>1. Какие файлы Cookie и локальное хранилище мы используем</h2>
      <p>
        Сайт сервиса <strong>{brand}</strong> использует <strong>минимально необходимый</strong>
        набор технических файлов Cookie и записей <code>localStorage</code>.
        <strong>Рекламные, кросс-сайтовые и зарубежные аналитические Cookie на сайте отсутствуют.</strong>
      </p>
      <div class="legal-table-wrap">
        <table class="legal-table">
          <thead>
            <tr>
              <th>Имя</th>
              <th>Тип</th>
              <th>Назначение</th>
              <th>Срок хранения</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="mono">chistodvor_cookie_consent</td>
              <td>Строго необходимый (Cookie + localStorage)</td>
              <td>Сохраняет ваш выбор в Cookie-баннере («Принять все» или «Только необходимые»), чтобы не показывать баннер повторно</td>
              <td>180 дней</td>
            </tr>
            <tr>
              <td class="mono">sneg_admin_token</td>
              <td>Строго необходимый (HttpOnly Cookie)</td>
              <td>Безопасная авторизация сотрудников и владельца в веб-админ-панели <code>/admin</code> (не устанавливается обычным посетителям)</td>
              <td>До 8 часов (время сессии)</td>
            </tr>
            <tr>
              <td class="mono">cryo_snow_fx</td>
              <td>Функциональный (localStorage)</td>
              <td>Запоминает, включён или выключен фоновый эффект снежинок в интерфейсе (сохраняется только при согласии на функциональные настройки либо после ручного переключения кнопки)</td>
              <td>До очистки браузера</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>2. Управление настройками Cookie</h2>
      <p>
        Вы можете в любой момент изменить свой выбор — обе опции («Принять все» и «Только
        необходимые») равнозначны и не ограничивают доступ к информации на сайте.
      </p>
      <p>
        <button class="btn btn-primary" type="button" onclick="window.openCookieSettings && window.openCookieSettings()">
          Открыть настройки Cookie
        </button>
      </p>
      <p>
        Также вы можете полностью запретить или удалить файлы Cookie в настройках вашего
        браузера (Chrome, Яндекс Браузер, Safari, Firefox, Edge).
      </p>
    """
    return _render_legal_page(
        cfg=cfg,
        title="Политика использования файлов Cookie",
        eyebrow="П. 4–5 · ПРОЗРАЧНЫЕ COOKIE",
        subtitle="Полный перечень файлов Cookie без скрытых рекламных и зарубежных трекеров.",
        active_key="cookies",
        body_html=body,
    )


@router.get("/legal/requisites", response_class=HTMLResponse, include_in_schema=False)
async def legal_requisites(container: AppContainer = Depends(get_container)) -> HTMLResponse:
    cfg = container.settings
    inn = _display_or_pending(cfg.legal_inn, "указывается в .env (LEGAL_INN)")
    ogrnip = _display_or_pending(cfg.legal_ogrnip, "указывается в .env (LEGAL_OGRNIP)")
    phone_digits = "".join(ch for ch in cfg.legal_phone if ch.isdigit())
    phone_html = (
        f'<a href="tel:+{phone_digits}">{_e(cfg.legal_phone)}</a>'
        if cfg.legal_phone.strip()
        else _display_or_pending("", "указывается в .env (LEGAL_PHONE)")
    )
    rkn = _display_or_pending(
        cfg.legal_rkn_reg_no,
        "уведомление подаётся на pd.rkn.gov.ru до старта сбора заявок (LEGAL_RKN_REG_NO)",
    )
    body = f"""
      <h2>1. Реквизиты Оператора и владельца сервиса</h2>
      <div class="legal-table-wrap">
        <table class="legal-table">
          <tbody>
            <tr><th>Коммерческое наименование</th><td><strong>{_e(cfg.brand_name)}</strong></td></tr>
            <tr><th>Оператор / Исполнитель</th><td><strong>{_e(cfg.legal_operator_name)}</strong> (Индивидуальный предприниматель)</td></tr>
            <tr><th>ИНН</th><td class="mono">{inn}</td></tr>
            <tr><th>ОГРНИП</th><td class="mono">{ogrnip}</td></tr>
            <tr><th>Дата государственной регистрации</th><td class="mono">{_e(cfg.legal_registration_date)}</td></tr>
            <tr><th>ОКПО / ОКТМО</th><td class="mono">{_e(cfg.legal_okpo)} / {_e(cfg.legal_oktmo)}</td></tr>
            <tr><th>Специальный налоговый режим</th><td><strong>{_e(cfg.legal_tax_regime)}</strong></td></tr>
            <tr><th>Регион регистрации и оказания услуг</th><td>{_e(cfg.legal_address)}</td></tr>
            <tr><th>Контактный телефон</th><td class="mono">{phone_html}</td></tr>
            <tr><th>E-mail по вопросам ПДн, претензий и возвратов</th><td class="mono"><a href="mailto:{_e(cfg.legal_email)}">{_e(cfg.legal_email)}</a></td></tr>
          </tbody>
        </table>
      </div>

      <h2>2. Инфраструктура в РФ, РКН и российская авторизация</h2>
      <div class="legal-table-wrap">
        <table class="legal-table">
          <tbody>
            <tr>
              <th>Размещение первичных БД и файлов (ч. 5 ст. 18 152-ФЗ)</th>
              <td><strong>{_e(cfg.legal_datacenter_info)}</strong></td>
            </tr>
            <tr>
              <th>Реестр операторов ПДн Роскомнадзора (ст. 22 152-ФЗ)</th>
              <td>{rkn}</td>
            </tr>
            <tr>
              <th>Авторизация пользователей (406-ФЗ / 149-ФЗ)</th>
              <td>Исключительно через российский мессенджер <strong>MAX</strong> и локальную учётную запись (иностранные OAuth-сервисы Google / Apple / Meta не используются)</td>
            </tr>
            <tr>
              <th>Платёжный провайдер и касса (54-ФЗ)</th>
              <td>Российский платёжный шлюз <strong>ЮKassa</strong> (СБП + онлайн-чеки 54-ФЗ)</td>
            </tr>
          </tbody>
        </table>
      </div>
    """
    return _render_legal_page(
        cfg=cfg,
        title="Реквизиты оператора, хостинг в РФ и РКН",
        eyebrow="П. 13–16 · ЗОЗПП · 152-ФЗ · 406-ФЗ",
        subtitle="Сведения об операторе сервиса, размещении серверов на территории РФ и статусе РКН.",
        active_key="requisites",
        body_html=body,
    )


@router.get("/legal/licenses", response_class=HTMLResponse, include_in_schema=False)
async def legal_licenses(container: AppContainer = Depends(get_container)) -> HTMLResponse:
    cfg = container.settings
    body = """
      <h2>1. Лицензии шрифтов (Self-Hosted в РФ, SIL OFL 1.1)</h2>
      <p>
        Все шрифты, используемые на сайте, размещены <strong>локально на нашем российском сервере</strong>
        (в каталоге <code>/static/fonts/</code>) без обращений к внешним серверам Google Fonts и
        распространяются на условиях свободной лицензии <strong>SIL Open Font License 1.1</strong>,
        разрешающей коммерческое использование и веб-размещение.
      </p>
      <div class="legal-table-wrap">
        <table class="legal-table">
          <thead>
            <tr>
              <th>Гарнитура</th>
              <th>Правообладатель / Авторы</th>
              <th>Лицензия</th>
              <th>Полный текст</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>Unbounded</strong></td>
              <td>The Unbounded Project Authors (2022)</td>
              <td>SIL Open Font License 1.1</td>
              <td><a href="/static/fonts/licenses/OFL-Unbounded.txt" target="_blank" rel="noopener">OFL-Unbounded.txt</a></td>
            </tr>
            <tr>
              <td><strong>Golos Text</strong></td>
              <td>The Golos Text Project Authors / Paratype (2019)</td>
              <td>SIL Open Font License 1.1</td>
              <td><a href="/static/fonts/licenses/OFL-Golos-Text.txt" target="_blank" rel="noopener">OFL-Golos-Text.txt</a></td>
            </tr>
            <tr>
              <td><strong>JetBrains Mono</strong></td>
              <td>The JetBrains Mono Project Authors (2020)</td>
              <td>SIL Open Font License 1.1</td>
              <td><a href="/static/fonts/licenses/OFL-JetBrains-Mono.txt" target="_blank" rel="noopener">OFL-JetBrains-Mono.txt</a></td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>2. Изображения, иконки и графика</h2>
      <ul>
        <li>Все иконки, паттерны и визуальные элементы интерфейса являются <strong>авторской векторной графикой (inline SVG)</strong> и процедурной анимацией HTML5 Canvas, созданной специально для сервиса.</li>
        <li>Стоковые фотографии сторонних авторов и изображения, защищённые чужими исключительными правами, на сайте <strong>не используются</strong>.</li>
        <li>Фотоотчёты «ДО/ПОСЛЕ» загружаются исполнителями в рамках конкретного заказа и хранятся в закрытом хранилище в РФ без публичной индексации.</li>
      </ul>

      <h2>3. Отсутствие сторонних зарубежных библиотек и трекеров</h2>
      <p>
        Фронтенд веб-портала и админ-панели написан на чистом HTML5 / CSS3 / JavaScript без
        подключения зарубежных CDN-серверов шрифтов и скриптов, зарубежных систем веб-аналитики
        и рекламных пикселей. При открытии страниц браузер посетителя обращается
        <strong>исключительно к домену данного сайта в РФ</strong>.
      </p>
    """
    return _render_legal_page(
        cfg=cfg,
        title="Лицензии шрифтов, графики и отсутствие внешних CDN",
        eyebrow="П. 8 И П. 19 · ГК РФ Ч. IV · SIL OFL 1.1",
        subtitle="Подтверждение легальности всех шрифтов и графических материалов на сайте.",
        active_key="licenses",
        body_html=body,
    )


@router.get("/legal/my-data", response_class=HTMLResponse, include_in_schema=False)
async def legal_my_data(container: AppContainer = Depends(get_container)) -> HTMLResponse:
    cfg = container.settings
    email = _e(cfg.legal_email)
    body = f"""
      <h2>Управление вашими персональными данными (ст. 14, 21 152-ФЗ)</h2>
      <p>
        Здесь вы можете в один шаг подать официальное требование на <strong>удаление своих
        персональных данных</strong>, <strong>отписку от рассылок</strong>, <strong>отзыв согласия</strong>
        или <strong>получение копии обрабатываемых данных</strong>. Форма запрашивает только
        минимальный контакт для поиска вашей записи и ответа — без лишних полей.
      </p>

      <div class="legal-callout legal-callout-warn">
        Если вы пользуетесь чат-ботом в <strong>MAX</strong>, вы также можете отправить напрямую
        команду <code>/unsubscribe</code> (мгновенная отписка от рассылок) или
        <code>/delete_my_data</code> (регистрация запроса на удаление данных).
      </div>

      <form id="dsrForm" class="dsr-form" novalidate>
        <div>
          <label style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:8px;text-transform:uppercase;letter-spacing:0.06em;font-family:var(--font-mono)">
            1. Выберите действие
          </label>
          <div class="dsr-types">
            <label class="dsr-type-card">
              <input type="radio" name="request_type" value="delete_data" checked />
              <span>
                <strong>Удалить мои данные</strong><br />
                <small style="color:var(--text-muted)">Полное удаление / обезличивание по ст. 21 152-ФЗ (до 10 раб. дней)</small>
              </span>
            </label>
            <label class="dsr-type-card">
              <input type="radio" name="request_type" value="unsubscribe_marketing" />
              <span>
                <strong>Отписаться от рассылок</strong><br />
                <small style="color:var(--text-muted)">Немедленный отзыв согласия на рекламные и сезонные сообщения</small>
              </span>
            </label>
            <label class="dsr-type-card">
              <input type="radio" name="request_type" value="export_data" />
              <span>
                <strong>Получить копию данных</strong><br />
                <small style="color:var(--text-muted)">Выгрузка обрабатываемых ПДн по ст. 14 152-ФЗ</small>
              </span>
            </label>
            <label class="dsr-type-card">
              <input type="radio" name="request_type" value="revoke_consent" />
              <span>
                <strong>Отозвать согласие на ПДн</strong><br />
                <small style="color:var(--text-muted)">Прекращение дальнейшей обработки данных</small>
              </span>
            </label>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px">
          <div>
            <label for="dsrContact" style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:6px">
              2. Контакт для поиска записи и ответа (e-mail, телефон или MAX ID) *
            </label>
            <input id="dsrContact" name="contact_value" class="input" required maxlength="200"
              placeholder="Например: user@mail.ru или +7 999 000-00-00" />
          </div>
          <div>
            <label for="dsrMaxId" style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:6px">
              MAX User ID (необязательно, только если знаете)
            </label>
            <input id="dsrMaxId" name="max_user_id" class="input" inputmode="numeric"
              placeholder="Например: 10001" />
          </div>
        </div>

        <div>
          <label for="dsrDetails" style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:6px">
            Комментарий или номер заказа (необязательно)
          </label>
          <textarea id="dsrDetails" name="details" class="input" rows="2" maxlength="1000"
            placeholder="Уточните детали обращения, если необходимо"></textarea>
        </div>

        <!-- Строго НЕпредзаполненные раздельные чекбоксы (п. 6, 9, 17) -->
        <label class="dsr-check">
          <input type="checkbox" id="dsrConsent" name="consent_accepted" />
          <span>
            Я даю отдельное <a href="/legal/consent" target="_blank">Согласие на обработку персональных данных</a>
            исключительно в целях рассмотрения и исполнения настоящего обращения, и ознакомлен(а) с
            <a href="/legal/privacy" target="_blank">Политикой обработки ПДн</a>. *
          </span>
        </label>

        <label class="dsr-check">
          <input type="checkbox" id="dsrAge" name="age_confirmed" />
          <span>
            Я подтверждаю, что мне исполнилось 18 лет (либо я являюсь родителем / законным
            представителем несовершеннолетнего субъекта ПДн). *
          </span>
        </label>

        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <button type="submit" id="dsrSubmit" class="btn btn-danger">
            Отправить запрос
          </button>
          <span style="font-size:12px;color:var(--text-muted)">
            Также можно написать напрямую на <a href="mailto:{email}">{email}</a>
          </span>
        </div>

        <div id="dsrResult" class="dsr-result" role="status" aria-live="polite"></div>
      </form>
    """
    extra_script = """
    <script>
      (function () {
        const form = document.getElementById('dsrForm');
        const resultEl = document.getElementById('dsrResult');
        const submitBtn = document.getElementById('dsrSubmit');
        if (!form) return;

        form.addEventListener('submit', async (ev) => {
          ev.preventDefault();
          resultEl.style.display = 'none';
          resultEl.classList.remove('error');

          const contact = document.getElementById('dsrContact').value.trim();
          const maxIdRaw = document.getElementById('dsrMaxId').value.trim();
          const details = document.getElementById('dsrDetails').value.trim();
          const consentAccepted = document.getElementById('dsrConsent').checked;
          const ageConfirmed = document.getElementById('dsrAge').checked;
          const reqTypeEl = form.querySelector('input[name="request_type"]:checked');
          const requestType = reqTypeEl ? reqTypeEl.value : 'delete_data';

          if (!contact) {
            resultEl.textContent = 'Пожалуйста, укажите контакт (e-mail, телефон или MAX ID) для идентификации и ответа.';
            resultEl.classList.add('error');
            resultEl.style.display = 'block';
            return;
          }
          if (!consentAccepted || !ageConfirmed) {
            resultEl.textContent = 'Для отправки запроса необходимо вручную отметить оба обязательных согласия (ПДн и подтверждение 18+).';
            resultEl.classList.add('error');
            resultEl.style.display = 'block';
            return;
          }

          let maxUserId = null;
          if (maxIdRaw) {
            if (!/^\\d+$/.test(maxIdRaw)) {
              resultEl.textContent = 'MAX User ID должен содержать только цифры.';
              resultEl.classList.add('error');
              resultEl.style.display = 'block';
              return;
            }
            maxUserId = Number(maxIdRaw);
          }

          submitBtn.disabled = true;
          try {
            const resp = await fetch('/api/v1/legal/data-requests', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                request_type: requestType,
                contact_value: contact,
                max_user_id: maxUserId,
                details: details || null,
                consent_accepted: true,
                age_confirmed: true
              })
            });
            const data = await resp.json();
            if (!resp.ok) {
              throw new Error(data.detail || (data.error && data.error.message) || 'Ошибка регистрации запроса');
            }
            resultEl.innerHTML =
              '<strong>Запрос зарегистрирован!</strong><br>' +
              'Номер обращения: <code>' + data.ticket_code + '</code> · Статус: <strong>' + data.status_label + '</strong><br>' +
              data.message;
            resultEl.style.display = 'block';
            form.reset();
          } catch (err) {
            resultEl.textContent = err.message || 'Не удалось отправить запрос';
            resultEl.classList.add('error');
            resultEl.style.display = 'block';
          } finally {
            submitBtn.disabled = false;
          }
        });
      })();
    </script>
    """
    return _render_legal_page(
        cfg=cfg,
        title="Мои данные: удаление, выгрузка и отписка",
        eyebrow="П. 6, 7, 17, 18, 20 · 152-ФЗ СТ. 14, 21",
        subtitle="Официальная форма удаления персональных данных, отзыва согласия и отписки от рассылок.",
        active_key="my-data",
        body_html=body,
        extra_script=extra_script,
    )


class PublicLegalMetaResponse(BaseModel):
    brand_name: str
    operator_name: str
    inn: str
    ogrnip: str
    address: str
    email: str
    phone: str
    rkn_reg_no: str
    datacenter_info: str
    doc_version: str
    doc_updated_date: str


@router.get("/api/v1/legal/public-meta", response_model=PublicLegalMetaResponse)
async def public_legal_meta(
    container: AppContainer = Depends(get_container),
) -> PublicLegalMetaResponse:
    cfg = container.settings
    return PublicLegalMetaResponse(
        brand_name=cfg.brand_name,
        operator_name=cfg.legal_operator_name,
        inn=cfg.legal_inn,
        ogrnip=cfg.legal_ogrnip,
        address=cfg.legal_address,
        email=cfg.legal_email,
        phone=cfg.legal_phone,
        rkn_reg_no=cfg.legal_rkn_reg_no,
        datacenter_info=cfg.legal_datacenter_info,
        doc_version=cfg.legal_doc_version,
        doc_updated_date=cfg.legal_doc_updated_date,
    )


class DataSubjectRequestCreate(BaseModel):
    request_type: Literal[
        "delete_data",
        "unsubscribe_marketing",
        "export_data",
        "revoke_consent",
    ]
    contact_value: str = Field(..., min_length=3, max_length=255)
    max_user_id: int | None = None
    details: str | None = Field(default=None, max_length=1000)
    consent_accepted: bool
    age_confirmed: bool


class DataSubjectRequestCreatedResponse(BaseModel):
    ticket_code: str
    request_type: str
    status: str
    status_label: str
    message: str


@router.post(
    "/api/v1/legal/data-requests",
    response_model=DataSubjectRequestCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_data_subject_request(
    body: DataSubjectRequestCreate,
    container: AppContainer = Depends(get_container),
) -> DataSubjectRequestCreatedResponse:
    if not body.consent_accepted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Необходимо подтвердить согласие на обработку ПДн для рассмотрения запроса.",
        )
    if not body.age_confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Необходимо подтвердить возраст 18+ или статус законного представителя.",
        )

    contact = body.contact_value.strip()
    now = datetime.now(UTC)
    ticket_code = f"DSR-{now.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"

    async with container.session_factory() as session:
        user: User | None = None
        filters = [
            User.email == contact,
            User.bot_phone == contact,
            User.shared_contact_phone == contact,
        ]
        if body.max_user_id is not None:
            filters.append(User.max_user_id == body.max_user_id)
        elif contact.isdigit():
            filters.append(User.max_user_id == int(contact))

        user = (await session.execute(select(User).where(or_(*filters)).limit(1))).scalar_one_or_none()

        initial_status = "pending"
        resolved_at: datetime | None = None
        resolution_note: str | None = None

        # Отписка от рассылок выполняется немедленно и фиксируется в user_consents
        if body.request_type == "unsubscribe_marketing":
            initial_status = "completed"
            resolved_at = now
            resolution_note = "Автоматически зафиксирован отзыв маркетинговых рассылок"
            if user is not None:
                session.add(
                    UserConsent(
                        user_id=user.id,
                        consent_type="marketing_revoked",
                        document_version=container.settings.legal_doc_version,
                        source="web_dsr",
                    )
                )

        dsr = DataSubjectRequest(
            ticket_code=ticket_code,
            request_type=body.request_type,
            status=initial_status,
            contact_value=contact,
            max_user_id=body.max_user_id or (user.max_user_id if user else None),
            user_id=user.id if user else None,
            details=body.details.strip() if body.details else None,
            consent_accepted_at=now,
            age_confirmed=True,
            source="web",
            resolution_note=resolution_note,
            resolved_at=resolved_at,
        )
        session.add(dsr)
        await session.commit()

    if body.request_type == "unsubscribe_marketing":
        return DataSubjectRequestCreatedResponse(
            ticket_code=ticket_code,
            request_type=body.request_type,
            status="completed",
            status_label="Выполнено немедленно",
            message=(
                "Вы отписаны от информационных и рекламных рассылок. "
                "Запись об отказе зафиксирована в системе."
            ),
        )

    return DataSubjectRequestCreatedResponse(
        ticket_code=ticket_code,
        request_type=body.request_type,
        status="pending",
        status_label="Принято в работу",
        message=(
            "Ваше обращение зарегистрировано и будет исполнено в срок до 10 рабочих дней "
            "в соответствии со ст. 14 и ст. 21 152-ФЗ. Сохраните номер обращения."
        ),
    )
