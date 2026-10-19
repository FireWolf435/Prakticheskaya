# -*- coding: utf-8 -*-
"""
Пункт 3.5 ТЗ — восстановление истории легаси-заявок и пересчёт SLA.

По журналу статусов (core.ticket_status_history) для каждой легаси-заявки
восстанавливается ход её жизни и заново считается SLA:

  * время реакции — от создания заявки до первого перевода «в работу»
    (статус «Назначена», при его отсутствии — «В работе»);
  * время решения — от создания до «Решена» (при отсутствии — до «Закрыта»).

Оба времени измеряются в рабочих часах по производственному календарю
(ref.production_calendar): учитываются только рабочие дни, внутри дня —
09:00–18:00 без обеда 13:00–14:00, в сокращённый день — до 17:00.
Интервалы, проведённые в статусе «Ожидание пользователя», из подсчёта
исключаются — таймер SLA стоит на паузе.

Нормативы (норма реакции и норма решения в рабочих часах) берутся из
нормативного справочника ref/priorities_sla.csv.

Нарушение — превышение факта над нормативом:
  * нарушение реакции — время реакции > норма реакции;
  * нарушение решения — время решения > норма решения.

Результат:
  * docs/sla_report.txt   — отчёт (методика, сводки, разбор аномалий);
  * docs/sla_tickets.csv  — построчная таблица по всем заявкам.
Целевые таблицы не изменяются.

Запуск:
    python analysis/sla.py
"""
import os
import sys
import csv
import datetime as dt
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "importer"))

import load_stage  # noqa: E402  (путь к данным, .env, DSN)

MSK = ZoneInfo("Europe/Moscow")

# Статусы, означающие, что заявку взяли в работу (для времени реакции).
REACTION_STATUSES = ("Назначена", "В работе")
# Статусы, означающие, что заявка решена (для времени решения).
DONE_STATUSES = ("Решена", "Закрыта")
# Статус, в котором таймер SLA стоит на паузе.
PAUSE_STATUS = "Ожидание пользователя"

CAL = {}          # date -> (is_workday, work_start, work_end, break_start, break_end)
NORM = {}         # priority -> (reaction_hours, resolve_hours)


def log(msg):
    print(msg, flush=True)


def _mins(t):
    """Время -> минуты от начала суток."""
    return t.hour * 60 + t.minute


