"""Протокол WeatherProvider и реализации Open-Meteo / Mock (ТЗ B8.3)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import httpx


@dataclass(slots=True)
class SnowfallForecast:
    lat: float
    lon: float
    expected_snow_cm: Decimal
    hours_ahead: int
    summary: str


class WeatherProvider(Protocol):
    async def get_snowfall_forecast(
        self, lat: float, lon: float, hours_ahead: int = 18
    ) -> SnowfallForecast: ...


class OpenMeteoWeatherProvider:
    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    async def get_snowfall_forecast(
        self, lat: float, lon: float, hours_ahead: int = 18
    ) -> SnowfallForecast:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                self.BASE_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "hourly": "snowfall",
                    "forecast_days": 2,
                    "timezone": "Europe/Moscow",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        snowfall_series = data.get("hourly", {}).get("snowfall", [])[:hours_ahead]
        total_cm = Decimal(str(round(sum(float(x or 0) for x in snowfall_series), 2)))
        return SnowfallForecast(
            lat=lat,
            lon=lon,
            expected_snow_cm=total_cm,
            hours_ahead=hours_ahead,
            summary=f"Ожидается ~{total_cm} см снега за {hours_ahead} ч",
        )


class MockWeatherProvider:
    def __init__(self, default_snow_cm: Decimal = Decimal("0.0")) -> None:
        self.default_snow_cm = default_snow_cm
        self._overrides: dict[str, Decimal] = {}

    def set_zone_snowfall(self, zone_code: str, snow_cm: Decimal) -> None:
        self._overrides[zone_code] = snow_cm

    async def get_snowfall_forecast(
        self, lat: float, lon: float, hours_ahead: int = 18, zone_code: str | None = None
    ) -> SnowfallForecast:
        cm = (
            self._overrides.get(zone_code, self.default_snow_cm)
            if zone_code
            else self.default_snow_cm
        )
        return SnowfallForecast(
            lat=lat,
            lon=lon,
            expected_snow_cm=cm,
            hours_ahead=hours_ahead,
            summary=f"[Mock] Прогноз снега: {cm} см за {hours_ahead} ч",
        )
