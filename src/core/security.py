"""Криптография, пароли (Argon2id), JWT, проверка секретов и IP allowlist ЮKassa."""

from __future__ import annotations

import hmac
import ipaddress
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from src.core.exceptions import UnauthorizedError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_admin_jwt(
    *,
    admin_id: str,
    email: str,
    role: str,
    secret: str,
    ttl_minutes: int,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": admin_id,
        "email": email,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_admin_jwt(token: str, secret: str) -> dict[str, Any]:
    try:
        return dict(jwt.decode(token, secret, algorithms=["HS256"]))
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Недействительный или просроченный токен авторизации") from exc


def verify_webhook_secret(expected: str, provided: str | None) -> bool:
    if not expected:
        return True
    if not provided:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def is_ip_in_cidrs(ip_str: str, cidrs: list[str]) -> bool:
    if not ip_str:
        return False
    # Обработка формата "ip:port" или "ip1, ip2"
    cleaned = ip_str.split(",")[0].strip()
    if cleaned.startswith("[") and "]" in cleaned:
        cleaned = cleaned[1 : cleaned.index("]")]
    elif cleaned.count(":") == 1 and "." in cleaned:
        cleaned = cleaned.split(":")[0]
    try:
        addr = ipaddress.ip_address(cleaned)
    except ValueError:
        return False
    for raw_cidr in cidrs:
        try:
            net = ipaddress.ip_network(raw_cidr, strict=False)
        except ValueError:
            continue
        if addr in net:
            return True
    return False
