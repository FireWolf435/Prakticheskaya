# -*- coding: utf-8 -*-
"""
Пункт 3.3 ТЗ — ТРАНСФОРМАЦИЯ ДАННЫХ: stage -> целевые схемы (ref, core).

Принципы (п. 3.3 ТЗ):
  * Ни одна строка источника не теряется молча: каждая попадает либо в
    целевую таблицу, либо в журнал stage.import_log со статусом и причиной.
      - loaded    — загружена как есть;
      - corrected — загружена после исправления (в reason указано, что изменили);
      - rejected  — отклонена (в reason указана причина).
  * Импорт идемпотентен: перед загрузкой целевые таблицы очищаются, поэтому
    повторный запуск приводит ровно к тому же результату.

Этап 3.3-А: справочники и кадры.
  ref/categories, ref/priorities_sla, ref/production_calendar,
  ref/engineer_skills, ref/duty_schedule, hr/departments, hr/employees.

Этап 3.3-Б: оборудование (CMDB).
  ref/locations, cmdb/assets_1c_export.xlsx, cmdb/asset_movements.csv.

Запуск:
    python importer/transform.py
"""
import csv
import datetime as dt
import io
import os
import re
import sys

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Целевые таблицы, которыми управляет трансформация (очищаются для идемпотентности).
TARGET_TABLES = [
    "ref.departments",
    "ref.categories",
    "ref.priorities_sla",
    "ref.production_calendar",
    "ref.engineer_skills",
    "ref.duty_schedule",
    "ref.locations",
    "core.employees",
    "core.assets",
    "core.asset_movements",
]


def log(msg):
    print(msg, flush=True)


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


def dsn_from_env(env):
    return (
        f"host=127.0.0.1 port={env.get('POSTGRES_PORT', '5432')} "
        f"dbname={env['POSTGRES_DB']} user={env['POSTGRES_USER']} "
        f"password={env['POSTGRES_PASSWORD']} connect_timeout=10"
    )


def stage_rows(cur, table):
    """Список (src_line, line_data) в порядке исходного файла."""
    cur.execute(f"SELECT src_line, line_data FROM stage.{table} ORDER BY src_line")
    return cur.fetchall()


def parse_csv_line(line, delimiter=","):
    return next(csv.reader(io.StringIO(line), delimiter=delimiter))


# ===========================================================================
# Проверка контрольной суммы СНИЛС (питоновское зеркало ref.snils_valid)
# ===========================================================================
def snils_valid(snils):
    digits = re.sub(r"\D", "", snils or "")
    if len(digits) != 11:
        return False
    s = sum(int(digits[i]) * (9 - i) for i in range(9))
    if s < 100:
        ctrl = s
    elif s % 101 == 100:
        ctrl = 0
    else:
        ctrl = s % 101
    return ctrl == int(digits[9:11])


