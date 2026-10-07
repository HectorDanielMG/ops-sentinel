from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web"
DB_PATH = Path(os.getenv("DATABASE_PATH", str(ROOT / "ops-sentinel.db")))
CHECK_INTERVAL = max(5, int(os.getenv("CHECK_INTERVAL_SECONDS", "30")))
REQUEST_TIMEOUT = max(1, float(os.getenv("REQUEST_TIMEOUT_SECONDS", "5")))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def initialize() -> None:
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS services (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                slo_target REAL NOT NULL DEFAULT 99.9,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
                checked_at TEXT NOT NULL,
                ok INTEGER NOT NULL,
                status_code INTEGER,
                latency_ms REAL NOT NULL,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS checks_service_time ON checks(service_id, checked_at);
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                service_id TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
                opened_at TEXT NOT NULL,
                resolved_at TEXT,
                message TEXT NOT NULL
            );
        """)
        count = db.execute("SELECT COUNT(*) FROM services").fetchone()[0]
        if count == 0:
            db.executemany(
                "INSERT INTO services(id,name,url,slo_target,created_at) VALUES(?,?,?,?,?)",
                [
                    ("demo-core", "API principal · demostración", "http://127.0.0.1:8000/demo/healthy", 99.5, now_iso()),
                    ("demo-flaky", "Compras · demostración", "http://127.0.0.1:8000/demo/flaky", 99.9, now_iso()),
                ],
            )


def service_payload(db: sqlite3.Connection, service: sqlite3.Row) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = db.execute(
        "SELECT ok, latency_ms, checked_at, status_code, error FROM checks "
        "WHERE service_id=? AND checked_at>=? ORDER BY checked_at DESC",
        (service["id"], since),
    ).fetchall()
    total = len(rows)
    uptime = (sum(r["ok"] for r in rows) / total * 100) if total else None
    allowed_error = 100 - service["slo_target"]
    if uptime is None:
        error_budget_remaining = None
    elif allowed_error == 0:
        error_budget_remaining = 100.0 if uptime == 100 else 0.0
    else:
        consumed = (100 - uptime) / allowed_error * 100
        error_budget_remaining = round(max(0, min(100, 100 - consumed)), 1)
    active = db.execute(
        "SELECT id,opened_at,message FROM incidents WHERE service_id=? AND resolved_at IS NULL",
        (service["id"],),
    ).fetchone()
    last = db.execute(
        "SELECT checked_at, status_code, latency_ms, error FROM checks "
        "WHERE service_id=? ORDER BY checked_at DESC LIMIT 1",
        (service["id"],),
    ).fetchone()
    last_checked = datetime.fromisoformat(last["checked_at"].replace("Z", "+00:00")) if last else None
    stale_after = CHECK_INTERVAL * 3
    is_stale = bool(last_checked and (datetime.now(timezone.utc) - last_checked).total_seconds() > stale_after)
    if active:
        status = "down"
    elif not last:
        status = "unknown"
    else:
        status = "stale" if is_stale else "up"
    latencies = sorted(r["latency_ms"] for r in rows)
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None
    return {
        "id": service["id"], "name": service["name"], "url": service["url"],
        "slo_target": service["slo_target"], "status": status,
        "check_stale_after_seconds": stale_after,
        "uptime_24h": round(uptime, 3) if uptime is not None else None,
        "error_budget_remaining_percent": error_budget_remaining,
        "checks_24h": total, "latency_ms": round(last["latency_ms"], 1) if last else None,
        "p95_latency_ms": round(p95, 1) if p95 is not None else None,
        "last_checked_at": last["checked_at"] if last else None,
        "last_status_code": last["status_code"] if last else None,
        "last_error": last["error"] if last else None,
        "active_incident": dict(active) if active else None,
    }


async def check_service(service_id: str) -> dict[str, Any]:
    with connect() as db:
        service = db.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
    if service is None:
        raise HTTPException(status_code=404, detail="No se encontró el servicio")

    started = time.perf_counter()
    code: int | None = None
    error: str | None = None
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(service["url"])
            code = response.status_code
            ok = 200 <= code < 400
            if not ok:
                error = f"Error HTTP {code}"
    except httpx.TimeoutException:
        ok, error = False, f"Tiempo de espera agotado después de {REQUEST_TIMEOUT:g} s"
    except httpx.RequestError as exc:
        ok, error = False, f"Error de conexión: {type(exc).__name__}"
    latency = round((time.perf_counter() - started) * 1000, 2)
    stamp = now_iso()

    with connect() as db:
        db.execute(
            "INSERT INTO checks(service_id,checked_at,ok,status_code,latency_ms,error) VALUES(?,?,?,?,?,?)",
            (service_id, stamp, int(ok), code, latency, error),
        )
        active = db.execute(
            "SELECT id FROM incidents WHERE service_id=? AND resolved_at IS NULL", (service_id,)
        ).fetchone()
        if not ok and active is None:
            db.execute(
                "INSERT INTO incidents(id,service_id,opened_at,message) VALUES(?,?,?,?)",
                (str(uuid4()), service_id, stamp, error or "Falló la comprobación del servicio"),
            )
        elif ok and active is not None:
            db.execute("UPDATE incidents SET resolved_at=? WHERE id=?", (stamp, active["id"]))
    return {"service_id": service_id, "ok": ok, "status_code": code, "latency_ms": latency, "error": error}


async def poll_forever() -> None:
    while True:
        with connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM services")]
        await asyncio.gather(*(check_service(service_id) for service_id in ids), return_exceptions=True)
        await asyncio.sleep(CHECK_INTERVAL)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize()
    task = asyncio.create_task(poll_forever())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Ops Sentinel", version="1.0.0", description="Monitor ligero de confiabilidad para servicios web", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


class ServiceInput(BaseModel):
    name: str = Field(min_length=2, max_length=80, description="Nombre que aparecerá en el panel")
    url: HttpUrl
    slo_target: float = Field(default=99.9, gt=0, le=100, description="Objetivo de disponibilidad, como porcentaje")


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "time": now_iso()}


@app.get("/demo/healthy")
def demo_healthy() -> dict[str, str]:
    return {"status": "ok", "service": "core-api"}


@app.get("/demo/flaky")
def demo_flaky() -> dict[str, str]:
    # Las fallas previsibles permiten ver la apertura y recuperación de incidentes.
    if int(time.time() // 40) % 2 == 0:
        raise HTTPException(status_code=503, detail="Demo outage window")
    return {"status": "ok", "service": "checkout-demo"}


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    with connect() as db:
        services = [service_payload(db, row) for row in db.execute("SELECT * FROM services ORDER BY created_at")]
    active = sum(s["status"] == "down" for s in services)
    measured = [s["uptime_24h"] for s in services if s["uptime_24h"] is not None]
    return {"service_count": len(services), "active_incidents": active,
            "fleet_uptime_24h": round(sum(measured) / len(measured), 3) if measured else None,
            "checked_at": now_iso()}


@app.get("/api/services")
def list_services() -> list[dict[str, Any]]:
    with connect() as db:
        return [service_payload(db, row) for row in db.execute("SELECT * FROM services ORDER BY created_at")]


@app.post("/api/services", status_code=201)
def create_service(payload: ServiceInput) -> dict[str, Any]:
    service_id = str(uuid4())
    try:
        with connect() as db:
            db.execute("INSERT INTO services(id,name,url,slo_target,created_at) VALUES(?,?,?,?,?)",
                       (service_id, payload.name.strip(), str(payload.url), payload.slo_target, now_iso()))
    except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Ya existe un servicio con esta URL") from exc
    return {"id": service_id, "name": payload.name.strip(), "url": str(payload.url), "slo_target": payload.slo_target}


@app.get("/api/incidents")
def list_incidents() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("""SELECT i.*,s.name AS service_name FROM incidents i
            JOIN services s ON s.id=i.service_id ORDER BY i.opened_at DESC LIMIT 50""").fetchall()
        return [dict(row) for row in rows]


@app.post("/api/checks/run")
async def run_checks() -> dict[str, Any]:
    with connect() as db:
        ids = [row[0] for row in db.execute("SELECT id FROM services")]
    results = await asyncio.gather(*(check_service(service_id) for service_id in ids))
    return {"results": results}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    services = list_services()
    lines = ["# HELP ops_sentinel_service_up Indica si la última comprobación tuvo éxito.",
             "# TYPE ops_sentinel_service_up gauge"]
    for service in services:
        label = service["name"].replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        lines.append(f'ops_sentinel_service_up{{service="{label}"}} {1 if service["status"] == "up" else 0}')
    lines += ["# HELP ops_sentinel_uptime_ratio_24h Proporción de disponibilidad según las comprobaciones de las últimas 24 horas.",
              "# TYPE ops_sentinel_uptime_ratio_24h gauge"]
    for service in services:
        label = service["name"].replace('"', '\\"')
        ratio = (service["uptime_24h"] or 0) / 100
        lines.append(f'ops_sentinel_uptime_ratio_24h{{service="{label}"}} {ratio:.6f}')
    lines += ["# HELP ops_sentinel_error_budget_remaining_ratio_24h Proporción del presupuesto de error SLO restante en las últimas 24 horas.",
              "# TYPE ops_sentinel_error_budget_remaining_ratio_24h gauge"]
    for service in services:
        if service["error_budget_remaining_percent"] is None:
            continue
        label = service["name"].replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        ratio = service["error_budget_remaining_percent"] / 100
        lines.append(f'ops_sentinel_error_budget_remaining_ratio_24h{{service="{label}"}} {ratio:.6f}')
    return "\n".join(lines) + "\n"
