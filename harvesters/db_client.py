"""
harvesters/db_client.py
Cliente Supabase via httpx directo — 100% síncrono, compatible con Python 3.14.
Implementa las operaciones necesarias: select, insert, upsert, update, delete.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from loguru import logger
from config.settings import SUPABASE_URL, SUPABASE_KEY

BASE = f"{SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}


def _get(table: str, params: dict = {}) -> list[dict]:
    resp = httpx.get(f"{BASE}/{table}", headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _post(table: str, data: dict | list, prefer: str = "return=representation") -> list[dict]:
    h = {**HEADERS, "Prefer": prefer}
    resp = httpx.post(f"{BASE}/{table}", headers=h, json=data, timeout=15)
    resp.raise_for_status()
    return resp.json() if resp.text else []


def _patch(table: str, data: dict, params: dict = {}) -> list[dict]:
    h = {**HEADERS, "Prefer": "return=representation"}
    resp = httpx.patch(f"{BASE}/{table}", headers=h, json=data, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json() if resp.text else []


def _delete(table: str, params: dict) -> None:
    resp = httpx.delete(f"{BASE}/{table}", headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()


def select(table: str, filters: dict = {}, limit: int = 1000) -> list[dict]:
    """SELECT con filtros opcionales."""
    params = {f"{k}": f"eq.{v}" for k, v in filters.items()}
    params["limit"] = limit
    return _get(table, params)


def insert(table: str, rows: list[dict]) -> list[dict]:
    """INSERT múltiples filas."""
    if not rows:
        return []
    return _post(table, rows)


def upsert(table: str, rows: list[dict], on_conflict: str) -> list[dict]:
    """UPSERT con columna(s) de conflicto."""
    if not rows:
        return []
    h = {**HEADERS, "Prefer": f"resolution=merge-duplicates,return=representation"}
    params = {"on_conflict": on_conflict}
    resp = httpx.post(f"{BASE}/{table}", headers=h, json=rows, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json() if resp.text else []


def update(table: str, data: dict, filters: dict) -> list[dict]:
    """UPDATE con filtros."""
    params = {f"{k}": f"eq.{v}" for k, v in filters.items()}
    return _patch(table, data, params)


def delete(table: str, filters: dict) -> None:
    """DELETE con filtros."""
    params = {f"{k}": f"eq.{v}" for k, v in filters.items()}
    _delete(table, params)


def track_api_usage(api_name: str, requests_used: int = 1) -> None:
    """Incrementa el contador de uso de API."""
    from datetime import date
    today = date.today().isoformat()
    try:
        existing = select("api_usage", {"api_name": api_name, "fecha": today})
        if existing:
            current = existing[0]["requests"]
            update("api_usage", {"requests": current + requests_used}, {"api_name": api_name, "fecha": today})
        else:
            insert("api_usage", [{"api_name": api_name, "fecha": today, "requests": requests_used, "limite": 100}])
    except Exception as e:
        logger.warning(f"track_api_usage error: {e}")


def get_api_usage(api_name: str) -> int:
    """Requests usados hoy para una API."""
    from datetime import date
    try:
        rows = select("api_usage", {"api_name": api_name, "fecha": date.today().isoformat()})
        return rows[0]["requests"] if rows else 0
    except Exception:
        return 0


# Alias para compatibilidad con código existente
def get_db():
    """Retorna este módulo como 'cliente' — compatibilidad con código anterior."""
    return _DBCompat()


class _DBCompat:
    """Wrapper de compatibilidad para código que usa db.table(x).select().execute()"""
    def table(self, name: str):
        return _TableCompat(name)


class _TableCompat:
    def __init__(self, name: str):
        self._name   = name
        self._method = None
        self._data   = None
        self._params = {}
        self._conflict = None

    def select(self, cols="*"):
        self._method = "select"
        return self

    def insert(self, data):
        self._method = "insert"
        self._data = data if isinstance(data, list) else [data]
        return self

    def upsert(self, data, on_conflict="id"):
        self._method  = "upsert"
        self._data    = data if isinstance(data, list) else [data]
        self._conflict = on_conflict
        return self

    def update(self, data):
        self._method = "update"
        self._data   = data
        return self

    def delete(self):
        self._method = "delete"
        return self

    def eq(self, col, val):
        self._params[col] = val
        return self

    def limit(self, n):
        self._params["limit"] = n
        return self

    def execute(self):
        if self._method == "select":
            data = select(self._name, {k: v for k, v in self._params.items() if k != "limit"},
                          limit=self._params.get("limit", 1000))
            return _Result(data)
        elif self._method == "insert":
            data = insert(self._name, self._data)
            return _Result(data)
        elif self._method == "upsert":
            data = upsert(self._name, self._data, self._conflict or "id")
            return _Result(data)
        elif self._method == "update":
            data = update(self._name, self._data, self._params)
            return _Result(data)
        elif self._method == "delete":
            delete(self._name, self._params)
            return _Result([])
        return _Result([])


class _Result:
    def __init__(self, data):
        self.data = data if isinstance(data, list) else []


if __name__ == "__main__":
    logger.info("Test db_client directo...")
    rows = select("partidos", limit=3)
    logger.info(f"Partidos en BD: primeros {len(rows)}")
    for r in rows:
        logger.info(f"  {r['equipo_local']} vs {r['equipo_visitante']} — {r['liga']}")
