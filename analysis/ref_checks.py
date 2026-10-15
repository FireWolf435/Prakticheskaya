# -*- coding: utf-8 -*-
"""
Пункт 3.2 ТЗ — анализ качества данных.
Раздел 6: справочники (ref/*.csv):
  categories, priorities_sla, production_calendar, engineer_skills, duty_schedule.

Разбираем CSV в stage.*_parsed и проверяем целостность ссылок с кадрами.
"""
import csv
import datetime as dt
import io
import os

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PARSED_DDL = [
    "DROP TABLE IF EXISTS stage.ref_categories_parsed",
    r"""
CREATE TABLE stage.ref_categories_parsed (
    code TEXT PRIMARY KEY, name TEXT, legacy_aliases TEXT
)""",
    "DROP TABLE IF EXISTS stage.ref_priorities_parsed",
    r"""
CREATE TABLE stage.ref_priorities_parsed (
    priority INT PRIMARY KEY, name TEXT,
    reaction_work_hours NUMERIC(6,2), resolve_work_hours NUMERIC(6,2)
)""",
    "DROP TABLE IF EXISTS stage.ref_calendar_parsed",
    r"""
CREATE TABLE stage.ref_calendar_parsed (
    cal_date DATE PRIMARY KEY, day_type TEXT, work_hours NUMERIC(5,2)
)""",
    "DROP TABLE IF EXISTS stage.ref_skills_parsed",
    r"""
CREATE TABLE stage.ref_skills_parsed (
    rec_no BIGINT PRIMARY KEY, tab_no TEXT, category_code TEXT, level INT
)""",
    "DROP TABLE IF EXISTS stage.ref_duty_parsed",
    r"""
CREATE TABLE stage.ref_duty_parsed (
    rec_no BIGINT PRIMARY KEY, tab_no TEXT,
    shift_start TIMESTAMPTZ, shift_end TIMESTAMPTZ
)""",
]

