# -*- coding: utf-8 -*-
"""
Пункт 3.7 ТЗ — СВЕРКА КАТАЛОГОВ.

Каталог LDAP (ldap/directory.ldif) сравнивается с кадровыми данными в БД
(core.employees) по табельному номеру (employeeNumber <-> tab_number).
Результат — отчёт о расхождениях с рекомендациями для службы ИБ
(docs/ldap_report.txt):

  * учётные записи LDAP без сотрудника в кадрах (служебные/системные);
  * учётные записи, оставшиеся у уволенных сотрудников (критично);
  * сотрудники кадров без учётной записи в LDAP;
  * расхождения ФИО, адресов почты и подразделений;
  * повторные (дублирующие) учётные записи с цифровым суффиксом;
  * состояние паролей учётных записей.

Скрипт ничего не меняет в базе — только читает и формирует отчёт.

Запуск:
    python analysis/ldap_sync.py
"""
import os
import re
import sys

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "importer"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import load_stage          # noqa: E402  (путь к данным, .env, DSN)
import ldap_checks         # noqa: E402  (разбор LDIF: parse_ldif, first)

REPORT_PATH = os.path.join(ROOT, "docs", "ldap_report.txt")

# Учётные записи, которые по регламенту не привязаны к сотруднику
# (служебные/системные) — для них готовим отдельные рекомендации.
SERVICE_HINTS = {
    "admin2": "административная учётная запись общего назначения — должна быть персональной",
    "scanner": "служебная учётная запись сетевого сканера — ограничить права и вход",
    "temp.buh": "временная учётная запись бухгалтера — проверить срок действия",
    "test": "тестовая учётная запись — в промышленной среде не нужна",
    "contractor.petrov": "учётная запись подрядчика — установить срок и минимальные права",
}


def collapse(text):
    """Убирает лишние пробелы и обрезает края."""
    return " ".join((text or "").split())


def norm_name(text):
    """ФИО к сравнимому виду: пробелы, регистр, «ё» -> «е»."""
    return collapse(text).lower().replace("ё", "е")


def hr_name(row):
    return collapse(f"{row['last_name']} {row['first_name']} {row['middle_name']}")


def classify_name_diff(ldap_name, emp_name):
    """Классифицирует расхождение ФИО: пробелы / «ё-е» / реальное различие."""
    if ldap_name == emp_name:
        return None
    if collapse(ldap_name) == collapse(emp_name):
        return "лишние пробелы"
    if norm_name(ldap_name) == norm_name(emp_name):
        return "написание «ё/е»"
    return "ФИО различается"


