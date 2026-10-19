# -*- coding: utf-8 -*-
"""
Пункт 3.8 ТЗ — НАГРУЗОЧНЫЕ ДАННЫЕ.

Генератор создаёт не менее 1 000 000 заявок в core.tickets, опираясь на
распределения, снятые с реальных (легаси) заявок:

  * по категориям, приоритетам и статусам;
  * «день недели x час суток» (по времени создания, часовой пояс МСК);
  * по времени решения (разница «создана -> закрыта»);
  * темы и описания берутся из легаси-заявок (чтобы полнотекстовый поиск
    и агрегаты были осмысленными).

Сгенерированные заявки помечаются префиксом номера GEN-. Это делает запуск
идемпотентным (перед вставкой удаляются прежние GEN-заявки) и не затрагивает
ни легаси-заявки, ни заявки из почты (SD-).

Дополнительно снимается производительность: время генерации, размер таблицы
и индексов, план выполнения типовых запросов на полном объёме и на одном
легаси-наборе (для сравнения). Итог — docs/perf_report.txt.

Запуск:
    python analysis/gen_load.py [ЧИСЛО_ЗАЯВОК]      # по умолчанию 1 000 000
"""
import datetime as dt
import os
import random
import sys
import time

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "importer"))

import load_stage          # noqa: E402  (.env, DSN)

GEN_PREFIX = "GEN-"
DEFAULT_N = 1_000_000
REPORT_PATH = os.path.join(ROOT, "docs", "perf_report.txt")
MSK = dt.timezone(dt.timedelta(hours=3))

# Типовые запросы для замера производительности.
# В каждой строке {} — место дополнительного фильтра (пусто либо легаси).
QUERIES = [
    ("Агрегат: число заявок по категориям за период",
     "SELECT c.name, count(*) AS cnt "
     "FROM core.tickets t JOIN ref.categories c ON c.category_id = t.category_id "
     "WHERE t.created_at >= '2025-01-01+03' AND t.created_at < '2026-01-01+03' {} "
     "GROUP BY c.name ORDER BY cnt DESC"),

    ("Оконная функция: топ-5 исполнителей по месяцам",
     "SELECT * FROM (SELECT date_trunc('month', t.created_at) AS mon, t.executor_id, "
     "count(*) AS cnt, row_number() OVER (PARTITION BY date_trunc('month', t.created_at) "
     "ORDER BY count(*) DESC) AS rn FROM core.tickets t "
     "WHERE t.executor_id IS NOT NULL {} GROUP BY 1,2) s WHERE rn <= 5"),

    ("Полнотекстовый поиск по теме и описанию",
     "SELECT count(*) FROM core.tickets t "
     "WHERE t.description_tsv @@ plainto_tsquery('russian', 'принтер не печатает') {}"),
]


def weighted_counts(rows):
    """[(значение, количество)] -> ([значения], [веса])."""
    values = [r[0] for r in rows]
    weights = [r[1] for r in rows]
    return values, weights


