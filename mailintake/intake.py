# -*- coding: utf-8 -*-
"""
Пункт 3.6 ТЗ — обработка входящей почты модулем mailintake.

Модуль читает все письма из mail/*.eml, разбирает их штатным пакетом email
(заголовки в RFC 2047, кодировки, вложения, признаки автоответа) и по каждому
письму принимает одно решение, которое фиксируется в журнале
stage.mail_intake_log (результат + причина):

    ticket_created — создана новая заявка;
    comment_added  — добавлен комментарий к существующей заявке;
    skipped        — письмо пропущено (автоответ, системное письмо);
    rejected       — письмо отклонено (фишинг, неизвестный/уволенный автор,
                     дубликат, заявка не найдена).

Правила разбора (ТЗ, п. 5.1.4 — «заголовки, кодировки, вложения, автоответы»):
  1) автоответы (заголовки Auto-Submitted / X-Autoreply или тема
     «Автоматический ответ») не создают ни заявок, ни комментариев;
  2) системные письма (mailer-daemon, Return-Path: <>) пропускаются;
  3) письма с признаками фишинга (Received-SPF: fail, поддельный Return-Path)
     отклоняются — заявку злоумышленнику не создаём;
  4) опасные вложения (исполняемые файлы) не прикрепляются к заявке,
     а изолируются в карантин;
  5) ответ с [#номер] в теме добавляет комментарий к заявке с этим номером;
  6) остальные письма от действующих сотрудников создают новую заявку
     (категория — по ключевым словам, приоритет — по признаку срочности);
  7) повторное письмо от того же автора с той же темой отклоняется как дубль.

Идемпотентность: перед обработкой удаляются следы предыдущего запуска —
заявки с номером SD-2026-* (создаются только этим модулем), комментарии с
source='mailintake' и строки журнала. Заявки легаси-системы (числовые номера)
и комментарии других источников не затрагиваются.

Запуск (после importer/transform.py):
    python mailintake/intake.py
"""
import datetime as dt
import email
import email.policy
import email.utils
import glob
import os
import re
import sys

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "importer"))

import load_stage  # noqa: E402  (путь к данным, .env, DSN)

# Префикс номеров заявок, создаваемых из почты (не пересекается с легаси-номерами
# 100001..121561). Удаление по этому префиксу делает повторный запуск идемпотентным.
TICKET_PREFIX = "SD-2026"

ATTACH_DIR = os.path.join(ROOT, "mailintake", "attachments")    # безопасные вложения
QUARANTINE_DIR = os.path.join(ROOT, "mailintake", "quarantine")  # опасные вложения
REPORT_PATH = os.path.join(ROOT, "docs", "mail_report.txt")

# Исполняемые расширения и сигнатура PE-файла — признаки опасного вложения.
DANGEROUS_EXT = {".exe", ".scr", ".bat", ".cmd", ".com", ".pif",
                 ".js", ".vbs", ".ps1", ".jar", ".msi"}
MAGIC_PE = b"MZ"

# Правила сопоставления темы/текста письма со справочником категорий.
# Порядок проверок важен: первое совпадение выигрывает.
CATEGORY_RULES = [
    ("Информационная безопасность", ["фишинг", "вирус", "подозрительн", "мошен",
                                     "взлом", "угроза", "спам"]),
    ("Телефония", ["телефон", "ip-телефон", "звонк", "гарнитур", "гудк"]),
    ("Печать", ["принтер", "печата", "картридж", "мфу", "сканер",
                "зажёвыва", "бумаг"]),
    ("1С", ["1с", "1c", "проведени", "бухгалтер"]),
    ("Электронная почта", ["outlook", "почт", "письм", "ящик", "переадрес",
                           "рассылк"]),
    ("Сеть и интернет", ["сетев", "сеть", "интернет", "сайт", "wi-fi", "wifi",
                         "лвс"]),
    ("Серверы и сервисы", ["сервер", "файлов", "файл", "сервис"]),
    ("Учётные записи и доступ", ["доступ", "парол", "учётн", "учетн",
                                 "разблокиров", "верните"]),
    ("Компьютеры и периферия", ["компьютер", "пк", "флешк", "usb", "монитор",
                                "клавиатур", "мыш", "периферия", "ноутбук",
                                "не включается"]),
    ("Прикладное ПО", ["программ", "софт", "установк", "приложени", "обновлен"]),
]

