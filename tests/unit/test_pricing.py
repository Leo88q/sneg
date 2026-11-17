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


def test_market_2026_snow_yard_snowblower_example() -> None:
    """МО 2026 (аренда техники): 120 м², 10–30 см (×1.5), снегоуборщик (42 ₽/м²), за 3 ч (×1.3), Малаховка."""
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
    # 120 * 42 * 1.5 * 1.3 = 9828 + 500 = 10328 -> round50 = 10350
    assert res.estimated_total == Decimal("10350.00")
    assert res.advance_amount == Decimal("3100.00")
    assert res.remainder_amount == Decimal("7250.00")
    assert res.is_night is False
    ue = res.pricing_snapshot["unit_economics"]
    assert ue["requires_equipment_rental"] is True
    assert Decimal(ue["equipment_rental_rub"]) == Decimal("2200.00")
    assert Decimal(ue["owner_net_profit_rub"]) > Decimal("1500.00")


def test_market_2026_snow_manual_tractor_and_snow_removal_with_rental_margin() -> None:
    """МО 2026: ручной минимум, аренда трактора (14 500 ₽ + 50% аванс) и вывоз снега (850 ₽/м³, мин. 13 500 ₽)."""
    day_utc = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)

    # 50 м² до 10 см вручную -> min_work_price 2500 + 500 выезд = 3000 (без аренды техники)
    res_small = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.SNOW_YARD,
            dispatch_fee=Decimal("500.00"),
            area_m2=Decimal("50"),
            depth_bucket=DepthBucket.UP_TO_10,
            cleaning_method=CleaningMethod.MANUAL,
            urgency=UrgencyLevel.TODAY,
        ),
        DEFAULT_TARIFF_CONFIG,
        now_utc=day_utc,
    )
    assert res_small.work_cost == Decimal("2500.00")
    assert res_small.estimated_total == Decimal("3000.00")
    assert res_small.advance_amount == Decimal("900.00")
    assert res_small.pricing_snapshot["unit_economics"]["requires_equipment_rental"] is False

    # 50 м² 10-30 см с вывозом (10 м³ -> мин. вывоза 13 500 ₽; аванс 50% для защиты брони самосвала!)
    res_rem_min = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.SNOW_YARD,
            dispatch_fee=Decimal("500.00"),
            area_m2=Decimal("50"),
            depth_bucket=DepthBucket.FROM_10_TO_30,
            cleaning_method=CleaningMethod.MANUAL,
            urgency=UrgencyLevel.TODAY,
            snow_removal=True,
        ),
        DEFAULT_TARIFF_CONFIG,
        now_utc=day_utc,
    )
    assert res_rem_min.extras_cost == Decimal("13500.00")
    assert res_rem_min.estimated_total == Decimal("17000.00")
    assert res_rem_min.advance_amount == Decimal("8500.00")
    assert Decimal(res_rem_min.pricing_snapshot["unit_economics"]["owner_net_profit_rub"]) > Decimal("2500.00")

    # 100 м² 10-30 см (автовыбор вручную до 100 м²) + вывоз 20 м³ * 850 = 17 000 ₽
    res_rem_vol = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.SNOW_YARD,
            dispatch_fee=Decimal("500.00"),
            area_m2=Decimal("100"),
            depth_bucket=DepthBucket.FROM_10_TO_30,
            urgency=UrgencyLevel.TODAY,
            snow_removal=True,
        ),
        DEFAULT_TARIFF_CONFIG,
        now_utc=day_utc,
    )
    assert res_rem_vol.cleaning_method == CleaningMethod.MANUAL
    assert res_rem_vol.extras_cost == Decimal("17000.00")
    assert res_rem_vol.estimated_total == Decimal("23500.00")
    assert res_rem_vol.advance_amount == Decimal("11750.00")

    # Трактор на субподряде: минимум 14 500 + 500 = 15 000 ₽, аванс 50% = 7 500 ₽, чистая прибыль > 3 000 ₽
    res_tractor = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.SNOW_YARD,
            dispatch_fee=Decimal("500.00"),
            area_m2=Decimal("200"),
            depth_bucket=DepthBucket.UP_TO_10,
            cleaning_method=CleaningMethod.TRACTOR,
            urgency=UrgencyLevel.TODAY,
        ),
        DEFAULT_TARIFF_CONFIG,
        now_utc=day_utc,
    )
    assert res_tractor.estimated_total == Decimal("15000.00")
    assert res_tractor.advance_amount == Decimal("7500.00")
    ue_tr = res_tractor.pricing_snapshot["unit_economics"]
    assert Decimal(ue_tr["equipment_rental_rub"]) == Decimal("10650.00")
    assert Decimal(ue_tr["owner_net_profit_rub"]) == Decimal("3165.00")


def test_tz_b7_2_example_2_car_rescue() -> None:
    """МО 2026 (субподряд 4x4): 3500 + 500 urgent + 1000 night + 700 suv + 3*120km = 6060 -> 6050 ₽."""
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
    assert res.estimated_total == Decimal("6050.00")
    assert res.advance_amount == Decimal("3050.00")
    assert res.remainder_amount == Decimal("3000.00")
    assert res.is_night is True
    assert Decimal(res.pricing_snapshot["unit_economics"]["owner_net_profit_rub"]) > Decimal("1400.00")


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
    # 100 * 40 * 1.0 * 1.5 = 6000 + 500 = 6500
    assert res.estimated_total == Decimal("6500.00")


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
    # 750 * 2 * 2 = 3000 + 2*2*200 (800) + 500 (dispatch) = 4300
    assert res_movers.estimated_total == Decimal("4300.00")

    # yard_help 1 чел × 2 ч = 1400 + 500 = 1900 -> min_order_total = 2000, min_advance = 700
    res_yard_min = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.YARD_HELP,
            dispatch_fee=Decimal("500.00"),
            workers_count=1,
            hours=2,
        ),
        DEFAULT_TARIFF_CONFIG,
        now_utc=day_utc,
    )
    assert res_yard_min.estimated_total == Decimal("2000.00")
    assert res_yard_min.advance_amount == Decimal("700.00")

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
    # base 4000 + 500 = 4500; 10% = 450 -> 4050
    assert res_group.group_discount_amount == Decimal("450.00")
    assert res_group.estimated_total == Decimal("4050.00")


