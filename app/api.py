from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from html import escape
from http import HTTPStatus
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from .config import DEFAULT_DB_PATH
from .errors import AppError, NotFoundError, ValidationError
from .services import FinanceService


@dataclass
class ApiResponse:
    status_code: int
    payload: dict | list | None = None
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = "application/json; charset=utf-8"
    raw_body: bytes | None = None

    @property
    def body(self) -> bytes:
        if self.raw_body is not None:
            return self.raw_body
        return json.dumps(self.payload or {}, indent=2).encode("utf-8")


class FinanceAPI:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.service = FinanceService(db_path)

    def handle(self, method: str, raw_path: str, headers: dict | None = None, body: bytes | None = None) -> ApiResponse:
        method = method.upper()
        headers = {str(key).lower(): value for key, value in (headers or {}).items()}
        body = body or b""
        parsed_url = urlsplit(raw_path)
        path = parsed_url.path.rstrip("/") or "/"
        query = {key: values[-1] for key, values in parse_qs(parsed_url.query).items()}
        wants_html = self._wants_html(headers, path)

        try:
            if method == "GET" and path == "/health":
                return ApiResponse(200, {"status": "ok"})

            if wants_html and path in {"/login", "/logout", "/register"}:
                return self._dispatch_public_html(method, path, headers, body)

            actor = self.service.authenticate(headers, allow_default_admin=not wants_html)
            return self._dispatch(actor, method, path, query, headers, body, wants_html)
        except AppError as exc:
            if wants_html:
                if exc.code == "authentication_error":
                    return self._redirect("/login")
                return self._html_page(
                    f"Error {exc.status_code}",
                    f"""
                    <section class="card">
                      <h1>{escape(exc.code.replace("_", " ").title())}</h1>
                      <p>{escape(exc.message)}</p>
                      <p><a href="/">Back to dashboard</a></p>
                    </section>
                    """,
                    status_code=exc.status_code,
                )
            payload = {"error": {"code": exc.code, "message": exc.message, "details": exc.details}}
            return ApiResponse(exc.status_code, payload)
        except Exception:
            if wants_html:
                return self._html_page(
                    "Server Error",
                    """
                    <section class="card">
                      <h1>Internal Server Error</h1>
                      <p>An unexpected error occurred.</p>
                      <p><a href="/">Back to dashboard</a></p>
                    </section>
                    """,
                    status_code=500,
                )
            return ApiResponse(
                500,
                {"error": {"code": "internal_server_error", "message": "An unexpected error occurred."}},
            )

    def wsgi_app(self, environ: dict, start_response: Callable) -> list[bytes]:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(length) if length > 0 else b""
        headers = self._headers_from_environ(environ)
        path = environ.get("PATH_INFO", "/")
        query_string = environ.get("QUERY_STRING")
        raw_path = f"{path}?{query_string}" if query_string else path
        response = self.handle(environ.get("REQUEST_METHOD", "GET"), raw_path, headers, body)
        status = f"{response.status_code} {HTTPStatus(response.status_code).phrase}"
        response_headers = [("Content-Type", response.content_type)]
        response_headers.extend(response.headers.items())
        start_response(status, response_headers)
        return [response.body]

    def _dispatch(
        self,
        actor: dict,
        method: str,
        path: str,
        query: dict,
        headers: dict,
        body: bytes,
        wants_html: bool,
    ) -> ApiResponse:
        if wants_html:
            html_response = self._dispatch_html(actor, method, path, query, headers, body)
            if html_response is not None:
                return html_response

        if method == "GET" and path == "/me":
            return ApiResponse(200, {"data": self.service.get_me(actor)})

        if method == "GET" and path == "/users":
            return ApiResponse(200, {"data": self.service.list_users(actor)})
        if method == "POST" and path == "/users":
            return ApiResponse(201, {"data": self.service.create_user(actor, self._body_data(headers, body))})

        user_match = re.fullmatch(r"/users/(\d+)", path)
        if user_match:
            user_id = int(user_match.group(1))
            if method == "GET":
                return ApiResponse(200, {"data": self.service.get_user(actor, user_id)})
            if method == "PUT":
                return ApiResponse(200, {"data": self.service.update_user(actor, user_id, self._body_data(headers, body))})
            raise NotFoundError(f"No route found for {method} {path}.")

        if method == "GET" and path == "/transactions":
            return ApiResponse(200, {"data": self.service.list_transactions(actor, query)})
        if method == "GET" and path == "/transactions/export":
            export = self.service.export_transactions(actor, query)
            return ApiResponse(
                200,
                headers={"Content-Disposition": f'attachment; filename="{export["filename"]}"'},
                content_type=export["content_type"],
                raw_body=export["body"],
            )
        if method == "POST" and path == "/transactions":
            return ApiResponse(201, {"data": self.service.create_transaction(actor, self._body_data(headers, body))})

        transaction_match = re.fullmatch(r"/transactions/(\d+)", path)
        if transaction_match:
            transaction_id = int(transaction_match.group(1))
            if method == "GET":
                return ApiResponse(200, {"data": self.service.get_transaction(actor, transaction_id)})
            if method == "PUT":
                return ApiResponse(
                    200,
                    {"data": self.service.update_transaction(actor, transaction_id, self._body_data(headers, body))},
                )
            if method == "DELETE":
                return ApiResponse(200, {"data": self.service.delete_transaction(actor, transaction_id)})
            raise NotFoundError(f"No route found for {method} {path}.")

        if method == "GET" and path == "/summaries/overview":
            return ApiResponse(200, {"data": self.service.get_overview(actor, query)})
        if method == "GET" and path == "/summaries/category-breakdown":
            return ApiResponse(200, {"data": self.service.get_category_breakdown(actor, query)})
        if method == "GET" and path == "/summaries/monthly-totals":
            return ApiResponse(200, {"data": self.service.get_monthly_totals(actor, query)})
        if method == "GET" and path == "/summaries/recent-activity":
            return ApiResponse(200, {"data": self.service.get_recent_activity(actor, query)})

        raise NotFoundError(f"No route found for {method} {path}.")

    def _dispatch_html(
        self,
        actor: dict,
        method: str,
        path: str,
        query: dict,
        headers: dict,
        body: bytes,
    ) -> ApiResponse | None:
        if method == "GET" and path == "/":
            return self._render_dashboard(actor)

        if method == "GET" and path == "/me":
            user = self.service.get_me(actor)
            content = f"""
            <section class="card">
              <h1>Current User</h1>
              <dl class="key-value">
                <dt>ID</dt><dd>{user["id"]}</dd>
                <dt>Name</dt><dd>{escape(user["name"])}</dd>
                <dt>Role</dt><dd>{escape(user["role"])}</dd>
                <dt>Created</dt><dd>{escape(user["created_at"])}</dd>
                <dt>Updated</dt><dd>{escape(user["updated_at"])}</dd>
              </dl>
            </section>
            """
            return self._html_page("Current User", content, actor=actor)

        if method == "GET" and path == "/users":
            users = self.service.list_users(actor)
            rows = "".join(
                f"<tr><td>{user['id']}</td><td>{escape(user['name'])}</td><td>{escape(user['role'])}</td></tr>"
                for user in users
            )
            content = f"""
            <section class="card">
              <h1>Users</h1>
              <table>
                <thead><tr><th>ID</th><th>Name</th><th>Role</th></tr></thead>
                <tbody>{rows}</tbody>
              </table>
            </section>
            """
            return self._html_page("Users", content, actor=actor)

        if method == "GET" and path == "/transactions":
            return self._render_transactions_page(actor, query)

        if method == "POST" and path == "/transactions":
            payload = self._body_data(headers, body)
            self.service.create_transaction(actor, payload)
            return self._redirect("/transactions")

        edit_match = re.fullmatch(r"/transactions/(\d+)/edit", path)
        if edit_match:
            transaction_id = int(edit_match.group(1))
            if method == "GET":
                return self._render_transaction_edit_page(actor, transaction_id)
            if method == "POST":
                payload = self._body_data(headers, body)
                self.service.update_transaction(actor, transaction_id, payload)
                return self._redirect(f"/transactions/{transaction_id}")

        delete_match = re.fullmatch(r"/transactions/(\d+)/delete", path)
        if delete_match and method == "POST":
            transaction_id = int(delete_match.group(1))
            self.service.delete_transaction(actor, transaction_id)
            return self._redirect("/transactions")

        transaction_match = re.fullmatch(r"/transactions/(\d+)", path)
        if transaction_match and method == "GET":
            return self._render_transaction_detail_page(actor, int(transaction_match.group(1)))

        if method == "GET" and path == "/summaries/overview":
            summary = self.service.get_overview(actor, query)
            content = f"""
            <section class="grid">
              <article class="card stat"><h2>Total Income</h2><p>{escape(summary["total_income"])}</p></article>
              <article class="card stat"><h2>Total Expenses</h2><p>{escape(summary["total_expenses"])}</p></article>
              <article class="card stat"><h2>Current Balance</h2><p>{escape(summary["current_balance"])}</p></article>
              <article class="card stat"><h2>Transactions</h2><p>{summary["transaction_count"]}</p></article>
            </section>
            """
            return self._html_page("Overview", content, actor=actor)

        if method == "GET" and path == "/summaries/category-breakdown":
            breakdown = self.service.get_category_breakdown(actor, query)
            rows = "".join(
                f"<tr><td>{escape(item['category'])}</td><td>{item['transaction_count']}</td>"
                f"<td>{escape(item['total_amount'])}</td></tr>"
                for item in breakdown["items"]
            )
            content = f"""
            <section class="card">
              <h1>Category Breakdown</h1>
              <table>
                <thead><tr><th>Category</th><th>Count</th><th>Total</th></tr></thead>
                <tbody>{rows}</tbody>
              </table>
            </section>
            """
            return self._html_page("Category Breakdown", content, actor=actor)

        if method == "GET" and path == "/summaries/monthly-totals":
            monthly_query = {**query}
            monthly_query.setdefault("year", str(date.today().year))
            monthly = self.service.get_monthly_totals(actor, monthly_query)
            rows = "".join(
                f"<tr><td>{item['month']}</td><td>{escape(item['income'])}</td>"
                f"<td>{escape(item['expenses'])}</td><td>{escape(item['net'])}</td></tr>"
                for item in monthly["items"]
            )
            content = f"""
            <section class="card">
              <h1>Monthly Totals ({monthly["year"]})</h1>
              <table>
                <thead><tr><th>Month</th><th>Income</th><th>Expenses</th><th>Net</th></tr></thead>
                <tbody>{rows}</tbody>
              </table>
            </section>
            """
            return self._html_page("Monthly Totals", content, actor=actor)

        if method == "GET" and path == "/summaries/recent-activity":
            recent = self.service.get_recent_activity(actor, query)
            rows = "".join(self._transaction_row_html(item, include_actions=False) for item in recent["items"])
            content = f"""
            <section class="card">
              <h1>Recent Activity</h1>
              <table>
                <thead>
                  <tr><th>ID</th><th>User</th><th>Amount</th><th>Type</th><th>Category</th><th>Date</th><th>Notes</th></tr>
                </thead>
                <tbody>{rows}</tbody>
              </table>
            </section>
            """
            return self._html_page("Recent Activity", content, actor=actor)

        return None

    def _dispatch_public_html(self, method: str, path: str, headers: dict, body: bytes) -> ApiResponse:
        if method == "GET" and path == "/login":
            return self._render_login_page()
        if method == "POST" and path == "/login":
            payload = self._body_data(headers, body)
            user = self.service.authenticate_credentials(payload.get("name"), payload.get("password"))
            return self._redirect("/", headers={"Set-Cookie": self._session_cookie(user["id"])})
        if method == "GET" and path == "/register":
            return self._render_register_page()
        if method == "POST" and path == "/register":
            created = self.service.register_user(self._body_data(headers, body))
            return self._redirect("/", headers={"Set-Cookie": self._session_cookie(created["id"])})
        if path == "/logout":
            return self._redirect("/login", headers={"Set-Cookie": self._clear_session_cookie()})
        raise NotFoundError(f"No route found for {method} {path}.")

    def _body_data(self, headers: dict, body: bytes) -> dict:
        content_type = headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
        if content_type == "application/x-www-form-urlencoded":
            if not body:
                raise ValidationError("A form body is required.")
            return {key: values[-1] for key, values in parse_qs(body.decode("utf-8"), keep_blank_values=True).items()}
        return self._json_body(body)

    def _json_body(self, body: bytes) -> dict:
        if not body:
            raise ValidationError("A JSON request body is required.")
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError("Request body must be valid JSON.") from exc

    def _headers_from_environ(self, environ: dict) -> dict:
        headers: dict[str, str] = {}
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                normalized = key[5:].replace("_", "-").lower()
                headers[normalized] = value
        if environ.get("CONTENT_TYPE"):
            headers["content-type"] = environ["CONTENT_TYPE"]
        if environ.get("CONTENT_LENGTH"):
            headers["content-length"] = environ["CONTENT_LENGTH"]
        return headers

    def _wants_html(self, headers: dict, path: str) -> bool:
        if path == "/":
            return True
        accept = headers.get("accept", "")
        return "text/html" in accept

    def _render_dashboard(self, actor: dict) -> ApiResponse:
        content = f"""
        <section class="grid">
          <article class="card">
            <h1>ExpensePilot Dashboard</h1>
            <p>This browser view lets you use the project without curl or custom headers.</p>
            <p class="badge">Current role: {escape(actor["role"])} ({escape(actor["name"])})</p>
          </article>
          <article class="card">
            <h2>Quick Links</h2>
            <ul>
              <li><a href="/me">My profile</a></li>
              <li><a href="/users">Users</a></li>
              <li><a href="/transactions">Transactions</a></li>
              <li><a href="/summaries/overview">Overview</a></li>
              <li><a href="/summaries/category-breakdown?type=expense">Category breakdown</a></li>
              <li><a href="/summaries/monthly-totals?year={date.today().year}">Monthly totals</a></li>
              <li><a href="/summaries/recent-activity">Recent activity</a></li>
              <li><a href="/transactions/export?format=csv">Download CSV export</a></li>
            </ul>
          </article>
        </section>
        """
        return self._html_page("ExpensePilot", content, actor=actor)

    def _render_login_page(self) -> ApiResponse:
        content = f"""
        <section class="grid">
          <article class="card">
            <h1>Sign In</h1>
            <p>Log in with your saved account. Each user sees their own transactions and history.</p>
            <form method="post" action="/login" class="form-grid">
              <label>Name<input name="name" placeholder="Samarth K" required></label>
              <label>Password<input type="password" name="password" placeholder="Enter password" required></label>
              <button type="submit">Log In</button>
            </form>
            <p class="muted">Need an account? <a href="/register">Create one here</a>.</p>
          </article>
          <article class="card">
            <h2>Demo Accounts</h2>
            <ul>
              <li>Samarth K / samarth123</li>
              <li>Anika Analyst / analyst123</li>
              <li>Victor Viewer / viewer123</li>
            </ul>
          </article>
        </section>
        """
        return self._html_page("Login", content)

    def _render_register_page(self) -> ApiResponse:
        content = """
        <section class="grid">
          <article class="card">
            <h1>Create Account</h1>
            <p>Register with your name and password. Your transactions and history will be stored against your own account.</p>
            <form method="post" action="/register" class="form-grid">
              <label>Name<input name="name" placeholder="Your name" required></label>
              <label>Password<input type="password" name="password" placeholder="Minimum 8 characters" required></label>
              <label>Role
                <select name="role">
                  <option value="analyst">analyst</option>
                  <option value="viewer">viewer</option>
                </select>
              </label>
              <button type="submit">Create Account</button>
            </form>
            <p class="muted">Already have an account? <a href="/login">Log in</a>.</p>
          </article>
        </section>
        """
        return self._html_page("Register", content)

    def _render_transactions_page(self, actor: dict, query: dict) -> ApiResponse:
        data = self.service.list_transactions(actor, query)
        items = data["items"]
        pagination = data["pagination"]

        user_options = ""
        if actor["role"] == "admin":
            user_options = "".join(
                f'<option value="{user["id"]}">{escape(user["name"])} ({escape(user["role"])})</option>'
                for user in self.service.list_users(actor)
            )

        filter_form = f"""
        <section class="card">
          <h1>Transactions</h1>
          <form method="get" action="/transactions" class="form-grid">
            <label>Type<input name="type" value="{escape(query.get("type", ""))}" placeholder="income or expense"></label>
            <label>Category<input name="category" value="{escape(query.get("category", ""))}" placeholder="Rent"></label>
            <label>Date From<input name="date_from" value="{escape(query.get("date_from", ""))}" placeholder="YYYY-MM-DD"></label>
            <label>Date To<input name="date_to" value="{escape(query.get("date_to", ""))}" placeholder="YYYY-MM-DD"></label>
            <label>Search<input name="search" value="{escape(query.get("search", ""))}" placeholder="notes text"></label>
            <label>Page Size<input name="page_size" value="{escape(query.get("page_size", "10"))}" placeholder="10"></label>
            <button type="submit">Apply Filters</button>
          </form>
          <p class="muted">Page {pagination["page"]} of transactions, total records: {pagination["total"]}</p>
        </section>
        """

        create_form = ""
        if actor["role"] == "admin":
            create_form = f"""
            <section class="card">
              <h2>Create Transaction</h2>
              <form method="post" action="/transactions" class="form-grid">
                <label>User<select name="user_id">{user_options}</select></label>
                <label>Amount<input name="amount" placeholder="125.00" required></label>
                <label>Type
                  <select name="type">
                    <option value="expense">expense</option>
                    <option value="income">income</option>
                  </select>
                </label>
                <label>Category<input name="category" placeholder="Travel" required></label>
                <label>Date<input name="date" placeholder="YYYY-MM-DD" required></label>
                <label>Notes<input name="notes" placeholder="Optional note"></label>
                <button type="submit">Create Transaction</button>
              </form>
            </section>
            """
        else:
            create_form = f"""
            <section class="card">
              <h2>Add Your Transaction</h2>
              <form method="post" action="/transactions" class="form-grid">
                <input type="hidden" name="user_id" value="{actor["id"]}">
                <label>Amount<input name="amount" placeholder="125.00" required></label>
                <label>Type
                  <select name="type">
                    <option value="expense">expense</option>
                    <option value="income">income</option>
                  </select>
                </label>
                <label>Category<input name="category" placeholder="Groceries" required></label>
                <label>Date<input name="date" placeholder="YYYY-MM-DD" required></label>
                <label>Notes<input name="notes" placeholder="Optional note"></label>
                <button type="submit">Save Transaction</button>
              </form>
            </section>
            """

        rows = "".join(self._transaction_row_html(item, include_actions=actor["role"] == "admin" or item["user_id"] == actor["id"]) for item in items)
        table = f"""
        <section class="card">
          <table>
            <thead>
              <tr>
                <th>ID</th><th>User</th><th>Amount</th><th>Type</th><th>Category</th><th>Date</th><th>Notes</th><th>Actions</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </section>
        """
        return self._html_page("Transactions", filter_form + create_form + table, actor=actor)

    def _render_transaction_detail_page(self, actor: dict, transaction_id: int) -> ApiResponse:
        item = self.service.get_transaction(actor, transaction_id)
        actions = f"""
        <div class="action-row">
          <a class="button-link" href="/transactions/{transaction_id}/edit">Edit</a>
          <form method="post" action="/transactions/{transaction_id}/delete">
            <button type="submit" class="danger">Delete</button>
          </form>
        </div>
        """ if actor["role"] == "admin" else ""
        content = f"""
        <section class="card">
          <h1>Transaction #{item["id"]}</h1>
          <dl class="key-value">
            <dt>User</dt><dd>{escape(item.get("user_name") or str(item["user_id"]))}</dd>
            <dt>Amount</dt><dd>{escape(item["amount"])}</dd>
            <dt>Type</dt><dd>{escape(item["type"])}</dd>
            <dt>Category</dt><dd>{escape(item["category"])}</dd>
            <dt>Date</dt><dd>{escape(item["date"])}</dd>
            <dt>Notes</dt><dd>{escape(item["notes"] or "-")}</dd>
          </dl>
          {actions}
        </section>
        """
        return self._html_page(f"Transaction {transaction_id}", content, actor=actor)

    def _render_transaction_edit_page(self, actor: dict, transaction_id: int) -> ApiResponse:
        item = self.service.get_transaction(actor, transaction_id)
        user_options = ""
        if actor["role"] == "admin":
            user_options = "".join(
                f'<option value="{user["id"]}" {"selected" if user["id"] == item["user_id"] else ""}>'
                f'{escape(user["name"])} ({escape(user["role"])})</option>'
                for user in self.service.list_users(actor)
            )
            user_field = f'<label>User<select name="user_id">{user_options}</select></label>'
        else:
            user_field = ""

        content = f"""
        <section class="card">
          <h1>Edit Transaction #{item["id"]}</h1>
          <form method="post" action="/transactions/{item["id"]}/edit" class="form-grid">
            {user_field}
            <label>Amount<input name="amount" value="{escape(item["amount"])}" required></label>
            <label>Type
              <select name="type">
                <option value="expense" {"selected" if item["type"] == "expense" else ""}>expense</option>
                <option value="income" {"selected" if item["type"] == "income" else ""}>income</option>
              </select>
            </label>
            <label>Category<input name="category" value="{escape(item["category"])}" required></label>
            <label>Date<input name="date" value="{escape(item["date"])}" required></label>
            <label>Notes<input name="notes" value="{escape(item["notes"] or "")}"></label>
            <button type="submit">Save Changes</button>
          </form>
        </section>
        """
        return self._html_page(f"Edit Transaction {transaction_id}", content, actor=actor)

    def _transaction_row_html(self, item: dict, *, include_actions: bool) -> str:
        actions = f"""
        <div class="action-row compact">
          <a class="button-link" href="/transactions/{item["id"]}">View</a>
          <a class="button-link" href="/transactions/{item["id"]}/edit">Edit</a>
          <form method="post" action="/transactions/{item["id"]}/delete">
            <button type="submit" class="danger">Delete</button>
          </form>
        </div>
        """ if include_actions else f'<a class="button-link" href="/transactions/{item["id"]}">View</a>'
        return (
            f"<tr><td>{item['id']}</td><td>{escape(item.get('user_name') or str(item['user_id']))}</td>"
            f"<td>{escape(item['amount'])}</td><td>{escape(item['type'])}</td>"
            f"<td>{escape(item['category'])}</td><td>{escape(item['date'])}</td>"
            f"<td>{escape(item['notes'] or '-')}</td><td>{actions}</td></tr>"
        )

    def _redirect(self, location: str, *, headers: dict[str, str] | None = None) -> ApiResponse:
        return ApiResponse(
            303,
            headers={"Location": location, **(headers or {})},
            content_type="text/html; charset=utf-8",
            raw_body=f'<html><body>Redirecting to <a href="{escape(location)}">{escape(location)}</a></body></html>'.encode(
                "utf-8"
            ),
        )

    def _html_page(self, title: str, content: str, *, actor: dict | None = None, status_code: int = 200) -> ApiResponse:
        actor_badge = ""
        if actor is not None:
            actor_badge = f'<div class="top-badge">Logged in as {escape(actor["name"])} ({escape(actor["role"])})</div>'
            nav_links = """
                <a href="/me">Profile</a>
                <a href="/transactions">Transactions</a>
                <a href="/summaries/overview">Overview</a>
                <a href="/summaries/recent-activity">Recent Activity</a>
                <a href="/login">Switch User</a>
                <a href="/logout">Logout</a>
            """
        else:
            nav_links = '<a href="/login">Login</a><a href="/register">Register</a>'

        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <title>{escape(title)}</title>
          <style>
            :root {{
              color-scheme: light;
              --bg: #f5f1e8;
              --panel: #fffdf8;
              --ink: #1f2933;
              --muted: #6b7280;
              --line: #d7c8b0;
              --accent: #0f766e;
              --accent-soft: #dff4ef;
              --danger: #b91c1c;
            }}
            * {{ box-sizing: border-box; }}
            body {{
              margin: 0;
              font-family: Georgia, "Times New Roman", serif;
              color: var(--ink);
              background: radial-gradient(circle at top, #fff7ea, var(--bg));
            }}
            .shell {{ max-width: 1120px; margin: 0 auto; padding: 32px 20px 48px; }}
            .topbar {{
              display: flex;
              justify-content: space-between;
              align-items: center;
              gap: 16px;
              margin-bottom: 24px;
              flex-wrap: wrap;
            }}
            .brand {{ font-size: 28px; font-weight: 700; text-decoration: none; color: var(--ink); }}
            .nav {{ display: flex; gap: 12px; flex-wrap: wrap; }}
            .nav a, .button-link {{
              text-decoration: none;
              color: var(--accent);
              border: 1px solid var(--line);
              padding: 8px 12px;
              border-radius: 999px;
              background: white;
              display: inline-block;
            }}
            .top-badge, .badge {{
              background: var(--accent-soft);
              color: var(--accent);
              padding: 10px 14px;
              border-radius: 999px;
              display: inline-block;
            }}
            .grid {{
              display: grid;
              grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
              gap: 16px;
            }}
            .card {{
              background: var(--panel);
              border: 1px solid rgba(122, 93, 54, 0.18);
              border-radius: 20px;
              padding: 20px;
              box-shadow: 0 10px 30px rgba(82, 56, 26, 0.08);
              margin-bottom: 16px;
            }}
            .stat p {{ font-size: 28px; margin: 8px 0 0; font-weight: 700; }}
            h1, h2 {{ margin-top: 0; }}
            table {{
              width: 100%;
              border-collapse: collapse;
              font-size: 14px;
            }}
            th, td {{
              text-align: left;
              padding: 10px 8px;
              border-bottom: 1px solid var(--line);
              vertical-align: top;
            }}
            .form-grid {{
              display: grid;
              grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
              gap: 12px;
            }}
            label {{
              display: flex;
              flex-direction: column;
              gap: 6px;
              font-size: 14px;
            }}
            input, select, button {{
              padding: 10px 12px;
              border-radius: 12px;
              border: 1px solid var(--line);
              font: inherit;
              background: white;
            }}
            button {{
              background: var(--accent);
              color: white;
              cursor: pointer;
            }}
            button.danger {{ background: var(--danger); }}
            .action-row {{
              display: flex;
              gap: 8px;
              align-items: center;
              flex-wrap: wrap;
            }}
            .action-row.compact form, .action-row form {{ margin: 0; }}
            .key-value {{
              display: grid;
              grid-template-columns: 140px 1fr;
              gap: 8px 12px;
            }}
            .key-value dt {{ font-weight: 700; }}
            .key-value dd {{ margin: 0; }}
            .muted {{ color: var(--muted); }}
            ul {{ padding-left: 20px; }}
          </style>
        </head>
        <body>
          <div class="shell">
            <div class="topbar">
              <a class="brand" href="/">ExpensePilot</a>
              <div class="nav">{nav_links}</div>
            </div>
            {actor_badge}
            {content}
          </div>
        </body>
        </html>
        """
        return ApiResponse(
            status_code,
            content_type="text/html; charset=utf-8",
            raw_body=html.encode("utf-8"),
        )

    def _session_cookie(self, user_id: int) -> str:
        return f"finance_user_id={user_id}; Path=/; HttpOnly; SameSite=Lax"

    def _clear_session_cookie(self) -> str:
        return "finance_user_id=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


def create_wsgi_app(db_path: str | Path = DEFAULT_DB_PATH) -> Callable:
    api = FinanceAPI(db_path)

    def app(environ: dict, start_response: Callable) -> list[bytes]:
        return api.wsgi_app(environ, start_response)

    return app