# Признаки срочности — повышают приоритет до «Высокий» (2).
URGENT_MARKS = ["очень срочно", "срочно", "asap", "горит", "!!"]

# Категория по умолчанию, если ни одно ключевое слово не сработало.
DEFAULT_CATEGORY = "Прикладное ПО"

# Регулярное выражение для номера заявки в теме ответа: [#120991].
TICKET_IN_SUBJECT = re.compile(r"\[#(\d+)\]")

# Простейшее маскирование секретов в тексте письма (требование ИБ).
SECRET_RE = re.compile(r"(парол\w*|логин\w*|pwd|password)\s*[:=]\s*\S+", re.IGNORECASE)


def log(msg):
    print(msg, flush=True)


def mask_secrets(text):
    """Заменяет значения, идущие после слов «пароль/логин/password», на ***."""
    return SECRET_RE.sub(lambda m: m.group(1) + ": ***", text or "")


def strip_quotes(text):
    """Убирает строки цитаты (начинающиеся с '>') из тела письма."""
    lines = [ln for ln in (text or "").splitlines() if not ln.lstrip().startswith(">")]
    return "\n".join(lines).strip()


# ===========================================================================
# Разбор письма
# ===========================================================================
def parse_letter(path):
    """Читает .eml и возвращает словарь с разобранными полями."""
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)

    name, addr = email.utils.parseaddr(str(msg.get("From", "") or ""))

    date = None
    hdr = msg.get("Date")
    if hdr is not None:
        date = getattr(hdr, "datetime", None)
        if date is None:
            try:
                date = email.utils.parsedate_to_datetime(str(hdr))
            except (TypeError, ValueError):
                date = None

    body = ""
    attachments = []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        filename = part.get_filename()
        is_attach = part.get_content_disposition() == "attachment"
        if is_attach or (filename and ctype not in ("text/plain", "text/html")):
            try:
                payload = part.get_payload(decode=True) or b""
            except Exception:
                payload = b""
            attachments.append({
                "filename": filename or "attachment.bin",
                "content_type": ctype,
                "data": payload,
            })
        elif ctype == "text/plain":
            try:
                body += part.get_content()
            except Exception:
                pass

    return {
        "path": path,
        "src_file": "mail/" + os.path.basename(path),
        "from_name": name,
        "from_email": (addr or "").lower(),
        "subject": str(msg.get("Subject", "") or "").strip(),
        "date": date,
        "message_id": str(msg.get("Message-ID", "") or "").strip(),
        "return_path": str(msg.get("Return-Path", "") or "").strip(),
        "spf": str(msg.get("Received-SPF", "") or "").strip(),
        "auto_submitted": str(msg.get("Auto-Submitted", "") or "").strip().lower(),
        "x_autoreply": str(msg.get("X-Autoreply", "") or "").strip().lower(),
        "body": body.strip(),
        "attachments": attachments,
    }


def is_system(letter):
    """Системное письмо: mailer-daemon или пустой обратный путь (Return-Path: <>)."""
    frm = letter["from_email"]
    return (
        frm.startswith("mailer-daemon@")
        or frm.startswith("no-reply@")
        or frm.startswith("noreply@")
        or letter["return_path"] == "<>"
    )


def is_autoreply(letter):
    """Автоответ: по служебным заголовкам или по теме письма."""
    if "auto-replied" in letter["auto_submitted"] or "auto-generated" in letter["auto_submitted"]:
        return True
    if letter["x_autoreply"] in ("yes", "true", "1"):
        return True
    return letter["subject"].lower().startswith("автоматический ответ")


def phishing_reason(letter):
    """Возвращает причину фишинга или None."""
    if "fail" in letter["spf"].lower():
        return f"признаки фишинга: Received-SPF={letter['spf']}"
    rp = letter["return_path"]
    if rp and rp != "<>" and "@corp.local" not in rp.lower():
        return f"признаки фишинга: поддельный обратный адрес {rp}"
    return None


