from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.core.enums import (
    CleaningMethod,
    DepthBucket,
    OrderStatus,
    ServiceType,
    UrgencyLevel,
)
from src.core.exceptions import InvalidStateTransitionError
from src.domain.geo.service import (
    GeoZoneCandidate,
    build_public_area_hint,
    resolve_geo_zone_by_coords,
    road_distance_km,
)
from src.domain.orders.state_machine import assert_order_transition, can_transition
from src.domain.pricing.calculator import (
    PriceCalculationInput,
    calculate_cancellation_refund,
    calculate_executor_earnings,
    calculate_price,
    resolve_group_discount_pct,
    round_to_step,
)
from src.domain.pricing.defaults import DEFAULT_GEO_ZONES, DEFAULT_TARIFF_CONFIG


def test_tz_b7_2_example_1_snow_yard() -> None:
    """TZ Section B7.2 Example 1: 120 m², 10-30cm, snowblower, within_3h, Malakhovka."""
    day_utc = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)  # 12:00 MSK (daytime)
    res = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.SNOW_YARD,
            dispatch_fee=Decimal("500.00"),
            area_m2=Decimal("120"),
            depth_bucket=DepthBucket.FROM_10_TO_30,
            cleaning_method=CleaningMethod.SNOWBLOWER_OR_MINILOADER,
            urgency=UrgencyLevel.WITHIN_3H,
        ),
        DEFAULT_TARIFF_CONFIG,
        now_utc=day_utc,
    )
    assert res.estimated_total == Decimal("5200.00")
    assert res.advance_amount == Decimal("1550.00")
    assert res.remainder_amount == Decimal("3650.00")
    assert res.is_night is False


def test_tz_b7_2_example_2_car_rescue() -> None:
    """TZ Section B7.2 Example 2: 2500 + 500 urgent + 1000 night + 500 suv + 3*100km = 4800 ₽."""
    night_utc = datetime(2026, 1, 15, 21, 0, tzinfo=UTC)  # 00:00 MSK
    res = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.CAR_RESCUE,
            vehicle_type="suv",
            distance_km=Decimal("8"),  # 5 km free + 3 billable km
            is_urgent_rescue=True,
        ),
        DEFAULT_TARIFF_CONFIG,
        now_utc=night_utc,
    )
    assert res.estimated_total == Decimal("4800.00")
    assert res.advance_amount == Decimal("2400.00")
    assert res.remainder_amount == Decimal("2400.00")
    assert res.is_night is True


def test_rounding_half_up_to_50_rub() -> None:
    assert round_to_step(Decimal("1225"), 50) == Decimal("1250.00")
    assert round_to_step(Decimal("1224.99"), 50) == Decimal("1200.00")
    assert round_to_step(Decimal("1560"), 50) == Decimal("1550.00")
    assert round_to_step(Decimal("1575"), 50) == Decimal("1600.00")


def test_night_msk_boosts_urgent_within_3h_to_1_5() -> None:
    night_utc = datetime(2026, 1, 15, 20, 30, tzinfo=UTC)  # 23:30 MSK
    res = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.SNOW_YARD,
            dispatch_fee=Decimal("500.00"),
            area_m2=Decimal("100"),
            depth_bucket=DepthBucket.UP_TO_10,
            cleaning_method=CleaningMethod.MANUAL,
            urgency=UrgencyLevel.WITHIN_3H,
        ),
        DEFAULT_TARIFF_CONFIG,
        now_utc=night_utc,
    )
    assert res.is_night is True
    # 100 * 25 * 1.0 * 1.5 = 3750 + 500 = 4250
    assert res.estimated_total == Decimal("4250.00")


def test_hourly_services_and_group_discount() -> None:
    day_utc = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
    res_movers = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.MOVERS,
            dispatch_fee=Decimal("500.00"),
            workers_count=2,
            hours=1,  # min 2 hours applies
            floors_without_lift=2,
        ),
        DEFAULT_TARIFF_CONFIG,
        now_utc=day_utc,
    )
    # 850 * 2 * 2 = 3400 + 2*2*150 (600) + 500 (dispatch) = 4500
    assert res_movers.estimated_total == Decimal("4500.00")

    disc_pct = resolve_group_discount_pct(2, DEFAULT_TARIFF_CONFIG)
    assert disc_pct == Decimal("10.00")
    res_group = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.SNOW_YARD,
            dispatch_fee=Decimal("500.00"),
            area_m2=Decimal("100"),
            depth_bucket=DepthBucket.UP_TO_10,
            cleaning_method=CleaningMethod.MANUAL,
            urgency=UrgencyLevel.TODAY,
            group_discount_pct=disc_pct,
        ),
        DEFAULT_TARIFF_CONFIG,
        now_utc=day_utc,
    )
    # base 2500 + 500 = 3000; 10% = 300 -> 2700
    assert res_group.group_discount_amount == Decimal("300.00")
    assert res_group.estimated_total == Decimal("2700.00")


