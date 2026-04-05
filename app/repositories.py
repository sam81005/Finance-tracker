from __future__ import annotations

import sqlite3
from pathlib import Path

from .database import adapt_placeholders, database_backend, managed_connection
from .errors import ConflictError, NotFoundError
from .models import utc_now_iso


class UserRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path
        self.backend = database_backend(db_path)

    def _query(self, sql: str) -> str:
        return adapt_placeholders(sql, self.db_path)

    def _is_integrity_error(self, exc: Exception) -> bool:
        return isinstance(exc, sqlite3.IntegrityError) or exc.__class__.__name__ in {"UniqueViolation", "IntegrityError"}

    def list_users(self) -> list[dict]:
        with managed_connection(self.db_path) as connection:
            rows = connection.execute(
                self._query("SELECT id, name, password_hash, role, created_at, updated_at FROM users ORDER BY id ASC")
            ).fetchall()
        return [dict(row) for row in rows]

    def get_by_id(self, user_id: int) -> dict:
        with managed_connection(self.db_path) as connection:
            row = connection.execute(
                self._query("SELECT id, name, password_hash, role, created_at, updated_at FROM users WHERE id = ?"),
                (user_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"User {user_id} was not found.")
        return dict(row)

    def get_by_name(self, name: str) -> dict:
        with managed_connection(self.db_path) as connection:
            row = connection.execute(
                self._query("""
                SELECT id, name, password_hash, role, created_at, updated_at
                FROM users
                WHERE LOWER(name) = LOWER(?)
                """),
                (name,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"User '{name}' was not found.")
        return dict(row)

    def create_user(self, data: dict) -> dict:
        now = utc_now_iso()
        try:
            with managed_connection(self.db_path) as connection:
                if self.backend == "postgres":
                    cursor = connection.execute(
                        self._query("""
                        INSERT INTO users (name, password_hash, role, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        RETURNING id
                        """),
                        (data["name"], data["password_hash"], data["role"], now, now),
                    )
                    user_id = cursor.fetchone()["id"]
                else:
                    cursor = connection.execute(
                        self._query("""
                        INSERT INTO users (name, password_hash, role, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """),
                        (data["name"], data["password_hash"], data["role"], now, now),
                    )
                    user_id = cursor.lastrowid
                connection.commit()
        except Exception as exc:
            if not self._is_integrity_error(exc):
                raise
            raise ConflictError("A user with that name already exists.") from exc
        return self.get_by_id(user_id)

    def update_user(self, user_id: int, data: dict) -> dict:
        current = self.get_by_id(user_id)
        updated = {**current, **data, "updated_at": utc_now_iso()}
        try:
            with managed_connection(self.db_path) as connection:
                connection.execute(
                    self._query("""
                    UPDATE users
                    SET name = ?, password_hash = ?, role = ?, updated_at = ?
                    WHERE id = ?
                    """),
                    (updated["name"], updated["password_hash"], updated["role"], updated["updated_at"], user_id),
                )
                connection.commit()
        except Exception as exc:
            if not self._is_integrity_error(exc):
                raise
            raise ConflictError("A user with that name already exists.") from exc
        return self.get_by_id(user_id)


class TransactionRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path
        self.backend = database_backend(db_path)

    def _query(self, sql: str) -> str:
        return adapt_placeholders(sql, self.db_path)

    def _base_query(self) -> str:
        return """
            FROM transactions
            JOIN users ON users.id = transactions.user_id
        """

    def _build_where_clause(self, filters: dict) -> tuple[str, list]:
        conditions: list[str] = []
        params: list[object] = []

        if filters.get("user_id") is not None:
            conditions.append("transactions.user_id = ?")
            params.append(filters["user_id"])
        if filters.get("entry_type"):
            conditions.append("transactions.entry_type = ?")
            params.append(filters["entry_type"])
        if filters.get("category"):
            conditions.append("LOWER(transactions.category) = LOWER(?)")
            params.append(filters["category"])
        if filters.get("date_from"):
            conditions.append("transactions.entry_date >= ?")
            params.append(filters["date_from"])
        if filters.get("date_to"):
            conditions.append("transactions.entry_date <= ?")
            params.append(filters["date_to"])
        if filters.get("search"):
            conditions.append(self._query("LOWER(COALESCE(transactions.notes, '')) LIKE LOWER(?)"))
            params.append(f"%{filters['search']}%")

        if not conditions:
            return "", params
        return " WHERE " + " AND ".join(conditions), params

    def list_transactions(self, filters: dict, *, page: int, page_size: int) -> tuple[list[dict], int]:
        where_clause, params = self._build_where_clause(filters)
        offset = (page - 1) * page_size

        select_sql = f"""
            SELECT
                transactions.id,
                transactions.user_id,
                users.name AS user_name,
                transactions.amount_cents,
                transactions.entry_type,
                transactions.category,
                transactions.entry_date,
                transactions.notes,
                transactions.created_at,
                transactions.updated_at
            {self._base_query()}
            {where_clause}
            ORDER BY transactions.entry_date DESC, transactions.id DESC
            LIMIT ? OFFSET ?
        """
        count_sql = f"SELECT COUNT(*) AS total {self._base_query()} {where_clause}"

        with managed_connection(self.db_path) as connection:
            rows = connection.execute(self._query(select_sql), [*params, page_size, offset]).fetchall()
            total = connection.execute(self._query(count_sql), params).fetchone()["total"]

        return [dict(row) for row in rows], total

    def list_all_transactions(self, filters: dict) -> list[dict]:
        where_clause, params = self._build_where_clause(filters)
        sql = f"""
            SELECT
                transactions.id,
                transactions.user_id,
                users.name AS user_name,
                transactions.amount_cents,
                transactions.entry_type,
                transactions.category,
                transactions.entry_date,
                transactions.notes,
                transactions.created_at,
                transactions.updated_at
            {self._base_query()}
            {where_clause}
            ORDER BY transactions.entry_date DESC, transactions.id DESC
        """
        with managed_connection(self.db_path) as connection:
            rows = connection.execute(self._query(sql), params).fetchall()
        return [dict(row) for row in rows]

    def get_by_id(self, transaction_id: int) -> dict:
        with managed_connection(self.db_path) as connection:
            row = connection.execute(
                self._query(f"""
                SELECT
                    transactions.id,
                    transactions.user_id,
                    users.name AS user_name,
                    transactions.amount_cents,
                    transactions.entry_type,
                    transactions.category,
                    transactions.entry_date,
                    transactions.notes,
                    transactions.created_at,
                    transactions.updated_at
                {self._base_query()}
                WHERE transactions.id = ?
                """),
                (transaction_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Transaction {transaction_id} was not found.")
        return dict(row)

    def create_transaction(self, data: dict) -> dict:
        now = utc_now_iso()
        with managed_connection(self.db_path) as connection:
            if self.backend == "postgres":
                cursor = connection.execute(
                    self._query("""
                    INSERT INTO transactions (
                        user_id, amount_cents, entry_type, category, entry_date, notes, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """),
                    (
                        data["user_id"],
                        data["amount_cents"],
                        data["entry_type"],
                        data["category"],
                        data["entry_date"],
                        data.get("notes"),
                        now,
                        now,
                    ),
                )
                transaction_id = cursor.fetchone()["id"]
            else:
                cursor = connection.execute(
                    self._query("""
                    INSERT INTO transactions (
                        user_id, amount_cents, entry_type, category, entry_date, notes, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """),
                    (
                        data["user_id"],
                        data["amount_cents"],
                        data["entry_type"],
                        data["category"],
                        data["entry_date"],
                        data.get("notes"),
                        now,
                        now,
                    ),
                )
                transaction_id = cursor.lastrowid
            connection.commit()
        return self.get_by_id(transaction_id)

    def update_transaction(self, transaction_id: int, data: dict) -> dict:
        current = self.get_by_id(transaction_id)
        merged = {**current, **data, "updated_at": utc_now_iso()}
        with managed_connection(self.db_path) as connection:
            connection.execute(
                self._query("""
                UPDATE transactions
                SET
                    user_id = ?,
                    amount_cents = ?,
                    entry_type = ?,
                    category = ?,
                    entry_date = ?,
                    notes = ?,
                    updated_at = ?
                WHERE id = ?
                """),
                (
                    merged["user_id"],
                    merged["amount_cents"],
                    merged["entry_type"],
                    merged["category"],
                    merged["entry_date"],
                    merged.get("notes"),
                    merged["updated_at"],
                    transaction_id,
                ),
            )
            connection.commit()
        return self.get_by_id(transaction_id)

    def delete_transaction(self, transaction_id: int) -> None:
        self.get_by_id(transaction_id)
        with managed_connection(self.db_path) as connection:
            connection.execute(self._query("DELETE FROM transactions WHERE id = ?"), (transaction_id,))
            connection.commit()

    def overview(self, filters: dict) -> dict:
        where_clause, params = self._build_where_clause(filters)
        sql = f"""
            SELECT
                COUNT(*) AS transaction_count,
                COALESCE(SUM(CASE WHEN entry_type = 'income' THEN amount_cents ELSE 0 END), 0) AS total_income_cents,
                COALESCE(SUM(CASE WHEN entry_type = 'expense' THEN amount_cents ELSE 0 END), 0) AS total_expense_cents
            FROM transactions
            {where_clause}
        """
        with managed_connection(self.db_path) as connection:
            row = connection.execute(self._query(sql), params).fetchone()
        return dict(row)

    def category_breakdown(self, filters: dict) -> list[dict]:
        where_clause, params = self._build_where_clause(filters)
        sql = f"""
            SELECT
                category,
                COUNT(*) AS transaction_count,
                SUM(amount_cents) AS total_cents
            FROM transactions
            {where_clause}
            GROUP BY category
            ORDER BY total_cents DESC, category ASC
        """
        with managed_connection(self.db_path) as connection:
            rows = connection.execute(self._query(sql), params).fetchall()
        return [dict(row) for row in rows]

    def monthly_totals(self, filters: dict, *, year: int) -> list[dict]:
        filters = {**filters, "date_from": f"{year}-01-01", "date_to": f"{year}-12-31"}
        where_clause, params = self._build_where_clause(filters)
        if self.backend == "postgres":
            sql = f"""
                SELECT
                    CAST(SUBSTRING(entry_date FROM 6 FOR 2) AS INTEGER) AS month,
                    COALESCE(SUM(CASE WHEN entry_type = 'income' THEN amount_cents ELSE 0 END), 0) AS income_cents,
                    COALESCE(SUM(CASE WHEN entry_type = 'expense' THEN amount_cents ELSE 0 END), 0) AS expense_cents
                FROM transactions
                {where_clause}
                GROUP BY CAST(SUBSTRING(entry_date FROM 6 FOR 2) AS INTEGER)
                ORDER BY month ASC
            """
        else:
            sql = f"""
                SELECT
                    CAST(strftime('%m', entry_date) AS INTEGER) AS month,
                    COALESCE(SUM(CASE WHEN entry_type = 'income' THEN amount_cents ELSE 0 END), 0) AS income_cents,
                    COALESCE(SUM(CASE WHEN entry_type = 'expense' THEN amount_cents ELSE 0 END), 0) AS expense_cents
                FROM transactions
                {where_clause}
                GROUP BY CAST(strftime('%m', entry_date) AS INTEGER)
                ORDER BY month ASC
            """
        with managed_connection(self.db_path) as connection:
            rows = connection.execute(self._query(sql), params).fetchall()
        return [dict(row) for row in rows]

    def recent_activity(self, filters: dict, *, limit: int) -> list[dict]:
        where_clause, params = self._build_where_clause(filters)
        sql = f"""
            SELECT
                transactions.id,
                transactions.user_id,
                users.name AS user_name,
                transactions.amount_cents,
                transactions.entry_type,
                transactions.category,
                transactions.entry_date,
                transactions.notes,
                transactions.created_at,
                transactions.updated_at
            {self._base_query()}
            {where_clause}
            ORDER BY transactions.entry_date DESC, transactions.id DESC
            LIMIT ?
        """
        with managed_connection(self.db_path) as connection:
            rows = connection.execute(self._query(sql), [*params, limit]).fetchall()
        return [dict(row) for row in rows]