# ===========================================================================
# Нормализация значений
# ===========================================================================
def parse_date(raw, is_birth):
    """Возвращает (date|None, исходный_формат|None)."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw, fmt).date(), fmt
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{2})", raw)
    if m:
        dd, mm, yy = (int(x) for x in m.groups())
        year = (1900 + yy) if (is_birth and yy >= 30) else (2000 + yy)
        try:
            return dt.date(year, mm, dd), "DD.MM.YY"
        except ValueError:
            return None, None
    return None, None


def norm_fio(fio):
    """Убирает лишние пробелы и приводит к нормальному регистру. Возвращает (значение, изменено)."""
    original = (fio or "").strip()
    collapsed = re.sub(r"\s+", " ", original)
    if collapsed and collapsed == collapsed.upper() and re.search(r"[А-ЯЁA-Z]", collapsed):
        collapsed = " ".join(w.capitalize() for w in collapsed.split(" "))
    changed = collapsed != original
    return collapsed, changed


def split_fio(fio):
    parts = fio.split(" ")
    last = parts[0] if len(parts) > 0 else ""
    first = parts[1] if len(parts) > 1 else ""
    middle = " ".join(parts[2:]) if len(parts) > 2 else None
    return last, first, middle


def norm_phone(raw):
    """Мобильный -> +7XXXXXXXXXX; внутренний 'доб. NNN' сохраняем. (значение, изменено)."""
    original = (raw or "").strip()
    if not original:
        return None, False
    if original.lower().startswith("доб"):
        digits = re.sub(r"\D", "", original)
        value = f"доб. {digits}" if digits else original
        return value, value != original
    digits = re.sub(r"\D", "", original)
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits[0] == "7":
        value = "+" + digits
    elif len(digits) == 10:
        value = "+7" + digits
    else:
        return original, False
    return value, value != original


def sp_execute(cur, sql, params):
    """Выполнить вставку в savepoint. Возвращает (ok, short_error, row)."""
    cur.execute("SAVEPOINT sp")
    try:
        cur.execute(sql, params)
        row = cur.fetchone() if cur.description is not None else None
        cur.execute("RELEASE SAVEPOINT sp")
        return True, None, row
    except Exception as exc:  # noqa: BLE001 - нужен текст ошибки СУБД
        cur.execute("ROLLBACK TO SAVEPOINT sp")
        msg = str(exc).strip().splitlines()[0]
        return False, msg, None


# ===========================================================================
# Специфика CMDB (оборудование)
# ===========================================================================
def canon_inv_no(raw):
    """Приводит любой формат инвентарного номера к канону ИН-NNNNNN.

    Источник смешивает форматы: '005459', 'ИН-005218', 'ИН005114', 'ин 6533',
    'ИH-005330' (латинская H). Ключ сопоставления с перемещениями — цифровая
    часть номера; в перемещениях номера записаны как ИН-NNNNNN (6 цифр).
    """
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    return "ИН-%06d" % int(digits)


def parse_cost(raw):
    """Стоимость -> float|None. Возвращает (value, failed)."""
    s = (raw or "").strip()
    if not s:
        return None, False
    t = s.replace("руб.", "").replace("\u00a0", " ").strip()
    t = t.replace(" ", "").replace(",", ".")
    try:
        return float(t), False
    except ValueError:
        return None, True


def parse_accept_date(raw):
    """Дата принятия к учёту -> date|None. Возвращает (value, failed)."""
    raw = (raw or "").strip()
    if not raw:
        return None, False
    for fmt in ("%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw, fmt).date(), False
        except ValueError:
            pass
    return None, True


def location_kind(name):
    """Тип физического места по его названию."""
    n = (name or "").strip()
    for prefix, kind in (("Корпус", "кабинет"), ("Склад", "склад"),
                         ("Серверная", "серверная"), ("Кроссовая", "кроссовая"),
                         ("Цех", "цех")):
        if n.startswith(prefix):
            return kind
    return None


# ===========================================================================
# Загрузка источников
# ===========================================================================
class Loader:
    def __init__(self, cur):
        self.cur = cur
        self.logbuf = []          # (source_file, source_table, src_line, status, reason)
        self.stats = {}           # src_file -> dict(total, loaded, corrected, rejected)
        self.corrections = {}     # reason -> count

    def _stat(self, src_file):
        return self.stats.setdefault(
            src_file, {"total": 0, "loaded": 0, "corrected": 0, "rejected": 0})

    def mark(self, src_file, src_table, src_line, status, reason=None, count=True):
        self.logbuf.append((src_file, src_table, src_line, status, reason))
        if count:
            self._stat(src_file)["total"] += 1
            self._stat(src_file)[status] += 1
            if status == "corrected" and reason:
                key = re.sub(r"№.*", "№…", reason)
                self.corrections[key] = self.corrections.get(key, 0) + 1

    def flush_log(self):
        if self.logbuf:
            self.cur.executemany(
                "INSERT INTO stage.import_log "
                "(source_file, source_table, src_line, status, reason) "
                "VALUES (%s, %s, %s, %s, %s)", self.logbuf)
            self.logbuf = []

    # --- справочники -------------------------------------------------------
    def load_categories(self):
        src, table = "ref/categories.csv", "ref_categories"
        code_id = {}
        for i, (ln, data) in enumerate(stage_rows(self.cur, table)):
            if i == 0:
                self.mark(src, table, ln, "rejected", "строка заголовка")
                continue
            row = parse_csv_line(data)
            code, name = row[0].strip(), row[1].strip()
            aliases = row[2].strip() if len(row) > 2 else ""
            cur_id = self.cur
            cur_id.execute(
                "INSERT INTO ref.categories(code, name, legacy_name, is_active) "
                "VALUES (%s, %s, %s, TRUE) RETURNING category_id",
                (code, name, aliases or None))
            code_id[code] = cur_id.fetchone()[0]
            self.mark(src, table, ln, "loaded")
        # Восстановление недостающей категории CLOUD (используется в навыках).
        if "CLOUD" not in code_id:
            self.cur.execute(
                "INSERT INTO ref.categories(code, name, legacy_name, description) "
                "VALUES ('CLOUD', 'Облачные сервисы', NULL, "
                "'Добавлено при трансформации: категория используется в навыках инженеров') "
                "RETURNING category_id")
            code_id["CLOUD"] = self.cur.fetchone()[0]
            self.corrections["добавлена отсутствующая категория CLOUD (есть в навыках)"] = \
                self.corrections.get("добавлена отсутствующая категория CLOUD (есть в навыках)", 0) + 1
            self.mark(src, table, 0, "corrected",
                      "добавлена отсутствующая категория CLOUD (есть в навыках инженеров)",
                      count=False)
        self.flush_log()
        return code_id

    def load_priorities(self):
        src, table = "ref/priorities_sla.csv", "ref_priorities_sla"
        for i, (ln, data) in enumerate(stage_rows(self.cur, table)):
            if i == 0:
                self.mark(src, table, ln, "rejected", "строка заголовка")
                continue
            row = parse_csv_line(data)
            priority = int(row[0])
            name = row[1].strip()
            reaction = float(row[2])
            resolve = float(row[3])
            self.cur.execute(
                "INSERT INTO ref.priorities_sla(priority, sla_hours, description) "
                "VALUES (%s, %s, %s)",
                (priority, resolve, f"{name}; время реакции {reaction:g} ч"))
            self.mark(src, table, ln, "loaded")
        self.flush_log()

    def load_calendar(self):
        src, table = "ref/production_calendar.csv", "ref_production_calendar"
        for i, (ln, data) in enumerate(stage_rows(self.cur, table)):
            if i == 0:
                self.mark(src, table, ln, "rejected", "строка заголовка")
                continue
            row = parse_csv_line(data)
            cal_date = dt.date.fromisoformat(row[0].strip())
            day_type = row[1].strip()
            is_work = day_type in ("work", "short")
            is_short = day_type == "short"
            work_end = "17:00" if is_short else "18:00"
            self.cur.execute(
                "INSERT INTO ref.production_calendar"
                "(cal_date, is_workday, is_short_day, work_start, work_end, "
                " break_start, break_end, note) "
                "VALUES (%s, %s, %s, '09:00', %s, '13:00', '14:00', %s)",
                (cal_date, is_work, is_short, work_end, day_type))
            self.mark(src, table, ln, "loaded")
        self.flush_log()

    # --- кадры -------------------------------------------------------------
    def load_departments(self):
        src, table = "hr/departments.csv", "hr_departments"
        rows = stage_rows(self.cur, table)
        dept_id = {}
        parent_of = {}
        head_tab_of = {}
        data_rows = []          # (ln, code, parent, name, head)
        for i, (ln, data) in enumerate(rows):
            if i == 0:
                self.mark(src, table, ln, "rejected", "строка заголовка")
                continue
            f = [x.strip() for x in parse_csv_line(data, ";")]
            code, parent, name, head = f[0], f[1], f[2], f[3]
            parent_of[code] = parent or None
            head_tab_of[code] = head or None
            data_rows.append((ln, code, name))

        valid = {c for _, c, _ in data_rows}
        # Вставка без родителя (self-FK), затем корректировка связей.
        for ln, code, name in data_rows:
            self.cur.execute(
                "INSERT INTO ref.departments(code, name) VALUES (%s, %s) "
                "RETURNING department_id", (code, name))
            dept_id[code] = self.cur.fetchone()[0]

        # Разрешение иерархии: битые ссылки и циклы -> подчиняем главному корню.
        root_code = next((c for c in valid if not parent_of.get(c)), None)
        effective = dict(parent_of)      # действующее ребро (меняется при разрыве цикла)
        resolved = {}
        fixed_reason = {}
        for code in sorted(valid):
            p = effective.get(code)
            if not p:
                resolved[code] = None
                continue
            if p not in valid:
                resolved[code] = root_code
                effective[code] = root_code
                fixed_reason[code] = (f"родитель {p} не найден — подразделение "
                                      f"подчинено корню {root_code or 'NULL'}")
                continue
            # проверка цикла по действующим рёбрам
            seen, node, cycle = set(), code, False
            while node:
                if node in seen:
                    cycle = True
                    break
                seen.add(node)
                node = effective.get(node)
            if cycle:
                # разрываем ровно одно ребро — на текущем узле
                resolved[code] = root_code
                effective[code] = root_code
                fixed_reason[code] = (f"обнаружен цикл в иерархии (через {p}) — "
                                      f"подразделение подчинено корню {root_code or 'NULL'}")
            else:
                resolved[code] = p

        for code, p in resolved.items():
            if p:
                self.cur.execute(
                    "UPDATE ref.departments SET parent_id=%s WHERE code=%s",
                    (dept_id[p], code))

        for ln, code, name in data_rows:
            if code in fixed_reason:
                self.mark(src, table, ln, "corrected", fixed_reason[code])
            else:
                self.mark(src, table, ln, "loaded")
        self.flush_log()
        return dept_id, head_tab_of

    def load_employees(self, dept_id):
        src, table = "hr/employees.csv", "hr_employees"
        rows = stage_rows(self.cur, table)
        emp_by_tab = {}
        for i, (ln, data) in enumerate(rows):
            if i == 0:
                self.mark(src, table, ln, "rejected", "строка заголовка")
                continue
            f = [x.strip() for x in parse_csv_line(data, ";")]
            (tab, fio_raw, _gender, birth_raw, snils_raw, dept_code,
             job, hire_raw, fire_raw, phone_raw, email_raw) = f

            reasons = []
            fio, fio_changed = norm_fio(fio_raw)
            if fio_changed:
                reasons.append("ФИО: убраны лишние пробелы/приведён регистр")
            last, first, middle = split_fio(fio)

            birth, bfmt = parse_date(birth_raw, is_birth=True)
            hire, hfmt = parse_date(hire_raw, is_birth=False)
            fire, ffmt = parse_date(fire_raw, is_birth=False)
            for fmt in (bfmt, hfmt, ffmt):
                if fmt and fmt != "%Y-%m-%d":
                    reasons.append("дата приведена к формату ISO")
                    break

            snils = snils_raw or None
            if snils and not snils_valid(snils):
                snils = None
                reasons.append("некорректная контрольная сумма СНИЛС — поле обнулено")

            phone, phone_changed = norm_phone(phone_raw)
            if phone_changed:
                reasons.append("телефон приведён к единому формату")

            email = email_raw.strip() or None
            department_id = dept_id.get(dept_code)

            # Обязательные поля
            if not tab or not re.fullmatch(r"\d+", tab):
                self.mark(src, table, ln, "rejected", f"некорректный табельный номер: {tab!r}")
                continue
            if not last or not first:
                self.mark(src, table, ln, "rejected", "пустое ФИО")
                continue
            if hire is None:
                self.mark(src, table, ln, "rejected", f"не разобрана дата приёма: {hire_raw!r}")
                continue

            status = "corrected" if reasons else "loaded"
            reason = "; ".join(dict.fromkeys(reasons)) if reasons else None
            ok, err, row = sp_execute(
                self.cur,
                "INSERT INTO core.employees "
                "(tab_number, snils, email, phone, birth_date, first_name, last_name, "
                " middle_name, department_id, position, hire_date, dismissal_date, is_active) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING employee_id",
                (tab, snils, email, phone, birth, first, last, middle, department_id,
                 job or None, hire, fire, fire is None))
            if not ok:
                self.mark(src, table, ln, "rejected", f"ошибка СУБД: {err}")
                continue
            emp_by_tab[tab] = row[0]
            self.mark(src, table, ln, status, reason)
        self.flush_log()
        return emp_by_tab

    def update_heads(self, dept_id, head_tab_of, emp_by_tab):
        for code, head in head_tab_of.items():
            if not head:
                continue
            emp_id = emp_by_tab.get(head)
            if emp_id and code in dept_id:
                self.cur.execute(
                    "UPDATE ref.departments SET head_employee_id=%s WHERE code=%s",
                    (emp_id, code))

    def load_skills(self, code_id, emp_by_tab):
        src, table = "ref/engineer_skills.csv", "ref_engineer_skills"
        for i, (ln, data) in enumerate(stage_rows(self.cur, table)):
            if i == 0:
                self.mark(src, table, ln, "rejected", "строка заголовка")
                continue
            row = parse_csv_line(data)
            tab, code, level = row[0].strip(), row[1].strip(), row[2].strip()
            emp_id = emp_by_tab.get(tab)
            cat_id = code_id.get(code)
            if emp_id is None:
                self.mark(src, table, ln, "rejected", f"инженер {tab} не найден в кадрах")
                continue
            if cat_id is None:
                self.mark(src, table, ln, "rejected", f"категория {code} не найдена")
                continue
            lvl = int(level) if level else None
            if lvl is None or not (1 <= lvl <= 5):
                self.mark(src, table, ln, "rejected", f"некорректный уровень навыка: {level!r}")
                continue
            ok, err, _ = sp_execute(
                self.cur,
                "INSERT INTO ref.engineer_skills(employee_id, category_id, level) "
                "VALUES (%s,%s,%s)", (emp_id, cat_id, lvl))
            self.mark(src, table, ln, "loaded" if ok else "rejected",
                      None if ok else f"ошибка СУБД: {err}")
        self.flush_log()

    def load_duty(self, emp_by_tab):
        src, table = "ref/duty_schedule.csv", "ref_duty_schedule"
        for i, (ln, data) in enumerate(stage_rows(self.cur, table)):
            if i == 0:
                self.mark(src, table, ln, "rejected", "строка заголовка")
                continue
            row = parse_csv_line(data)
            tab = row[0].strip()
            start = dt.datetime.fromisoformat(row[1].strip())
            end = dt.datetime.fromisoformat(row[2].strip())
            emp_id = emp_by_tab.get(tab)
            if emp_id is None:
                self.mark(src, table, ln, "rejected", f"инженер {tab} не найден в кадрах")
                continue
            # Пересечение ловит ограничение СУБД duty_schedule_no_overlap (EXCLUDE USING gist).
            ok, err, _ = sp_execute(
                self.cur,
                "INSERT INTO ref.duty_schedule(engineer_id, shift_start, shift_end) "
                "VALUES (%s,%s,%s)", (emp_id, start, end))
            if ok:
                self.mark(src, table, ln, "loaded")
            else:
                self.mark(src, table, ln, "rejected",
                          "пересечение смен инженера (ограничение СУБД duty_schedule_no_overlap)")
        self.flush_log()

    # --- оборудование (CMDB) ----------------------------------------------
    def collect_location_names(self):
        """Все уникальные названия мест из выгрузки 1С и журнала перемещений."""
        names = set()
        header_seen = False
        for _ln, data in stage_rows(self.cur, "cmdb_assets_1c"):
            cells = [c.strip() for c in data.split("\t")]
            if not header_seen:
                if cells and cells[0].startswith("Инвентарный"):
                    header_seen = True
                continue
            if len(cells) > 7 and cells[7] and canon_inv_no(cells[0]):
                names.add(cells[7])
        for i, (ln, data) in enumerate(stage_rows(self.cur, "cmdb_asset_movements")):
            if i == 0:
                continue
            rec = (parse_csv_line(data) + [""] * 5)[:5]
            for value in (rec[1].strip(), rec[2].strip()):
                if value:
                    names.add(value)
        return sorted(names)

    def load_locations(self, names):
        """Справочник физических мест. Строки-источники учтены в своих файлах,
        поэтому здесь count=False (справочник — производная сущность)."""
        src, table = "ref/locations (производный)", "ref.locations"
        loc_id = {}
        for name in names:
            self.cur.execute(
                "INSERT INTO ref.locations(name, kind) VALUES (%s, %s) "
                "RETURNING location_id", (name, location_kind(name)))
            loc_id[name] = self.cur.fetchone()[0]
            self.mark(src, table, 0, "loaded",
                      "место из выгрузки 1С / журнала перемещений", count=False)
        self.flush_log()
        return loc_id

    def load_assets(self, emp_by_tab, loc_id):
        src, table = "cmdb/assets_1c_export.xlsx", "cmdb_assets_1c"
        asset_by_key = {}
        seen_serials = {}
        header_seen = False
        for ln, data in stage_rows(self.cur, table):
            cells = [c.strip() for c in data.split("\t")]
            if not header_seen:
                if cells and cells[0].startswith("Инвентарный"):
                    self.mark(src, table, ln, "rejected", "строка заголовка")
                    header_seen = True
                else:
                    self.mark(src, table, ln, "rejected",
                              "титульная строка выгрузки (не данные)")
                continue

            c = (cells + [""] * 12)[:12]
            (inv_raw, name, model, serial, accept_raw, cost_raw,
             mol, loc, state, _note) = c[:10]

            key = canon_inv_no(inv_raw)
            if key is None:
                self.mark(src, table, ln, "rejected",
                          "итоговая/пустая строка выгрузки 1С (не является оборудованием)")
                continue

            reasons = []
            if key != inv_raw:
                reasons.append("инвентарный номер приведён к канонической форме ИН-NNNNNN")

            serial_no = serial or None
            if serial_no and serial_no in seen_serials:
                reasons.append(f"серийный номер {serial_no!r} уже встречался — "
                               f"обнулён у дубля")
                serial_no = None
            elif serial_no:
                seen_serials[serial_no] = key

            purchase, dfail = parse_accept_date(accept_raw)
            if dfail:
                reasons.append(f"некорректная дата принятия к учёту {accept_raw!r} — "
                               f"поле обнулено")

            cost, cfail = parse_cost(cost_raw)
            if cfail:
                reasons.append(f"некорректная стоимость {cost_raw!r} — поле обнулено")

            if not name:
                self.mark(src, table, ln, "rejected", "не указан тип оборудования")
                continue

            manufacturer = model.split()[0] if model else None
            responsible_id = emp_by_tab.get(mol) if mol else None
            location_id = loc_id.get(loc) if loc else None
            is_active = state != "Списано"

            status = "corrected" if reasons else "loaded"
            reason = "; ".join(dict.fromkeys(reasons)) if reasons else None
            ok, err, row = sp_execute(
                self.cur,
                "INSERT INTO core.assets "
                "(inv_no, serial_no, asset_type, model, manufacturer, state, cost, "
                " purchase_date, responsible_id, location_id, is_active) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING asset_id",
                (key, serial_no, name, model or None, manufacturer, state, cost,
                 purchase, responsible_id, location_id, is_active))
            if not ok:
                self.mark(src, table, ln, "rejected", f"ошибка СУБД: {err}")
                continue
            asset_by_key[key] = row[0]
            self.mark(src, table, ln, status, reason)
        self.flush_log()
        return asset_by_key

    def load_asset_movements(self, asset_by_key, loc_id, emp_by_tab):
        src, table = "cmdb/asset_movements.csv", "cmdb_asset_movements"
        for i, (ln, data) in enumerate(stage_rows(self.cur, table)):
            if i == 0:
                self.mark(src, table, ln, "rejected", "строка заголовка")
                continue
            rec = (parse_csv_line(data) + [""] * 5)[:5]
            inv_raw, from_loc, to_loc, moved_on, by_tab = [x.strip() for x in rec]

            key = canon_inv_no(inv_raw)
            asset_id = asset_by_key.get(key)
            if asset_id is None:
                self.mark(src, table, ln, "rejected",
                          f"оборудование {inv_raw!r} не найдено в выгрузке 1С")
                continue
            try:
                moved = dt.date.fromisoformat(moved_on)
            except ValueError:
                self.mark(src, table, ln, "rejected", f"не разобрана дата перемещения: {moved_on!r}")
                continue

            reasons = []
            from_id = loc_id.get(from_loc)
            to_id = loc_id.get(to_loc)
            if from_loc and from_id is None:
                reasons.append(f"исходное место {from_loc!r} не найдено в справочнике — обнулено")
            if to_loc and to_id is None:
                reasons.append(f"конечное место {to_loc!r} не найдено в справочнике — обнулено")
            moved_by = emp_by_tab.get(by_tab) if by_tab else None
            if by_tab and moved_by is None:
                reasons.append(f"исполнитель {by_tab!r} не найден в кадрах — обнулён")

            status = "corrected" if reasons else "loaded"
            reason = "; ".join(dict.fromkeys(reasons)) if reasons else None
            ok, err, _ = sp_execute(
                self.cur,
                "INSERT INTO core.asset_movements "
                "(asset_id, from_location, to_location, moved_at, moved_by) "
                "VALUES (%s,%s,%s,%s,%s)",
                (asset_id, from_id, to_id, moved, moved_by))
            if not ok:
                self.mark(src, table, ln, "rejected", f"ошибка СУБД: {err}")
                continue
            self.mark(src, table, ln, status, reason)
        self.flush_log()


def main():
    log("=" * 78)
    log("ТРАНСФОРМАЦИЯ ДАННЫХ В ЦЕЛЕВЫЕ СХЕМЫ (пункт 3.3 ТЗ, этапы 3.3-А / 3.3-Б)")
    log("=" * 78)

    env = load_env(os.path.join(ROOT, ".env"))
    dsn = dsn_from_env(env)

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE " + ", ".join(TARGET_TABLES) + " RESTART IDENTITY CASCADE")
            cur.execute("DELETE FROM stage.import_log WHERE src_line > 0")

            ld = Loader(cur)
            # --- 3.3-А: справочники и кадры ---
            code_id = ld.load_categories()
            ld.load_priorities()
            ld.load_calendar()
            dept_id, head_tab_of = ld.load_departments()
            emp_by_tab = ld.load_employees(dept_id)
            ld.update_heads(dept_id, head_tab_of, emp_by_tab)
            ld.load_skills(code_id, emp_by_tab)
            ld.load_duty(emp_by_tab)
            # --- 3.3-Б: оборудование (CMDB) ---
            loc_id = ld.load_locations(ld.collect_location_names())
            asset_by_key = ld.load_assets(emp_by_tab, loc_id)
            ld.load_asset_movements(asset_by_key, loc_id, emp_by_tab)

            stats = ld.stats
            corrections = ld.corrections

    # --- отчёт -------------------------------------------------------------
    order = ["ref/categories.csv", "ref/priorities_sla.csv", "ref/production_calendar.csv",
             "ref/engineer_skills.csv", "ref/duty_schedule.csv",
             "hr/departments.csv", "hr/employees.csv",
             "cmdb/assets_1c_export.xlsx", "cmdb/asset_movements.csv"]
    out = ["ОТЧЁТ О ТРАНСФОРМАЦИИ ДАННЫХ (пункт 3.3, этапы 3.3-А / 3.3-Б)", ""]
    head = f"{'Источник':<32}{'Всего':>8}{'loaded':>9}{'corrected':>11}{'rejected':>10}"
    out.append(head)
    out.append("-" * len(head))
    tot = {"total": 0, "loaded": 0, "corrected": 0, "rejected": 0}
    for src in order:
        s = stats.get(src, {"total": 0, "loaded": 0, "corrected": 0, "rejected": 0})
        out.append(f"{src:<32}{s['total']:>8}{s['loaded']:>9}{s['corrected']:>11}{s['rejected']:>10}")
        for k in tot:
            tot[k] += s[k]
    out.append("-" * len(head))
    out.append(f"{'ИТОГО':<32}{tot['total']:>8}{tot['loaded']:>9}{tot['corrected']:>11}{tot['rejected']:>10}")
    out.append("")
    out.append("Проверка сверки: всего = loaded + corrected + rejected -> "
               f"{tot['total']} = {tot['loaded']} + {tot['corrected']} + {tot['rejected']}")
    out.append("")
    out.append("Исправления по причинам:")
    for reason, cnt in sorted(corrections.items(), key=lambda x: -x[1]):
        out.append(f"  {cnt:>5}  {reason}")
    out.append("")
    out.append("Справочник физических мест (ref.locations) строится как производная")
    out.append("сущность из выгрузки 1С и журнала перемещений (в сверке по файлам")
    out.append("не участвует, чтобы не считать одни и те же исходные строки дважды).")

    path = os.path.join(ROOT, "docs", "transform_report.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")

    log("")
    for line in out:
        log(line)
    log("")
    log("Отчёт сохранён: " + path)
    log("Готово.")


if __name__ == "__main__":
    sys.exit(main())
