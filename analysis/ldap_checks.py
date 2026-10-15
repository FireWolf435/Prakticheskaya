# -*- coding: utf-8 -*-
"""
Пункт 3.2 ТЗ — анализ качества данных.
Раздел 4: LDAP (ldap/directory.ldif)

Разбираем LDIF (несколько objectClass на запись!), кладём в stage.ldap_users_parsed
и сверяем с кадрами по табельному номеру (employeeNumber) и подразделению.
"""
import os

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PARSED_DDL = [
    "DROP TABLE IF EXISTS stage.ldap_users_parsed",
    r"""
CREATE TABLE stage.ldap_users_parsed (
    rec_no           BIGINT PRIMARY KEY,
    dn               TEXT,
    uid              TEXT,
    cn               TEXT,
    sn               TEXT,
    givenname        TEXT,
    displayname      TEXT,
    mail             TEXT,
    employeenumber   TEXT,
    departmentnumber TEXT,
    title            TEXT,
    has_passwd       BOOLEAN
)
""",
]

CHECKS = [
    ("L1. Количество пользователей LDAP",
     "SELECT count(*) AS users FROM stage.ldap_users_parsed"),

    ("L2. Организация и структура (из корневых записей)",
     r"""
SELECT line_data FROM stage.ldap_directory
WHERE line_data LIKE 'dn:%' AND line_data NOT LIKE '%uid=%'
ORDER BY src_line
"""),

    ("L3. Организация o (значение)",
     r"""
SELECT line_data FROM stage.ldap_directory WHERE line_data LIKE 'o:%'
"""),

    ("L4. Почтовый домен пользователей",
     r"""
SELECT substring(mail from '@(.*)$') AS domain, count(*) AS cnt
FROM stage.ldap_users_parsed WHERE mail <> ''
GROUP BY 1 ORDER BY 2 DESC
"""),

    ("L5. Дубли uid",
     r"""
SELECT uid, count(*) FROM stage.ldap_users_parsed WHERE uid <> ''
GROUP BY uid HAVING count(*) > 1
"""),

    ("L6. Дубли email",
     r"""
SELECT mail, count(*) FROM stage.ldap_users_parsed WHERE mail <> ''
GROUP BY mail HAVING count(*) > 1
"""),

    ("L7. Пустые обязательные поля",
     r"""
SELECT
    count(*) FILTER (WHERE uid = '')              AS uid,
    count(*) FILTER (WHERE cn = '')               AS cn,
    count(*) FILTER (WHERE mail = '')             AS mail,
    count(*) FILTER (WHERE employeenumber = '')   AS employee_number,
    count(*) FILTER (WHERE departmentnumber = '') AS department_number
FROM stage.ldap_users_parsed
"""),

    ("L8. Пользователи LDAP с паролем",
     r"""
SELECT count(*) FILTER (WHERE has_passwd) AS with_passwd,
       count(*) FILTER (WHERE NOT has_passwd) AS without_passwd
FROM stage.ldap_users_parsed
"""),

    ("L9. Пользователи LDAP, которых нет в кадрах (по табельному номеру)",
     r"""
SELECT l.uid, l.employeenumber, l.cn
FROM stage.ldap_users_parsed l
WHERE l.employeenumber <> ''
  AND NOT EXISTS (SELECT 1 FROM stage.v_hr_employees e WHERE e.tab_no = l.employeenumber)
ORDER BY l.employeenumber
"""),

    ("L10. Сотрудники кадров, отсутствующие в LDAP",
     r"""
SELECT e.tab_no, e.fio, e.dept_code
FROM stage.v_hr_employees e
WHERE NOT EXISTS (
    SELECT 1 FROM stage.ldap_users_parsed l WHERE l.employeenumber = e.tab_no
)
ORDER BY e.tab_no LIMIT 40
"""),

    ("L11. Сверка подразделений LDAP и кадров (расхождения)",
     r"""
SELECT l.employeenumber, l.uid, l.departmentnumber AS ldap_dept, e.dept_code AS hr_dept
FROM stage.ldap_users_parsed l
JOIN stage.v_hr_employees e ON e.tab_no = l.employeenumber
WHERE l.departmentnumber <> e.dept_code
ORDER BY l.employeenumber LIMIT 40
"""),

    ("L12. Сверка ФИО: displayName (LDAP) и ФИО (кадры)",
     r"""
SELECT l.employeenumber, l.displayname AS ldap_name, e.fio AS hr_name
FROM stage.ldap_users_parsed l
JOIN stage.v_hr_employees e ON e.tab_no = l.employeenumber
WHERE lower(l.displayname) <> lower(e.fio)
ORDER BY l.employeenumber LIMIT 40
"""),

    ("L13. Сверка email LDAP и кадров",
     r"""
SELECT l.employeenumber, l.mail AS ldap_mail, e.email_raw AS hr_mail
FROM stage.ldap_users_parsed l
JOIN stage.v_hr_employees e ON e.tab_no = l.employeenumber
WHERE lower(l.mail) <> lower(e.email_raw)
ORDER BY l.employeenumber LIMIT 40
"""),

    ("L14. Формат uid (примеры)",
     r"""
SELECT uid, count(*) FROM (
    SELECT CASE
        WHEN uid ~ '^[a-z]\.[a-z]+$' THEN 'и.фамилия (норма)'
        ELSE 'другое: ' || uid
    END AS uid FROM stage.ldap_users_parsed
) t GROUP BY uid ORDER BY 2 DESC LIMIT 15
"""),

    ("L15. Сервисные и дублирующие учётные записи (uid с цифрой / без табельного)",
     r"""
SELECT uid, cn, employeenumber FROM stage.ldap_users_parsed
WHERE uid ~ '\d$' OR employeenumber = ''
ORDER BY uid
"""),

    ("L16. Учётные записи с суффиксом '2' (повторные) и их связь с кадрами",
     r"""
SELECT l.uid, l.employeenumber, e.fio
FROM stage.ldap_users_parsed l
LEFT JOIN stage.v_hr_employees e ON e.tab_no = l.employeenumber
WHERE l.uid ~ '\d$'
ORDER BY l.uid
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


def parse_ldif(text):
    """Возвращает список словарей {ключ: [значения]} по записям LDIF."""
    entries = []
    cur = {}
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if line.strip() == "":
            if cur:
                entries.append(cur)
                cur = {}
            continue
        if line[:1] in (" ", "\t"):
            # продолжение значения предыдущего ключа
            if entries or cur:
                pass
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            cur.setdefault(k.strip().lower(), []).append(v.strip())
    if cur:
        entries.append(cur)
    return entries


def first(d, key):
    vals = d.get(key)
    return vals[0] if vals else ""


def main():
    env = load_env(os.path.join(ROOT, ".env"))
    dsn = (f"host=127.0.0.1 port={env.get('POSTGRES_PORT', '5432')} "
           f"dbname={env['POSTGRES_DB']} user={env['POSTGRES_USER']} "
           f"password={env['POSTGRES_PASSWORD']} connect_timeout=10")

    out = ["АНАЛИЗ КАЧЕСТВА ДАННЫХ — РАЗДЕЛ 4: LDAP (ldap/directory.ldif)", ""]

    with psycopg.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for ddl in PARSED_DDL:
                cur.execute(ddl)
            cur.execute("SELECT line_data FROM stage.ldap_directory ORDER BY src_line")
            text = "\n".join(r[0] for r in cur.fetchall())
            entries = parse_ldif(text)

            rows = []
            rec = 0
            for d in entries:
                ocs = [v.lower() for v in d.get("objectclass", [])]
                if not any("person" in v for v in ocs) or "uid" not in d:
                    continue
                rec += 1
                rows.append((
                    rec, first(d, "dn"), first(d, "uid"), first(d, "cn"),
                    first(d, "sn"), first(d, "givenname"), first(d, "displayname"),
                    first(d, "mail").lower(), first(d, "employeenumber"),
                    first(d, "departmentnumber"), first(d, "title"),
                    "userpassword" in d,
                ))
            with cur.copy(
                "COPY stage.ldap_users_parsed (rec_no, dn, uid, cn, sn, givenname,"
                " displayname, mail, employeenumber, departmentnumber, title, has_passwd)"
                " FROM STDIN"
            ) as copy:
                for r in rows:
                    copy.write_row(r)

        out.append(f"Всего записей в LDIF: {len(entries)}; пользователей: {len(rows)}")
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

    path = os.path.join(ROOT, "docs", "anomalies", "04_ldap.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("OK: " + path)


if __name__ == "__main__":
    main()
