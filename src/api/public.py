"""Публичный API витрины «ЧистоДвор»: каталог услуг, живой калькулятор и онлайн-заказ."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.api.deps import get_container
from src.core.config import Settings
from src.core.container import AppContainer
from src.core.enums import (
    METHOD_LABELS,
    SERVICE_LABELS,
    STATUS_LABELS,
    AccessConstraint,
    CleaningMethod,
    ConsentType,
    ContactMode,
    DepthBucket,
    OrderStatus,
    ServiceType,
    UrgencyLevel,
    UserRole,
)
from src.core.exceptions import NotFoundError, ValidationAppError
from src.domain.pricing.calculator import (
    PriceCalculationInput,
    calculate_price,
    to_decimal,
)
from src.infra.db.models import GeoZone, Order, Payment, User, UserConsent

router = APIRouter(prefix="/api/v1/public", tags=["public"])

DEPTH_LABELS: dict[DepthBucket, str] = {
    DepthBucket.UP_TO_10: "до 10 см",
    DepthBucket.FROM_10_TO_30: "10–30 см",
    DepthBucket.OVER_30: "более 30 см",
}

URGENCY_LABELS: dict[UrgencyLevel, str] = {
    UrgencyLevel.TODAY: "Сегодня",
    UrgencyLevel.WITHIN_3H: "В течение 3 часов",
    UrgencyLevel.NOW_OR_NIGHT: "Срочно / ночь",
}

VEHICLE_LABELS: dict[str, str] = {
    "sedan": "Легковой / седан",
    "suv": "Внедорожник / кроссовер",
    "commercial": "Коммерческий / фургон",
}

WEB_USER_ID_OFFSET = 100_000_000_000


def normalize_ru_phone(raw: str) -> tuple[str, str]:
    """Нормализует российский номер к (+7XXXXXXXXXX, 7XXXXXXXXXX)."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 10 and digits.startswith("9"):
        digits = "7" + digits
    elif len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) != 11 or not digits.startswith("7"):
        raise ValidationAppError(
            "Укажите корректный российский номер телефона (например, +7 977 924-02-93)"
        )
    return f"+{digits}", digits


def web_client_synthetic_max_id(phone_digits_11: str) -> int:
    """Детерминированный отрицательный ID для веб-клиентов (не пересекается с MAX ID)."""
    return -(WEB_USER_ID_OFFSET + int(phone_digits_11))


def _resolve_payment_mode(cfg: Settings) -> str:
    if cfg.payment_provider == "yookassa":
        return "yookassa"
    if cfg.app_env == "development":
        return "dev_mock"
    return "dispatcher_confirm"


