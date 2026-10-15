# -*- coding: utf-8 -*-
"""Проверка загрузки: количество строк в stage и примеры данных."""
import os
import sys

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TABLES = [
    "stage.ref_categories", "stage.ref_priorities_sla",
    "stage.ref_production_calendar", "stage.ref_engineer_skills",
    "stage.ref_duty_schedule", "stage.hr_departments", "stage.hr_employees",
    "stage.cmdb_assets_1c", "stage.cmdb_asset_movements",
    "stage.legacy_tickets", "stage.legacy_ticket_status_history",
    "stage.ldap_directory", "stage.mail_messages",
]


def load_env(path):
    env = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main():
    env = load_env(os.path.join(ROOT, ".env"))
    dsn = (f"host=127.0.0.1 port={env.get('POSTGRES_PORT', '5432')} "
           f"dbname={env['POSTGRES_DB']} user={env['POSTGRES_USER']} "
           f"password={env['POSTGRES_PASSWORD']} connect_timeout=10")

    out = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            out.append("ПРОВЕРКА STAGE")
            out.append(f"{'Таблица':<40}{'Строк':>8}")
            out.append("-" * 48)
            total = 0
            for t in TABLES:
                cur.execute(f"SELECT count(*) FROM {t}")
                n = cur.fetchone()[0]
                total += n
                out.append(f"{t:<40}{n:>8}")
            out.append("-" * 48)
            out.append(f"{'ИТОГО':<40}{total:>8}")

            out.append("")
            out.append("ПРИМЕРЫ (кириллица должна читаться нормально):")
            for t, cols in [
                ("stage.hr_employees", "src_line, line_data"),
                ("stage.cmdb_asset_movements", "src_line, line_data"),
                ("stage.legacy_tickets", "src_line, line_data"),
                ("stage.cmdb_assets_1c", "src_line, line_data"),
            ]:
                out.append("")
                out.append(f"-- {t} --")
                cur.execute(f"SELECT {cols} FROM {t} WHERE src_line <= 3 ORDER BY src_line")
                for src_line, data in cur.fetchall():
                    out.append(f"  [{src_line:>6}] {data[:160]}")

    path = os.path.join(ROOT, "docs", "staging_verify.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("Отчёт проверки сохранён: " + path)


if __name__ == "__main__":
    main()