def _aware(day, mins):
    """Дата + минуты -> момент времени в московском часовом поясе."""
    return datetime.combine(day, time(mins // 60, mins % 60), MSK)


def day_windows(day):
    """Рабочие окна дня в минутах от начала суток (без обеда)."""
    c = CAL.get(day)
    if not c or not c[0]:
        return []
    _, ws, we, bs, be = c
    ws, we, bs, be = _mins(ws), _mins(we), _mins(bs), _mins(be)
    out = []
    for a, b in ((ws, bs), (be, we)):
        a, b = max(a, ws), min(b, we)
        if b > a:
            out.append((a, b))
    return out


def work_minutes(t1, t2, missing_days):
    """Рабочие минуты между моментами t1 и t2 (t2 > t1)."""
    if t2 <= t1:
        return 0.0
    total = 0.0
    day = t1.date()
    last = t2.date()
    while day <= last:
        if day not in CAL:
            missing_days.add(day)
        for a, b in day_windows(day):
            s, e = max(t1, _aware(day, a)), min(t2, _aware(day, b))
            if e > s:
                total += (e - s).total_seconds() / 60.0
        day += timedelta(days=1)
    return total


def metric_minutes(t1, t2, pauses, missing_days):
    """Рабочие минуты от t1 до t2 за вычетом пауз «Ожидание пользователя»."""
    if t1 is None or t2 is None or t2 <= t1:
        return None
    total = work_minutes(t1, t2, missing_days)
    for p1, p2 in pauses:
        a, b = max(p1, t1), min(p2, t2)
        if b > a:
            total -= work_minutes(a, b, set())
    return max(total, 0.0)


def read_norms():
    """Нормативы из ref/priorities_sla.csv: priority -> (реакция, решение)."""
    path = os.path.join(load_stage.DATA_DIR, "ref", "priorities_sla.csv")
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    norms = {}
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        norms[int(r[0])] = (float(r[2]), float(r[3]))
    return norms


def load_calendar(cur):
    cur.execute(
        "SELECT cal_date, is_workday, work_start, work_end, break_start, break_end "
        "FROM ref.production_calendar ORDER BY cal_date")
    for d, is_work, ws, we, bs, be in cur.fetchall():
        CAL[d] = (is_work, ws, we, bs, be)


def load_tickets(cur):
    cur.execute(
        "SELECT t.ticket_id, t.ticket_number, t.priority, t.status, "
        "       t.created_at, t.closed_at, c.name "
        "FROM core.tickets t "
        "JOIN ref.categories c ON c.category_id = t.category_id "
        "ORDER BY t.ticket_id")
    return cur.fetchall()


def load_history(cur):
    """ticket_id -> [(момент, статус_перехода)] в порядке событий."""
    cur.execute(
        "SELECT ticket_id, changed_at, to_status "
        "FROM core.ticket_status_history "
        "ORDER BY ticket_id, changed_at, history_id")
    hist = {}
    for tid, changed_at, to_status in cur.fetchall():
        hist.setdefault(tid, []).append((changed_at, to_status))
    return hist


def local(t):
    """Момент -> московское время."""
    return t.astimezone(MSK) if t is not None else None


def analyze(ticket, events, missing_days):
    """Считает время реакции и решения для одной заявки."""
    status = ticket[3]
    created = local(ticket[4])
    closed = local(ticket[5])

    first_reaction = None
    first_done = None
    pauses = []
    pause_start = None
    for when, status in events:
        when = local(when)
        if pause_start is not None:
            pauses.append((pause_start, when))
            pause_start = None
        if status == PAUSE_STATUS:
            pause_start = when
        if first_reaction is None and status in REACTION_STATUSES:
            first_reaction = when
        if first_done is None and status in DONE_STATUSES:
            first_done = when

    # Если журнал почему-то не содержит события решения, но заявка решена или
    # закрыта — берём дату закрытия из карточки (и отмечаем это в отчёте).
    # Для отклонённых заявок время решения не считается: заявка не решена.
    fallback = False
    if first_done is None and closed is not None and status in DONE_STATUSES:
        first_done = closed
        fallback = True

    reaction = metric_minutes(created, first_reaction, [], missing_days) \
        if first_reaction is not None else None
    resolve = metric_minutes(created, first_done, pauses, missing_days) \
        if first_done is not None else None
    return reaction, resolve, fallback


def main():
    env = load_stage.load_env(os.path.join(ROOT, ".env"))
    dsn = load_stage.dsn_from_env(env)
    NORM.update(read_norms())

    missing_days = set()
    results = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            load_calendar(cur)
            tickets = load_tickets(cur)
            hist = load_history(cur)

    open_fallback = 0
    paused_tickets = sum(1 for evs in hist.values()
                         if any(s == PAUSE_STATUS for _, s in evs))
    for t in tickets:
        tid, number, priority, status, created_at, closed_at, category = t
        created = local(created_at)
        closed = local(closed_at)
        broken = closed is not None and created is not None and closed < created
        reaction, resolve, fallback = analyze(t, hist.get(tid, []), missing_days)
        if fallback:
            open_fallback += 1
        results.append({
            "number": number, "priority": priority, "status": status,
            "category": category, "created": created, "closed": closed,
            "broken": broken,
            "reaction": reaction, "resolve": resolve,
        })

    # --- сборка отчёта -----------------------------------------------------
    out = []
    out.append("ОТЧЁТ: ИСТОРИЯ ЛЕГАСИ-ЗАЯВОК И ПЕРЕСЧЁТ SLA (пункт 3.5 ТЗ)")
    out.append("=" * 78)
    out.append("")
    out.append("1. Методика расчёта")
    out.append("-" * 78)
    out.append("   Время реакции — от создания заявки до первого перевода в работу")
    out.append("   (статус «Назначена», при отсутствии — «В работе»).")
    out.append("   Время решения — от создания до «Решена» (при отсутствии — до «Закрыта»).")
    out.append("   Оба времени — в рабочих часах: рабочие дни, 09:00–18:00 без обеда")
    out.append("   13:00–14:00, в сокращённый день — до 17:00. Интервалы в статусе")
    out.append("   «Ожидание пользователя» исключены (таймер на паузе).")
    out.append("   Нормативы — из ref/priorities_sla.csv:")
    for p in sorted(NORM):
        r, s = NORM[p]
        out.append(f"      приоритет {p}: реакция {r:g} ч, решение {s:g} ч")
    out.append("")

    # 2. Общая сводка
    total = len(results)
    broken = [r for r in results if r["broken"]]
    good = [r for r in results if not r["broken"]]
    with_reaction = [r for r in good if r["reaction"] is not None]
    with_resolve = [r for r in good if r["resolve"] is not None]

    def viol(r, metric, kind):
        # r[metric] — минуты, норматив — часы
        norm = NORM[r["priority"]][kind] * 60.0
        return r[metric] is not None and r[metric] > norm

    reaction_viol = [r for r in with_reaction if viol(r, "reaction", 0)]
    resolve_viol = [r for r in with_resolve if viol(r, "resolve", 1)]
    rejected = len([r for r in good if r["status"] == "Отклонена"])

    def pct(n, d):
        return f"{100.0 * n / d:.1f}%" if d else "—"

    out.append("2. Общая сводка")
    out.append("-" * 78)
    out.append(f"   Всего легаси-заявок:                {total}")
    out.append(f"   Из них «закрыта раньше создания»:   {len(broken)}"
               "  (исключены из расчёта, см. п. 5)")
    out.append(f"   Учтено заявок:                      {len(good)}")
    out.append(f"   С рассчитанной реакцией:            {len(with_reaction)}")
    out.append(f"   С рассчитанным решением:            {len(with_resolve)}")
    out.append(f"   Без решения (ещё не решены):        {len(good) - len(with_resolve)}")
    out.append(f"     в т.ч. отклонённых:               {rejected}"
               "  (заявка не решена — SLA решения не считается)")
    out.append(f"   Нарушений реакции:                  {len(reaction_viol)}"
               f"  ({pct(len(reaction_viol), len(with_reaction))})")
    out.append(f"   Нарушений решения:                  {len(resolve_viol)}"
               f"  ({pct(len(resolve_viol), len(with_resolve))})")
    out.append("")

    # 3. По приоритетам
    out.append("3. Разрез по приоритетам")
    out.append("-" * 78)
    head = (f"   {'Приор.':<7}{'Заявок':>8}{'Норма реш.':>12}{'Средн. реш.':>13}"
            f"{'Наруш. реш.':>13}{'Наруш. реак.':>14}")
    out.append(head)
    for p in sorted(NORM):
        grp = [r for r in good if r["priority"] == p]
        rr = [r for r in grp if r["resolve"] is not None]
        avg = sum(r["resolve"] for r in rr) / len(rr) if rr else None
        out.append(f"   {p:<7}{len(grp):>8}{NORM[p][1]:>12g}"
                   f"{(f'{avg / 60:.1f}' if avg is not None else '—'):>13}"
                   f"{len([r for r in rr if r['resolve'] > NORM[p][1] * 60]):>13}"
                   f"{len([r for r in grp if r['reaction'] is not None and r['reaction'] > NORM[p][0] * 60]):>14}")
    out.append(f"   (средн. реш. — в часах)")
    out.append("")

    # 4. По категориям
    out.append("4. Разрез по категориям")
    out.append("-" * 78)
    cats = {}
    for r in good:
        cats.setdefault(r["category"], []).append(r)
    ordered = sorted(cats.items(), key=lambda kv: -len(kv[1]))
    out.append(f"   {'Категория':<34}{'Заявок':>8}{'Средн. реш.':>13}{'Наруш. реш.':>13}")
    for name, grp in ordered:
        rr = [r for r in grp if r["resolve"] is not None]
        avg = sum(r["resolve"] for r in rr) / len(rr) if rr else None
        nv = len([r for r in rr if r["resolve"] > NORM[r["priority"]][1] * 60])
        out.append(f"   {name[:33]:<34}{len(grp):>8}"
                   f"{(f'{avg / 60:.1f}' if avg is not None else '—'):>13}{nv:>13}")
    out.append("   (средн. реш. — в часах)")
    out.append("")

    # 5. Аномалии
    out.append("5. Заявки с аномалией «закрыта раньше создания»")
    out.append("-" * 78)
    if broken:
        out.append(f"   {'Номер':<12}{'Создана':<22}{'Закрыта':<22}{'Статус':<24}")
        for r in sorted(broken, key=lambda r: r["created"]):
            out.append(f"   {r['number']:<12}"
                       f"{r['created'].strftime('%Y-%m-%d %H:%M:%S'):<22}"
                       f"{r['closed'].strftime('%Y-%m-%d %H:%M:%S'):<22}{r['status']:<24}")
    else:
        out.append("   не найдено")
    out.append("")

    # 6. Примечания
    out.append("6. Примечания")
    out.append("-" * 78)
    if open_fallback:
        out.append("   * заявок, где решение пришлось взять из даты закрытия карточки,")
        out.append(f"     так как в журнале нет события «Решена»/«Закрыта»: {open_fallback}")
    else:
        out.append("   * время решения везде определено по журналу статусов")
    out.append(f"   * заявок с паузами «Ожидание пользователя»: {paused_tickets}"
               " — паузы исключены из времени решения")
    if with_reaction:
        mr = max(r["reaction"] for r in with_reaction)
        out.append(f"   * максимальное время реакции по всем заявкам — {mr / 60:.2f} раб. ч"
                   " (свойство исходных данных: почему у приоритетов 3 и 4")
        out.append("     нарушений реакции нет — их нормы 3 и 9 ч не достигаются)")
    if missing_days:
        days = sorted(missing_days)
        out.append(f"   * дней вне производственного календаря: {len(days)}"
                   f" ({days[0]}..{days[-1]}) — считались нерабочими")
    else:
        out.append("   * все даты заявок покрыты производственным календарём")
    out.append("   * построчная таблица по всем заявкам — docs/sla_tickets.csv")
    out.append("     (формируется этим же скриптом; файл данных, не хранится в репозитории)")
    out.append("")

    report = os.path.join(ROOT, "docs", "sla_report.txt")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")

    csv_path = os.path.join(ROOT, "docs", "sla_tickets.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Номер", "Приоритет", "Категория", "Статус", "Создана",
                    "Реакция_ч", "Норма_реакции_ч", "Наруш_реакции",
                    "Решение_ч", "Норма_решения_ч", "Наруш_решения", "Аномалия"])
        for r in results:
            nr, ns = NORM[r["priority"]]
            w.writerow([
                r["number"], r["priority"], r["category"], r["status"],
                r["created"].strftime("%Y-%m-%d %H:%M:%S"),
                f"{r['reaction'] / 60:.2f}" if r["reaction"] is not None else "",
                f"{nr:g}",
                ("да" if r["reaction"] is not None and r["reaction"] > nr * 60 else
                 ("нет" if r["reaction"] is not None else "")),
                f"{r['resolve'] / 60:.2f}" if r["resolve"] is not None else "",
                f"{ns:g}",
                ("да" if r["resolve"] is not None and r["resolve"] > ns * 60 else
                 ("нет" if r["resolve"] is not None else "")),
                "закрыта раньше создания" if r["broken"] else "",
            ])

    log("")
    for line in out:
        log(line)
    log("Отчёт сохранён: " + report)
    log("Таблица по заявкам: " + csv_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
