"""Зависимости FastAPI и хелперы доступа к контейнеру и RBAC."""

from __future__ import annotations

import uuid

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.container import AppContainer
from src.core.enums import AdminRole
from src.core.exceptions import PermissionDeniedError, UnauthorizedError
from src.core.security import decode_admin_jwt
from src.infra.db.models import AdminAccount, AuditLog


def get_container(request: Request) -> AppContainer:
    return request.app.state.container  # type: ignore[no-any-return]


async def get_current_admin(
    request: Request,
    container: AppContainer = Depends(get_container),
) -> AdminAccount:
    token = request.cookies.get("sneg_admin_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
    if not token:
        raise UnauthorizedError("Требуется авторизация администратора")

    payload = decode_admin_jwt(token, container.settings.admin_jwt_secret)
    admin_id = uuid.UUID(str(payload["sub"]))
    async with container.session_factory() as session:
        admin = await session.get(AdminAccount, admin_id)
    if admin is None or not admin.is_active:
        raise UnauthorizedError("Учётная запись администратора неактивна")
    return admin


def require_roles(*allowed: AdminRole):
    allowed_vals = {r.value for r in allowed}

    async def _checker(admin: AdminAccount = Depends(get_current_admin)) -> AdminAccount:
        if admin.role not in allowed_vals:
            raise PermissionDeniedError(
                f"Недостаточно прав (роль {admin.role}, требуется: {', '.join(sorted(allowed_vals))})"
            )
        return admin

    return _checker


async def write_audit(
    session: AsyncSession,
    *,
    admin: AdminAccount,
    action: str,
    entity_type: str,
    entity_id: str,
    diff: dict | None = None,
    ip: str | None = None,
) -> None:
    session.add(
        AuditLog(
            admin_id=admin.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            diff_json=diff or {},
            ip_address=ip,
        )
    )
