from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .errors import ValidationError

ALLOWED_ROLES = {"viewer", "analyst", "admin"}
ALLOWED_ENTRY_TYPES = {"income", "expense"}
MONEY_PLACES = Decimal("0.01")
PASSWORD_MIN_LENGTH = 8


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_positive_int(value: object, field_name: str, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must be a valid integer.") from exc

    if parsed < minimum:
        raise ValidationError(f"{field_name} must be at least {minimum}.")
    if maximum is not None and parsed > maximum:
        raise ValidationError(f"{field_name} must be at most {maximum}.")
    return parsed


def normalize_role(value: object) -> str:
    role = str(value or "").strip().lower()
    if role not in ALLOWED_ROLES:
        raise ValidationError(f"role must be one of: {', '.join(sorted(ALLOWED_ROLES))}.")
    return role


def normalize_entry_type(value: object) -> str:
    entry_type = str(value or "").strip().lower()
    if entry_type not in ALLOWED_ENTRY_TYPES:
        raise ValidationError("type must be either 'income' or 'expense'.")
    return entry_type


def normalize_date(value: object, field_name: str = "date") -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must use YYYY-MM-DD format.") from exc


def normalize_text(
    value: object,
    field_name: str,
    *,
    required: bool,
    max_length: int,
) -> str | None:
    if value is None:
        if required:
            raise ValidationError(f"{field_name} is required.")
        return None

    text = str(value).strip()
    if required and not text:
        raise ValidationError(f"{field_name} is required.")
    if len(text) > max_length:
        raise ValidationError(f"{field_name} must be {max_length} characters or fewer.")
    return text or None


def amount_to_cents(value: object) -> int:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("amount must be a valid decimal number.") from exc

    if decimal_value <= 0:
        raise ValidationError("amount must be greater than 0.")

    quantized = decimal_value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)
    if decimal_value != quantized:
        raise ValidationError("amount must have at most 2 decimal places.")
    return int((quantized * 100).to_integral_value())


def cents_to_amount(cents: int) -> str:
    return f"{Decimal(cents) / Decimal(100):.2f}"


def normalize_password(value: object, field_name: str = "password") -> str:
    password = str(value or "")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValidationError(f"{field_name} must be at least {PASSWORD_MIN_LENGTH} characters long.")
    if len(password) > 128:
        raise ValidationError(f"{field_name} must be 128 characters or fewer.")
    return password


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, digest = password_hash.split("$", maxsplit=1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000).hex()
    return hmac.compare_digest(candidate, digest)


def validate_user_payload(payload: dict, *, partial: bool = False) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")

    allowed_fields = {"name", "role", "password"}
    unknown_fields = set(payload) - allowed_fields
    if unknown_fields:
        raise ValidationError(f"Unknown user fields: {', '.join(sorted(unknown_fields))}.")

    if partial and not payload:
        raise ValidationError("At least one field is required for update.")

    normalized: dict[str, object] = {}

    if "name" in payload or not partial:
        normalized["name"] = normalize_text(payload.get("name"), "name", required=True, max_length=80)
    if "role" in payload or not partial:
        normalized["role"] = normalize_role(payload.get("role"))
    if "password" in payload or not partial:
        normalized["password"] = normalize_password(payload.get("password"))

    return normalized


def validate_transaction_payload(payload: dict, *, partial: bool = False) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")

    allowed_fields = {"user_id", "amount", "type", "category", "date", "notes"}
    unknown_fields = set(payload) - allowed_fields
    if unknown_fields:
        raise ValidationError(f"Unknown transaction fields: {', '.join(sorted(unknown_fields))}.")

    if partial and not payload:
        raise ValidationError("At least one field is required for update.")

    normalized: dict[str, object] = {}

    if "user_id" in payload or not partial:
        normalized["user_id"] = parse_positive_int(payload.get("user_id"), "user_id")
    if "amount" in payload or not partial:
        normalized["amount_cents"] = amount_to_cents(payload.get("amount"))
    if "type" in payload or not partial:
        normalized["entry_type"] = normalize_entry_type(payload.get("type"))
    if "category" in payload or not partial:
        normalized["category"] = normalize_text(payload.get("category"), "category", required=True, max_length=60)
    if "date" in payload or not partial:
        normalized["entry_date"] = normalize_date(payload.get("date"))
    if "notes" in payload or not partial:
        normalized["notes"] = normalize_text(payload.get("notes"), "notes", required=False, max_length=500)

    return normalized
