from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = os.getenv("DATABASE_URL") or (BASE_DIR / "data" / "finance.db")
DEFAULT_HOST = os.getenv("FINANCE_HOST", "0.0.0.0" if os.getenv("PORT") else "127.0.0.1")
DEFAULT_PORT = int(os.getenv("PORT") or os.getenv("FINANCE_PORT", "8000"))
