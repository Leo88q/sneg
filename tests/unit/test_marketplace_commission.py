from decimal import Decimal

import pytest

from src.core.exceptions import ValidationAppError
from src.domain.pricing.marketplace import estimate_marketplace_commission


@pytest.mark.parametrize(
    ("total", "commission", "executor"),
    [
        ("10000.00", "2700.00", "7300.00"),
        ("10350.00", "2794.50", "7555.50"),
        ("0.50", "0.14", "0.36"),
        ("0.01", "0.00", "0.01"),
        ("9999999999.99", "2700000000.00", "7299999999.99"),
    ],
)
def test_commission_on_entire_total(total: str, commission: str, executor: str) -> None:
    result = estimate_marketplace_commission(Decimal(total))
    assert result.commission_percent == Decimal("27")
    assert result.commission == Decimal(commission)
    assert result.executor_after_commission == Decimal(executor)
    assert result.commission + result.executor_after_commission == result.total


@pytest.mark.parametrize("total", ["0", "-1", "NaN", "Infinity", "1.001", "10000000000"])
def test_invalid_total_is_rejected(total: str) -> None:
    with pytest.raises(ValidationAppError):
        estimate_marketplace_commission(Decimal(total))