def _build_calc_input_and_params(
    req: PublicQuoteRequest,
    *,
    dispatch_fee: Decimal,
) -> tuple[PriceCalculationInput, dict[str, Any], int]:
    svc = req.service_type
    params: dict[str, Any] = {"source": "web_site"}
    required_executors = 1

    if svc == ServiceType.SNOW_YARD:
        area = Decimal(str(req.area_m2 or 80))
        params.update(
            {
                "area_m2": float(area),
                "depth_bucket": req.depth_bucket.value,
                "depth_label": DEPTH_LABELS[req.depth_bucket],
                "access_constraint": req.access_constraint.value,
                "snow_removal": req.snow_removal,
            }
        )
        calc_in = PriceCalculationInput(
            service_type=svc,
            dispatch_fee=dispatch_fee,
            area_m2=area,
            depth_bucket=req.depth_bucket,
            access_constraint=req.access_constraint,
            cleaning_method=req.cleaning_method,
            urgency=req.urgency,
            snow_removal=req.snow_removal,
        )
    elif svc == ServiceType.SNOW_ROAD:
        length = Decimal(str(req.length_m or 60))
        width = Decimal(str(req.width_m or 4))
        params.update(
            {
                "length_m": float(length),
                "width_m": float(width),
                "area_m2": float(length * width),
                "depth_bucket": req.depth_bucket.value,
                "depth_label": DEPTH_LABELS[req.depth_bucket],
                "access_constraint": req.access_constraint.value,
                "snow_removal": req.snow_removal,
            }
        )
        calc_in = PriceCalculationInput(
            service_type=svc,
            dispatch_fee=dispatch_fee,
            length_m=length,
            width_m=width,
            depth_bucket=req.depth_bucket,
            access_constraint=req.access_constraint,
            cleaning_method=req.cleaning_method,
            urgency=req.urgency,
            snow_removal=req.snow_removal,
        )
    elif svc == ServiceType.CAR_RESCUE:
        vtype = req.vehicle_type if req.vehicle_type in VEHICLE_LABELS else "sedan"
        dist = Decimal(str(req.distance_km if req.distance_km is not None else 3))
        params.update(
            {
                "vehicle_type": VEHICLE_LABELS[vtype],
                "vehicle_code": vtype,
                "distance_km": float(dist),
                "is_urgent_rescue": req.is_urgent_rescue,
            }
        )
        calc_in = PriceCalculationInput(
            service_type=svc,
            dispatch_fee=dispatch_fee,
            vehicle_type=vtype,
            distance_km=dist,
            is_urgent_rescue=req.is_urgent_rescue,
            urgency=UrgencyLevel.NOW_OR_NIGHT if req.is_urgent_rescue else UrgencyLevel.WITHIN_3H,
        )
    elif svc in (ServiceType.YARD_HELP, ServiceType.MOVERS):
        hours = req.hours or 2
        workers = req.workers_count or 1
        required_executors = workers
        params.update(
            {
                "hours": hours,
                "workers_count": workers,
                "floors_without_lift": req.floors_without_lift,
                "needs_equipment": req.needs_equipment,
            }
        )
        calc_in = PriceCalculationInput(
            service_type=svc,
            dispatch_fee=dispatch_fee,
            hours=hours,
            workers_count=workers,
            floors_without_lift=req.floors_without_lift,
            needs_equipment=req.needs_equipment,
            urgency=req.urgency,
        )
    elif svc == ServiceType.ROOF_CLEANING:
        area = Decimal(str(req.area_m2 or 80))
        params.update({"area_m2": float(area)})
        calc_in = PriceCalculationInput(
            service_type=svc,
            dispatch_fee=dispatch_fee,
            area_m2=area,
            urgency=req.urgency,
        )
    elif svc == ServiceType.REAGENTS:
        area = Decimal(str(req.area_m2 or 100))
        params.update({"area_m2": float(area), "as_addon": req.as_addon})
        calc_in = PriceCalculationInput(
            service_type=svc,
            dispatch_fee=dispatch_fee,
            area_m2=area,
            as_addon=req.as_addon,
            urgency=req.urgency,
        )
    else:
        raise ValidationAppError(f"Неизвестный тип услуги: {svc}")

    return calc_in, params, required_executors


class PublicQuoteRequest(BaseModel):
    service_type: ServiceType
    zone_code: str = Field(default="malakhovka", min_length=2, max_length=64)
    area_m2: float | None = Field(default=None, ge=10, le=10000)
    length_m: float | None = Field(default=None, ge=5, le=2000)
    width_m: float | None = Field(default=None, ge=2, le=15)
    depth_bucket: DepthBucket = DepthBucket.FROM_10_TO_30
    access_constraint: AccessConstraint = AccessConstraint.STANDARD
    cleaning_method: CleaningMethod | None = None
    urgency: UrgencyLevel = UrgencyLevel.TODAY
    snow_removal: bool = False
    vehicle_type: str = Field(default="sedan")
    distance_km: float | None = Field(default=3.0, ge=0, le=100)
    is_urgent_rescue: bool = True
    hours: int = Field(default=2, ge=1, le=24)
    workers_count: int = Field(default=1, ge=1, le=10)
    floors_without_lift: int = Field(default=0, ge=0, le=30)
    needs_equipment: bool = False
    as_addon: bool = False


