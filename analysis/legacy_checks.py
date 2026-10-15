# -*- coding: utf-8 -*-
"""
Пункт 3.2 ТЗ — анализ качества данных.
Раздел 3: легаси-система заявок (legacy/tickets.csv, legacy/ticket_status_history.csv).

Файл tickets.csv содержит многострочные поля в кавычках, поэтому парсим его
модулем csv (а не split), после чего кладём в вспомогательные таблицы stage и
ищем аномалии SQL-запросами.
"""
import csv
import io
import os

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PARSED_DDL = [
    "DROP TABLE IF EXISTS stage.legacy_tickets_parsed",
    r"""
CREATE TABLE stage.legacy_tickets_parsed (
    rec_no        BIGINT PRIMARY KEY,
    number        TEXT,
    created       TEXT,
    author_tab    TEXT,
    category      TEXT,
    priority      TEXT,
    subject       TEXT,
    description   TEXT,
    assignee_tab  TEXT,
    status        TEXT,
    closed        TEXT,
    inv_no        TEXT
)
""",
    "DROP TABLE IF EXISTS stage.legacy_status_history_parsed",
    r"""
CREATE TABLE stage.legacy_status_history_parsed (
    rec_no      BIGINT PRIMARY KEY,
    ticket_no   TEXT,
    moment      TEXT,
    old_status  TEXT,
    new_status  TEXT,
    who_tab     TEXT
)
""",
]

