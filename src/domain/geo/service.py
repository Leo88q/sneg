"""Расчёт расстояний (Haversine × дорожный коэффициент) и определение геозон."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

EARTH_RADIUS_KM = 6371.0
ROAD_FACTOR = 1.3


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def road_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> Decimal:
    dist = haversine_km(lat1, lon1, lat2, lon2) * ROAD_FACTOR
    return Decimal(f"{dist:.2f}")


def point_in_polygon(lat: float, lon: float, polygon_geojson: dict[str, Any] | None) -> bool:
    if not polygon_geojson:
        return False
    geom_type = polygon_geojson.get("type")
    coords = polygon_geojson.get("coordinates")
    if geom_type != "Polygon" or not coords:
        return False
    ring = coords[0]
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = float(ring[i][0]), float(ring[i][1])  # GeoJSON: [lon, lat]
        xj, yj = float(ring[j][0]), float(ring[j][1])
        intersect = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


@dataclass(slots=True)
class GeoZoneCandidate:
    id: Any
    code: str
    name: str
    is_active: bool
    dispatch_fee: Decimal
    base_lat: float
    base_lon: float
    polygon_geojson: dict[str, Any] | None


# Координаты центров и радиусов для fallback-резолва без полигона (МО, Люберецкий р-н / Лыткарино)
KNOWN_ZONE_CENTERS: dict[str, tuple[float, float, float]] = {
    "malakhovka": (55.6435, 38.0068, 4.5),
    "tomilino": (55.6546, 37.9493, 3.8),
    "kraskovo": (55.6562, 37.9918, 3.8),
    "bykovo": (55.6352, 38.0812, 4.5),
    "lytkarino": (55.5798, 37.9032, 5.5),
    "oktyabrsky": (55.6081, 37.9776, 3.5),
    "lyubertsy": (55.6789, 37.8932, 5.0),
}


def resolve_geo_zone_by_coords(
    lat: float,
    lon: float,
    zones: list[GeoZoneCandidate],
) -> tuple[GeoZoneCandidate | None, Decimal]:
    active_zones = [z for z in zones if z.is_active]
    for zone in active_zones:
        if point_in_polygon(lat, lon, zone.polygon_geojson):
            return zone, road_distance_km(zone.base_lat, zone.base_lon, lat, lon)

    best_zone: GeoZoneCandidate | None = None
    best_dist = float("inf")
    for zone in active_zones:
        center = KNOWN_ZONE_CENTERS.get(zone.code)
        if center:
            clat, clon, radius = center
            d = haversine_km(clat, clon, lat, lon)
            if d <= radius and d < best_dist:
                best_dist = d
                best_zone = zone
    if best_zone is not None:
        return best_zone, road_distance_km(best_zone.base_lat, best_zone.base_lon, lat, lon)
    return None, Decimal("0.00")


def build_public_area_hint(zone_name: str, address_text: str) -> str:
    """Формирует публичный ориентир для общего чата БЕЗ номера дома/участка/квартиры."""
    snt_match = re.search(r"(СНТ\s+[«\"']?[\w\s-]+?[»\"']?)(?:[,.\s]|$)", address_text, flags=re.I)
    if snt_match:
        return f"{zone_name}, {snt_match.group(1).strip()}"
    return f"{zone_name} (точный адрес после «Беру»)"


def normalize_cluster_key(zone_code: str, address_text: str) -> str:
    """Нормализует ключ СНТ/улицы для группировки соседских заказов (Этап 3)."""
    cleaned = address_text.lower()
    # Убираем номера домов/участков/квартир
    cleaned = re.sub(r"\b(д\.?|дом|уч\.?|участок|кв\.?|стр\.?)\s*\d+\w*", "", cleaned)
    cleaned = re.sub(r"\d+", "", cleaned)
    cleaned = re.sub(r"[^\wа-яё]+", " ", cleaned).strip()
    tokens = [t for t in cleaned.split() if len(t) > 2][:3]
    suffix = "_".join(tokens) if tokens else "general"
    return f"{zone_code}:{suffix}"
