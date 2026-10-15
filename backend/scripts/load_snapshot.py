"""Copia la base de demo (backend/data/dmc.db, generada por seed_demo.py) a la base de DATABASE_URL (Postgres).

Uso:
  python scripts/load_snapshot.py           # solo si la base destino está vacía
  python scripts/load_snapshot.py --force   # recrea las tablas y vuelve a copiar (igual que POST /api/admin/reset-demo)

Después de cambiar el esquema de las tablas, usa --force: sin él, create_all no altera tablas existentes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.services.snapshot import copy_snapshot  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if config.IS_SQLITE:
        sys.exit("DATABASE_URL apunta a SQLite: no hay nada que copiar (seed_demo.py ya escribe ahí).")
    print(copy_snapshot(engine, config.SQLITE_FILE, force=args.force))


if __name__ == "__main__":
    main()
