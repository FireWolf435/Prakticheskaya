# -*- coding: utf-8 -*-
"""
Загрузчик исходных данных в схему stage — пункт 3.1 ТЗ.

Принцип «как есть»: одна исходная строка файла = одна строка таблицы stage.
Добавляются только служебные столбцы:
    src_line  — номер строки в исходном файле (1..N)
    src_file  — имя исходного файла

Идемпотентность: перед загрузкой все таблицы stage очищаются, поэтому
повторный запуск приводит ровно к тому же результату (без дублей).

Запуск:
    python importer/load_stage.py [путь_к_папке_с_данными]
"""
import os
import sys
import glob

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Путь к распакованным исходным данным (можно переопределить аргументом
# или переменной окружения SD_DATA_DIR).
DEFAULT_DATA = os.environ.get(
    "SD_DATA_DIR",
    r"C:\Users\user\Downloads\AyuGram Desktop\servicedesk_data_unpacked\servicedesk_data",
)
DATA_DIR = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DATA

# Таблицы схемы stage, которые чистим перед загрузкой.
STAGE_TABLES = [
    "stage.hr_departments",
    "stage.hr_employees",
    "stage.cmdb_assets_1c",
    "stage.cmdb_asset_movements",
    "stage.legacy_tickets",
    "stage.legacy_ticket_status_history",
    "stage.ref_categories",
    "stage.ref_priorities_sla",
    "stage.ref_production_calendar",
    "stage.ref_engineer_skills",
    "stage.ref_duty_schedule",
    "stage.ldap_directory",
    "stage.mail_messages",
]

# Текстовые источники: (таблица stage, файл, кодировка)
TEXT_SOURCES = [
    ("stage.ref_categories",            "ref/categories.csv",            "utf-8"),
    ("stage.ref_priorities_sla",        "ref/priorities_sla.csv",        "utf-8"),
    ("stage.ref_production_calendar",   "ref/production_calendar.csv",   "utf-8"),
    ("stage.ref_engineer_skills",       "ref/engineer_skills.csv",       "utf-8"),
    ("stage.ref_duty_schedule",         "ref/duty_schedule.csv",         "utf-8"),
    ("stage.hr_departments",            "hr/departments.csv",            "cp1251"),
    ("stage.hr_employees",              "hr/employees.csv",              "cp1251"),
    ("stage.cmdb_asset_movements",      "cmdb/asset_movements.csv",      "utf-8"),
    ("stage.legacy_tickets",            "legacy/tickets.csv",            "utf-8-sig"),
    ("stage.legacy_ticket_status_history", "legacy/ticket_status_history.csv", "utf-8-sig"),
    ("stage.ldap_directory",            "ldap/directory.ldif",           "utf-8"),
]

XLSX_SOURCE = ("stage.cmdb_assets_1c", "cmdb/assets_1c_export.xlsx")


def log(msg):
    print(msg, flush=True)


def load_env(path):
    """Простой разбор .env (без внешних зависимостей)."""
    env = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def dsn_from_env(env):
    return (
        f"host=127.0.0.1 port={env.get('POSTGRES_PORT', '5432')} "
        f"dbname={env['POSTGRES_DB']} user={env['POSTGRES_USER']} "
        f"password={env['POSTGRES_PASSWORD']} connect_timeout=10"
    )


def decode(raw, enc):
    """Декодирование с запасными кодировками, чтобы не потерять строки."""
    try:
        return raw.decode(enc)
    except UnicodeDecodeError:
        for alt in ("utf-8", "cp1251", "latin-1"):
            try:
                return raw.decode(alt)
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1", errors="replace")


def read_file_lines(path, enc):
    """Возвращает список физических строк файла (без символов перевода строки)."""
    with open(path, "rb") as f:
        raw = f.read()
    text = decode(raw, enc)
    lines = text.split("\n")
    if lines and lines[-1] == "":       # завершающий перевод строки
        lines.pop()
    return [ln[:-1] if ln.endswith("\r") else ln for ln in lines]


def read_xlsx_lines(path):
    """Каждая строка листа Excel -> одна строка (ячейки через TAB)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    lines = []
    try:
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c) for c in row]
                lines.append("\t".join(cells))
    finally:
        wb.close()
    return lines


def copy_lines(cur, table, src_file, lines):
    """Пакетная вставка строк в stage-таблицу через COPY."""
    sql = f"COPY {table} (src_line, src_file, line_data) FROM STDIN"
    with cur.copy(sql) as copy:
        for i, line in enumerate(lines, start=1):
            copy.write_row((i, src_file, line))


def main():
    log("=" * 78)
    log("ЗАГРУЗКА ИСХОДНЫХ ДАННЫХ В СХЕМУ stage (пункт 3.1 ТЗ)")
    log("Папка с данными: " + DATA_DIR)
    log("=" * 78)

    env = load_env(os.path.join(ROOT, ".env"))
    dsn = dsn_from_env(env)

    report = []          # (src_file, src_table, count)
    total_rows = 0

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # --- очистка для идемпотентности -------------------------------
            cur.execute("TRUNCATE " + ", ".join(STAGE_TABLES) + " RESTART IDENTITY")
            cur.execute("TRUNCATE stage.import_log RESTART IDENTITY")

            # --- текстовые источники --------------------------------------
            for table, rel, enc in TEXT_SOURCES:
                path = os.path.join(DATA_DIR, rel)
                if not os.path.exists(path):
                    log(f"  [НЕТ ФАЙЛА] {rel}")
                    continue
                lines = read_file_lines(path, enc)
                copy_lines(cur, table, rel, lines)
                report.append((rel, table, len(lines)))
                total_rows += len(lines)
                log(f"  {rel:<38} -> {table:<38} {len(lines):>7} строк")

            # --- Excel ----------------------------------------------------
            x_table, x_rel = XLSX_SOURCE
            x_path = os.path.join(DATA_DIR, x_rel)
            if os.path.exists(x_path):
                lines = read_xlsx_lines(x_path)
                copy_lines(cur, x_table, x_rel, lines)
                report.append((x_rel, x_table, len(lines)))
                total_rows += len(lines)
                log(f"  {x_rel:<38} -> {x_table:<38} {len(lines):>7} строк")

            # --- Почта ----------------------------------------------------
            eml_files = sorted(glob.glob(os.path.join(DATA_DIR, "mail", "*.eml")))
            mail_lines = 0
            for eml in eml_files:
                rel = "mail/" + os.path.basename(eml)
                lines = read_file_lines(eml, "utf-8")
                copy_lines(cur, "stage.mail_messages", rel, lines)
                mail_lines += len(lines)
            if eml_files:
                report.append(("mail/*.eml", "stage.mail_messages", mail_lines))
                total_rows += mail_lines
                log(f"  {'mail/*.eml':<38} -> {'stage.mail_messages':<38} "
                    f"{mail_lines:>7} строк ({len(eml_files)} писем)")

            # --- журнал импорта (сводки по файлам) ------------------------
            for rel, table, n in report:
                cur.execute(
                    "INSERT INTO stage.import_log "
                    "(source_file, source_table, src_line, status, reason) "
                    "VALUES (%s, %s, 0, 'loaded', %s)",
                    (rel, table, f"Загружено {n} строк"),
                )

    # --- отчёт ------------------------------------------------------------
    lines_out = []
    lines_out.append("ОТЧЁТ О ЗАГРУЗКЕ В stage")
    lines_out.append("Папка с данными: " + DATA_DIR)
    lines_out.append("")
    lines_out.append(f"{'Файл':<40}{'Таблица stage':<40}{'Строк':>8}")
    lines_out.append("-" * 88)
    for rel, table, n in report:
        lines_out.append(f"{rel:<40}{table:<40}{n:>8}")
    lines_out.append("-" * 88)
    lines_out.append(f"{'ИТОГО':<80}{total_rows:>8}")

    report_path = os.path.join(ROOT, "docs", "staging_report.txt")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out) + "\n")

    log("")
    log("ИТОГО загружено строк: " + str(total_rows))
    log("Отчёт сохранён: " + report_path)
    log("Готово.")


if __name__ == "__main__":
    main()
