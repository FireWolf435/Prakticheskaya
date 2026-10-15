# -*- coding: utf-8 -*-
"""
Пункт 3.2 ТЗ — анализ качества данных.
Раздел 1: кадровая система (hr/employees.csv, hr/departments.csv).

Скрипт:
  1) создаёт представления stage.v_hr_employees / _dates / _departments,
     которые «разбирают» строку CSV на колонки;
  2) прогоняет набор проверок-аномалий;
  3) сохраняет результат в docs/anomalies/01_hr.txt.
"""
import os

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VIEWS = [
    "DROP VIEW IF EXISTS stage.v_hr_employees_dates CASCADE",
    "DROP VIEW IF EXISTS stage.v_hr_departments CASCADE",
    "DROP VIEW IF EXISTS stage.v_hr_employees CASCADE",
    r"""
CREATE OR REPLACE VIEW stage.v_hr_employees AS
SELECT
    src_line,
    btrim(split_part(line_data, ';', 1))  AS tab_no,
    btrim(split_part(line_data, ';', 2))  AS fio,
    btrim(split_part(line_data, ';', 3))  AS gender,
    btrim(split_part(line_data, ';', 4))  AS birth_raw,
    btrim(split_part(line_data, ';', 5))  AS snils_raw,
    btrim(split_part(line_data, ';', 6))  AS dept_code,
    btrim(split_part(line_data, ';', 7))  AS job_title,
    btrim(split_part(line_data, ';', 8))  AS hire_raw,
    btrim(split_part(line_data, ';', 9))  AS fire_raw,
    btrim(split_part(line_data, ';', 10)) AS phone_raw,
    btrim(split_part(line_data, ';', 11)) AS email_raw
FROM stage.hr_employees
WHERE src_line > 1
""",
    r"""
CREATE OR REPLACE VIEW stage.v_hr_departments AS
SELECT
    src_line,
    btrim(split_part(line_data, ';', 1)) AS dept_code,
    btrim(split_part(line_data, ';', 2)) AS parent_code,
    btrim(split_part(line_data, ';', 3)) AS name,
    btrim(split_part(line_data, ';', 4)) AS head_tab
FROM stage.hr_departments
WHERE src_line > 1
""",
    # Разбор дат с учётом неоднозначности двузначного года.
    # Для даты рождения: yy >= 30 -> 19yy, иначе 20yy.
    # Для дат приёма/увольнения (недавние): yy -> 20yy.
    r"""
CREATE OR REPLACE VIEW stage.v_hr_employees_dates AS
SELECT
    src_line, tab_no, fio, birth_raw, hire_raw, fire_raw,
    CASE
        WHEN birth_raw ~ '^\d{2}\.\d{2}\.\d{4}$' THEN to_date(birth_raw, 'DD.MM.YYYY')
        WHEN birth_raw ~ '^\d{2}/\d{2}/\d{4}$'   THEN to_date(birth_raw, 'DD/MM/YYYY')
        WHEN birth_raw ~ '^\d{4}-\d{2}-\d{2}$'   THEN to_date(birth_raw, 'YYYY-MM-DD')
        WHEN birth_raw ~ '^\d{2}\.\d{2}\.\d{2}$' THEN make_date(
                (CASE WHEN substring(birth_raw, 7, 2)::int >= 30 THEN 1900 ELSE 2000 END)
                + substring(birth_raw, 7, 2)::int,
                substring(birth_raw, 4, 2)::int,
                substring(birth_raw, 1, 2)::int)
    END AS birth_date,
    CASE
        WHEN hire_raw ~ '^\d{2}\.\d{2}\.\d{4}$' THEN to_date(hire_raw, 'DD.MM.YYYY')
        WHEN hire_raw ~ '^\d{2}/\d{2}/\d{4}$'   THEN to_date(hire_raw, 'DD/MM/YYYY')
        WHEN hire_raw ~ '^\d{4}-\d{2}-\d{2}$'   THEN to_date(hire_raw, 'YYYY-MM-DD')
        WHEN hire_raw ~ '^\d{2}\.\d{2}\.\d{2}$' THEN make_date(
                2000 + substring(hire_raw, 7, 2)::int,
                substring(hire_raw, 4, 2)::int,
                substring(hire_raw, 1, 2)::int)
    END AS hire_date,
    CASE
        WHEN fire_raw ~ '^\d{2}\.\d{2}\.\d{4}$' THEN to_date(fire_raw, 'DD.MM.YYYY')
        WHEN fire_raw ~ '^\d{2}/\d{2}/\d{4}$'   THEN to_date(fire_raw, 'DD/MM/YYYY')
        WHEN fire_raw ~ '^\d{4}-\d{2}-\d{2}$'   THEN to_date(fire_raw, 'YYYY-MM-DD')
        WHEN fire_raw ~ '^\d{2}\.\d{2}\.\d{2}$' THEN make_date(
                2000 + substring(fire_raw, 7, 2)::int,
                substring(fire_raw, 4, 2)::int,
                substring(fire_raw, 1, 2)::int)
    END AS fire_date
FROM stage.v_hr_employees
""",
]

