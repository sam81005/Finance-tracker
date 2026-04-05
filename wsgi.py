from __future__ import annotations

from app import create_wsgi_app
from app.config import DEFAULT_DB_PATH

app = create_wsgi_app(DEFAULT_DB_PATH)