class PublicCreateOrderRequest(PublicQuoteRequest):
    address_text: str = Field(..., min_length=5, max_length=400)
    client_name: str = Field(default="", max_length=120)
    client_phone: str = Field(..., min_length=10, max_length=32)
    client_email: str | None = Field(default=None, max_length=255)
    client_comment: str | None = Field(default=None, max_length=1000)
    consent_pdn: bool = False
    consent_offer: bool = False


@router.get("/catalog")
async def get_public_catalog(
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    cfg = container.settings
    async with container.session_factory() as session:
        zones = list(
            (await session.execute(select(GeoZone).order_by(GeoZone.dispatch_fee.asc(), GeoZone.name.asc())))
            .scalars()
            .all()
        )
        tariff = await container.orchestrator.get_active_tariff(session)
        tcfg = tariff.config_json

    snow_methods = tcfg.get("snow", {}).get("methods", {})
    car_cfg = tcfg.get("car_rescue", {})
    hourly_cfg = tcfg.get("hourly", {})
    roof_cfg = tcfg.get("roof_and_reagents", {})
    adv_pcts = tcfg.get("advance_pct_by_service", {})
    min_order = int(to_decimal(tcfg.get("min_order_total_rub", 2000)))

    services = [
        {
            "code": ServiceType.SNOW_YARD.value,
            "title": SERVICE_LABELS[ServiceType.SNOW_YARD],
            "short_title": "Снег во дворе",
            "category": "Зима · участки",
            "price_from_label": f"от {snow_methods.get('manual', {}).get('rate_per_m2', 40)} ₽/м²",
            "min_price_rub": int(
                max(min_order, int(snow_methods.get("manual", {}).get("min_work_price", 2500)))
            ),
            "advance_pct": int(adv_pcts.get("snow_yard", 30)),
            "summary": "Дорожки, парковка, отмостка и площадка перед домом. Вручную или техникой, по желанию с вывозом снега.",
        },
        {
            "code": ServiceType.SNOW_ROAD.value,
            "title": SERVICE_LABELS[ServiceType.SNOW_ROAD],
            "short_title": "Дорога и проезд",
            "category": "Зима · СНТ и проезды",
            "price_from_label": f"от {snow_methods.get('tractor', {}).get('rate_per_m2', 20)} ₽/м²",
            "min_price_rub": int(
                snow_methods.get("snowblower_or_miniloader", {}).get("min_work_price", 3000)
            ),
            "advance_pct": int(adv_pcts.get("snow_road", 30)),
            "summary": "Подъезд к дому, улица в СНТ, заезд в ворота. Автоподбор мини-погрузчика или трактора под ширину проезда.",
        },
        {
            "code": ServiceType.CAR_RESCUE.value,
            "title": SERVICE_LABELS[ServiceType.CAR_RESCUE],
            "short_title": "Застряла машина",
            "category": "Срочный выезд",
            "price_from_label": f"от {int(car_cfg.get('base_price', 2500))} ₽",
            "min_price_rub": int(car_cfg.get("base_price", 2500)),
            "advance_pct": int(adv_pcts.get("car_rescue", 50)),
            "summary": "Откапываем и вызволяем авто из сугроба или колеи. Радиус 5 км включён в базовую цену.",
        },
        {
            "code": ServiceType.ROOF_CLEANING.value,
            "title": SERVICE_LABELS[ServiceType.ROOF_CLEANING],
            "short_title": "Расчистка крыши",
            "category": "Высотные работы",
            "price_from_label": f"от {int(roof_cfg.get('roof_rate_per_m2', 90))} ₽/м²",
            "min_price_rub": int(roof_cfg.get("roof_min_price", 6000)),
            "advance_pct": int(adv_pcts.get("roof_cleaning", 40)),
            "summary": "Сброс снега, наледи и сосулек. Выезжают только допущенные исполнители со страховкой.",
        },
        {
            "code": ServiceType.REAGENTS.value,
            "title": SERVICE_LABELS[ServiceType.REAGENTS],
            "short_title": "Посыпка реагентами",
            "category": "Антигололёд",
            "price_from_label": f"от {int(roof_cfg.get('reagents_rate_per_m2', 20))} ₽/м²",
            "min_price_rub": int(roof_cfg.get("reagents_min_price", 1000)),
            "advance_pct": int(adv_pcts.get("reagents", 50)),
            "summary": "Противогололёдная обработка ступеней, дорожек и въезда. В паре с уборкой — без доплаты за выезд.",
        },
        {
            "code": ServiceType.YARD_HELP.value,
            "title": SERVICE_LABELS[ServiceType.YARD_HELP],
            "short_title": "Помощь по двору",
            "category": "Всесезонно",
            "price_from_label": f"от {int(hourly_cfg.get('yard_help_rate_per_hour', 700))} ₽/ч",
            "min_price_rub": int(
                max(
                    min_order,
                    int(hourly_cfg.get("yard_help_rate_per_hour", 700))
                    * int(hourly_cfg.get("min_hours", 2)),
                )
            ),
            "advance_pct": int(adv_pcts.get("yard_help", 30)),
            "summary": "Уборка территории, перенос дров, наведение порядка на участке в любое время года. От 2 часов.",
        },
        {
            "code": ServiceType.MOVERS.value,
            "title": SERVICE_LABELS[ServiceType.MOVERS],
            "short_title": "Грузчики",
            "category": "Всесезонно",
            "price_from_label": f"от {int(hourly_cfg.get('movers_rate_per_hour', 750))} ₽/ч",
            "min_price_rub": int(
                max(
                    min_order,
                    int(hourly_cfg.get("movers_rate_per_hour", 750))
                    * int(hourly_cfg.get("min_hours", 2)),
                )
            ),
            "advance_pct": int(adv_pcts.get("movers", 30)),
            "summary": "Погрузка, разгрузка, переезд дома или дачи. От 1 до 10 исполнителей, минимум 2 часа.",
        },
    ]

    active_zones = [
        {
            "code": z.code,
            "name": z.name,
            "tier": z.tier,
            "dispatch_fee": int(z.dispatch_fee),
            "free_radius_km": float(z.free_radius_km),
            "per_km_rate": int(z.per_km_rate),
        }
        for z in zones
        if z.is_active
    ]
    planned_zones = [
        {"code": z.code, "name": z.name, "tier": z.tier}
        for z in zones
        if not z.is_active
    ]

    phone_digits = "".join(ch for ch in cfg.legal_phone if ch.isdigit())
    return {
        "brand_name": cfg.brand_name,
        "phone": cfg.legal_phone,
        "phone_href": f"+{phone_digits}" if phone_digits else "",
        "email": cfg.legal_email,
        "app_env": cfg.app_env,
        "payment_mode": _resolve_payment_mode(cfg),
        "simulator_enabled": cfg.enable_simulator,
        "max_bot_url": cfg.max_bot_url.strip(),
        "rounding_step_rub": int(tcfg.get("rounding_step_rub", 50)),
        "min_order_total_rub": min_order,
        "group_discount_tiers": tcfg.get("group_discount_tiers", []),
        "zones": active_zones,
        "planned_zones": planned_zones,
        "services": services,
    }


@router.post("/orders/quote")
async def quote_public_order(
    body: PublicQuoteRequest,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        zone = (
            await session.execute(
                select(GeoZone).where(
                    GeoZone.code == body.zone_code, GeoZone.is_active.is_(True)
                )
            )
        ).scalar_one_or_none()
        if zone is None:
            raise ValidationAppError("Выбранный район пока не обслуживается")
        tariff = await container.orchestrator.get_active_tariff(session)

    calc_in, _params, required_executors = _build_calc_input_and_params(
        body, dispatch_fee=zone.dispatch_fee
    )
    breakdown = calculate_price(calc_in, tariff.config_json)
    adv_pct = int(
        to_decimal(
            tariff.config_json.get("advance_pct_by_service", {}).get(body.service_type.value, 30)
        )
    )

    return {
        "service_type": body.service_type.value,
        "service_label": SERVICE_LABELS[body.service_type],
        "zone_code": zone.code,
        "zone_name": zone.name,
        "cleaning_method": breakdown.cleaning_method.value if breakdown.cleaning_method else None,
        "cleaning_method_label": (
            METHOD_LABELS[breakdown.cleaning_method] if breakdown.cleaning_method else None
        ),
        "area_m2": float(breakdown.area_m2) if breakdown.area_m2 is not None else None,
        "required_executors": required_executors,
        "is_night": breakdown.is_night,
        "work_cost": str(breakdown.work_cost),
        "dispatch_fee": str(breakdown.dispatch_fee),
        "extras_cost": str(breakdown.extras_cost),
        "estimated_total": str(breakdown.estimated_total),
        "advance_amount": str(breakdown.advance_amount),
        "remainder_amount": str(breakdown.remainder_amount),
        "advance_pct": adv_pct,
    }


@router.post("/orders", status_code=status.HTTP_201_CREATED)
async def create_public_order(
    body: PublicCreateOrderRequest,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    if not body.consent_pdn:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Необходимо отметить согласие на обработку персональных данных (152-ФЗ).",
        )
    if not body.consent_offer:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Необходимо принять условия публичной оферты и правила возврата средств.",
        )

    formatted_phone, phone_digits = normalize_ru_phone(body.client_phone)
    clean_name = " ".join(body.client_name.strip().split()) or "Клиент"
    clean_address = " ".join(body.address_text.strip().split())
    clean_email = body.client_email.strip().lower() if body.client_email and body.client_email.strip() else None
    clean_comment = body.client_comment.strip() if body.client_comment and body.client_comment.strip() else None

    if len(clean_address) < 5:
        raise ValidationAppError("Укажите подробный адрес (улица, дом или СНТ)")
    if clean_email and ("@" not in clean_email or "." not in clean_email.split("@")[-1]):
        raise ValidationAppError("Проверьте корректность адреса электронной почты")

    synthetic_max_id = web_client_synthetic_max_id(phone_digits)
    name_parts = clean_name.split(" ", 1)
    first_name = name_parts[0][:120]
    last_name = name_parts[1][:120] if len(name_parts) > 1 else None

    async with container.session_factory() as session:
        zone = (
            await session.execute(
                select(GeoZone).where(
                    GeoZone.code == body.zone_code, GeoZone.is_active.is_(True)
                )
            )
        ).scalar_one_or_none()
        if zone is None:
            raise ValidationAppError("Выбранный район пока не обслуживается")

        user = (
            await session.execute(select(User).where(User.max_user_id == synthetic_max_id))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                max_user_id=synthetic_max_id,
                max_chat_id=None,
                first_name=first_name,
                last_name=last_name,
                role=UserRole.CLIENT.value,
                shared_contact_phone=formatted_phone,
                email=clean_email,
            )
            session.add(user)
            await session.flush()
        else:
            user.first_name = first_name
            if last_name is not None:
                user.last_name = last_name
            user.shared_contact_phone = formatted_phone
            if clean_email:
                user.email = clean_email

        doc_ver = container.settings.legal_doc_version
        for ctype in (ConsentType.PDN, ConsentType.CLIENT_OFFER, ConsentType.PHONE_TRANSFER):
            session.add(
                UserConsent(
                    user_id=user.id,
                    consent_type=ctype.value,
                    document_version=doc_ver,
                    source="web_form",
                )
            )
        await session.commit()
        client_id = user.id
        zone_id = zone.id
        zone_name = zone.name
        zone_dispatch_fee = zone.dispatch_fee

    calc_in, params, required_executors = _build_calc_input_and_params(
        body, dispatch_fee=zone_dispatch_fee
    )
    distance_km = (
        Decimal(str(body.distance_km))
        if body.service_type == ServiceType.CAR_RESCUE and body.distance_km is not None
        else None
    )

    order, payment = await container.orchestrator.create_order_with_advance(
        client_user_id=client_id,
        service_type=body.service_type,
        geo_zone_id=zone_id,
        address_text=clean_address,
        calc_input=calc_in,
        params_json=params,
        distance_km=distance_km,
        required_executors=required_executors,
        contact_mode=ContactMode.DIRECT_PHONE,
        client_comment=clean_comment,
    )

    pay_mode = _resolve_payment_mode(container.settings)
    payment_url = payment.confirmation_url if pay_mode in ("yookassa", "dev_mock") else None
    remainder = (order.final_total - order.advance_amount).quantize(Decimal("0.01"))

    return {
        "order_id": str(order.id),
        "public_number": order.public_number,
        "status": order.status,
        "status_label": STATUS_LABELS.get(OrderStatus(order.status), order.status),
        "payment_status": order.payment_status,
        "service_type": order.service_type,
        "service_label": SERVICE_LABELS.get(ServiceType(order.service_type), order.service_type),
        "zone_name": zone_name,
        "address_text": order.address_text,
        "estimated_total": str(order.estimated_total),
        "advance_amount": str(order.advance_amount),
        "remainder_amount": str(remainder),
        "payment_mode": pay_mode,
        "payment_url": payment_url,
        "phone_last4": phone_digits[-4:],
        "dispatcher_phone": container.settings.legal_phone,
        "created_at": order.created_at.isoformat(),
    }


@router.get("/orders/{public_number}/status")
async def get_public_order_status(
    public_number: int,
    phone_last4: str = Query(..., min_length=4, max_length=4),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    clean_last4 = "".join(ch for ch in phone_last4 if ch.isdigit())
    if len(clean_last4) != 4:
        raise ValidationAppError("Укажите последние 4 цифры номера телефона")

    async with container.session_factory() as session:
        order = (
            await session.execute(select(Order).where(Order.public_number == public_number))
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundError("Заказ с таким номером и телефоном не найден")
        client = await session.get(User, order.client_id)
        phone_raw = (client.shared_contact_phone or client.bot_phone or "") if client else ""
        phone_digits = "".join(ch for ch in phone_raw if ch.isdigit())
        if not phone_digits.endswith(clean_last4):
            raise NotFoundError("Заказ с таким номером и телефоном не найден")

        zone = await session.get(GeoZone, order.geo_zone_id) if order.geo_zone_id else None
        pending_pay = (
            await session.execute(
                select(Payment)
                .where(Payment.order_id == order.id, Payment.status == "pending")
                .order_by(Payment.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    pay_mode = _resolve_payment_mode(container.settings)
    payment_url = (
        pending_pay.confirmation_url
        if pending_pay is not None and pay_mode in ("yookassa", "dev_mock")
        else None
    )

    return {
        "public_number": order.public_number,
        "service_label": SERVICE_LABELS.get(ServiceType(order.service_type), order.service_type),
        "zone_name": zone.name if zone else "",
        "status": order.status,
        "status_label": STATUS_LABELS.get(OrderStatus(order.status), order.status),
        "payment_status": order.payment_status,
        "estimated_total": str(order.estimated_total),
        "final_total": str(order.final_total),
        "advance_amount": str(order.advance_amount),
        "paid_amount": str(order.paid_amount),
        "payment_mode": pay_mode,
        "payment_url": payment_url,
        "created_at": order.created_at.isoformat(),
    }
