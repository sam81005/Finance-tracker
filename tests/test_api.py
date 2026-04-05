from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.api import FinanceAPI
from app.seed import ensure_seed_data


class FinanceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_finance.db"
        ensure_seed_data(self.db_path)
        self.api = FinanceAPI(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def request(self, method: str, path: str, *, user_id: int | None = None, body: dict | None = None) -> tuple[int, dict]:
        headers = {}
        if user_id is not None:
            headers["X-User-Id"] = str(user_id)
        payload = json.dumps(body).encode("utf-8") if body is not None else b""
        response = self.api.handle(method, path, headers, payload)
        return response.status_code, json.loads(response.body.decode("utf-8"))

    def test_health_is_public(self) -> None:
        status, payload = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

    def test_browser_dashboard_redirects_to_login_without_session(self) -> None:
        response = self.api.handle("GET", "/", {"Accept": "text/html"}, b"")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/login")

    def test_login_page_renders_html(self) -> None:
        response = self.api.handle("GET", "/login", {"Accept": "text/html"}, b"")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "text/html; charset=utf-8")
        self.assertIn("Sign In", response.body.decode("utf-8"))

    def test_browser_login_sets_cookie_for_named_user(self) -> None:
        response = self.api.handle(
            "POST",
            "/login",
            {
                "Accept": "text/html",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            "name=Anika+Analyst&password=analyst123".encode("utf-8"),
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/")
        self.assertIn("finance_user_id=2", response.headers["Set-Cookie"])

    def test_browser_registration_creates_user_and_logs_them_in(self) -> None:
        response = self.api.handle(
            "POST",
            "/register",
            {
                "Accept": "text/html",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            "name=Riya+Sharma&password=riya1234&role=analyst".encode("utf-8"),
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/")
        self.assertIn("finance_user_id=", response.headers["Set-Cookie"])

        status, payload = self.request("GET", "/transactions", user_id=4)
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["pagination"]["total"], 0)

    def test_browser_logout_clears_cookie(self) -> None:
        response = self.api.handle("GET", "/logout", {"Accept": "text/html", "Cookie": "finance_user_id=2"}, b"")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertIn("Max-Age=0", response.headers["Set-Cookie"])

    def test_browser_session_can_access_dashboard(self) -> None:
        response = self.api.handle("GET", "/", {"Accept": "text/html", "Cookie": "finance_user_id=2"}, b"")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ExpensePilot Dashboard", response.body.decode("utf-8"))
        self.assertIn("Anika Analyst", response.body.decode("utf-8"))

    def test_requests_without_header_default_to_admin(self) -> None:
        status, payload = self.request("GET", "/me")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["name"], "Samarth K")
        self.assertEqual(payload["data"]["role"], "admin")

    def test_me_returns_authenticated_user(self) -> None:
        status, payload = self.request("GET", "/me", user_id=2)
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["name"], "Anika Analyst")
        self.assertEqual(payload["data"]["role"], "analyst")

    def test_viewer_cannot_apply_advanced_transaction_filters(self) -> None:
        status, payload = self.request("GET", "/transactions?category=Coffee", user_id=3)
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "permission_denied")

    def test_viewer_cannot_access_other_users_transaction(self) -> None:
        status, payload = self.request("GET", "/transactions/1", user_id=3)
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "permission_denied")

    def test_transactions_list_includes_pagination(self) -> None:
        status, payload = self.request("GET", "/transactions?page=1&page_size=1", user_id=2)
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["data"]["items"]), 1)
        self.assertEqual(payload["data"]["pagination"]["page"], 1)
        self.assertEqual(payload["data"]["pagination"]["page_size"], 1)
        self.assertGreaterEqual(payload["data"]["pagination"]["total"], 2)

    def test_admin_can_create_update_and_delete_transaction(self) -> None:
        create_status, created = self.request(
            "POST",
            "/transactions",
            user_id=1,
            body={
                "user_id": 3,
                "amount": "42.50",
                "type": "expense",
                "category": "Books",
                "date": "2026-03-10",
                "notes": "Finance reading",
            },
        )
        self.assertEqual(create_status, 201)
        transaction_id = created["data"]["id"]
        self.assertEqual(created["data"]["amount"], "42.50")

        update_status, updated = self.request(
            "PUT",
            f"/transactions/{transaction_id}",
            user_id=1,
            body={"category": "Learning", "notes": "Backend assignment material"},
        )
        self.assertEqual(update_status, 200)
        self.assertEqual(updated["data"]["category"], "Learning")

        delete_status, deleted = self.request("DELETE", f"/transactions/{transaction_id}", user_id=1)
        self.assertEqual(delete_status, 200)
        self.assertEqual(deleted["data"]["message"], "Transaction deleted successfully.")

        fetch_status, fetch_payload = self.request("GET", f"/transactions/{transaction_id}", user_id=1)
        self.assertEqual(fetch_status, 404)
        self.assertEqual(fetch_payload["error"]["code"], "not_found")

    def test_browser_form_can_create_transaction_with_user_session(self) -> None:
        response = self.api.handle(
            "POST",
            "/transactions",
            {
                "Accept": "text/html",
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": "finance_user_id=2",
            },
            (
                "amount=99.50&type=expense&category=Food&date=2026-03-22&notes=Lunch"
            ).encode("utf-8"),
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/transactions")

        status, payload = self.request("GET", "/transactions", user_id=2)
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["pagination"]["total"], 4)

    def test_non_admin_can_update_and_delete_own_transaction(self) -> None:
        create_status, created = self.request(
            "POST",
            "/transactions",
            user_id=2,
            body={
                "amount": "15.00",
                "type": "expense",
                "category": "Snacks",
                "date": "2026-03-08",
                "notes": "Tea break",
                "user_id": 2,
            },
        )
        self.assertEqual(create_status, 201)
        transaction_id = created["data"]["id"]

        update_status, updated = self.request(
            "PUT",
            f"/transactions/{transaction_id}",
            user_id=2,
            body={"category": "Team Snacks"},
        )
        self.assertEqual(update_status, 200)
        self.assertEqual(updated["data"]["category"], "Team Snacks")

        delete_status, deleted = self.request("DELETE", f"/transactions/{transaction_id}", user_id=2)
        self.assertEqual(delete_status, 200)
        self.assertEqual(deleted["data"]["message"], "Transaction deleted successfully.")

    def test_analyst_can_view_detailed_breakdown(self) -> None:
        status, payload = self.request("GET", "/summaries/category-breakdown?type=expense", user_id=2)
        self.assertEqual(status, 200)
        self.assertTrue(payload["data"]["items"])
        self.assertIn("category", payload["data"]["items"][0])
        self.assertIn("total_amount", payload["data"]["items"][0])

    def test_analyst_can_export_transactions_as_csv(self) -> None:
        response = self.api.handle("GET", "/transactions/export?format=csv", {"X-User-Id": "2"}, b"")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "text/csv; charset=utf-8")
        self.assertIn("attachment; filename=", response.headers["Content-Disposition"])

        lines = response.body.decode("utf-8").splitlines()
        self.assertEqual(
            lines[0],
            "id,user_id,user_name,amount,type,category,date,notes,created_at,updated_at",
        )
        self.assertTrue(any("Salary" in line for line in lines[1:]))

    def test_invalid_transaction_payload_returns_validation_error(self) -> None:
        status, payload = self.request(
            "POST",
            "/transactions",
            user_id=1,
            body={
                "user_id": 2,
                "amount": "-1.00",
                "type": "expense",
                "category": "Rent",
                "date": "2026-03-10",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "validation_error")


if __name__ == "__main__":
    unittest.main()
