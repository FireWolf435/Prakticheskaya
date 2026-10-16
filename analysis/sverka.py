# -*- coding: utf-8 -*-
"""
Пункт 3.4 ТЗ — СВЕРКА данных по каждому источнику.

Проверяемое равенство:
    строк в файле = строк в staging = загружено + отклонено
где «загружено» = loaded + corrected (исправленные строки тоже попали в цель),
а «отклонено» = rejected (строки в журнале stage.import_log с причиной).

Строки файла считаются по исходным данным на диске, строки staging — запросом к
stage.<таблица>, а итоги переноса — по журналу stage.import_log (записи о строках
имеют src_line > 0; сводки загрузчика с src_line = 0 не учитываются).

Источники, перенос которых выполняется на отдельных шагах
(mail — 3.6, ldap — 3.7), на этом шаге проходят только сверку
«файл = staging»; столбец «загружено + отклонено» у них помечается «—».

Запуск:
    python analysis/sverka.py
"""
import os
import sys
import glob

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "importer"))

import load_stage  # noqa: E402  (переиспользуем чтение файлов и путь к данным)


def log(msg):
    print(msg, flush=True)


def build_sources():
    """Список источников: (подпись, таблица stage, список файлов, кодировка)."""
    sources = []
    for table, rel, enc in load_stage.TEXT_SOURCES:
        sources.append((rel, table, [rel], enc))
    x_table, x_rel = load_stage.XLSX_SOURCE
    sources.append((x_rel, x_table, [x_rel], None))
    mail = sorted(os.path.basename(p) for p in
                  glob.glob(os.path.join(load_stage.DATA_DIR, "mail", "*.eml")))
    if mail:
        sources.append(("mail/*.eml", "stage.mail_messages",
                        ["mail/" + m for m in mail], "utf-8"))
    return sources


def file_line_count(rel, enc):
    """Число физических строк файла-источника (как при загрузке в stage)."""
    path = os.path.join(load_stage.DATA_DIR, rel)
    if not os.path.exists(path):
        return None
    if rel.lower().endswith(".xlsx"):
        return len(load_stage.read_xlsx_lines(path))
    return len(load_stage.read_file_lines(path, enc))


def main():
    env = load_stage.load_env(os.path.join(ROOT, ".env"))
    dsn = load_stage.dsn_from_env(env)
    sources = build_sources()

    rows = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for label, table, files, enc in sources:
                n_file = 0
                missing = False
                for rel in files:
                    cnt = file_line_count(rel, enc)
                    if cnt is None:
                        missing = True
                    else:
                        n_file += cnt
                cur.execute(f"SELECT count(*) FROM {table}")
                n_stage = cur.fetchone()[0]
                cur.execute(
                    "SELECT status, count(*) FROM stage.import_log "
                    "WHERE src_line > 0 AND source_file = ANY(%s) GROUP BY status",
                    (files,))
                counts = dict(cur.fetchall())
                loaded = counts.get("loaded", 0)
                corrected = counts.get("corrected", 0)
                rejected = counts.get("rejected", 0)
                rows.append({
                    "label": label, "table": table, "file": n_file, "stage": n_stage,
                    "loaded": loaded, "corrected": corrected, "rejected": rejected,
                    "moved": loaded + corrected + rejected, "missing": missing,
                })

    out = []
    out.append("ОТЧЁТ О СВЕРКЕ ДАННЫХ (пункт 3.4 ТЗ)")
    out.append("Равенство: строк в файле = строк в staging = загружено + отклонено")
    out.append("")
    head = (f"{'Источник':<36}{'В файле':>9}{'В staging':>11}"
            f"{'Загружено+откл.':>17}{'Вердикт':>10}")
    out.append(head)
    out.append("-" * len(head))

    ok_all = True
    pending = []
    for r in rows:
        if r["missing"]:
            verdict = "НЕТ ФАЙЛА"
            ok_all = False
        elif r["moved"] == 0:
            verdict = "staging"
            pending.append(r["label"])
            if r["file"] != r["stage"]:
                verdict = "РАСХОЖД."
                ok_all = False
        elif r["file"] == r["stage"] == r["moved"]:
            verdict = "OK"
        else:
            verdict = "РАСХОЖД."
            ok_all = False
        moved_str = "—" if r["moved"] == 0 else str(r["moved"])
        out.append(f"{r['label']:<36}{r['file']:>9}{r['stage']:>11}"
                   f"{moved_str:>17}{verdict:>10}")

    out.append("")
    tf = sum(r["file"] for r in rows if not r["missing"])
    ts = sum(r["stage"] for r in rows)
    tm = sum(r["moved"] for r in rows)
    out.append(f"{'ИТОГО':<36}{tf:>9}{ts:>11}{tm:>17}")
    out.append("")
    out.append("Примечания:")
    out.append("  * «staging» — файл загружен в staging, перенос в цель выполняется")
    out.append("    отдельным шагом (mail — п. 3.6, ldap — п. 3.7).")
    out.append("  * «загружено + отклонено» = loaded + corrected + rejected;")
    out.append("    строка заголовка каждого файла имеет статус rejected и входит в сумму.")
    out.append("  * для legacy/tickets.csv число строк и число записей отличается из-за")
    out.append("    многострочных полей (см. docs/transform_report.txt).")

    path = os.path.join(ROOT, "docs", "sverka_report.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")

    log("")
    for line in out:
        log(line)
    log("")
    log("Отчёт сохранён: " + path)
    log("ИТОГ СВЕРКИ: " + ("все источники сходятся" if ok_all else "ЕСТЬ РАСХОЖДЕНИЯ"))
    if pending:
        log("Ожидают переноса (3.6/3.7): " + ", ".join(pending))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
