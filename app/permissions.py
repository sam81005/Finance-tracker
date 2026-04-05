from __future__ import annotations

from .errors import PermissionDenied


def require_roles(user: dict, *roles: str) -> None:
    if user["role"] not in roles:
        allowed = ", ".join(roles)
        raise PermissionDenied(f"This action requires one of the following roles: {allowed}.")