def divert_dangerous(letter):
    """Отделяет опасные вложения: возвращает (опасные, безопасные)."""
    dangerous, safe = [], []
    for att in letter["attachments"]:
        ext = os.path.splitext(att["filename"])[1].lower()
        if ext in DANGEROUS_EXT or att["data"][:2] == MAGIC_PE:
            dangerous.append(att)
        else:
            safe.append(att)
    return dangerous, safe


def pick_category(letter, cat_by_name):
    """Категория по ключевым словам (тема + текст). Возвращает (id, имя, слово)."""
    text = (letter["subject"] + " " + letter["body"]).lower()
    for name, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in text:
                return cat_by_name.get(name), name, kw
    return None, None, None


# ===========================================================================
# Основной проход
# ===========================================================================
class Intake:
    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()
        self.cat_by_name = {}
        self.emp_by_email = {}
        self.seen_new = {}          # ключ дубля (email, тема) -> src_file
        self.next_seq = 0

    def load_dicts(self):
        self.cur.execute("SELECT name, category_id FROM ref.categories")
        self.cat_by_name = {name: cid for name, cid in self.cur.fetchall()}

        self.cur.execute(
            "SELECT lower(email), employee_id, is_active, dismissal_date "
            "FROM core.employees WHERE email IS NOT NULL")
        for addr, emp_id, is_active, dismiss in self.cur.fetchall():
            self.emp_by_email[addr] = (emp_id, is_active, dismiss)

    def reset(self):
        """Удаляет результаты предыдущего запуска (идемпотентность)."""
        self.cur.execute("DELETE FROM core.ticket_comments WHERE source = 'mailintake'")
        self.cur.execute("DELETE FROM core.tickets WHERE ticket_number LIKE %s",
                         (TICKET_PREFIX + "%",))
        self.cur.execute("TRUNCATE stage.mail_intake_log RESTART IDENTITY")

    def add_log(self, letter, action, ticket_number=None, ticket_id=None, reason=None):
        self.cur.execute(
            "INSERT INTO stage.mail_intake_log "
            "(src_file, message_id, from_email, subject, letter_date, action, "
            " ticket_number, ticket_id, reason) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (letter["src_file"], letter["message_id"], letter["from_email"],
             letter["subject"], letter["date"], action, ticket_number,
             ticket_id, reason))

    def save_attachment(self, base_dir, letter, att):
        """Сохраняет вложение на диск, возвращает относительный путь и размер."""
        sub = os.path.join(base_dir, letter["src_file"].replace("mail/", "").replace(".eml", ""))
        os.makedirs(sub, exist_ok=True)
        safe_name = re.sub(r"[\\/:*?\"<>|]", "_", att["filename"])
        path = os.path.join(sub, safe_name)
        with open(path, "wb") as f:
            f.write(att["data"])
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        return rel, len(att["data"])

    # --- обработка одного письма -------------------------------------------
    def handle(self, letter):
        frm = letter["from_email"]

        if is_system(letter):
            self.add_log(letter, "skipped", reason="системное письмо (mailer-daemon / пустой Return-Path) — заявка не создаётся")
            return "skipped"
        if is_autoreply(letter):
            self.add_log(letter, "skipped", reason="автоответ — заявка/комментарий не создаётся")
            return "skipped"
        phish = phishing_reason(letter)
        if phish:
            self.add_log(letter, "rejected", reason=phish + " — заявка не создаётся")
            return "rejected"

        match = TICKET_IN_SUBJECT.search(letter["subject"])
        if match:
            return self.handle_reply(letter, match.group(1))
        return self.handle_new(letter, frm)

    # --- ответ на существующую заявку --------------------------------------
    def handle_reply(self, letter, number):
        self.cur.execute(
            "SELECT ticket_id FROM core.tickets WHERE ticket_number = %s", (number,))
        row = self.cur.fetchone()
        if row is None:
            self.add_log(letter, "rejected", ticket_number=number,
                         reason=f"заявка #{number} из темы письма не найдена в системе")
            return "rejected"

        ticket_id = row[0]
        emp = self.emp_by_email.get(letter["from_email"])
        author_id = emp[0] if emp else None
        author_email = None if emp else (letter["from_email"] or None)
        body = mask_secrets(strip_quotes(letter["body"])) or "(пустое письмо)"
        self.cur.execute(
            "INSERT INTO core.ticket_comments "
            "(ticket_id, author_id, author_email, body, is_internal, source, "
            " created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,FALSE,'mailintake',%s,%s) RETURNING comment_id",
            (ticket_id, author_id, author_email, body,
             letter["date"] or dt.datetime.now(dt.timezone.utc),
             letter["date"] or dt.datetime.now(dt.timezone.utc)))
        self.add_log(letter, "comment_added", ticket_number=number,
                     ticket_id=ticket_id, reason="добавлен комментарий к заявке")
        return "comment_added"

    # --- новое письмо -------------------------------------------------------
    def handle_new(self, letter, frm):
        emp = self.emp_by_email.get(frm)
        if emp is None:
            self.add_log(letter, "rejected",
                         reason=f"отправитель {frm or '(не определён)'} не найден среди сотрудников — заявка не создаётся")
            return "rejected"
        emp_id, is_active, dismiss = emp
        if not is_active:
            when = dismiss.strftime("%d.%m.%Y") if dismiss else "дата неизвестна"
            self.add_log(letter, "rejected",
                         reason=f"отправитель уволен ({when}) — заявка на доступ не создаётся (ИБ)")
            return "rejected"

        dup_key = (frm, letter["subject"].lower())
        if dup_key in self.seen_new:
            self.add_log(letter, "rejected",
                         reason=f"дубликат письма {self.seen_new[dup_key]} (тот же автор и тема)")
            return "rejected"

        dangerous, _safe = divert_dangerous(letter)

        subject = letter["subject"]
        notes = []
        if not subject:
            subject = " ".join(letter["body"].split())[:80] or "Без темы"
            notes.append("тема пустая — взята из текста письма")
        if len(subject) > 500:
            subject = subject[:497] + "..."
            notes.append("тема обрезана до 500 символов")

        cat_id, cat_name, kw = pick_category(letter, self.cat_by_name)
        if cat_id is None:
            cat_id = self.cat_by_name[DEFAULT_CATEGORY]
            cat_name = DEFAULT_CATEGORY
            notes.append(f"категория не распознана — назначена «{DEFAULT_CATEGORY}»")
        else:
            notes.append(f"категория «{cat_name}» (ключевое слово «{kw}»)")

        low = (letter["subject"] + " " + letter["body"]).lower()
        if any(mark in low for mark in URGENT_MARKS):
            priority = 2
            notes.append("приоритет повышен до «Высокий» (признак срочности)")
        else:
            priority = 3

        self.next_seq += 1
        number = f"{TICKET_PREFIX}-{self.next_seq:04d}"
        created = letter["date"] or dt.datetime.now(dt.timezone.utc)
        description = mask_secrets(letter["body"]) or None

        self.cur.execute(
            "INSERT INTO core.tickets "
            "(ticket_number, author_id, category_id, priority, status, subject, "
            " description, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,'Новая',%s,%s,%s,%s) RETURNING ticket_id",
            (number, emp_id, cat_id, priority, subject, description, created, created))
        ticket_id = self.cur.fetchone()[0]
        self.seen_new[dup_key] = letter["src_file"]

        # Безопасные вложения сохраняем и прикрепляем к заявке.
        for att in _safe:
            rel, size = self.save_attachment(ATTACH_DIR, letter, att)
            self.cur.execute(
                "INSERT INTO core.ticket_attachments "
                "(ticket_id, file_name, file_path, file_size, mime_type, uploaded_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (ticket_id, att["filename"], rel, size, att["content_type"], created))
        if _safe:
            notes.append(f"сохранено вложений: {len(_safe)}")

        # Опасные вложения — в карантин, к заявке не прикрепляем.
        if dangerous:
            names = []
            for att in dangerous:
                self.save_attachment(QUARANTINE_DIR, letter, att)
                names.append(att["filename"])
            notes.append("опасные вложения изолированы в карантин: " + ", ".join(names))

        self.add_log(letter, "ticket_created", ticket_number=number,
                     ticket_id=ticket_id, reason="; ".join(notes))
        return "ticket_created"


def main():
    eml_files = sorted(glob.glob(os.path.join(load_stage.DATA_DIR, "mail", "*.eml")))
    log("=" * 78)
    log("ОБРАБОТКА ВХОДЯЩЕЙ ПОЧТЫ (пункт 3.6 ТЗ, модуль mailintake)")
    log(f"Писем найдено: {len(eml_files)}")
    log("=" * 78)

    letters = [parse_letter(p) for p in eml_files]

    env = load_stage.load_env(os.path.join(ROOT, ".env"))
    dsn = load_stage.dsn_from_env(env)

    stats = {"ticket_created": 0, "comment_added": 0, "skipped": 0, "rejected": 0}
    with psycopg.connect(dsn) as conn:
        it = Intake(conn)
        it.reset()
        it.load_dicts()
        for letter in letters:
            outcome = it.handle(letter)
            stats[outcome] += 1
        conn.commit()

        it.cur.execute(
            "SELECT src_file, from_email, subject, action, ticket_number, reason "
            "FROM stage.mail_intake_log ORDER BY src_file")
        log_rows = it.cur.fetchall()

    write_report(letters, stats, log_rows)

    log("")
    for key in ("ticket_created", "comment_added", "skipped", "rejected"):
        log(f"  {key:<16} {stats[key]}")
    log(f"Отчёт сохранён: {REPORT_PATH}")
    log("Готово.")


def write_report(letters, stats, rows):
    """Формирует docs/mail_report.txt по журналу обработки."""
    out = []
    out.append("ОТЧЁТ ОБ ОБРАБОТКЕ ВХОДЯЩЕЙ ПОЧТЫ (пункт 3.6 ТЗ, модуль mailintake)")
    out.append("")
    out.append(f"Обработано писем: {len(letters)}")
    out.append("")
    out.append("ИТОГИ")
    out.append(f"  создано заявок        : {stats['ticket_created']}")
    out.append(f"  добавлено комментариев: {stats['comment_added']}")
    out.append(f"  пропущено (skipped)   : {stats['skipped']}")
    out.append(f"  отклонено (rejected)  : {stats['rejected']}")
    out.append("")
    out.append("ПРАВИЛА РАЗБОРА (ТЗ, п. 5.1.4)")
    out.append("  1) автоответы (Auto-Submitted / X-Autoreply / тема «Автоматический ответ»)")
    out.append("     не создают ни заявок, ни комментариев;")
    out.append("  2) системные письма (mailer-daemon, Return-Path: <>) пропускаются;")
    out.append("  3) письма с признаками фишинга (SPF=fail, поддельный Return-Path) отклоняются;")
    out.append("  4) опасные вложения (исполняемые файлы) изолируются в карантин;")
    out.append("  5) ответ с [#номер] в теме добавляет комментарий к существующей заявке;")
    out.append("  6) новое письмо от действующего сотрудника создаёт заявку")
    out.append("     (категория — по ключевым словам, приоритет — по признаку срочности);")
    out.append("  7) дубликат (тот же автор и тема) повторно не создаётся;")
    out.append("  8) отправитель-уволенный сотрудник не создаёт заявку на доступ (ИБ).")
    out.append("")
    out.append("ПОСТРОЧНЫЙ ЖУРНАЛ (mail/..eml)")
    out.append("-" * 100)
    for src, frm, subj, action, num, reason in rows:
        out.append(f"{src}  [{action}]  {num or ''}")
        out.append(f"    от: {frm}   тема: {subj or '(пусто)'}")
        if reason:
            out.append(f"    причина: {reason}")
    out.append("-" * 100)
    out.append("")
    out.append("ПРИМЕЧАНИЯ")
    out.append("  * заявки, созданные из почты, получают номера SD-2026-NNNN (номеров")
    out.append("    легаси-системы 100001..121561 это не затрагивает);")
    out.append("  * у ряда писем тема содержала лишний ведущий пробел (следствие переноса")
    out.append("    заголовка) — при разборе тема нормализуется;")
    out.append("  * согласования с SLA (core.tickets.sla_*) выполняются отдельным этапом.")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