def main():
    env = load_stage.load_env(os.path.join(ROOT, ".env"))
    dsn = load_stage.dsn_from_env(env)

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT line_data FROM stage.ldap_directory ORDER BY src_line")
            ldif = "\n".join(r[0] for r in cur.fetchall())
            entries = ldap_checks.parse_ldif(ldif)
            first = ldap_checks.first

            ldap = []
            for d in entries:
                ocs = [v.lower() for v in d.get("objectclass", [])]
                if not any("person" in v for v in ocs) or "uid" not in d:
                    continue
                ldap.append({
                    "uid": first(d, "uid"),
                    "cn": first(d, "cn"),
                    "displayname": first(d, "displayname"),
                    "mail": first(d, "mail").lower(),
                    "tab": first(d, "employeenumber"),
                    "dept": first(d, "departmentnumber"),
                    "title": first(d, "title"),
                    "has_passwd": "userpassword" in d,
                })

            cur.execute(
                "SELECT e.tab_number, e.last_name, e.first_name, e.middle_name,"
                " e.email, d.code, e.is_active, e.dismissal_date "
                "FROM core.employees e "
                "LEFT JOIN ref.departments d ON d.department_id = e.department_id")

            emp = {}
            for tab, ln, fn, mn, email, code, active, dismiss in cur.fetchall():
                emp[tab] = {
                    "tab": tab, "last_name": ln, "first_name": fn,
                    "middle_name": mn or "", "email": (email or "").lower(),
                    "dept": code, "active": active, "dismiss": dismiss,
                }

    # --- сопоставление -------------------------------------------------------
    ldap_by_tab = {l["tab"]: l for l in ldap if l["tab"]}
    service = [l for l in ldap if not l["tab"]]
    not_in_hr = [l for l in ldap if l["tab"] and l["tab"] not in emp]
    matched = [l for l in ldap if l["tab"] in emp]
    dismissed_acc = [l for l in matched if not emp[l["tab"]]["active"]]
    no_account = [e for tab, e in emp.items() if tab not in ldap_by_tab]

    # --- расхождения ФИО / почты / подразделений ------------------------------
    name_diff = []
    mail_diff = []
    dept_diff = []
    for l in matched:
        e = emp[l["tab"]]
        lname = collapse(l["displayname"] or l["cn"])
        ename = hr_name(e)
        kind = classify_name_diff(lname, ename)
        if kind:
            name_diff.append((e, l, lname, ename, kind))
        if l["mail"] and l["mail"] != e["email"]:
            mail_diff.append((e, l))
        if l["dept"] and l["dept"] != e["dept"]:
            dept_diff.append((e, l))

    # --- повторные учётные записи (цифровой суффикс) --------------------------
    all_uids = {l["uid"] for l in ldap}
    dups = []
    for l in sorted(ldap, key=lambda x: x["uid"]):
        m = re.match(r"^(.*?)(\d+)$", l["uid"])
        if not m:
            continue
        base = m.group(1)
        dups.append((l, base, base in all_uids))

    with_pass = sum(1 for l in ldap if l["has_passwd"])

    # --- отчёт ---------------------------------------------------------------
    out = []
    w = out.append
    w("ОТЧЁТ О СВЕРКЕ КАТАЛОГА LDAP С КАДРОВЫМИ ДАННЫМИ (пункт 3.7 ТЗ)")
    w("")
    w("Источник каталога: ldap/directory.ldif   Целевые кадры: core.employees")
    w("Сопоставление: employeeNumber (LDAP) <-> tab_number (кадры).")
    w("")
    w("СВОДКА СВЕРКИ")
    w("-" * 68)
    w(f"  Записей в LDAP (пользователи)                  {len(ldap)}")
    w(f"    в т.ч. с табельным номером                   {len(ldap_by_tab)}")
    w(f"    в т.ч. служебных (без табельного номера)     {len(service)}")
    w(f"  Сотрудников в кадрах (core.employees)          {len(emp)}")
    w(f"  Сопоставлено по табельному номеру              {len(matched)}")
    w(f"  Учётные записи без сотрудника в кадрах         {len(not_in_hr)}")
    w(f"  Учётные записи уволенных сотрудников           {len(dismissed_acc)}")
    w(f"  Сотрудники кадров без учётной записи           {len(no_account)}")
    w(f"  Расхождения ФИО                                {len(name_diff)}")
    w(f"  Расхождения адресов почты                      {len(mail_diff)}")
    w(f"  Расхождения подразделений                      {len(dept_diff)}")
    w(f"  Повторные учётные записи (цифровой суффикс)    {len(dups)}")
    w(f"  Учётные записи с паролем                       {with_pass} из {len(ldap)}")
    w("")

    # A. Служебные учётные записи
    w("=" * 100)
    w("A. УЧЁТНЫЕ ЗАПИСИ LDAP БЕЗ СОТРУДНИКА В КАДРАХ (служебные/системные)")
    w("")
    w("Таких записей: %d. Они не привязаны к табельному номеру и потому не"
      % len(service))
    w("сопоставляются с кадрами автоматически — требуют ручного контроля ИБ.")
    w("")
    w(f"{'uid':<20} {'e-mail':<34} рекомендация")
    w("-" * 100)
    for l in sorted(service, key=lambda x: x["uid"]):
        hint = SERVICE_HINTS.get(l["uid"], "проверить необходимость и срок действия")
        w(f"{l['uid']:<20} {l['mail']:<34} {hint}")
    w("")
    w("РЕКОМЕНДАЦИИ ИБ:")
    w("  1) завести реестр служебных учётных записей с указанием владельца и срока;")
    w("  2) административные действия выполнять из персональных учётных записей —")
    w("     общая запись admin2 не даёт разграничить ответственность;")
    w("  3) служебным записям (scanner) запретить интерактивный вход;")
    w("  4) временные и тестовые записи (temp.buh, test) отключать по завершении;")
    w("  5) для подрядчиков (contractor.petrov) устанавливать срок действия.")
    w("")

    # B. Учётные записи уволенных
    w("=" * 100)
    w("B. УЧЁТНЫЕ ЗАПИСИ, ОСТАВШИЕСЯ У УВОЛЕННЫХ СОТРУДНИКОВ  (критично)")
    w("")
    w("Таких записей: %d. По регламенту учётная запись должна блокироваться в"
      % len(dismissed_acc))
    w("день увольнения; активная запись уволенного — риск несанкционированного")
    w("доступа.")
    w("")
    w(f"{'таб.№':<8} {'ФИО':<34} {'uid':<16} дата увольнения")
    w("-" * 100)
    for l in sorted(dismissed_acc, key=lambda x: x["tab"]):
        e = emp[l["tab"]]
        when = e["dismiss"].strftime("%d.%m.%Y") if e["dismiss"] else "не указана"
        w(f"{e['tab']:<8} {hr_name(e):<34} {l['uid']:<16} {when}")
    w("")
    w("РЕКОМЕНДАЦИИ ИБ:")
    w("  1) немедленно заблокировать перечисленные учётные записи (NOLOGIN);")
    w("  2) проверить журналы входов на предмет активности после даты увольнения;")
    w("  3) настроить автоматическую блокировку: кадровая выгрузка -> каталог.")
    w("")

    # C. Сотрудники без учётной записи
    active_no_acc = [e for e in no_account if e["active"]]
    w("=" * 100)
    w("C. СОТРУДНИКИ КАДРОВ БЕЗ УЧЁТНОЙ ЗАПИСИ В LDAP")
    w("")
    w("Всего: %d (из них действующих: %d)." % (len(no_account), len(active_no_acc)))
    if active_no_acc:
        w("")
        w("ДЕЙСТВУЮЩИЕ сотрудники без учётной записи — требуют внимания:")
        w(f"{'таб.№':<8} {'ФИО':<34} подразделение")
        w("-" * 100)
        for e in sorted(active_no_acc, key=lambda x: x["tab"]):
            w(f"{e['tab']:<8} {hr_name(e):<34} {e['dept'] or '-'}")
    else:
        w("Все сотрудники без учётной записи уволены — это корректно: учётные")
        w("записи уволенных в каталоге отсутствуют (п. B не пересекается).")
    w("")
    for e in sorted(no_account, key=lambda x: x["tab"]):
        when = e["dismiss"].strftime("%d.%m.%Y") if e["dismiss"] else "не указана"
        w(f"  {e['tab']:<8} {hr_name(e):<34} уволен {when}")
    w("")
    w("РЕКОМЕНДАЦИИ ИБ:")
    w("  1) действующим сотрудникам без учётной записи — завести по регламенту;")
    w("  2) уволенным учётные записи не создавать (текущее состояние корректно).")
    w("")

    # D. Расхождения ФИО
    w("=" * 100)
    w("D. РАСХОЖДЕНИЯ ФИО")
    w("")
    w("Всего расхождений: %d." % len(name_diff))
    w("")
    w("Примечание: на этапе анализа (п. 3.2) в ФИО было найдено 12 расхождений, в том")
    w("числе из-за лишних пробелов. При загрузке кадров (п. 3.3) ФИО были разобраны")
    w("на составляющие и лишние пробелы устранены, поэтому в сверке осталось 6")
    w("расхождений: три «ё/е» и три реально различающихся ФИО.")
    w("")
    w(f"{'таб.№':<8} {'ФИО в LDAP':<34} {'ФИО в кадрах':<34} характер")
    w("-" * 100)
    for e, l, lname, ename, kind in sorted(name_diff, key=lambda x: x[0]["tab"]):
        w(f"{e['tab']:<8} {lname:<34} {ename:<34} {kind}")
    w("")
    w("РЕКОМЕНДАЦИИ ИБ:")
    w("  1) расхождения «ё/е» — косметика, привести ФИО к единому формату;")
    w("  2) реально различающиеся ФИО (в т.ч. смена фамилии) — требовать")
    w("     подтверждение кадровой службы и обновлять обе системы одновременно;")
    w("  3) расхождение имени в каталоге и кадрах затрудняет разбор инцидентов.")
    w("")

    # E. Расхождения почты
    w("=" * 100)
    w("E. РАСХОЖДЕНИЯ АДРЕСОВ ПОЧТЫ")
    w("")
    w("Всего: %d. В каталоге адрес есть, в кадрах отсутствует." % len(mail_diff))
    w("")
    w(f"{'таб.№':<8} {'ФИО':<34} {'почта в LDAP':<32} почта в кадрах")
    w("-" * 100)
    for e, l in sorted(mail_diff, key=lambda x: x[0]["tab"]):
        w(f"{e['tab']:<8} {hr_name(e):<34} {l['mail']:<32} {e['email'] or '(пусто)'}")
    w("")
    w("РЕКОМЕНДАЦИИ ИБ:")
    w("  1) источником истины для адреса почты назначить каталог LDAP;")
    w("  2) заполнить адреса в кадрах либо считать поле в кадрах необязательным;")
    w("  3) следить, чтобы адрес для служебной переписки существовал обязательно.")
    w("")

    # F. Расхождения подразделений
    w("=" * 100)
    w("F. РАСХОЖДЕНИЯ ПОДРАЗДЕЛЕНИЙ")
    w("")
    w("Всего: %d (сравнение departmentNumber в LDAP и кода подразделения в кадрах)."
      % len(dept_diff))
    w("")
    if dept_diff:
        w(f"{'таб.№':<8} {'ФИО':<34} {'LDAP':<8} кадры")
        w("-" * 100)
        for e, l in sorted(dept_diff, key=lambda x: x[0]["tab"]):
            w(f"{e['tab']:<8} {hr_name(e):<34} {l['dept']:<8} {e['dept'] or '-'}")
        w("Примечание: часть подразделений в кадрах была переподчинена при")
        w("исправлении битых ссылок (п. 3.3) — расхождение может быть следствием.")
    else:
        w("Расхождений нет: подразделение сотрудника совпадает в обеих системах.")
    w("")
    w("РЕКОМЕНДАЦИИ ИБ:")
    w("  1) назначить владельца справочника подразделений (кадры);")
    w("  2) синхронизировать коды подразделений при переводе сотрудника.")
    w("")

    # G. Повторные учётные записи
    w("=" * 100)
    w("G. ПОВТОРНЫЕ (ДУБЛИРУЮЩИЕ) УЧЁТНЫЕ ЗАПИСИ")
    w("")
    w("Найдено записей с цифровым суффиксом в имени: %d." % len(dups))
    w("")
    w(f"{'uid':<20} {'таб.№':<8} базовая запись существует")
    w("-" * 100)
    for l, base, base_exists in dups:
        exists = "да" if base_exists else "нет"
        w(f"{l['uid']:<20} {l['tab'] or '-':<8} {exists}")
    w("")
    w("РЕКОМЕНДАЦИИ ИБ:")
    w("  1) для каждой пары определить действующую запись, вторую — отключить;")
    w("  2) лишние записи накапливают права и мешают разбору инцидентов;")
    w("  3) при переименовании сотрудника переименовывать запись, а не плодить новую.")
    w("")

    # H. Пароли
    w("=" * 100)
    w("H. СОСТОЯНИЕ ПАРОЛЕЙ")
    w("")
    w("Учётных записей с установленным паролем: %d из %d." % (with_pass, len(ldap)))
    w("")
    w("РЕКОМЕНДАЦИИ ИБ:")
    w("  1) убедиться, что пароли хранятся только в виде стойких хешей;")
    w("  2) задать срок действия пароля и требовать смену при увольнении;")
    w("  3) для административных учётных записей ввести обязательную MFA.")
    w("")

    # Итог
    w("=" * 100)
    w("ИТОГОВЫЕ РЕКОМЕНДАЦИИ СЛУЖБЕ ИБ")
    w("")
    w("  1) ввести автоматическую синхронизацию «кадры -> LDAP» (создание при")
    w("     приёме, блокировка при увольнении, переименование при смене ФИО);")
    w("  2) ввести реестр служебных и подрядных учётных записей со сроком действия;")
    w("  3) устранить критические расхождения п. B (учётные записи уволенных);")
    w("  4) привести ФИО к единому формату в обеих системах;")
    w("  5) назначить источник истины для адреса почты и кода подразделения.")
    w("")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")

    print("Отчёт сохранён:", REPORT_PATH, flush=True)
    print(f"LDAP: {len(ldap)} учётных записей; сопоставлено {len(matched)};", flush=True)
    print(f"уволенных с учёткой {len(dismissed_acc)}; без учётки {len(no_account)};", flush=True)
    print(f"ФИО {len(name_diff)}; почта {len(mail_diff)}; дубли {len(dups)}.", flush=True)


if __name__ == "__main__":
    main()