CHECKS = [
    ("T1. Заявки: количество записей (после разбора)", 
     "SELECT count(*) AS rows FROM stage.legacy_tickets_parsed"),

    ("T2. Статусы заявок (сырые значения)",
     r"""
SELECT status, count(*) AS cnt FROM stage.legacy_tickets_parsed
GROUP BY status ORDER BY cnt DESC
"""),

    ("T2а. Статусы заявок без учёта регистра (нормализация key)",
     r"""
SELECT lower(status) AS status_norm, count(*) AS cnt,
       string_agg(DISTINCT status, ', ') AS variants
FROM stage.legacy_tickets_parsed
GROUP BY lower(status) ORDER BY cnt DESC
"""),

    ("T3. Категории заявок",
     r"""
SELECT category, count(*) AS cnt FROM stage.legacy_tickets_parsed
GROUP BY category ORDER BY cnt DESC
"""),

    ("T4. Приоритеты",
     r"""
SELECT priority, count(*) AS cnt FROM stage.legacy_tickets_parsed
GROUP BY priority ORDER BY cnt DESC
"""),

    ("T5. Форматы даты создания",
     r"""
SELECT
    CASE
        WHEN created ~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$' THEN 'YYYY-MM-DD HH:MM:SS'
        WHEN created = '' THEN '(пусто)'
        ELSE 'другое: ' || created
    END AS fmt, count(*) AS cnt
FROM stage.legacy_tickets_parsed
GROUP BY 1 ORDER BY 2 DESC LIMIT 10
"""),

    ("T6. Форматы даты закрытия",
     r"""
SELECT
    CASE
        WHEN closed = '' THEN '(пусто)'
        WHEN closed ~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$' THEN 'YYYY-MM-DD HH:MM:SS'
        ELSE 'другое: ' || closed
    END AS fmt, count(*) AS cnt
FROM stage.legacy_tickets_parsed
GROUP BY 1 ORDER BY 2 DESC LIMIT 10
"""),

    ("T7. Закрыта РАНЬШЕ создания",
     r"""
SELECT number, created, closed FROM stage.legacy_tickets_parsed
WHERE created ~ '^\d{4}-' AND closed ~ '^\d{4}-'
  AND closed::timestamp < created::timestamp
ORDER BY 1 LIMIT 20
"""),

    ("T8. Статус 'закрыта', но нет даты закрытия (и наоборот)",
     r"""
SELECT
    count(*) FILTER (WHERE lower(status) LIKE '%закрыт%' AND closed = '') AS closed_status_no_date,
    count(*) FILTER (WHERE closed <> '' AND lower(status) NOT LIKE '%закрыт%') AS date_but_not_closed
FROM stage.legacy_tickets_parsed
"""),

    ("T9. 🔐 Секреты в текстах заявок (пароль/логин)",
     r"""
SELECT
    count(*) FILTER (WHERE description ~* '(парол|password|passwd|pwd|passwd|Pa\$\$)') AS with_password,
    count(*) FILTER (WHERE description ~* 'логин') AS with_login,
    count(*) FILTER (WHERE subject  ~* '(парол|password|логин)') AS in_subject
FROM stage.legacy_tickets_parsed
"""),

    ("T9а. Примеры заявок с паролями",
     r"""
SELECT number, subject, left(description, 160) AS description
FROM stage.legacy_tickets_parsed
WHERE description ~* '(парол|password|passwd|pwd|Pa\$\$)'
ORDER BY number LIMIT 15
"""),

    ("T10. Пустые значения по столбцам заявок",
     r"""
SELECT
    count(*) FILTER (WHERE number = '')       AS number,
    count(*) FILTER (WHERE created = '')      AS created,
    count(*) FILTER (WHERE author_tab = '')   AS author_tab,
    count(*) FILTER (WHERE category = '')     AS category,
    count(*) FILTER (WHERE priority = '')     AS priority,
    count(*) FILTER (WHERE subject = '')      AS subject,
    count(*) FILTER (WHERE description = '')  AS description,
    count(*) FILTER (WHERE assignee_tab = '') AS assignee_tab,
    count(*) FILTER (WHERE status = '')       AS status,
    count(*) FILTER (WHERE closed = '')       AS closed,
    count(*) FILTER (WHERE inv_no = '')       AS inv_no
FROM stage.legacy_tickets_parsed
"""),

    ("T11. Многострочные описания (перенос строки внутри)",
     r"""
SELECT count(*) AS multiline FROM stage.legacy_tickets_parsed
WHERE description LIKE '%' || chr(10) || '%'
"""),

    ("T12. Автор(таб) — не найден среди сотрудников",
     r"""
SELECT count(*) AS unknown_authors
FROM (SELECT DISTINCT author_tab FROM stage.legacy_tickets_parsed WHERE author_tab <> '') t
WHERE NOT EXISTS (SELECT 1 FROM stage.v_hr_employees e WHERE e.tab_no = t.author_tab)
"""),

    ("T13. Исполнитель(таб) — не найден среди сотрудников",
     r"""
SELECT count(*) AS unknown_assignees
FROM (SELECT DISTINCT assignee_tab FROM stage.legacy_tickets_parsed WHERE assignee_tab <> '') t
WHERE NOT EXISTS (SELECT 1 FROM stage.v_hr_employees e WHERE e.tab_no = t.assignee_tab)
"""),

    ("T14. Номера заявок: дубликаты",
     r"""
SELECT number, count(*) FROM stage.legacy_tickets_parsed
GROUP BY number HAVING count(*) > 1
"""),

    ("T15. Инвентарный номер в заявке: примеры форматов",
     r"""
SELECT
    CASE
        WHEN inv_no = '' THEN '(пусто)'
        WHEN inv_no ~ '^ИН-\d+$' THEN 'ИН-NNNNNN'
        WHEN inv_no ~ '^\d+$' THEN 'NNNNNN'
        ELSE 'другое: ' || inv_no
    END AS fmt, count(*) AS cnt
FROM stage.legacy_tickets_parsed
GROUP BY 1 ORDER BY 2 DESC LIMIT 10
"""),

    ("H1. История статусов: количество записей",
     "SELECT count(*) AS rows FROM stage.legacy_status_history_parsed"),

    ("H2. История: новые статусы (сырые значения)",
     r"""
SELECT new_status, count(*) AS cnt FROM stage.legacy_status_history_parsed
GROUP BY new_status ORDER BY cnt DESC
"""),

    ("H3. История: старые статусы (сырые значения)",
     r"""
SELECT old_status, count(*) AS cnt FROM stage.legacy_status_history_parsed
GROUP BY old_status ORDER BY cnt DESC
"""),

    ("H4. Истории статусов: все статусы вместе (единый набор)",
     r"""
SELECT s, count(*) AS cnt FROM (
    SELECT old_status AS s FROM stage.legacy_status_history_parsed WHERE old_status <> ''
    UNION ALL
    SELECT new_status FROM stage.legacy_status_history_parsed WHERE new_status <> ''
) u
GROUP BY s ORDER BY cnt DESC
"""),

    ("H5. История: Кто(таб) — не найден среди сотрудников",
     r"""
SELECT count(*) AS unknown
FROM (SELECT DISTINCT who_tab FROM stage.legacy_status_history_parsed WHERE who_tab <> '') t
WHERE NOT EXISTS (SELECT 1 FROM stage.v_hr_employees e WHERE e.tab_no = t.who_tab)
"""),

    ("H6. История: заявка, которой нет в списке заявок",
     r"""
SELECT count(DISTINCT h.ticket_no) AS orphan_tickets
FROM stage.legacy_status_history_parsed h
WHERE NOT EXISTS (
    SELECT 1 FROM stage.legacy_tickets_parsed t WHERE t.number = h.ticket_no
)
"""),

    ("H7. ⏰ Нестыковка времени: событие истории РАНЬШЕ создания заявки (часовые пояса?)",
     r"""
SELECT count(*) AS events_before_creation,
       count(DISTINCT h.ticket_no) AS tickets_affected,
       min(h.moment) AS min_moment,
       max(t.created) AS max_created
FROM stage.legacy_status_history_parsed h
JOIN stage.legacy_tickets_parsed t ON t.number = h.ticket_no
WHERE h.moment ~ '^\d{4}-' AND t.created ~ '^\d{4}-'
  AND h.moment::timestamp < t.created::timestamp
"""),

    ("H8. Заявки без истории статусов",
     r"""
SELECT count(*) AS no_history
FROM stage.legacy_tickets_parsed t
WHERE NOT EXISTS (
    SELECT 1 FROM stage.legacy_status_history_parsed h WHERE h.ticket_no = t.number
)
"""),

    ("H9. Заявки: статус в карточке != последний статус в истории",
     r"""
WITH last_h AS (
    SELECT DISTINCT ON (ticket_no) ticket_no, new_status, moment
    FROM stage.legacy_status_history_parsed
    ORDER BY ticket_no, moment::timestamp DESC
)
SELECT count(*) AS mismatched
FROM stage.legacy_tickets_parsed t
JOIN last_h h ON h.ticket_no = t.number
WHERE lower(t.status) <> lower(h.new_status)
"""),

    ("T16. Даты с таймзоной в created/closed",
     r"""
SELECT
    count(*) FILTER (WHERE created ~ 'T' OR created ~ '\+') AS created_has_tz,
    count(*) FILTER (WHERE closed  ~ 'T' OR closed  ~ '\+') AS closed_has_tz
FROM stage.legacy_tickets_parsed
"""),

    ("H10. Статус карточки vs последний из истории (нормализация)",
     r"""
WITH last_h AS (
    SELECT DISTINCT ON (ticket_no) ticket_no, lower(new_status) AS st
    FROM stage.legacy_status_history_parsed
    ORDER BY ticket_no, moment::timestamp DESC
)
SELECT count(*) AS mismatched_norm
FROM stage.legacy_tickets_parsed t
JOIN last_h h ON h.ticket_no = t.number
WHERE trim(lower(t.status)) <> trim(h.st)
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

def parse_legacy(cur):
    # tickets.csv
    cur.execute("SELECT line_data FROM stage.legacy_tickets ORDER BY src_line")
    text = "\n".join(r[0] for r in cur.fetchall())
    reader = csv.reader(io.StringIO(text), delimiter=";")
    rows = list(reader)
    header = rows[0]
    data = [r for r in rows[1:] if not (len(r) == 0 or (len(r) == 1 and r[0].strip() == ""))]
    from collections import Counter
    widths = Counter(len(r) for r in data)
    bad = [r for r in data if len(r) != 11]
    with cur.copy(
        "COPY stage.legacy_tickets_parsed (rec_no, number, created, author_tab, category,"
        " priority, subject, description, assignee_tab, status, closed, inv_no) FROM STDIN"
    ) as copy:
        for i, r in enumerate(data, start=1):
            r = (r + [""] * 11)[:11]
            copy.write_row((i, *[c.strip() for c in r]))
    tickets_n = len(data)

    # ticket_status_history.csv
    cur.execute("SELECT line_data FROM stage.legacy_ticket_status_history ORDER BY src_line")
    text2 = "\n".join(r[0] for r in cur.fetchall())
    reader2 = csv.reader(io.StringIO(text2), delimiter=";")
    rows2 = list(reader2)
    header2 = rows2[0]
    data2 = [r for r in rows2[1:] if not (len(r) == 0 or (len(r) == 1 and r[0].strip() == ""))]
    with cur.copy(
        "COPY stage.legacy_status_history_parsed (rec_no, ticket_no, moment, old_status,"
        " new_status, who_tab) FROM STDIN"
    ) as copy:
        for i, r in enumerate(data2, start=1):
            r = (r + [""] * 5)[:5]
            copy.write_row((i, *[c.strip() for c in r]))
    hist_n = len(data2)
    return header, tickets_n, header2, hist_n, widths, bad

def main():
    env = load_env(os.path.join(ROOT, ".env"))
    dsn = (f"host=127.0.0.1 port={env.get('POSTGRES_PORT', '5432')} "
           f"dbname={env['POSTGRES_DB']} user={env['POSTGRES_USER']} "
           f"password={env['POSTGRES_PASSWORD']} connect_timeout=10")

    out = ["АНАЛИЗ КАЧЕСТВА ДАННЫХ — РАЗДЕЛ 3: ЛЕГАСИ-СИСТЕМА ЗАЯВОК (legacy/)", ""]

    with psycopg.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for ddl in PARSED_DDL:
                cur.execute(ddl)
            header, tickets_n, header2, hist_n, widths, bad = parse_legacy(cur)
        out.append(f"Разобрано заявок: {tickets_n}; записей истории статусов: {hist_n}")
        out.append("Заголовок tickets.csv: " + "; ".join(header))
        out.append("Заголовок history.csv: " + "; ".join(header2))
        out.append(f"Распределение числа полей в записях tickets: {dict(widths)}")
        out.append(f"Записей с числом полей != 11: {len(bad)}")
        for r in bad[:10]:
            out.append("   подозрительная запись: " + repr([str(x)[:40] for x in r]))
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
                    rows = cur.fetchall()
                    out.append(" | ".join(cols))
                    out.append("-" * 100)
                    for r in rows[:100]:
                        vals = []
                        for v in r:
                            s = "" if v is None else str(v)
                            if len(s) > 160:
                                s = s[:160] + "…"
                            vals.append(s)
                        out.append(" | ".join(vals))
                    out.append(f"(строк в результате: {len(rows)})")
                except Exception as e:
                    out.append("ОШИБКА: " + str(e))
                out.append("")

    path = os.path.join(ROOT, "docs", "anomalies", "03_legacy.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("OK: " + path)

if __name__ == "__main__":
    main()
