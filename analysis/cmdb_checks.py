# -*- coding: utf-8 -*-
"""
Пункт 3.2 ТЗ — анализ качества данных.
Раздел 2: CMDB — оборудование (cmdb/asset_movements.csv) и выгрузка 1С
(cmdb/assets_1c_export.xlsx).

Файл перемещений содержит запятые внутри значений, поэтому его нельзя
разобрать простым разбиением строки — используем полноценный CSV-парсер
(модуль csv) и кладём разобранные данные во вспомогательные таблицы stage,
после чего аномалии ищем обычными SQL-запросами.
"""
import csv
import io
import os
from collections import Counter

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PARSED_DDL = [
    "DROP TABLE IF EXISTS stage.cmdb_movements_parsed",
    r"""
CREATE TABLE stage.cmdb_movements_parsed (
    src_line           BIGINT PRIMARY KEY,
    inv_no             TEXT,
    from_location      TEXT,
    to_location        TEXT,
    moved_on           TEXT,
    performed_by_tab   TEXT
)
""",
    "DROP TABLE IF EXISTS stage.cmdb_assets_parsed",
    r"""
CREATE TABLE stage.cmdb_assets_parsed (
    src_line      BIGINT PRIMARY KEY,
    inv_no        TEXT,
    name          TEXT,
    model         TEXT,
    serial        TEXT,
    accept_date   TEXT,
    cost          TEXT,
    mol_tab       TEXT,
    location      TEXT,
    state         TEXT,
    note          TEXT
)
""",
]