def test_roof_cleaning_and_reagents_pricing() -> None:
    res_roof_min = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.ROOF_CLEANING,
            dispatch_fee=Decimal("500.00"),
            area_m2=Decimal("50"),
        ),
        DEFAULT_TARIFF_CONFIG,
    )
    # 50 * 90 = 4500 < min 6000 -> 6000 + 500 = 6500
    assert res_roof_min.estimated_total == Decimal("6500.00")

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
    # addon -> dispatch_fee = 0; 100 * 20 = 2000
    assert res_reag.dispatch_fee == Decimal("0.00")
    assert res_reag.estimated_total == Decimal("2000.00")

    res_reag_min = calculate_price(
        PriceCalculationInput(
            service_type=ServiceType.REAGENTS,
            dispatch_fee=Decimal("500.00"),
            area_m2=Decimal("30"),
            as_addon=True,
        ),
        DEFAULT_TARIFF_CONFIG,
    )
    # 30 * 20 = 600 < min 1000 -> 1000
    assert res_reag_min.estimated_total == Decimal("1000.00")


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


def test_alembic_0003_upgrades_legacy_v1_tariff_to_market_2026() -> None:
    """Миграция 0003 безопасно переводит живую БД со старым v1 (25 ₽/м²) на активный v2 МО 2026."""
    import importlib.util
    import json
    import uuid
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from src.infra.db.models import Base

    mig_path = Path("migrations/versions/0003_market_2026_tariffs.py")
    spec = importlib.util.spec_from_file_location("mig_0003", mig_path)
    assert spec is not None and spec.loader is not None
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    old_v1 = {
        "min_order_total_rub": 1500,
        "snow": {"methods": {"manual": {"rate_per_m2": 25, "min_work_price": 1500}}},
    }
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO tariff_configs (id, version, is_active, config_json, comment, created_at) "
                "VALUES (:id, 1, 1, :cfg, 'old v1', :ts)"
            ),
            {"id": uuid.uuid4().hex, "cfg": json.dumps(old_v1), "ts": datetime.now(UTC).isoformat()},
        )

    # Применяем upgrade() через Alembic op context
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mig.upgrade()

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT version, is_active, config_json FROM tariff_configs ORDER BY version ASC")
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][0] == 1 and not rows[0][1]
        assert rows[1][0] == 2 and bool(rows[1][1])
        v2_cfg = json.loads(rows[1][2]) if isinstance(rows[1][2], str) else rows[1][2]
        assert v2_cfg["min_order_total_rub"] == 2000
        assert v2_cfg["snow"]["methods"]["manual"]["rate_per_m2"] == 40
        assert v2_cfg["snow"]["methods"]["tractor"]["min_work_price"] == 14500
        assert v2_cfg["snow"]["snow_removal"]["min_removal_price"] == 13500
        assert v2_cfg["car_rescue"]["base_price"] == 3500
        assert v2_cfg["hourly"]["movers_rate_per_hour"] == 750

    # Повторный вызов upgrade() — безопасный no-op
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mig.upgrade()

    with engine.connect() as conn:
        count = conn.execute(sa.text("SELECT COUNT(1) FROM tariff_configs")).scalar()
        assert count == 2


def test_alembic_0004_upgrades_v2_without_rental_to_v3_subcontract_tariffs() -> None:
    """Миграция 0004 переводит БД с v2 (трактор 5 000 ₽) на v3 с заложенной арендой техники (14 500 ₽)."""
    import importlib.util
    import json
    import uuid
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from src.infra.db.models import Base

    mig_path = Path("migrations/versions/0004_subcontract_rental_tariffs.py")
    spec = importlib.util.spec_from_file_location("mig_0004", mig_path)
    assert spec is not None and spec.loader is not None
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    v2_no_rental = {
        "min_order_total_rub": 2000,
        "snow": {
            "methods": {
                "manual": {"rate_per_m2": 40, "min_work_price": 2500},
                "tractor": {"rate_per_m2": 20, "min_work_price": 5000},
            }
        },
    }
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO tariff_configs (id, version, is_active, config_json, comment, created_at) "
                "VALUES (:id, 2, 1, :cfg, 'Рыночный прайс МО 2026 (Люберцы / Малаховка, автомиграция v2)', :ts)"
            ),
            {
                "id": uuid.uuid4().hex,
                "cfg": json.dumps(v2_no_rental),
                "ts": datetime.now(UTC).isoformat(),
            },
        )

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mig.upgrade()

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT version, is_active, config_json FROM tariff_configs ORDER BY version ASC")
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][0] == 2 and not rows[0][1]
        assert rows[1][0] == 3 and bool(rows[1][1])
        v3_cfg = json.loads(rows[1][2]) if isinstance(rows[1][2], str) else rows[1][2]
        assert v3_cfg["snow"]["methods"]["tractor"]["min_work_price"] == 14500
        assert v3_cfg["snow"]["methods"]["snowblower_or_miniloader"]["min_work_price"] == 5500
        assert v3_cfg["snow"]["snow_removal"]["min_removal_price"] == 13500
        assert v3_cfg["car_rescue"]["base_price"] == 3500