def test_roof_cleaning_and_reagents_pricing() -> None:
    res_roof = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.ROOF_CLEANING,
            dispatch_fee=Decimal("500.00"),
            area_m2=Decimal("80"),
        ),
        DEFAULT_TARIFF_CONFIG,
    )
    # 80 * 90 = 7200 + 500 = 7700
    assert res_roof.estimated_total == Decimal("7700.00")

    res_reag = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.REAGENTS,
            dispatch_fee=Decimal("500.00"),
            area_m2=Decimal("100"),
            as_addon=True,
        ),
        DEFAULT_TARIFF_CONFIG,
    )
    # addon -> dispatch_fee = 0; 100 * 15 = 1500
    assert res_reag.dispatch_fee == Decimal("0.00")
    assert res_reag.estimated_total == Decimal("1500.00")


def test_cancellation_refund_rules() -> None:
    # Service cancel -> 100% refund, 0 compensation
    assert calculate_cancellation_refund(
        status=OrderStatus.EN_ROUTE,
        paid_amount=Decimal("1550"),
        dispatch_fee=Decimal("500"),
        by_service=True,
    ) == (Decimal("1550.00"), Decimal("0.00"))

    # Client before takeoff -> 100% refund
    assert calculate_cancellation_refund(
        status=OrderStatus.SEARCHING_EXECUTOR,
        paid_amount=Decimal("1550"),
        dispatch_fee=Decimal("500"),
        by_service=False,
    ) == (Decimal("1550.00"), Decimal("0.00"))

    # Client after en_route -> minus dispatch fee, dispatch comp goes to executor
    assert calculate_cancellation_refund(
        status=OrderStatus.EN_ROUTE,
        paid_amount=Decimal("1550"),
        dispatch_fee=Decimal("500"),
        by_service=False,
    ) == (Decimal("1050.00"), Decimal("500.00"))


def test_executor_earnings_split() -> None:
    earning, dispatch_comp, bonus = calculate_executor_earnings(
        final_total=Decimal("5200.00"),
        dispatch_fee=Decimal("500.00"),
        executor_share_pct=Decimal("65.00"),
        dispatch_comp_pct=Decimal("100.00"),
        bonus_amount=Decimal("300.00"),
        workers_count=1,
    )
    # work_base = 4700 * 65% = 3055; dispatch = 500; bonus = 300 -> total 3855
    assert earning == Decimal("3055.00")
    assert dispatch_comp == Decimal("500.00")
    assert bonus == Decimal("300.00")


def test_geo_zone_resolution_and_public_hint_privacy() -> None:
    zones = [
        GeoZoneCandidate(
            id=idx,
            code=z["code"],
            name=z["name"],
            is_active=z["is_active"],
            dispatch_fee=Decimal(str(z["dispatch_fee"])),
            base_lat=float(z["base_lat"]),
            base_lon=float(z["base_lon"]),
            polygon_geojson=z.get("polygon_geojson"),
        )
        for idx, z in enumerate(DEFAULT_GEO_ZONES)
    ]
    z_mal, dist_mal = resolve_geo_zone_by_coords(55.6435, 38.0068, zones)
    assert z_mal is not None and z_mal.code == "malakhovka"
    assert dist_mal >= Decimal("0")

    z_lyt, _ = resolve_geo_zone_by_coords(55.5798, 37.9032, zones)
    assert z_lyt is not None and z_lyt.code == "lytkarino"

    z_out, _ = resolve_geo_zone_by_coords(55.7558, 37.6173, zones)  # Moscow center
    assert z_out is None

    dist = road_distance_km(55.6435, 38.0068, 55.6546, 37.9493)
    assert Decimal("2.0") < dist < Decimal("8.0")

    hint = build_public_area_hint("Малаховка", "пос. Малаховка, ул. Красковское шоссе, д. 14, кв. 5")
    assert "Малаховка" in hint
    assert "14" not in hint
    assert "Красковское" not in hint


def test_order_state_machine_enforces_valid_transitions() -> None:
    assert can_transition(OrderStatus.SEARCHING_EXECUTOR, OrderStatus.ASSIGNED)
    assert not can_transition(OrderStatus.SEARCHING_EXECUTOR, OrderStatus.COMPLETED)
    with pytest.raises(InvalidStateTransitionError):
        assert_order_transition(OrderStatus.COMPLETED, OrderStatus.IN_PROGRESS)
