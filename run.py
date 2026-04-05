from __future__ import annotations

from wsgiref.simple_server import make_server

from app import create_wsgi_app
from app.config import DEFAULT_DB_PATH, DEFAULT_HOST, DEFAULT_PORT
from app.seed import ensure_seed_data


def main() -> None:
    ensure_seed_data(DEFAULT_DB_PATH)
    app = create_wsgi_app(DEFAULT_DB_PATH)
    with make_server(DEFAULT_HOST, DEFAULT_PORT, app) as server:
        print(f"Finance backend running on http://{DEFAULT_HOST}:{DEFAULT_PORT}")
        print("Seeded users: admin=1, analyst=2, viewer=3")
        server.serve_forever()


if __name__ == "__main__":
    main()
