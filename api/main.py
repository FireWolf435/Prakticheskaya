from __future__ import annotations

import os
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Service Desk API", version="1.0.0")


def dsn() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is not configured")
    return value


class TicketCreate(BaseModel):
    author_id: int
    category_id: int
    priority: int = Field(ge=1, le=4)
    subject: str = Field(min_length=1, max_length=500)
    description: str | None = None


class StatusChange(BaseModel):
    new_status: str
    comment: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    with psycopg.connect(dsn()) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/v1/tickets")
def list_tickets(limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    with psycopg.connect(dsn(), row_factory=psycopg.rows.dict_row) as conn:
        return conn.execute(
            "SELECT ticket_id, ticket_number, status, priority, subject, created_at, sla_deadline "
            "FROM core.tickets WHERE is_deleted = false ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (limit, offset),
        ).fetchall()


@app.post("/api/v1/tickets", status_code=201)
def create_ticket(ticket: TicketCreate) -> dict[str, Any]:
    with psycopg.connect(dsn(), row_factory=psycopg.rows.dict_row) as conn:
        return conn.execute(
            "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, description) "
            "VALUES ('API-' || to_char(clock_timestamp(), 'YYYYMMDDHH24MISSMS'), %s, %s, %s, 'Новая', %s, %s) "
            "RETURNING ticket_id, ticket_number, status, priority, subject, sla_deadline",
            (ticket.author_id, ticket.category_id, ticket.priority, ticket.subject, ticket.description),
        ).fetchone()


@app.post("/api/v1/tickets/{ticket_id}/status")
def change_status(ticket_id: int, change: StatusChange) -> dict[str, str]:
    with psycopg.connect(dsn()) as conn:
        try:
            conn.execute("SELECT core.change_ticket_status(%s, %s, %s)", (ticket_id, change.new_status, change.comment))
        except psycopg.Error as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": change.new_status}


@app.get("/api/v1/tickets/{ticket_id}")
def get_ticket(ticket_id: int) -> dict[str, Any]:
    with psycopg.connect(dsn(), row_factory=psycopg.rows.dict_row) as conn:
        ticket = conn.execute(
            "SELECT ticket_id, ticket_number, status, priority, subject, description, created_at, "
            "updated_at, sla_deadline, sla_spent_min, author_id, executor_id, department_id "
            "FROM core.tickets WHERE ticket_id = %s AND is_deleted = false",
            (ticket_id,),
        ).fetchone()
        if ticket is None:
            raise HTTPException(status_code=404, detail="Заявка не найдена")
        ticket["history"] = conn.execute(
            "SELECT from_status, to_status, changed_at, changed_by, comment "
            "FROM core.ticket_status_history WHERE ticket_id = %s ORDER BY changed_at",
            (ticket_id,),
        ).fetchall()
        return ticket


@app.get("/api/v1/reports/engineer-load")
def engineer_load() -> list[dict[str, Any]]:
    with psycopg.connect(dsn(), row_factory=psycopg.rows.dict_row) as conn:
        return conn.execute(
            "SELECT * FROM core.report_engineer_load(now() - interval '30 days', now())"
        ).fetchall()


@app.get("/api/v1/assets/{asset_id}")
def get_asset(asset_id: int) -> dict[str, Any]:
    with psycopg.connect(dsn(), row_factory=psycopg.rows.dict_row) as conn:
        asset = conn.execute(
            "SELECT asset_id, inv_no, serial_no, asset_type, model, manufacturer, state, "
            "responsible_id, location_id, is_active FROM core.assets WHERE asset_id = %s",
            (asset_id,),
        ).fetchone()
        if asset is None:
            raise HTTPException(status_code=404, detail="Оборудование не найдено")
        return asset


@app.get("/api/v1/admin/users")
def list_users(limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    with psycopg.connect(dsn(), row_factory=psycopg.rows.dict_row) as conn:
        return conn.execute(
            "SELECT employee_id, tab_number, first_name, last_name, email, department_id, "
            "is_active FROM core.employees ORDER BY last_name, first_name LIMIT %s",
            (limit,),
        ).fetchall()
