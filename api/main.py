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
