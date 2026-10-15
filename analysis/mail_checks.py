# -*- coding: utf-8 -*-
"""
Пункт 3.2 ТЗ — анализ качества данных.
Раздел 5: корпоративная почта (mail/*.eml, 35 писем).

Разбираем письма штатным модулем email (RFC2047-заголовки, вложения),
складываем сводку в stage.mail_parsed и ищем аномалии SQL-запросами.
"""
import base64
import email
import email.header
import os

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PARSED_DDL = [
    "DROP TABLE IF EXISTS stage.mail_parsed",
    r"""
CREATE TABLE stage.mail_parsed (
    src_file        TEXT PRIMARY KEY,
    from_raw        TEXT,
    from_name       TEXT,
    from_email      TEXT,
    to_addr         TEXT,
    reply_to        TEXT,
    subject         TEXT,
    date_raw        TEXT,
    content_type    TEXT,
    n_attachments   INT,
    attach_names    TEXT,
    attach_magic    TEXT,
    body_preview    TEXT,
    return_path     TEXT,
    received_spf    TEXT
)
""",
]

CHECKS = [
    ("M1. Количество писем", "SELECT count(*) AS letters FROM stage.mail_parsed"),

    ("M2. Список писем (отправитель, тема, вложения)",
     r"""
SELECT src_file, from_email, subject, n_attachments, attach_names
FROM stage.mail_parsed ORDER BY src_file
"""),

    ("M3. Письма с вложениями",
     r"""
SELECT src_file, from_email, attach_names, attach_magic
FROM stage.mail_parsed WHERE n_attachments > 0 ORDER BY src_file
"""),

    ("M4. 🔥 Опасные вложения (исполняемые файлы)",
     r"""
SELECT src_file, from_email, attach_names, attach_magic
FROM stage.mail_parsed
WHERE attach_magic ILIKE '%PE%' OR attach_names ~* '\.(exe|scr|bat|cmd|js|vbs|ps1)$'
ORDER BY src_file
"""),

    ("M5. Отправители не из домена corp.local",
     r"""
SELECT src_file, from_email, from_name
FROM stage.mail_parsed
WHERE from_email <> '' AND from_email !~* '@corp\.local$'
ORDER BY src_file
"""),

    ("M6. Email отправителя в ВЕРХНЕМ регистре (по сырому заголовку)",
     r"""
SELECT src_file, from_raw, from_email
FROM stage.mail_parsed
WHERE from_raw ~ '[A-Z][A-Z0-9._%+-]*@[A-Za-z0-9.-]+'
ORDER BY src_file
"""),

    ("M7. Поле Reply-To (не совпадает с From)",
     r"""
SELECT src_file, from_email, reply_to
FROM stage.mail_parsed WHERE reply_to <> '' ORDER BY src_file
"""),

    ("M8. Отправители, которых нет среди сотрудников",
     r"""
SELECT DISTINCT m.from_email
FROM stage.mail_parsed m
WHERE m.from_email <> '' AND m.from_email !~* '@corp\.local$'
"""),

    ("M9. Скрытые секреты (пароли/логины) в письмах",
     r"""
SELECT src_file, subject, left(body_preview, 160) AS body
FROM stage.mail_parsed
WHERE body_preview ~* '(парол|password|pwd|Pa\$\$)' OR subject ~* '(парол|password)'
ORDER BY src_file
"""),

    ("M10. Тема письма пустая или подозрительная",
     r"""
SELECT src_file, subject FROM stage.mail_parsed
WHERE subject = '' OR length(subject) > 100
ORDER BY src_file
"""),

    ("M11. 🔥 SPF fail / подделка Return-Path (спуфинг отправителя)",
     r"""
SELECT src_file, from_email, return_path, received_spf
FROM stage.mail_parsed
WHERE received_spf ~* 'fail'
   OR (return_path <> '' AND return_path !~* '@corp\.local$')
ORDER BY src_file
"""),

    ("M12. Признаки фишинга/BEC в теме или тексте",
     r"""
SELECT src_file, from_email, subject, left(body_preview, 140) AS body
FROM stage.mail_parsed
WHERE (subject || ' ' || body_preview) ~* '(срочно|реквизит|перевед|подтверд|доступ администратор|парол|оплат|счет)'
ORDER BY src_file
"""),

    ("M13. Письма-дубликаты (тот же отправитель и тема)",
     r"""
SELECT from_email, subject, count(*) AS cnt, string_agg(src_file, ', ' ORDER BY src_file) AS files
FROM stage.mail_parsed
GROUP BY from_email, subject
HAVING count(*) > 1
ORDER BY cnt DESC, from_email
"""),

    ("M14. Системные письма (mailer-daemon / автоответы)",
     r"""
SELECT src_file, from_email, subject FROM stage.mail_parsed
WHERE from_email ~ 'mailer-daemon|no-reply|noreply'
   OR subject ~* '(автоматический ответ|undelivered|returned to sender)'
ORDER BY src_file
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


def dec(s):
    if not s:
        return ""
    try:
        parts = email.header.decode_header(s)
        out = []
        for data, enc in parts:
            if isinstance(data, bytes):
                out.append(data.decode(enc or "utf-8", errors="replace"))
            else:
                out.append(data)
        return "".join(out)
    except Exception:
        return s


def magic(b):
    if not b:
        return ""
    if b[:2] == b"MZ":
        return "Windows PE (MZ)"
    if b[:4] == b"%PDF":
        return "PDF"
    if b[:2] == b"PK":
        return "ZIP/Office"
    if b[:4] == b"Rar!":
        return "RAR"
    if b[:2] == b"\x1f\x8b":
        return "GZIP"
    return "binary:" + b[:4].hex()


def main():
    env = load_env(os.path.join(ROOT, ".env"))
    dsn = (f"host=127.0.0.1 port={env.get('POSTGRES_PORT', '5432')} "
           f"dbname={env['POSTGRES_DB']} user={env['POSTGRES_USER']} "
           f"password={env['POSTGRES_PASSWORD']} connect_timeout=10")

    out = ["АНАЛИЗ КАЧЕСТВА ДАННЫХ — РАЗДЕЛ 5: ПОЧТА (mail/*.eml)", ""]

    with psycopg.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for ddl in PARSED_DDL:
                cur.execute(ddl)
            cur.execute("SELECT src_file, src_line, line_data FROM stage.mail_messages ORDER BY src_file, src_line")
            groups = {}
            for src_file, src_line, line in cur.fetchall():
                groups.setdefault(src_file, []).append(line)

            rows = []
            for src_file in sorted(groups):
                text = "\n".join(groups[src_file])
                msg = email.message_from_string(text)
                from_raw = msg.get("From", "")
                name = dec(email.utils.parseaddr(from_raw)[0]) if from_raw else ""
                addr = email.utils.parseaddr(from_raw)[1] if from_raw else ""
                to_addr = email.utils.parseaddr(msg.get("To", ""))[1]
                reply_to = email.utils.parseaddr(msg.get("Reply-To", ""))[1]
                subject = dec(msg.get("Subject", ""))
                date_raw = msg.get("Date", "")
                ctype = msg.get_content_type()
                attach_names, magics, body_preview, natt = [], [], "", 0
                if msg.is_multipart():
                    for part in msg.walk():
                        cd = part.get("Content-Disposition", "")
                        fn = part.get_filename()
                        if fn:
                            natt += 1
                            fn = dec(fn)
                            attach_names.append(fn)
                            payload = part.get_payload(decode=True) or b""
                            magics.append(magic(payload))
                        elif part.get_content_type() == "text/plain":
                            payload = part.get_payload(decode=True) or b""
                            if not body_preview:
                                body_preview = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                else:
                    payload = msg.get_payload(decode=True) or b""
                    body_preview = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
                rows.append((
                    src_file, from_raw, name, addr.lower(), to_addr, reply_to,
                    subject, date_raw, ctype, natt,
                    "; ".join(attach_names), "; ".join(magics),
                    body_preview[:400].replace("\n", " "),
                    email.utils.parseaddr(msg.get("Return-Path", ""))[1],
                    msg.get("Received-SPF", ""),
                ))
            with cur.copy(
                "COPY stage.mail_parsed (src_file, from_raw, from_name, from_email, to_addr,"
                " reply_to, subject, date_raw, content_type, n_attachments, attach_names,"
                " attach_magic, body_preview, return_path, received_spf) FROM STDIN"
            ) as copy:
                for r in rows:
                    copy.write_row(r)

        out.append(f"Разобрано писем: {len(rows)}")
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
                            if len(s) > 170:
                                s = s[:170] + "…"
                            vals.append(s)
                        out.append(" | ".join(vals))
                    out.append(f"(строк в результате: {len(data)})")
                except Exception as e:
                    out.append("ОШИБКА: " + str(e))
                out.append("")

    path = os.path.join(ROOT, "docs", "anomalies", "05_mail.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("OK: " + path)


if __name__ == "__main__":
    main()
