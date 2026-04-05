from __future__ import annotations

import csv
import json
from io import StringIO
from http.cookies import SimpleCookie
from pathlib import Path

from .database import initialize_database
from .errors import AuthenticationError, NotFoundError, PermissionDenied, ValidationError
from .models import (
    cents_to_amount,
    hash_password,
    normalize_date,
    normalize_entry_type,
    normalize_password,
    parse_positive_int,
    validate_transaction_payload,
    validate_user_payload,
    verify_password,
)
from .permissions import require_roles
from .repositories import TransactionRepository, UserRepository


class FinanceService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path
        initialize_database(db_path)
        self.users = UserRepository(db_path)
        self.transactions = TransactionRepository(db_path)

    def authenticate(self, headers: dict, *, allow_default_admin: bool = True) -> dict:
        user_id = headers.get("x-user-id") or self._cookie_user_id(headers.get("cookie"))
        if user_id is None:
            if allow_default_admin:
                return self.users.get_by_id(1)
            raise AuthenticationError("Please log in to continue.")
        try:
            return self.users.get_by_id(int(user_id))
        except ValueError as exc:
            raise AuthenticationError("User id must be a valid integer.") from exc

    def list_login_users(self) -> list[dict]:
        return [self._serialize_user(user) for user in self.users.list_users()]

    def authenticate_credentials(self, name: object, password: object) -> dict:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise AuthenticationError("Name is required.")
        normalized_password = normalize_password(password, "password")
        try:
            user = self.users.get_by_name(normalized_name)
        except NotFoundError as exc:
            raise AuthenticationError("Invalid name or password.") from exc
        if not verify_password(normalized_password, user.get("password_hash", "")):
            raise AuthenticationError("Invalid name or password.")
        return user

    def register_user(self, payload: dict) -> dict:
        validated = validate_user_payload({**payload, "role": payload.get("role", "analyst")})
        if validated["role"] == "admin":
            raise PermissionDenied("Public registration cannot create admin users.")
        created = self.users.create_user(
            {
                "name": validated["name"],
                "role": validated["role"],
                "password_hash": hash_password(validated["password"]),
            }
        )
        return self._serialize_user(created)

    def list_users(self, actor: dict) -> list[dict]:
        require_roles(actor, "admin")
        return [self._serialize_user(user) for user in self.users.list_users()]

    def create_user(self, actor: dict, payload: dict) -> dict:
        require_roles(actor, "admin")
        validated = validate_user_payload(payload)
        created = self.users.create_user(
            {
                "name": validated["name"],
                "role": validated["role"],
                "password_hash": hash_password(validated["password"]),
            }
        )
        return self._serialize_user(created)

    def get_user(self, actor: dict, user_id: int) -> dict:
        if actor["role"] != "admin" and actor["id"] != user_id:
            raise PermissionDenied("You can only access your own user profile.")
        return self._serialize_user(self.users.get_by_id(user_id))

    def update_user(self, actor: dict, user_id: int, payload: dict) -> dict:
        require_roles(actor, "admin")
        validated = validate_user_payload(payload, partial=True)
        update_data: dict[str, object] = {}
        if "name" in validated:
            update_data["name"] = validated["name"]
        if "role" in validated:
            update_data["role"] = validated["role"]
        if "password" in validated:
            update_data["password_hash"] = hash_password(validated["password"])
        updated = self.users.update_user(user_id, update_data)
        return self._serialize_user(updated)

    def get_me(self, actor: dict) -> dict:
        return self._serialize_user(actor)

    def list_transactions(self, actor: dict, query: dict) -> dict:
        page = parse_positive_int(query.get("page", "1"), "page")
        page_size = parse_positive_int(query.get("page_size", "10"), "page_size", maximum=100)
        filters = self._transaction_filters(actor, query)
        items, total = self.transactions.list_transactions(filters, page=page, page_size=page_size)
        return {
            "items": [self._serialize_transaction(item) for item in items],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
            },
        }

    def export_transactions(self, actor: dict, query: dict) -> dict:
        export_format = str(query.get("format", "csv")).strip().lower()
        if export_format not in {"csv", "json"}:
            raise ValidationError("format must be either 'csv' or 'json'.")

        filters = self._transaction_filters(actor, query)
        items = [self._serialize_transaction(item) for item in self.transactions.list_all_transactions(filters)]

        if export_format == "json":
            payload = {"transaction_count": len(items), "items": items}
            return {
                "filename": "transactions_export.json",
                "content_type": "application/json; charset=utf-8",
                "body": json.dumps(payload, indent=2).encode("utf-8"),
            }

        buffer = StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=[
                "id",
                "user_id",
                "user_name",
                "amount",
                "type",
                "category",
                "date",
                "notes",
                "created_at",
                "updated_at",
            ],
        )
        writer.writeheader()
        writer.writerows(items)
        return {
            "filename": "transactions_export.csv",
            "content_type": "text/csv; charset=utf-8",
            "body": buffer.getvalue().encode("utf-8"),
        }

    def get_transaction(self, actor: dict, transaction_id: int) -> dict:
        transaction = self.transactions.get_by_id(transaction_id)
        self._assert_transaction_visibility(actor, transaction)
        return self._serialize_transaction(transaction)

    def create_transaction(self, actor: dict, payload: dict) -> dict:
        payload = dict(payload)
        if actor["role"] != "admin" and "user_id" not in payload:
            payload["user_id"] = actor["id"]
        validated = validate_transaction_payload(payload)
        if actor["role"] == "admin":
            self.users.get_by_id(validated["user_id"])
        else:
            if validated["user_id"] != actor["id"]:
                raise PermissionDenied("You can only create transactions for your own account.")
        if actor["role"] != "admin":
            validated["user_id"] = actor["id"]
        created = self.transactions.create_transaction(validated)
        return self._serialize_transaction(created)

    def update_transaction(self, actor: dict, transaction_id: int, payload: dict) -> dict:
        current = self.transactions.get_by_id(transaction_id)
        self._assert_transaction_modification(actor, current)
        validated = validate_transaction_payload(payload, partial=True)
        if actor["role"] == "admin" and "user_id" in validated:
            self.users.get_by_id(validated["user_id"])
        elif actor["role"] != "admin" and "user_id" in validated and validated["user_id"] != actor["id"]:
            raise PermissionDenied("You cannot move transactions to another account.")
        updated = self.transactions.update_transaction(transaction_id, validated)
        return self._serialize_transaction(updated)

    def delete_transaction(self, actor: dict, transaction_id: int) -> dict:
        transaction = self.transactions.get_by_id(transaction_id)
        self._assert_transaction_modification(actor, transaction)
        self.transactions.delete_transaction(transaction_id)
        return {
            "message": "Transaction deleted successfully.",
            "transaction": self._serialize_transaction(transaction),
        }

    def get_overview(self, actor: dict, query: dict) -> dict:
        filters = self._summary_filters(actor, query, allow_advanced=actor["role"] in {"analyst", "admin"})
        result = self.transactions.overview(filters)
        total_income = result["total_income_cents"]
        total_expenses = result["total_expense_cents"]
        return {
            "transaction_count": result["transaction_count"],
            "total_income": cents_to_amount(total_income),
            "total_expenses": cents_to_amount(total_expenses),
            "current_balance": cents_to_amount(total_income - total_expenses),
        }

    def get_category_breakdown(self, actor: dict, query: dict) -> dict:
        require_roles(actor, "analyst", "admin")
        filters = self._summary_filters(actor, query, allow_advanced=True)
        if "type" in query:
            filters["entry_type"] = normalize_entry_type(query["type"])
        rows = self.transactions.category_breakdown(filters)
        return {
            "items": [
                {
                    "category": row["category"],
                    "transaction_count": row["transaction_count"],
                    "total_amount": cents_to_amount(row["total_cents"]),
                }
                for row in rows
            ]
        }

    def get_monthly_totals(self, actor: dict, query: dict) -> dict:
        require_roles(actor, "analyst", "admin")
        year = parse_positive_int(query.get("year"), "year", minimum=2000, maximum=2100)
        filters = self._summary_filters(actor, query, allow_advanced=True)
        rows = self.transactions.monthly_totals(filters, year=year)
        return {
            "year": year,
            "items": [
                {
                    "month": row["month"],
                    "income": cents_to_amount(row["income_cents"]),
                    "expenses": cents_to_amount(row["expense_cents"]),
                    "net": cents_to_amount(row["income_cents"] - row["expense_cents"]),
                }
                for row in rows
            ],
        }

    def get_recent_activity(self, actor: dict, query: dict) -> dict:
        limit = parse_positive_int(query.get("limit", "5"), "limit", maximum=25)
        filters = self._summary_filters(actor, query, allow_advanced=actor["role"] in {"analyst", "admin"})
        items = self.transactions.recent_activity(filters, limit=limit)
        return {"items": [self._serialize_transaction(item) for item in items]}

    def _serialize_user(self, user: dict) -> dict:
        return {
            "id": user["id"],
            "name": user["name"],
            "role": user["role"],
            "created_at": user["created_at"],
            "updated_at": user["updated_at"],
        }

    def _serialize_transaction(self, transaction: dict) -> dict:
        return {
            "id": transaction["id"],
            "user_id": transaction["user_id"],
            "user_name": transaction.get("user_name"),
            "amount": cents_to_amount(transaction["amount_cents"]),
            "type": transaction["entry_type"],
            "category": transaction["category"],
            "date": transaction["entry_date"],
            "notes": transaction["notes"],
            "created_at": transaction["created_at"],
            "updated_at": transaction["updated_at"],
        }

    def _assert_transaction_visibility(self, actor: dict, transaction: dict) -> None:
        if actor["role"] == "admin":
            return
        if transaction["user_id"] != actor["id"]:
            raise PermissionDenied("You can only access your own transactions.")

    def _assert_transaction_modification(self, actor: dict, transaction: dict) -> None:
        if actor["role"] == "admin":
            return
        if transaction["user_id"] != actor["id"]:
            raise PermissionDenied("You can only modify your own transactions.")

    def _target_user_id(self, actor: dict, raw_user_id: str | None) -> int | None:
        if actor["role"] == "admin":
            if raw_user_id is None:
                return None
            user_id = parse_positive_int(raw_user_id, "user_id")
            self.users.get_by_id(user_id)
            return user_id

        if raw_user_id is None:
            return actor["id"]

        user_id = parse_positive_int(raw_user_id, "user_id")
        if user_id != actor["id"]:
            raise PermissionDenied("You can only request data for your own account.")
        return actor["id"]

    def _summary_filters(self, actor: dict, query: dict, *, allow_advanced: bool) -> dict:
        filters = {"user_id": self._target_user_id(actor, query.get("user_id"))}
        if query.get("date_from"):
            if not allow_advanced:
                raise PermissionDenied("Your role cannot apply advanced summary filters.")
            filters["date_from"] = normalize_date(query["date_from"], "date_from")
        if query.get("date_to"):
            if not allow_advanced:
                raise PermissionDenied("Your role cannot apply advanced summary filters.")
            filters["date_to"] = normalize_date(query["date_to"], "date_to")
        if query.get("date_from") and query.get("date_to") and filters["date_from"] > filters["date_to"]:
            raise ValidationError("date_from must be before or equal to date_to.")
        return filters

    def _transaction_filters(self, actor: dict, query: dict) -> dict:
        filters = {"user_id": self._target_user_id(actor, query.get("user_id"))}
        advanced_keys = {"type", "category", "date_from", "date_to", "search"}
        if actor["role"] == "viewer":
            used_advanced = advanced_keys.intersection({key for key, value in query.items() if value})
            if used_advanced:
                raise PermissionDenied("Viewers can list transactions but cannot apply advanced filters.")

        if query.get("type"):
            filters["entry_type"] = normalize_entry_type(query["type"])
        if query.get("category"):
            filters["category"] = str(query["category"]).strip()
        if query.get("date_from"):
            filters["date_from"] = normalize_date(query["date_from"], "date_from")
        if query.get("date_to"):
            filters["date_to"] = normalize_date(query["date_to"], "date_to")
        if query.get("search"):
            search = str(query["search"]).strip()
            if len(search) > 100:
                raise ValidationError("search must be 100 characters or fewer.")
            filters["search"] = search
        if filters.get("date_from") and filters.get("date_to") and filters["date_from"] > filters["date_to"]:
            raise ValidationError("date_from must be before or equal to date_to.")
        return filters

    def _cookie_user_id(self, raw_cookie: str | None) -> str | None:
        if not raw_cookie:
            return None
        cookie = SimpleCookie()
        cookie.load(raw_cookie)
        morsel = cookie.get("finance_user_id")
        if morsel is None:
            return None
        value = morsel.value.strip()
        return value or None
