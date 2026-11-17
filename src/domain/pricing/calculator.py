"""Детерминированный тарифный калькулятор (Decimal, округление до 50 ₽, ТЗ B7)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from src.core.enums import (
    AccessConstraint,
    CleaningMethod,
    DepthBucket,
    OrderStatus,
    ServiceType,
    UrgencyLevel,
)
from src.core.exceptions import ValidationAppError

MSK_TZ = ZoneInfo("Europe/Moscow")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100")


def to_decimal(val: Any) -> Decimal:
    return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_to_step(amount: Decimal, step: int = 50) -> Decimal:
    if step <= 0:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    step_dec = Decimal(step)
    units = (amount / step_dec).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (units * step_dec).quantize(Decimal("0.01"))


def is_night_msk(dt_utc: datetime | None, tariff_config: dict[str, Any]) -> bool:
    current = (dt_utc or datetime.now(UTC)).astimezone(MSK_TZ)
    window = tariff_config.get("night_window_msk", {"start_hour": 22, "end_hour": 7})
    start_h = int(window.get("start_hour", 22))
    end_h = int(window.get("end_hour", 7))
    hour = current.hour
    if start_h > end_h:
        return hour >= start_h or hour < end_h
    return start_h <= hour < end_h


def auto_select_cleaning_method(
    *,
    area_m2: Decimal,
    depth_bucket: DepthBucket,
    access_constraint: AccessConstraint,
    service_type: ServiceType,
    tariff_config: dict[str, Any],
    road_length_m: Decimal | None = None,
) -> CleaningMethod:
    thresholds = tariff_config["snow"]["auto_select_thresholds_m2"]
    manual_max = Decimal(str(thresholds.get("manual_max", 80)))
    small_max = Decimal(str(thresholds.get("small_tech_max", 400)))

    if access_constraint == AccessConstraint.NARROW_GATE:
        if area_m2 <= Decimal("150") and depth_bucket != DepthBucket.OVER_30:
            return CleaningMethod.MANUAL
        return CleaningMethod.SNOWBLOWER_OR_MINILOADER

    if (
        service_type == ServiceType.SNOW_ROAD
        and access_constraint == AccessConstraint.WIDE_ROAD
        and road_length_m is not None
        and road_length_m > Decimal("100")
        and depth_bucket == DepthBucket.OVER_30
    ):
        return CleaningMethod.TRACTOR

    if area_m2 <= manual_max and depth_bucket != DepthBucket.OVER_30:
        return CleaningMethod.MANUAL

    if area_m2 <= small_max or access_constraint != AccessConstraint.WIDE_ROAD:
        return CleaningMethod.SNOWBLOWER_OR_MINILOADER

    return CleaningMethod.TRACTOR


@dataclass(slots=True)
class PriceCalculationInput:
    service_type: ServiceType
    dispatch_fee: Decimal = ZERO
    # Снег / дорога
    area_m2: Decimal | None = None
    length_m: Decimal | None = None
    width_m: Decimal | None = None
    depth_bucket: DepthBucket = DepthBucket.UP_TO_10
    access_constraint: AccessConstraint = AccessConstraint.STANDARD
    cleaning_method: CleaningMethod | None = None
    urgency: UrgencyLevel = UrgencyLevel.TODAY
    snow_removal: bool = False
    # Машина в сугробе
    vehicle_type: str = "sedan"
    distance_km: Decimal = ZERO
    is_urgent_rescue: bool = True
    force_night: bool | None = None
    # Почасовые / грузчики
    hours: int = 2
    workers_count: int = 1
    floors_without_lift: int = 0
    needs_equipment: bool = False
    # Крыши / реагенты
    pitch_coeff: Decimal = Decimal("1.0")
    icing_coeff: Decimal = Decimal("1.0")
    as_addon: bool = False
    # Групповая скидка (Этап 3)
    group_discount_pct: Decimal = ZERO


@dataclass(slots=True)
class PriceBreakdown:
    service_type: ServiceType
    estimated_total: Decimal
    advance_amount: Decimal
    remainder_amount: Decimal
    work_cost: Decimal
    dispatch_fee: Decimal
    extras_cost: Decimal
    group_discount_amount: Decimal
    estimated_executor_payout: Decimal
    executor_share_pct: Decimal
    cleaning_method: CleaningMethod | None = None
    area_m2: Decimal | None = None
    is_night: bool = False
    pricing_snapshot: dict[str, Any] = field(default_factory=dict)


def resolve_group_discount_pct(orders_count: int, tariff_config: dict[str, Any]) -> Decimal:
    tiers = sorted(
        tariff_config.get("group_discount_tiers", []),
        key=lambda t: int(t["min_orders"]),
        reverse=True,
    )
    for tier in tiers:
        if orders_count >= int(tier["min_orders"]):
            return to_decimal(tier["discount_pct"])
    return ZERO


def calculate_price(
    inp: PriceCalculationInput,
    tariff_config: dict[str, Any],
    *,
    now_utc: datetime | None = None,
    executor_share_override: Decimal | None = None,
) -> PriceBreakdown:
    step = int(tariff_config.get("rounding_step_rub", 50))
    min_order_total = to_decimal(tariff_config.get("min_order_total_rub", 2000))
    min_advance = to_decimal(tariff_config.get("min_advance_rub", 700))
    night_active = (
        inp.force_night if inp.force_night is not None else is_night_msk(now_utc, tariff_config)
    )

    svc = inp.service_type
    work_cost = ZERO
    dispatch_fee = to_decimal(inp.dispatch_fee)
    extras_cost = ZERO
    discount_amount = ZERO
    selected_method: CleaningMethod | None = None
    effective_area: Decimal | None = None
    details: dict[str, Any] = {"service_type": svc.value, "is_night": night_active}

    if svc in (ServiceType.SNOW_YARD, ServiceType.SNOW_ROAD):
        if svc == ServiceType.SNOW_ROAD:
            if inp.length_m is None or inp.width_m is None:
                raise ValidationAppError("Для расчистки дороги требуются длина и ширина")
            if inp.length_m < Decimal("5") or inp.length_m > Decimal("2000"):
                raise ValidationAppError("Длина дороги должна быть от 5 до 2000 м")
            if inp.width_m < Decimal("2") or inp.width_m > Decimal("15"):
                raise ValidationAppError("Ширина дороги должна быть от 2 до 15 м")
            effective_area = to_decimal(inp.length_m * inp.width_m)
        else:
            if inp.area_m2 is None:
                raise ValidationAppError("Для уборки снега требуется площадь (м²)")
            if inp.area_m2 < Decimal("10") or inp.area_m2 > Decimal("10000"):
                raise ValidationAppError("Площадь должна быть от 10 до 10 000 м²")
            effective_area = to_decimal(inp.area_m2)

        selected_method = inp.cleaning_method or auto_select_cleaning_method(
            area_m2=effective_area,
            depth_bucket=inp.depth_bucket,
            access_constraint=inp.access_constraint,
            service_type=svc,
            tariff_config=tariff_config,
            road_length_m=inp.length_m,
        )

        snow_cfg = tariff_config["snow"]
        method_cfg = snow_cfg["methods"][selected_method.value]
        rate_per_m2 = to_decimal(method_cfg["rate_per_m2"])
        min_work_price = to_decimal(method_cfg["min_work_price"])
        depth_coeff = to_decimal(snow_cfg["depth_coeff"][inp.depth_bucket.value])

        effective_urgency = inp.urgency
        if night_active and effective_urgency == UrgencyLevel.WITHIN_3H:
            effective_urgency = UrgencyLevel.NOW_OR_NIGHT
        urgency_coeff = to_decimal(snow_cfg["urgency_coeff"][effective_urgency.value])

        work_raw = effective_area * rate_per_m2 * depth_coeff * urgency_coeff
        work_cost = max(min_work_price, work_raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        removal_volume_m3 = 0
        if inp.snow_removal:
            depth_avg = Decimal(str(snow_cfg["depth_avg_m"][inp.depth_bucket.value]))
            removal_volume_m3 = max(1, math.ceil(float(effective_area * depth_avg)))
            rem_cfg = snow_cfg["snow_removal"]
            rem_raw = Decimal(removal_volume_m3) * to_decimal(rem_cfg["rate_per_m3"])
            extras_cost = max(to_decimal(rem_cfg["min_removal_price"]), rem_raw)

        subtotal = work_cost + dispatch_fee + extras_cost
        if inp.group_discount_pct > ZERO:
            discount_amount = (subtotal * inp.group_discount_pct / HUNDRED).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        raw_total = max(min_order_total, subtotal - discount_amount)
        estimated_total = round_to_step(raw_total, step)

        details.update(
            {
                "area_m2": str(effective_area),
                "cleaning_method": selected_method.value,
                "rate_per_m2": str(rate_per_m2),
                "min_work_price": str(min_work_price),
                "depth_bucket": inp.depth_bucket.value,
                "depth_coeff": str(depth_coeff),
                "urgency": effective_urgency.value,
                "urgency_coeff": str(urgency_coeff),
                "snow_removal": inp.snow_removal,
                "removal_volume_m3": removal_volume_m3,
                "group_discount_pct": str(inp.group_discount_pct),
            }
        )

    elif svc == ServiceType.CAR_RESCUE:
        rcfg = tariff_config["car_rescue"]
        base_price = to_decimal(rcfg["base_price"])
        urgency_add = to_decimal(rcfg["urgency_surcharge"]) if inp.is_urgent_rescue else ZERO
        night_add = to_decimal(rcfg["night_surcharge"]) if night_active else ZERO
        vehicle_map = rcfg.get("vehicle_surcharge", {})
        vehicle_add = to_decimal(vehicle_map.get(inp.vehicle_type, 0))
        free_km = Decimal(str(rcfg.get("free_radius_km", 5)))
        per_km = to_decimal(rcfg.get("per_km_rate", 100))
        billable_km = max(0, math.ceil(float(inp.distance_km - free_km)))
        km_add = (Decimal(billable_km) * per_km).quantize(Decimal("0.01"))

        work_cost = base_price + urgency_add + night_add + vehicle_add
        dispatch_fee = km_add
        raw_total = work_cost + dispatch_fee
        estimated_total = round_to_step(raw_total, step)

        details.update(
            {
                "base_price": str(base_price),
                "urgency_surcharge": str(urgency_add),
                "night_surcharge": str(night_add),
                "vehicle_type": inp.vehicle_type,
                "vehicle_surcharge": str(vehicle_add),
                "distance_km": str(to_decimal(inp.distance_km)),
                "billable_km": billable_km,
                "km_surcharge": str(km_add),
            }
        )

    elif svc in (ServiceType.YARD_HELP, ServiceType.MOVERS):
        hcfg = tariff_config["hourly"]
        min_hours = int(hcfg.get("min_hours", 2))
        if inp.workers_count < 1 or inp.workers_count > 10:
            raise ValidationAppError("Число исполнителей должно быть от 1 до 10")
        if inp.hours < 1 or inp.hours > 24:
            raise ValidationAppError("Число часов должно быть от 1 до 24")
        billable_hours = max(inp.hours, min_hours)
        rate_key = (
            "yard_help_rate_per_hour" if svc == ServiceType.YARD_HELP else "movers_rate_per_hour"
        )
        hourly_rate = to_decimal(hcfg[rate_key])
        work_cost = (hourly_rate * Decimal(inp.workers_count) * Decimal(billable_hours)).quantize(
            Decimal("0.01")
        )
        floor_add = ZERO
        if svc == ServiceType.MOVERS and inp.floors_without_lift > 0:
            floor_rate = to_decimal(hcfg.get("floor_no_lift_surcharge_per_worker", 200))
            floor_add = floor_rate * Decimal(inp.floors_without_lift) * Decimal(inp.workers_count)
        equip_add = to_decimal(hcfg.get("equipment_fee", 1500)) if inp.needs_equipment else ZERO
        extras_cost = (floor_add + equip_add).quantize(Decimal("0.01"))

        subtotal = work_cost + dispatch_fee + extras_cost
        if inp.group_discount_pct > ZERO:
            discount_amount = (subtotal * inp.group_discount_pct / HUNDRED).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        estimated_total = round_to_step(max(min_order_total, subtotal - discount_amount), step)

        details.update(
            {
                "hourly_rate": str(hourly_rate),
                "workers_count": inp.workers_count,
                "requested_hours": inp.hours,
                "billable_hours": billable_hours,
                "floors_without_lift": inp.floors_without_lift,
                "floor_surcharge": str(floor_add),
                "equipment_fee": str(equip_add),
            }
        )

    elif svc == ServiceType.ROOF_CLEANING:
        if inp.area_m2 is None or inp.area_m2 <= ZERO:
            raise ValidationAppError("Укажите площадь крыши (м²)")
        effective_area = to_decimal(inp.area_m2)
        rcfg = tariff_config["roof_and_reagents"]
        rate = to_decimal(rcfg["roof_rate_per_m2"])
        min_roof = to_decimal(rcfg["roof_min_price"])
        work_cost = max(min_roof, effective_area * rate * inp.pitch_coeff * inp.icing_coeff)
        work_cost = work_cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        estimated_total = round_to_step(work_cost + dispatch_fee, step)
        details.update({"area_m2": str(effective_area), "roof_rate_per_m2": str(rate)})

    elif svc == ServiceType.REAGENTS:
        if inp.area_m2 is None or inp.area_m2 <= ZERO:
            raise ValidationAppError("Укажите площадь посыпки (м²)")
        effective_area = to_decimal(inp.area_m2)
        rcfg = tariff_config["roof_and_reagents"]
        rate = to_decimal(rcfg["reagents_rate_per_m2"])
        min_reag = to_decimal(rcfg["reagents_min_price"])
        work_cost = max(min_reag, effective_area * rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if inp.as_addon:
            dispatch_fee = ZERO
        estimated_total = round_to_step(work_cost + dispatch_fee, step)
        details.update({"area_m2": str(effective_area), "as_addon": inp.as_addon})

    else:
        raise ValidationAppError(f"Неизвестный тип услуги: {svc}")

    advance_pct = to_decimal(tariff_config["advance_pct_by_service"].get(svc.value, 30))
    raw_advance = round_to_step(estimated_total * advance_pct / HUNDRED, step)
    advance_amount = min(estimated_total, max(min_advance, raw_advance))
    if estimated_total < min_advance:
        advance_amount = estimated_total
    remainder_amount = (estimated_total - advance_amount).quantize(Decimal("0.01"))

    share_pct = (
        executor_share_override
        if executor_share_override is not None
        else to_decimal(tariff_config["executor_share_pct_by_service"].get(svc.value, 65))
    )
    dispatch_comp_pct = to_decimal(
        tariff_config.get("dispatch_compensation_pct_to_executor", 100)
    )
    est_payout = (
        work_cost * share_pct / HUNDRED + dispatch_fee * dispatch_comp_pct / HUNDRED
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    snapshot = {
        "formula_details": details,
        "work_cost": str(work_cost),
        "dispatch_fee": str(dispatch_fee),
        "extras_cost": str(extras_cost),
        "group_discount_amount": str(discount_amount),
        "estimated_total": str(estimated_total),
        "advance_pct": str(advance_pct),
        "advance_amount": str(advance_amount),
        "executor_share_pct": str(share_pct),
        "dispatch_compensation_pct": str(dispatch_comp_pct),
    }

    return PriceBreakdown(
        service_type=svc,
        estimated_total=estimated_total,
        advance_amount=advance_amount,
        remainder_amount=remainder_amount,
        work_cost=work_cost,
        dispatch_fee=dispatch_fee,
        extras_cost=extras_cost,
        group_discount_amount=discount_amount,
        estimated_executor_payout=est_payout,
        executor_share_pct=share_pct,
        cleaning_method=selected_method,
        area_m2=effective_area,
        is_night=night_active,
        pricing_snapshot=snapshot,
    )


def calculate_cancellation_refund(
    *,
    status: OrderStatus,
    paid_amount: Decimal,
    dispatch_fee: Decimal,
    by_service: bool = False,
) -> tuple[Decimal, Decimal]:
    """Возвращает (refund_to_client, dispatch_compensation_to_executor)."""
    paid = to_decimal(paid_amount)
    if paid <= ZERO:
        return ZERO, ZERO
    if by_service:
        return paid, ZERO
    if status in (
        OrderStatus.AWAITING_ADVANCE,
        OrderStatus.SEARCHING_EXECUTOR,
        OrderStatus.PARTIALLY_ASSIGNED,
        OrderStatus.ASSIGNED,
    ):
        return paid, ZERO
    # После выезда (en_route, arrived, price_adjustment_pending) удерживаем стоимость выезда
    withheld = min(paid, to_decimal(dispatch_fee))
    refund = (paid - withheld).quantize(Decimal("0.01"))
    return refund, withheld


def calculate_executor_earnings(
    *,
    final_total: Decimal,
    dispatch_fee: Decimal,
    executor_share_pct: Decimal,
    dispatch_comp_pct: Decimal,
    bonus_amount: Decimal,
    workers_count: int = 1,
) -> tuple[Decimal, Decimal, Decimal]:
    """Возвращает (order_earning, dispatch_compensation, bonus) на одного исполнителя."""
    workers = Decimal(max(1, workers_count))
    work_base = max(ZERO, to_decimal(final_total) - to_decimal(dispatch_fee))
    earning = ((work_base * to_decimal(executor_share_pct) / HUNDRED) / workers).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    dispatch_comp = (
        (to_decimal(dispatch_fee) * to_decimal(dispatch_comp_pct) / HUNDRED) / workers
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    bonus = (to_decimal(bonus_amount) / workers).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return earning, dispatch_comp, bonus
