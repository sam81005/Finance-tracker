from __future__ import annotations

from pathlib import Path

from .database import initialize_database
from .errors import NotFoundError
from .models import hash_password
from .repositories import TransactionRepository, UserRepository

DEFAULT_USERS = [
    {"name": "Samarth K", "role": "admin", "password": "samarth123"},
    {"name": "Anika Analyst", "role": "analyst", "password": "analyst123"},
    {"name": "Victor Viewer", "role": "viewer", "password": "viewer123"},
]

DEFAULT_TRANSACTIONS = [
    {"user_id": 2, "amount_cents": 850000, "entry_type": "income", "category": "Salary", "entry_date": "2026-03-01", "notes": "March salary"},
    {"user_id": 2, "amount_cents": 120000, "entry_type": "expense", "category": "Rent", "entry_date": "2026-03-03", "notes": "Apartment rent"},
    {"user_id": 2, "amount_cents": 18000, "entry_type": "expense", "category": "Internet", "entry_date": "2026-03-05", "notes": "Monthly broadband"},
    {"user_id": 3, "amount_cents": 320000, "entry_type": "income", "category": "Freelance", "entry_date": "2026-03-04", "notes": "Design retainer"},
    {"user_id": 3, "amount_cents": 7600, "entry_type": "expense", "category": "Coffee", "entry_date": "2026-03-04", "notes": "Client meeting"},
]


def ensure_seed_data(db_path: str | Path) -> None:
    initialize_database(db_path)
    users = UserRepository(db_path)
    transactions = TransactionRepository(db_path)

    existing_users = users.list_users()
    if existing_users:
        _repair_default_users(users)
        return

    for user in DEFAULT_USERS:
        users.create_user(
            {
                "name": user["name"],
                "role": user["role"],
                "password_hash": hash_password(user["password"]),
            }
        )
    for transaction in DEFAULT_TRANSACTIONS:
        transactions.create_transaction(transaction)


def _repair_default_users(users: UserRepository) -> None:
    try:
        legacy_admin = users.get_by_name("Alice Admin")
    except NotFoundError:
        legacy_admin = None

    if legacy_admin is not None:
        users.update_user(
            legacy_admin["id"],
            {
                "name": "Samarth K",
                "role": "admin",
                "password_hash": hash_password("samarth123"),
            },
        )

    for user in DEFAULT_USERS[1:]:
        try:
            existing = users.get_by_name(user["name"])
        except NotFoundError:
            continue
        if not existing.get("password_hash"):
            users.update_user(
                existing["id"],
                {
                    "name": user["name"],
                    "role": user["role"],
                    "password_hash": hash_password(user["password"]),
                },
            )