def main():
    n_tickets = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_N
    random.seed(42)            # воспроизводимость результата

    env = load_stage.load_env(os.path.join(ROOT, ".env"))
    dsn = load_stage.dsn_from_env(env)

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # ---------------- распределения из легаси ----------------
            legacy_filter = f"ticket_number NOT LIKE '{GEN_PREFIX}%'"

            cur.execute(f"SELECT category_id, count(*) FROM core.tickets "
                        f"WHERE {legacy_filter} GROUP BY 1 ORDER BY 1")
            cat_ids, cat_w = weighted_counts(cur.fetchall())

            cur.execute(f"SELECT priority, count(*) FROM core.tickets "
                        f"WHERE {legacy_filter} GROUP BY 1 ORDER BY 1")
            prios, prio_w = weighted_counts(cur.fetchall())

            cur.execute(f"SELECT status, count(*) FROM core.tickets "
                        f"WHERE {legacy_filter} GROUP BY 1 ORDER BY 1")
            statuses, status_w = weighted_counts(cur.fetchall())

            # день недели x час суток (МСК)
            cur.execute(
                f"SELECT EXTRACT(ISODOW FROM created_at AT TIME ZONE 'Europe/Moscow')::int AS dow, "
                f"       EXTRACT(HOUR   FROM created_at AT TIME ZONE 'Europe/Moscow')::int AS hr, "
                f"       count(*) FROM core.tickets WHERE {legacy_filter} GROUP BY 1,2")
            hours_by_dow = {d: ([], []) for d in range(1, 8)}
            dow_w = {d: 0 for d in range(1, 8)}
            for dow, hr, cnt in cur.fetchall():
                hours_by_dow[dow][0].append(hr)
                hours_by_dow[dow][1].append(cnt)
                dow_w[dow] += cnt
            dow_values = sorted(dow_w)
            dow_weights = [dow_w[d] for d in dow_values]

            # время решения (часы от создания до закрытия)
            cur.execute(
                f"SELECT EXTRACT(EPOCH FROM (closed_at - created_at)) / 3600.0 "
                f"FROM core.tickets WHERE {legacy_filter} "
                f"AND closed_at IS NOT NULL AND closed_at > created_at")
            resolutions = [float(r[0]) for r in cur.fetchall() if r[0] is not None]

            # период легаси-данных
            cur.execute(f"SELECT min(created_at), max(created_at) FROM core.tickets "
                        f"WHERE {legacy_filter}")
            d_min, d_max = cur.fetchone()

            # темы/описания по категориям (для осмысленного текста)
            cur.execute(f"SELECT category_id, subject, description FROM core.tickets "
                        f"WHERE {legacy_filter}")
            texts_by_cat = {c: [] for c in cat_ids}
            for cid, subj, desc in cur.fetchall():
                texts_by_cat.setdefault(cid, []).append((subj, desc))

            # действующие сотрудники и их подразделения
            cur.execute("SELECT employee_id, department_id FROM core.employees "
                        "WHERE is_active")
            employees = cur.fetchall()

            legacy_max_id = None
            cur.execute(f"SELECT max(ticket_id) FROM core.tickets WHERE {legacy_filter}")
            legacy_max_id = cur.fetchone()[0]

        # даты периода: для каждого дня недели — свой список дат
        dates_by_dow = {d: [] for d in range(1, 8)}
        day = d_min.astimezone(MSK).date()
        last = d_max.astimezone(MSK).date()
        while day <= last:
            dates_by_dow[day.isoweekday()].append(day)
            day += dt.timedelta(days=1)

        # ---------------- генерация ----------------
        print(f"Генерация {n_tickets} заявок...", flush=True)
        t0 = time.time()
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM core.tickets WHERE ticket_number LIKE '{GEN_PREFIX}%'")

            cols = ("ticket_number, author_id, category_id, priority, status, subject, "
                    "description, executor_id, department_id, created_at, updated_at, closed_at")
            seq = 0
            with cur.copy(f"COPY core.tickets ({cols}) FROM STDIN") as copy:
                for _ in range(n_tickets):
                    cid = random.choices(cat_ids, cat_w, k=1)[0]
                    subj, desc = random.choice(texts_by_cat.get(cid) or [("Заявка", None)])
                    prio = random.choices(prios, prio_w, k=1)[0]
                    status = random.choices(statuses, status_w, k=1)[0]
                    emp_id, dept = random.choice(employees)

                    dow = random.choices(dow_values, dow_weights, k=1)[0]
                    hrs, hw = hours_by_dow[dow]
                    hr = random.choices(hrs, hw, k=1)[0] if hrs else 10
                    day = random.choice(dates_by_dow[dow])
                    created = dt.datetime(day.year, day.month, day.day, hr,
                                          random.randint(0, 59), random.randint(0, 59),
                                          tzinfo=MSK)

                    closed = None
                    if status == "Закрыта" and resolutions:
                        closed = created + dt.timedelta(hours=random.choice(resolutions))

                    seq += 1
                    number = f"{GEN_PREFIX}{seq:07d}"
                    copy.write_row((number, emp_id, cid, prio, status, subj, desc,
                                    emp_id, dept, created, closed or created, closed))
            conn.commit()
        gen_seconds = time.time() - t0
        rate = n_tickets / gen_seconds if gen_seconds else 0
        print(f"  вставлено за {gen_seconds:.1f} c ({rate:,.0f} строк/с)".replace(",", " "),
              flush=True)

        # ---------------- замеры производительности ----------------
        out = []
        w = out.append
        w("ОТЧЁТ О НАГРУЗОЧНЫХ ДАННЫХ И ПРОИЗВОДИТЕЛЬНОСТИ (пункт 3.8 ТЗ)")
        w("")
        w("ПАРАМЕТРЫ ГЕНЕРАЦИИ")
        w(f"  сгенерировано заявок        : {n_tickets:,}".replace(",", " "))
        w(f"  метка номера                : {GEN_PREFIX}0000001 ...")
        w(f"  период создания             : {d_min.astimezone(MSK):%d.%m.%Y} .. "
          f"{d_max.astimezone(MSK):%d.%m.%Y} (как в легаси)")
        w(f"  распределения сняты с       : легаси-заявок (без {GEN_PREFIX} и SD-)")
        w("  генератор воспроизводим     : фиксированное зерно (seed = 42)")
        w("")
        w("ВРЕМЯ ГЕНЕРАЦИИ")
        w(f"  вставлено заявок            : {n_tickets:,}".replace(",", " "))
        w(f"  затрачено времени           : {gen_seconds:.1f} c")
        w(f"  скорость                    : {rate:,.0f} строк/с".replace(",", " "))
        w("")

        with conn.cursor() as cur:
            cur.execute("SELECT pg_size_pretty(pg_total_relation_size('core.tickets')), "
                        "       pg_size_pretty(pg_relation_size('core.tickets')), "
                        "       pg_size_pretty(pg_indexes_size('core.tickets'))")
            total, tbl, idx = cur.fetchone()
            w("РАЗМЕР ТАБЛИЦЫ core.tickets")
            w(f"  всего с индексами           : {total}")
            w(f"  данные                      : {tbl}")
            w(f"  индексы                     : {idx}")
            w("")

            # сверка фактического распределения с целевым
            w("КОНТРОЛЬ РАСПРЕДЕЛЕНИЙ (легаси vs сгенерированные)")
            for title, expr, join in (
                    ("категория", "c.name",
                     "JOIN ref.categories c ON c.category_id = t.category_id"),
                    ("приоритет", "t.priority::text", ""),
                    ("статус", "t.status::text", "")):
                cur.execute(
                    f"SELECT {expr}, "
                    f"  count(*) FILTER (WHERE t.ticket_number NOT LIKE '{GEN_PREFIX}%') AS a, "
                    f"  count(*) FILTER (WHERE t.ticket_number LIKE '{GEN_PREFIX}%') AS b "
                    f"FROM core.tickets t {join} GROUP BY 1 ORDER BY 1")
                rows = cur.fetchall()
                ta = sum(r[1] for r in rows) or 1
                tb = sum(r[2] for r in rows) or 1
                w(f"  {title}:")
                for val, a, b in rows:
                    w(f"    {str(val):<24} легаси {a / ta * 100:5.1f}%   "
                      f"сгенерировано {b / tb * 100:5.1f}%")
            w("")

            # планы выполнения
            cur.execute("ANALYZE core.tickets")
            w("ПРОИЗВОДИТЕЛЬНОСТЬ ЗАПРОСОВ (EXPLAIN ANALYZE)")
            w(f"  полный объём: {n_tickets:,} + легаси; сравнение — только легаси"
              .replace(",", " "))
            w("")
            for title, sql in QUERIES:
                for label, extra in (("полный объём", ""),
                                     ("только легаси",
                                      f"AND t.ticket_id <= {legacy_max_id}")):
                    cur.execute("EXPLAIN (ANALYZE, BUFFERS, TIMING) " + sql.format(extra))
                    plan = "\n".join(r[0] for r in cur.fetchall())
                    exec_ms = ""
                    for line in plan.splitlines():
                        if "Execution Time" in line:
                            exec_ms = line.strip()
                    w(f"  - {title} [{label}]: {exec_ms}")
            w("")

            cur.execute(f"SELECT count(*) FROM core.tickets "
                        f"WHERE ticket_number LIKE '{GEN_PREFIX}%'")
            gen_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM core.tickets")
            all_count = cur.fetchone()[0]

        w("ИТОГ")
        w(f"  сгенерированных заявок в БД : {gen_count:,}".replace(",", " "))
        w(f"  всего заявок в таблице      : {all_count:,}".replace(",", " "))
        w("")
        w("ПРИМЕЧАНИЯ")
        w("  * сгенерированные заявки помечены префиксом GEN- и удаляются запросом")
        w("    DELETE FROM core.tickets WHERE ticket_number LIKE 'GEN-%';")
        w("  * плотность заявок высокая намеренно — это нагрузочный набор;")
        w("  * колонка description_tsv (полнотекстовый индекс) заполняется")
        w("    автоматически при вставке и входит в замер времени генерации.")

        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")

    print("Отчёт сохранён:", REPORT_PATH, flush=True)


if __name__ == "__main__":
    main()
