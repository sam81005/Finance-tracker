# ExpensePilot

This project is a Python-based finance system backend built for the assignment brief. It focuses on clean application structure, role-aware behavior, financial summaries, validation, and a lightweight persistence layer.

I intentionally used only Python's standard library in this environment because no web framework packages were preinstalled. That choice keeps the project easy to run anywhere while still demonstrating backend design, REST-style endpoints, business logic, SQLite persistence, input validation, and automated tests.

## What It Covers

- CRUD operations for financial records
- Filtering by type, category, date range, and notes search
- Pagination for transaction listing
- Financial summaries such as total income, total expenses, current balance, category breakdown, monthly totals, and recent activity
- Basic user and role handling with `viewer`, `analyst`, and `admin`
- Role-based access rules enforced in the service layer
- Transaction export in CSV or JSON format
- SQLite persistence with seeded sample data
- JSON responses with appropriate HTTP status codes
- Automated tests using `unittest`

## Role Behavior

- `viewer`
  - Can view their own records
  - Can view overview summary and recent activity
  - Cannot use advanced filters or access detailed analytics
- `analyst`
  - Can view their own records
  - Can apply filters
  - Can access detailed summaries such as category breakdown and monthly totals
- `admin`
  - Can view all users and records
  - Can create, update, and delete financial records
  - Can create and update users
  - Can request data globally or for a specific user

## Assumptions

- Authentication is intentionally simplified. If no `X-User-Id` header is sent, the API defaults to the seeded admin user so it works directly in a browser or simple request.
- You can still send `X-User-Id` to simulate analyst and viewer behavior.
- Non-admin users can only access their own data.
- Money is stored as integer cents in SQLite to avoid floating-point precision issues.
- Viewers have intentionally limited analytics/filtering access to make role differences clear.

## Project Structure

```text
finance_backend/
├── app/
│   ├── api.py
│   ├── config.py
│   ├── database.py
│   ├── errors.py
│   ├── models.py
│   ├── permissions.py
│   ├── repositories.py
│   ├── seed.py
│   └── services.py
├── tests/
│   └── test_api.py
├── data/
├── run.py
└── README.md
```

## How To Run

1. Open the project directory:

   ```bash
   cd "/Users/samarthkarmakar/Documents/New project/finance_backend"
   ```

2. Start the server:

   ```bash
   python3 run.py
   ```

3. Open the app in your browser:

   ```text
   http://127.0.0.1:8000/
   ```

4. The JSON API is also available at:

   ```text
   http://127.0.0.1:8000
   ```

On startup the app seeds three demo users:

- `1` -> Samarth K
- `2` -> Anika Analyst
- `3` -> Victor Viewer

Default demo passwords:

- `Samarth K` -> `samarth123`
- `Anika Analyst` -> `analyst123`
- `Victor Viewer` -> `viewer123`

The browser UI now uses a simple multi-user login flow with signup and logout support.
Users can also create their own account from the `/register` page, and their transactions are stored against their own user record in SQLite.
JSON API requests without auth headers still default to Samarth K for easy testing.

## How To Test

```bash
python3 -m unittest discover -s tests -v
```

## Cloud Database And Deployment

This project is now prepared for deployment with a managed PostgreSQL database.

- Local development still defaults to SQLite
- Production can use PostgreSQL by setting `DATABASE_URL`
- The app reads the hosting platform `PORT` automatically
- A [render.yaml](/Users/samarthkarmakar/Documents/New%20project/finance_backend/render.yaml) blueprint and [wsgi.py](/Users/samarthkarmakar/Documents/New%20project/finance_backend/wsgi.py) entrypoint are included

### Render Setup

1. Push this project to GitHub.
2. Create a new Render Blueprint or Web Service from the repo.
3. Render will use `render.yaml` to provision:
   - a Python web service
   - a managed PostgreSQL database
4. The service will install dependencies from [requirements.txt](/Users/samarthkarmakar/Documents/New%20project/finance_backend/requirements.txt).
5. The app will start with:

   ```bash
   gunicorn wsgi:app
   ```

### Environment Variables

- `DATABASE_URL`
  - used automatically for PostgreSQL in deployed environments
- `PORT`
  - provided automatically by platforms like Render
- `FINANCE_HOST` and `FINANCE_PORT`
  - optional overrides for local use

## Main Endpoints

### Health and User Info

- `GET /health`
- `GET /me`
- `GET /users`
- `POST /users`
- `GET /users/{id}`
- `PUT /users/{id}`

### Transactions

- `GET /transactions`
- `GET /transactions/export`
- `POST /transactions`
- `GET /transactions/{id}`
- `PUT /transactions/{id}`
- `DELETE /transactions/{id}`

### Summaries

- `GET /summaries/overview`
- `GET /summaries/category-breakdown`
- `GET /summaries/monthly-totals`
- `GET /summaries/recent-activity`

## Example Requests

Browser flow:

- Open `http://127.0.0.1:8000/`
- Log in with a seeded account or create a new account
- Use `Switch User` or `Logout` from the top navigation

Get the logged-in analyst profile:

```bash
curl -H "X-User-Id: 2" http://127.0.0.1:8000/me
```

Use the API without any auth header:

```bash
curl http://127.0.0.1:8000/me
```

List analyst transactions with filters:

```bash
curl -H "X-User-Id: 2" "http://127.0.0.1:8000/transactions?type=expense&date_from=2026-03-01&date_to=2026-03-31"
```

List transactions with pagination:

```bash
curl -H "X-User-Id: 2" "http://127.0.0.1:8000/transactions?page=1&page_size=2"
```

Create a transaction as admin:

```bash
curl -X POST http://127.0.0.1:8000/transactions \
  -H "Content-Type: application/json" \
  -H "X-User-Id: 1" \
  -d '{
    "user_id": 3,
    "amount": "125.00",
    "type": "expense",
    "category": "Travel",
    "date": "2026-03-20",
    "notes": "Taxi reimbursement"
  }'
```

Get monthly totals for the analyst:

```bash
curl -H "X-User-Id: 2" "http://127.0.0.1:8000/summaries/monthly-totals?year=2026"
```

Export analyst transactions as CSV:

```bash
curl -H "X-User-Id: 2" "http://127.0.0.1:8000/transactions/export?format=csv"
```

Export admin-visible transactions for a specific user as JSON:

```bash
curl -H "X-User-Id: 1" "http://127.0.0.1:8000/transactions/export?format=json&user_id=3"
```

## Design Notes

- The API layer is intentionally thin and delegates business rules to `services.py`.
- Repositories isolate SQL concerns from validation and permission logic.
- Errors are normalized into consistent JSON responses.
- The database is initialized automatically, and the seed step makes the project easy to evaluate quickly.

## Why This Fits The Assignment

This solution demonstrates:

- clear Python code organization
- persistence and query handling with SQLite
- API design and routing
- business logic for finance summaries
- validation and predictable error behavior
- role-based access handling
- pagination and export handling
- documentation and test coverage
