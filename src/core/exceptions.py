"""Иерархия доменных и прикладных исключений."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    code: str = "app_error"
    http_status: int = 400

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404


class ValidationAppError(AppError):
    code = "validation_error"
    http_status = 422


class InvalidStateTransitionError(AppError):
    code = "invalid_state_transition"
    http_status = 409


class OrderAlreadyTakenError(AppError):
    code = "order_already_taken"
    http_status = 409


class MediaRequiredError(AppError):
    code = "media_required"
    http_status = 409


class PermissionDeniedError(AppError):
    code = "permission_denied"
    http_status = 403


class UnauthorizedError(AppError):
    code = "unauthorized"
    http_status = 401


class OutOfServiceAreaError(AppError):
    code = "out_of_service_area"
    http_status = 422


class PaymentVerificationError(AppError):
    code = "payment_verification_failed"
    http_status = 403


class ExternalIntegrationError(AppError):
    code = "external_integration_error"
    http_status = 502