CHECKS = [
    ("1. Общее количество сотрудников",
     "SELECT count(*) AS rows FROM stage.v_hr_employees"),

    ("2. Форматы даты рождения",
     r"""
SELECT
    CASE
        WHEN birth_raw = '' THEN '(пусто)'
        WHEN birth_raw ~ '^\d{2}\.\d{2}\.\d{4}$' THEN 'DD.MM.YYYY'
        WHEN birth_raw ~ '^\d{2}/\d{2}/\d{4}$'   THEN 'DD/MM/YYYY'
        WHEN birth_raw ~ '^\d{2}\.\d{2}\.\d{2}$' THEN 'DD.MM.YY'
        WHEN birth_raw ~ '^\d{4}-\d{2}-\d{2}$'   THEN 'YYYY-MM-DD'
        ELSE 'другое'
    END AS format,
    count(*) AS cnt,
    min(birth_raw) AS example
FROM stage.v_hr_employees
GROUP BY 1
ORDER BY 2 DESC
"""),

    ("3. Форматы даты приёма (ДатаПриема)",
     r"""
SELECT
    CASE
        WHEN hire_raw = '' THEN '(пусто)'
        WHEN hire_raw ~ '^\d{2}\.\d{2}\.\d{4}$' THEN 'DD.MM.YYYY'
        WHEN hire_raw ~ '^\d{2}/\d{2}/\d{4}$'   THEN 'DD/MM/YYYY'
        WHEN hire_raw ~ '^\d{2}\.\d{2}\.\d{2}$' THEN 'DD.MM.YY'
        WHEN hire_raw ~ '^\d{4}-\d{2}-\d{2}$'   THEN 'YYYY-MM-DD'
        ELSE 'другое'
    END AS format,
    count(*) AS cnt,
    min(hire_raw) AS example
FROM stage.v_hr_employees
GROUP BY 1
ORDER BY 2 DESC
"""),

    ("4. Форматы даты увольнения (ДатаУвольнения)",
     r"""
SELECT
    CASE
        WHEN fire_raw = '' THEN '(пусто)'
        WHEN fire_raw ~ '^\d{2}\.\d{2}\.\d{4}$' THEN 'DD.MM.YYYY'
        WHEN fire_raw ~ '^\d{2}/\d{2}/\d{4}$'   THEN 'DD/MM/YYYY'
        WHEN fire_raw ~ '^\d{2}\.\d{2}\.\d{2}$' THEN 'DD.MM.YY'
        WHEN fire_raw ~ '^\d{4}-\d{2}-\d{2}$'   THEN 'YYYY-MM-DD'
        ELSE 'другое'
    END AS format,
    count(*) AS cnt,
    min(fire_raw) AS example
FROM stage.v_hr_employees
GROUP BY 1
ORDER BY 2 DESC
"""),

    ("5. Форматы телефона (ТелефонМоб) — детально",
     r"""
SELECT
    CASE
        WHEN phone_raw = '' THEN '(пусто)'
        WHEN phone_raw ~ '^доб\.'                         THEN 'внутренний (доб. NNN)'
        WHEN phone_raw ~ '^\+7\d{10}$'                    THEN '+7XXXXXXXXXX'
        WHEN phone_raw ~ '^8\d{10}$'                      THEN '8XXXXXXXXXX (без дефисов)'
        WHEN phone_raw ~ '^8-\d{3}-\d{3}-\d{2}-\d{2}$'    THEN '8-XXX-XXX-XX-XX'
        WHEN phone_raw ~ '^\+7'                           THEN '+7 с разделителями'
        WHEN phone_raw ~ '^7'                             THEN '7XXXXXXXXXX (без +)'
        ELSE 'другое'
    END AS format,
    count(*) AS cnt,
    min(phone_raw) AS example
FROM stage.v_hr_employees
GROUP BY 1
ORDER BY 2 DESC
"""),

    ("5а. Нестандартные телефоны (примеры)",
     r"""
SELECT tab_no, fio, phone_raw
FROM stage.v_hr_employees
WHERE phone_raw <> ''
  AND phone_raw !~ '^\+7\d{10}$'
  AND phone_raw !~ '^8-\d{3}-\d{3}-\d{2}-\d{2}$'
ORDER BY src_line
LIMIT 30
"""),

    ("6. Проверка формата email",
     r"""
SELECT
    count(*) FILTER (WHERE email_raw = '') AS empty,
    count(*) FILTER (WHERE email_raw <> ''
                       AND email_raw ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$') AS valid,
    count(*) FILTER (WHERE email_raw <> ''
                       AND email_raw !~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$') AS invalid
FROM stage.v_hr_employees
"""),

    ("6а. Сотрудники без email",
     r"""
SELECT tab_no, fio
FROM stage.v_hr_employees
WHERE email_raw = ''
ORDER BY src_line
"""),

    ("7. СНИЛС: формат",
     r"""
SELECT
    count(*) FILTER (WHERE snils_raw = '') AS empty,
    count(*) FILTER (WHERE snils_raw ~ '^\d{3}-\d{3}-\d{3} \d{2}$') AS well_formed,
    count(*) FILTER (WHERE snils_raw <> ''
                       AND snils_raw !~ '^\d{3}-\d{3}-\d{3} \d{2}$') AS bad_format
FROM stage.v_hr_employees
"""),

    ("8. СНИЛС: неверная контрольная сумма",
     r"""
SELECT tab_no, fio, snils_raw
FROM stage.v_hr_employees
WHERE snils_raw ~ '^\d{3}-\d{3}-\d{3} \d{2}$'
  AND NOT ref.snils_valid(snils_raw)
ORDER BY src_line
"""),

    ("9. СНИЛС: дубликаты (один СНИЛС у нескольких табельных номеров)",
     r"""
SELECT snils_raw, count(*) AS cnt,
       string_agg(tab_no || ' (' || fio || ')', '; ' ORDER BY tab_no) AS people
FROM stage.v_hr_employees
WHERE snils_raw <> ''
GROUP BY snils_raw
HAVING count(*) > 1
ORDER BY cnt DESC, snils_raw
"""),

    ("10. Дубли ФИО (одинаковое ФИО, разные табельные номера)",
     r"""
SELECT fio, count(*) AS cnt, string_agg(tab_no, ', ' ORDER BY tab_no) AS tab_nos
FROM stage.v_hr_employees
GROUP BY fio
HAVING count(*) > 1
ORDER BY cnt DESC, fio
"""),

    ("11. Дубли табельных номеров",
     r"""
SELECT tab_no, count(*) AS cnt
FROM stage.v_hr_employees
GROUP BY tab_no
HAVING count(*) > 1
ORDER BY cnt DESC, tab_no
"""),

    ("12. Табельный номер: не число",
     r"""
SELECT tab_no, fio FROM stage.v_hr_employees WHERE tab_no !~ '^\d+$' ORDER BY src_line
"""),

    ("13. Пол: распределение значений",
     r"""
SELECT gender, count(*) AS cnt FROM stage.v_hr_employees GROUP BY gender ORDER BY cnt DESC
"""),

    ("14. Пустые значения по столбцам",
     r"""
SELECT
    count(*) FILTER (WHERE tab_no = '')    AS tab_no,
    count(*) FILTER (WHERE fio = '')       AS fio,
    count(*) FILTER (WHERE gender = '')    AS gender,
    count(*) FILTER (WHERE birth_raw = '') AS birth,
    count(*) FILTER (WHERE snils_raw = '') AS snils,
    count(*) FILTER (WHERE dept_code = '') AS dept,
    count(*) FILTER (WHERE job_title = '') AS job,
    count(*) FILTER (WHERE hire_raw = '')  AS hire,
    count(*) FILTER (WHERE fire_raw = '')  AS fire,
    count(*) FILTER (WHERE phone_raw = '') AS phone,
    count(*) FILTER (WHERE email_raw = '') AS email
FROM stage.v_hr_employees
"""),

    ("15. Должность содержит ':' (признак руководителя)",
     r"""
SELECT count(*) AS cnt FROM stage.v_hr_employees WHERE job_title LIKE '%:%'
"""),

    ("15а. Примеры должностей-руководителей",
     r"""
SELECT tab_no, job_title FROM stage.v_hr_employees
WHERE job_title LIKE '%:%' ORDER BY src_line LIMIT 20
"""),

    ("16. Подразделения сотрудников, которых нет в справочнике",
     r"""
SELECT DISTINCT e.dept_code
FROM stage.v_hr_employees e
LEFT JOIN stage.v_hr_departments d ON d.dept_code = e.dept_code
WHERE d.dept_code IS NULL
ORDER BY 1
"""),

    ("17. Невидимые символы NBSP (U+00A0) в кадровых строках",
     r"""
SELECT src_line, line_data FROM stage.hr_employees
WHERE position(chr(160) in line_data) > 0 LIMIT 20
"""),

    ("18. Строки без ожидаемых 11 полей",
     r"""
SELECT src_line, line_data FROM stage.hr_employees
WHERE src_line > 1
  AND (length(line_data) - length(replace(line_data, ';', ''))) + 1 <> 11
ORDER BY src_line LIMIT 20
"""),

    ("19. ФИО с двойными/лишними пробелами",
     r"""
SELECT tab_no, '[' || fio || ']' AS fio_invisible, length(fio) AS len
FROM stage.v_hr_employees
WHERE fio ~ '\s{2,}' OR fio <> btrim(fio)
ORDER BY src_line
"""),

    ("20. ФИО в ВЕРХНЕМ регистре (несогласованный регистр)",
     r"""
SELECT tab_no, fio FROM stage.v_hr_employees
WHERE fio = upper(fio) AND fio ~ '[А-ЯЁ]'
ORDER BY src_line
"""),

    ("21. Дата рождения распарсена: выпадает в будущее",
     r"""
SELECT tab_no, fio, birth_raw, birth_date
FROM stage.v_hr_employees_dates
WHERE birth_date > CURRENT_DATE
ORDER BY birth_date
"""),

    ("22. Увольнение РАНЬШЕ приёма",
     r"""
SELECT tab_no, fio, hire_raw, fire_raw, hire_date, fire_date
FROM stage.v_hr_employees_dates
WHERE hire_date IS NOT NULL AND fire_date IS NOT NULL AND fire_date < hire_date
ORDER BY src_line
"""),

    ("23. Нереалистичный возраст при приёме (<14 или >80 лет)",
     r"""
SELECT tab_no, fio, birth_date, hire_date,
       (hire_date - birth_date) / 365.25 AS age_at_hire
FROM stage.v_hr_employees_dates
WHERE birth_date IS NOT NULL AND hire_date IS NOT NULL
  AND ((hire_date - birth_date) < 14 * 365 OR (hire_date - birth_date) > 80 * 365)
ORDER BY age_at_hire
"""),

    ("24. Адреса почты: дубликаты",
     r"""
SELECT lower(email_raw) AS email, count(*) AS cnt, string_agg(tab_no, ', ') AS tab_nos
FROM stage.v_hr_employees
WHERE email_raw <> ''
GROUP BY lower(email_raw)
HAVING count(*) > 1
ORDER BY cnt DESC, email
"""),

    ("25. Один СНИЛС — разные ФИО (несогласованность, включая регистр)",
     r"""
SELECT snils_raw,
       count(DISTINCT fio) AS distinct_fio,
       string_agg(tab_no || ': ' || fio, '; ' ORDER BY tab_no) AS people
FROM stage.v_hr_employees
WHERE snils_raw <> ''
GROUP BY snils_raw
HAVING count(DISTINCT fio) > 1
ORDER BY snils_raw
"""),

    ("26. Руководители подразделений: head_tab нет среди сотрудников",
     r"""
SELECT d.dept_code, d.name, d.head_tab
FROM stage.v_hr_departments d
LEFT JOIN stage.v_hr_employees e ON e.tab_no = d.head_tab
WHERE e.tab_no IS NULL
ORDER BY d.dept_code
"""),

    ("27. Руководитель: подразделение сотрудника не совпадает с возглавляемым",
     r"""
SELECT e.tab_no, e.fio, e.dept_code AS own_dept,
       d.dept_code AS headed_dept, d.name AS headed_name
FROM stage.v_hr_employees e
JOIN stage.v_hr_departments d ON d.head_tab = e.tab_no
WHERE e.dept_code <> d.dept_code
ORDER BY e.tab_no
"""),

    ("28. Дата увольнения в будущем",
     r"""
SELECT tab_no, fio, fire_raw, fire_date FROM stage.v_hr_employees_dates
WHERE fire_date > CURRENT_DATE ORDER BY fire_date
"""),

    ("29. Дата приёма в будущем",
     r"""
SELECT tab_no, fio, hire_raw, hire_date FROM stage.v_hr_employees_dates
WHERE hire_date > CURRENT_DATE ORDER BY hire_date
"""),

    ("30. Возраст больше 100 лет",
     r"""
SELECT tab_no, fio, birth_date,
       floor((CURRENT_DATE - birth_date) / 365.25) AS age
FROM stage.v_hr_employees_dates
WHERE birth_date IS NOT NULL AND birth_date < CURRENT_DATE - INTERVAL '100 years'
ORDER BY birth_date
"""),

    ("31. Должность-руководитель не совпадает с названием подразделения",
     r"""
SELECT e.tab_no, e.job_title, d.name AS dept_name
FROM stage.v_hr_employees e
JOIN stage.v_hr_departments d ON d.head_tab = e.tab_no
WHERE e.job_title <> 'Руководитель: ' || d.name
ORDER BY e.tab_no
"""),

    ("32. Подразделения: битая ссылка на родителя",
     r"""
SELECT d.dept_code, d.parent_code
FROM stage.v_hr_departments d
WHERE d.parent_code <> ''
  AND NOT EXISTS (SELECT 1 FROM stage.v_hr_departments p WHERE p.dept_code = d.parent_code)
ORDER BY d.dept_code
"""),

    ("33. Подразделения: дубли кодов",
     r"""
SELECT dept_code, count(*) FROM stage.v_hr_departments
GROUP BY dept_code HAVING count(*) > 1
"""),

    ("34. Подразделения: сам себе родитель",
     r"""
SELECT dept_code, parent_code FROM stage.v_hr_departments
WHERE dept_code = parent_code
"""),

    ("35. Возраст подразделений: глубина иерархии (WITH RECURSIVE)",
     r"""
WITH RECURSIVE tree AS (
    SELECT dept_code, parent_code, 1 AS depth
    FROM stage.v_hr_departments WHERE parent_code = ''
    UNION ALL
    SELECT d.dept_code, d.parent_code, t.depth + 1
    FROM stage.v_hr_departments d JOIN tree t ON d.parent_code = t.dept_code
)
SELECT max(depth) AS max_depth, count(*) AS reachable
FROM tree
"""),

    ("36. Подразделения, недостижимые от корня (отрыв от иерархии)",
     r"""
WITH RECURSIVE tree AS (
    SELECT dept_code FROM stage.v_hr_departments WHERE parent_code = ''
    UNION ALL
    SELECT d.dept_code FROM stage.v_hr_departments d JOIN tree t ON d.parent_code = t.dept_code
)
SELECT d.dept_code, d.parent_code, d.name
FROM stage.v_hr_departments d
WHERE d.dept_code NOT IN (SELECT dept_code FROM tree)
ORDER BY d.dept_code
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


def main():
    env = load_env(os.path.join(ROOT, ".env"))
    dsn = (f"host=127.0.0.1 port={env.get('POSTGRES_PORT', '5432')} "
           f"dbname={env['POSTGRES_DB']} user={env['POSTGRES_USER']} "
           f"password={env['POSTGRES_PASSWORD']} connect_timeout=10")

    out = []
    out.append("АНАЛИЗ КАЧЕСТВА ДАННЫХ — РАЗДЕЛ 1: КАДРОВАЯ СИСТЕМА (hr/)")
    out.append("")

    with psycopg.connect(dsn) as conn:
        conn.autocommit = True   # каждая проверка независима
        with conn.cursor() as cur:
            for ddl in VIEWS:
                cur.execute(ddl)

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

    path = os.path.join(ROOT, "docs", "anomalies", "01_hr.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("Отчёт сохранён: " + path)


if __name__ == "__main__":
    main()