CHECKS = [
    ("R1. Количество записей по справочникам", r"""
SELECT 'categories' AS ref, count(*) FROM stage.ref_categories_parsed
UNION ALL SELECT 'priorities', count(*) FROM stage.ref_priorities_parsed
UNION ALL SELECT 'calendar', count(*) FROM stage.ref_calendar_parsed
UNION ALL SELECT 'skills', count(*) FROM stage.ref_skills_parsed
UNION ALL SELECT 'duty', count(*) FROM stage.ref_duty_parsed
"""),

    ("R2. Категории заявок", r"""
SELECT code, name, legacy_aliases FROM stage.ref_categories_parsed ORDER BY code
"""),

    ("R3. Нормативы SLA по приоритетам", r"""
SELECT priority, name, reaction_work_hours AS реакция_ч, resolve_work_hours AS решение_ч
FROM stage.ref_priorities_parsed ORDER BY priority
"""),

    ("R4. 🔥 Категории в навыках, которых нет в справочнике категорий", r"""
SELECT DISTINCT s.category_code
FROM stage.ref_skills_parsed s
WHERE NOT EXISTS (SELECT 1 FROM stage.ref_categories_parsed c WHERE c.code = s.category_code)
ORDER BY 1
"""),

    ("R5. Инженеры в навыках, которых нет в кадрах (по табельному)", r"""
SELECT DISTINCT s.tab_no
FROM stage.ref_skills_parsed s
WHERE NOT EXISTS (SELECT 1 FROM stage.v_hr_employees e WHERE e.tab_no = s.tab_no)
ORDER BY 1
"""),

    ("R6. Уровень навыка вне диапазона 1..3", r"""
SELECT tab_no, category_code, level FROM stage.ref_skills_parsed
WHERE level IS NULL OR level < 1 OR level > 3
"""),

    ("R7. Дубли навыков (табельный + категория)", r"""
SELECT tab_no, category_code, count(*) FROM stage.ref_skills_parsed
GROUP BY tab_no, category_code HAVING count(*) > 1
"""),

    ("R8. Инженеры: сколько, навыков на человека", r"""
SELECT count(DISTINCT tab_no) AS инженеров,
       count(*) AS всего_навыков,
       round(count(*)::numeric / NULLIF(count(DISTINCT tab_no),0), 2) AS в_среднем
FROM stage.ref_skills_parsed
"""),

    ("R9. Дежурства: табельные, которых нет в кадрах", r"""
SELECT DISTINCT d.tab_no
FROM stage.ref_duty_parsed d
WHERE NOT EXISTS (SELECT 1 FROM stage.v_hr_employees e WHERE e.tab_no = d.tab_no)
ORDER BY 1
"""),

    ("R10. Дежурства: окончание раньше или равно началу", r"""
SELECT rec_no, tab_no, shift_start, shift_end
FROM stage.ref_duty_parsed WHERE shift_end <= shift_start ORDER BY rec_no
"""),

    ("R11. 🔥 Дежурства: длительность смены не 8 часов", r"""
SELECT rec_no, tab_no, shift_start, shift_end,
       round(extract(epoch FROM (shift_end - shift_start))/3600.0, 2) AS часов
FROM stage.ref_duty_parsed
WHERE round(extract(epoch FROM (shift_end - shift_start))/3600.0, 2) <> 8
ORDER BY rec_no
"""),

    ("R12. Дежурства: пересекающиеся смены одного инженера", r"""
SELECT a.tab_no, a.shift_start AS a_from, a.shift_end AS a_to, b.shift_start AS b_from, b.shift_end AS b_to
FROM stage.ref_duty_parsed a
JOIN stage.ref_duty_parsed b
  ON a.tab_no = b.tab_no AND a.rec_no < b.rec_no
 AND a.shift_start < b.shift_end AND b.shift_start < a.shift_end
ORDER BY a.tab_no, a.shift_start LIMIT 40
"""),

    ("R13. Дежурства: диапазон и количество", r"""
SELECT min(shift_start)::date AS с, max(shift_end)::date AS по, count(*) AS смен,
       count(DISTINCT tab_no) AS инженеров
FROM stage.ref_duty_parsed
"""),

    ("R14. Календарь: типы дней и часы", r"""
SELECT day_type, count(*) AS дней, min(work_hours) AS мин_ч, max(work_hours) AS макс_ч
FROM stage.ref_calendar_parsed GROUP BY day_type ORDER BY day_type
"""),

    ("R15. Календарь: дубликаты дат", r"""
SELECT cal_date, count(*) FROM stage.ref_calendar_parsed GROUP BY cal_date HAVING count(*) > 1
"""),

    ("R16. Календарь: диапазон и полнота", r"""
SELECT min(cal_date) AS с, max(cal_date) AS по, count(*) AS дней,
       (max(cal_date) - min(cal_date) + 1) AS должно_быть
FROM stage.ref_calendar_parsed
"""),

    ("R17. Календарь: рабочий день без 8 часов / выходной с часами", r"""
SELECT cal_date, day_type, work_hours FROM stage.ref_calendar_parsed
WHERE (day_type = 'work' AND work_hours <> 8) OR (day_type = 'weekend' AND work_hours <> 0)
ORDER BY cal_date LIMIT 40
"""),

    ("R18. 🔥 Календарь: покрытие периода заявок (01.09.2026 – 16.10.2026)", r"""
SELECT count(*) AS отсутствует_дней
FROM generate_series('2026-09-01'::date, '2026-10-16'::date, '1 day') d
WHERE NOT EXISTS (SELECT 1 FROM stage.ref_calendar_parsed c WHERE c.cal_date = d)
"""),

    ("R19. Дежурства: смены в выходные по производственному календарю", r"""
SELECT count(*) AS смен_в_выходные
FROM stage.ref_duty_parsed d
JOIN stage.ref_calendar_parsed c ON c.cal_date = d.shift_start::date
WHERE c.day_type = 'weekend'
"""),

    ("R20. Справочник категорий: дубли кодов/названий", r"""
SELECT code, name, count(*) OVER (PARTITION BY name) AS по_названию
FROM stage.ref_categories_parsed WHERE code IS NULL OR name IS NULL
"""),
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


def rows_of(cur, table):
    cur.execute(f"SELECT line_data FROM stage.{table} ORDER BY src_line")
    return [r[0] for r in cur.fetchall()]


def main():
    env = load_env(os.path.join(ROOT, ".env"))
    dsn = (f"host=127.0.0.1 port={env.get('POSTGRES_PORT', '5432')} "
           f"dbname={env['POSTGRES_DB']} user={env['POSTGRES_USER']} "
           f"password={env['POSTGRES_PASSWORD']} connect_timeout=10")

    out = ["АНАЛИЗ КАЧЕСТВА ДАННЫХ — РАЗДЕЛ 6: СПРАВОЧНИКИ (ref/*.csv)", ""]

    with psycopg.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for ddl in PARSED_DDL:
                cur.execute(ddl)

            # categories
            cats = list(csv.reader(io.StringIO("\n".join(rows_of(cur, "ref_categories")))))
            cur.executemany(
                "INSERT INTO stage.ref_categories_parsed(code,name,legacy_aliases) VALUES(%s,%s,%s)",
                [tuple(r + [""] * (3 - len(r)))[:3] for r in cats[1:] if r],
            )

            # priorities_sla
            pri = list(csv.reader(io.StringIO("\n".join(rows_of(cur, "ref_priorities_sla")))))
            cur.executemany(
                "INSERT INTO stage.ref_priorities_parsed(priority,name,reaction_work_hours,resolve_work_hours)"
                " VALUES(%s,%s,%s,%s)",
                [(int(r[0]), r[1], r[2], r[3]) for r in pri[1:] if r and r[0].strip()],
            )

            # production_calendar
            cal = list(csv.reader(io.StringIO("\n".join(rows_of(cur, "ref_production_calendar")))))
            cal_rows = []
            for r in cal[1:]:
                if not r or not r[0].strip():
                    continue
                cal_rows.append((dt.date.fromisoformat(r[0]), r[1], r[2]))
            cur.executemany(
                "INSERT INTO stage.ref_calendar_parsed(cal_date,day_type,work_hours) VALUES(%s,%s,%s)",
                cal_rows,
            )

            # engineer_skills
            sk = list(csv.reader(io.StringIO("\n".join(rows_of(cur, "ref_engineer_skills")))))
            sk_rows = []
            for i, r in enumerate(sk[1:], start=1):
                if not r or not r[0].strip():
                    continue
                level = int(r[2]) if len(r) > 2 and r[2].strip() else None
                sk_rows.append((i, r[0].strip(), r[1].strip(), level))
            cur.executemany(
                "INSERT INTO stage.ref_skills_parsed(rec_no,tab_no,category_code,level) VALUES(%s,%s,%s,%s)",
                sk_rows,
            )

            # duty_schedule
            duty = list(csv.reader(io.StringIO("\n".join(rows_of(cur, "ref_duty_schedule")))))
            duty_rows = []
            for i, r in enumerate(duty[1:], start=1):
                if not r or not r[0].strip():
                    continue
                duty_rows.append((i, r[0].strip(), r[1], r[2]))
            cur.executemany(
                "INSERT INTO stage.ref_duty_parsed(rec_no,tab_no,shift_start,shift_end) VALUES(%s,%s,%s,%s)",
                duty_rows,
            )

        out.append(f"Разобрано: категорий={len(cats)-1}, приоритетов={len(pri)-1}, "
                   f"календарь={len(cal_rows)}, навыков={len(sk_rows)}, дежурств={len(duty_rows)}")
        out.append("")

        with conn.cursor() as cur:
            for title, sql in CHECKS:
                out.append("=" * 100)
                out.append("### " + title)
                out.append("")
                out.append("SQL:")
                out.append(sql.strip())
                out.append("")
                try:
                    cur.execute(sql)
                    cols = [d.name for d in cur.description]
                    data = cur.fetchall()
                    out.append(" | ".join(cols))
                    out.append("-" * 100)
                    for r in data[:100]:
                        vals = []
                        for v in r:
                            s = "" if v is None else str(v)
                            if len(s) > 160:
                                s = s[:160] + "…"
                            vals.append(s)
                        out.append(" | ".join(vals))
                    out.append(f"(строк в результате: {len(data)})")
                except Exception as e:
                    out.append("ОШИБКА: " + str(e))
                out.append("")

    path = os.path.join(ROOT, "docs", "anomalies", "06_ref.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("OK: " + path)


if __name__ == "__main__":
    main()