CHECKS = [
    ("M1. Перемещения: количество строк",
     "SELECT count(*) AS rows FROM stage.cmdb_movements_parsed"),

    ("M2. Инвентарные номера в перемещениях: примеры и длина",
     r"""
SELECT DISTINCT inv_no, length(inv_no) AS len
FROM stage.cmdb_movements_parsed
ORDER BY inv_no
LIMIT 15
"""),

    ("M3. Инвентарные номера: не только латиница/цифры/дефис (скрытые символы)",
     r"""
SELECT inv_no,
       inv_no ~ '^[A-Za-z0-9\-]+$' AS ascii_only
FROM (SELECT DISTINCT inv_no FROM stage.cmdb_movements_parsed) t
WHERE inv_no !~ '^[A-Za-z0-9\-]+$'
ORDER BY inv_no
LIMIT 20
"""),

    ("M4. Перемещения: формат даты moved_on",
     r"""
SELECT
    CASE
        WHEN moved_on ~ '^\d{4}-\d{2}-\d{2}$' THEN 'YYYY-MM-DD'
        WHEN moved_on = '' THEN '(пусто)'
        ELSE 'другое'
    END AS fmt, count(*) AS cnt, min(moved_on) AS example
FROM stage.cmdb_movements_parsed
GROUP BY 1 ORDER BY 2 DESC
"""),

    ("M5. Перемещения: дата в будущем",
     r"""
SELECT inv_no, moved_on FROM stage.cmdb_movements_parsed
WHERE moved_on ~ '^\d{4}-\d{2}-\d{2}$' AND moved_on::date > CURRENT_DATE
ORDER BY moved_on LIMIT 20
"""),

    ("M6. Перемещения: 'откуда' = 'куда' (бессмысленное перемещение)",
     r"""
SELECT count(*) AS cnt FROM stage.cmdb_movements_parsed
WHERE from_location = to_location
"""),

    ("M7. Перемещения: пустые значения",
     r"""
SELECT
    count(*) FILTER (WHERE inv_no = '')           AS inv_no,
    count(*) FILTER (WHERE from_location = '')    AS from_location,
    count(*) FILTER (WHERE to_location = '')      AS to_location,
    count(*) FILTER (WHERE moved_on = '')         AS moved_on,
    count(*) FILTER (WHERE performed_by_tab = '') AS performed_by
FROM stage.cmdb_movements_parsed
"""),

    ("M8. Перемещения: филиалы/локации (примеры)",
     r"""
SELECT from_location, count(*) FROM stage.cmdb_movements_parsed
GROUP BY from_location ORDER BY 2 DESC LIMIT 15
"""),

    ("A1. Оборудование (1С): количество строк данных",
     "SELECT count(*) AS rows FROM stage.cmdb_assets_parsed"),

    ("A2. Оборудование: формат инвентарного номера",
     r"""
SELECT
    inv_no,
    length(inv_no) AS len,
    inv_no ~ '^\d+$'        AS all_digits,
    inv_no ~ '^\+'          AS has_plus,
    substring(inv_no from '^(0+)') AS leading_zeros
FROM stage.cmdb_assets_parsed
ORDER BY src_line
LIMIT 10
"""),

    ("A3. Оборудование: инвентарные номера — распределение по префиксу/виду",
     r"""
SELECT
    CASE
        WHEN inv_no ~ '^\d+$'                              THEN 'только цифры'
        WHEN inv_no ~ '^(ИН|ин)-?\d+$'                     THEN 'с префиксом ИН-'
        ELSE 'другое: ' || inv_no
    END AS kind,
    count(*) AS cnt
FROM stage.cmdb_assets_parsed
GROUP BY 1 ORDER BY 2 DESC
LIMIT 15
"""),

    ("A4. Оборудование: стоимость — форматы",
     r"""
SELECT
    CASE
        WHEN cost = '' THEN '(пусто)'
        WHEN cost ~ '^\d{1,3}( \d{3})*,\d{2} руб\.$' THEN '12 345,67 руб.'
        WHEN cost ~ '^\d{1,3}( \d{3})*,\d{2}$'       THEN '12 345,67'
        WHEN cost ~ '^\d+,\d{2}$'                    THEN '12345,67'
        WHEN cost ~ '^\d{1,3}( \d{3})*\.\d{2}$'      THEN '12 345.67 (точка)'
        WHEN cost ~ '^\d+\.\d{2}$'                   THEN '12345.67 (точка)'
        ELSE 'другое'
    END AS fmt, count(*) AS cnt, min(cost) AS example
FROM stage.cmdb_assets_parsed
GROUP BY 1 ORDER BY 2 DESC
"""),

    ("A4а. Оборудование: примеры нестандартной стоимости",
     r"""
SELECT inv_no, cost FROM stage.cmdb_assets_parsed
WHERE cost <> '' AND cost !~ '^\d{1,3}( \d{3})*,\d{2} руб\.$'
ORDER BY src_line LIMIT 20
"""),

    ("A5. Оборудование: формат даты принятия к учёту",
     r"""
SELECT
    CASE
        WHEN accept_date = '' THEN '(пусто)'
        WHEN accept_date ~ '^\d{2}\.\d{2}\.\d{4}$'          THEN 'DD.MM.YYYY'
        WHEN accept_date ~ '^\d{4}-\d{2}-\d{2} 00:00:00$'   THEN 'YYYY-MM-DD 00:00:00'
        WHEN accept_date ~ '^\d{4}-\d{2}-\d{2}$'            THEN 'YYYY-MM-DD'
        WHEN accept_date ~ '^\d{2}\.\d{2}\.\d{4} .*$'       THEN 'DD.MM.YYYY + время'
        ELSE 'другое'
    END AS fmt, count(*) AS cnt, min(accept_date) AS example
FROM stage.cmdb_assets_parsed
GROUP BY 1 ORDER BY 2 DESC
"""),

    ("A6. Оборудование: значения состояния",
     r"""
SELECT state, count(*) AS cnt FROM stage.cmdb_assets_parsed
GROUP BY state ORDER BY 2 DESC
"""),

    ("A7. Оборудование: пустые значения по столбцам",
     r"""
SELECT
    count(*) FILTER (WHERE inv_no = '')      AS inv_no,
    count(*) FILTER (WHERE name = '')        AS name,
    count(*) FILTER (WHERE model = '')       AS model,
    count(*) FILTER (WHERE serial = '')      AS serial,
    count(*) FILTER (WHERE accept_date = '') AS accept_date,
    count(*) FILTER (WHERE cost = '')        AS cost,
    count(*) FILTER (WHERE mol_tab = '')     AS mol_tab,
    count(*) FILTER (WHERE location = '')    AS location,
    count(*) FILTER (WHERE state = '')       AS state
FROM stage.cmdb_assets_parsed
"""),

    ("A8. Оборудование: дубли серийных номеров",
     r"""
SELECT serial, count(*) AS cnt, string_agg(inv_no, ', ') AS inv_nos
FROM stage.cmdb_assets_parsed
WHERE serial <> ''
GROUP BY serial HAVING count(*) > 1
ORDER BY cnt DESC, serial LIMIT 30
"""),

    ("A9. Оборудование: дубли инвентарных номеров",
     r"""
SELECT inv_no, count(*) FROM stage.cmdb_assets_parsed
GROUP BY inv_no HAVING count(*) > 1
"""),

    ("X1. Инвентарные номера 1С: скрытые символы, строчные буквы, пробелы, мусор",
     r"""
SELECT DISTINCT inv_no,
       inv_no ~ 'H'      AS lat_H,
       inv_no ~ ' '      AS has_space,
       inv_no ~ '[а-яё]' AS lower_cyr,
       inv_no = ''       AS empty
FROM stage.cmdb_assets_parsed
WHERE inv_no = ''
   OR inv_no = 'Итого по странице'
   OR inv_no ~ 'H'
   OR inv_no ~ ' '
   OR inv_no ~ '[а-яё]'
ORDER BY inv_no
LIMIT 50
"""),

    ("X2. Инвентарные номера 1С: мусорные строки-итоги", 
     r"""
SELECT count(*) AS junk_rows
FROM stage.cmdb_assets_parsed
WHERE inv_no = 'Итого по странице' OR inv_no = ''
"""),

    ("X2b. Прямая сверка (без нормализации): совпадение номеров перемещений и 1С",
     r"""
SELECT
    count(DISTINCT m.inv_no) AS mov_total,
    count(DISTINCT m.inv_no) FILTER (WHERE EXISTS (
        SELECT 1 FROM stage.cmdb_assets_parsed a WHERE a.inv_no = m.inv_no
    )) AS matched_exact,
    count(DISTINCT m.inv_no) FILTER (WHERE NOT EXISTS (
        SELECT 1 FROM stage.cmdb_assets_parsed a WHERE a.inv_no = m.inv_no
    )) AS unmatched_exact
FROM stage.cmdb_movements_parsed m
"""),

    ("X3. Нормализация и перекрёстная сверка по цифровой части номера",
     r"""
WITH m AS (
    SELECT DISTINCT ltrim(regexp_replace(inv_no, '\D', '', 'g'), '0') AS k
    FROM stage.cmdb_movements_parsed
),
a AS (
    SELECT DISTINCT ltrim(regexp_replace(inv_no, '\D', '', 'g'), '0') AS k
    FROM stage.cmdb_assets_parsed
    WHERE inv_no <> '' AND inv_no <> 'Итого по странице'
)
SELECT
    (SELECT count(*) FROM m) AS mov_keys,
    (SELECT count(*) FROM a) AS asset_keys,
    (SELECT count(*) FROM m WHERE k <> '' AND k NOT IN (SELECT k FROM a)) AS mov_without_asset,
    (SELECT count(*) FROM a WHERE k <> '' AND k NOT IN (SELECT k FROM m)) AS asset_without_move
"""),

    ("X4. Перемещения, ссылающиеся на оборудование, которого нет в 1С (примеры)",
     r"""
WITH a AS (
    SELECT DISTINCT ltrim(regexp_replace(inv_no, '\D', '', 'g'), '0') AS k
    FROM stage.cmdb_assets_parsed
    WHERE inv_no <> '' AND inv_no <> 'Итого по странице'
)
SELECT DISTINCT m.inv_no
FROM stage.cmdb_movements_parsed m
WHERE ltrim(regexp_replace(m.inv_no, '\D', '', 'g'), '0') NOT IN (SELECT k FROM a)
ORDER BY m.inv_no
LIMIT 30
"""),

    ("X5. Дубли серийных номеров: одно устройство в двух форматах номера",
     r"""
SELECT serial, count(*) AS cnt, string_agg(inv_no, '  |  ') AS inv_nos
FROM stage.cmdb_assets_parsed
WHERE serial <> ''
GROUP BY serial HAVING count(*) > 1
ORDER BY cnt DESC, serial
"""),

    ("X6. Сводка аномалий инвентарных номеров 1С",
     r"""
SELECT
    count(*)                                                        AS total_rows,
    count(*) FILTER (WHERE inv_no ~ 'H')                            AS latin_H,
    count(*) FILTER (WHERE inv_no ~ ' ')                            AS with_space,
    count(*) FILTER (WHERE inv_no ~ '[а-яё]')                       AS lowercase_cyr,
    count(*) FILTER (WHERE inv_no = '')                             AS empty_no,
    count(*) FILTER (WHERE inv_no LIKE '%Итого%'
                        OR inv_no LIKE '%Всего%')                   AS junk_totals,
    count(*) FILTER (WHERE inv_no ~ '^\d+$')                        AS plain_digits,
    count(*) FILTER (WHERE inv_no ~ '^ИН-\d+$')                     AS normal_IN_prefix
FROM stage.cmdb_assets_parsed
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


def parse_movements(cur):
    cur.execute("SELECT line_data FROM stage.cmdb_asset_movements ORDER BY src_line")
    lines = [r[0] for r in cur.fetchall()]
    text = "\n".join(lines)
    reader = csv.reader(io.StringIO(text), delimiter=",")
    rows = list(reader)
    header = rows[0]
    data = rows[1:]
    with cur.copy(
        "COPY stage.cmdb_movements_parsed "
        "(src_line, inv_no, from_location, to_location, moved_on, performed_by_tab) "
        "FROM STDIN"
    ) as copy:
        for i, r in enumerate(data, start=2):
            r = (r + ["", "", "", "", ""])[:5]
            copy.write_row((i, r[0].strip(), r[1].strip(), r[2].strip(),
                            r[3].strip(), r[4].strip()))
    return header, len(data)


def parse_assets(cur):
    cur.execute("SELECT src_line, line_data FROM stage.cmdb_assets_1c ORDER BY src_line")
    rows = cur.fetchall()
    header = None
    data = []
    for src_line, line in rows:
        cells = [c.strip() for c in line.split("\t")]
        if header is None:
            if cells and cells[0].startswith("Инвентарный"):
                header = cells
            continue
        data.append((src_line, (cells + [""] * 10)[:10]))
    with cur.copy(
        "COPY stage.cmdb_assets_parsed "
        "(src_line, inv_no, name, model, serial, accept_date, cost, mol_tab, "
        " location, state, note) FROM STDIN"
    ) as copy:
        for src_line, c in data:
            copy.write_row((src_line, c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8], c[9]))
    return header, len(data)


def char_inspection(conn):
    """Поиск скрытых/нестандартных символов в инвентарных номерах (подсказка encode())."""
    out = []
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT inv_no FROM stage.cmdb_movements_parsed")
        mov = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT inv_no FROM stage.cmdb_assets_parsed")
        aset = [r[0] for r in cur.fetchall()]

    def report(title, values):
        out.append("--- " + title + " ---")
        # символы с кодом > 127 и «похожие на дефис»
        dashed = Counter()
        for v in values:
            for ch in v:
                o = ord(ch)
                if o > 127 or ch in ("-", "‐", "‑", "‒", "–", "—", "−", "−"):
                    dashed[(ch, o)] += 1
        for (ch, o), n in sorted(dashed.items(), key=lambda x: -x[1]):
            name = {
                0x2D: "HYPHEN-MINUS (обычный дефис)",
                0x2010: "HYPHEN",
                0x2011: "NON-BREAKING HYPHEN",
                0x2012: "FIGURE DASH",
                0x2013: "EN DASH",
                0x2014: "EM DASH",
                0x2212: "MINUS SIGN",
                0x00A0: "NO-BREAK SPACE",
                0x200B: "ZERO WIDTH SPACE",
                0xFEFF: "ZERO WIDTH NO-BREAK SPACE (BOM)",
            }.get(o, "")
            out.append(f"  {ch!r}  U+{o:04X}  встречается {n} раз  {name}")
        # показать repr нескольких значений
        out.append("  примеры repr():")
        for v in values[:5]:
            out.append("    " + repr(v) + "   ->  " + repr(v.encode("unicode_escape")))
        out.append("")

    report("Инвентарные номера в ПЕРЕМЕЩЕНИЯХ", mov)
    report("Инвентарные номера в 1С (оборудование)", aset)
    return out


def main():
    env = load_env(os.path.join(ROOT, ".env"))
    dsn = (f"host=127.0.0.1 port={env.get('POSTGRES_PORT', '5432')} "
           f"dbname={env['POSTGRES_DB']} user={env['POSTGRES_USER']} "
           f"password={env['POSTGRES_PASSWORD']} connect_timeout=10")

    out = ["АНАЛИЗ КАЧЕСТВА ДАННЫХ — РАЗДЕЛ 2: CMDB (оборудование)", ""]

    with psycopg.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for ddl in PARSED_DDL:
                cur.execute(ddl)
            header_m, n_m = parse_movements(cur)
            header_a, n_a = parse_assets(cur)
        out.append(f"Разобрано перемещений: {n_m}; оборудования из 1С: {n_a}")
        out.append("Заголовок перемещений: " + "; ".join(header_m))
        out.append("")

        out.append("=" * 100)
        out.append("### ПРОВЕРКА СКРЫТЫХ СИМВОЛОВ (подсказка README: через encode())")
        out.append("")
        out.extend(char_inspection(conn))
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
                            if len(s) > 150:
                                s = s[:150] + "…"
                            vals.append(s)
                        out.append(" | ".join(vals))
                    out.append(f"(строк в результате: {len(rows)})")
                except Exception as e:
                    out.append("ОШИБКА: " + str(e))
                out.append("")

    path = os.path.join(ROOT, "docs", "anomalies", "02_cmdb.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("Отчёт сохранён: " + path)


if __name__ == "__main__":
    main()
