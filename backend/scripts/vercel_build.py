"""Build de Vercel: genera la base de demostración (snapshot) y, con Postgres, la carga si está vacía.

Corre seed_demo.py contra backend/data/dmc.db con el reloj de la demo y deja el archivo autocontenido
(sin -wal) para poder abrirlo en modo inmutable desde el bundle de solo lectura. Si DATABASE_URL es
Postgres (Neon), copia el snapshot allí cuando no tiene datos; si no, la función lo copia a /tmp.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BACKEND_DIR / "data" / "dmc.db"


def main() -> None:
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{DB_PATH.as_posix()}",
        "SIM_NOW": os.getenv("SIM_NOW") or "2026-10-12T19:00:00",
        "LLM_MOCK": "1",
        "DISABLE_SCHEDULER": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    for suffix in ("", "-wal", "-shm"):
        Path(f"{DB_PATH}{suffix}").unlink(missing_ok=True)
    subprocess.run([sys.executable, str(BACKEND_DIR / "scripts" / "seed_demo.py")], env=env, check=True)

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("VACUUM")
    con.close()
    print(f"Base de demo lista: {DB_PATH} ({DB_PATH.stat().st_size // 1024} KB)")

    if os.getenv("DATABASE_URL", "").startswith(("postgres://", "postgresql://")):
        subprocess.run([sys.executable, str(BACKEND_DIR / "scripts" / "load_snapshot.py")],
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, check=True)
    else:
        print("Sin Postgres: la función copiará la base a /tmp al arrancar.")


if __name__ == "__main__":
    main()
