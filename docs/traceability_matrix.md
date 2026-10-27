# Матрица трассировки (ПМ.11, ПК 2.1)

## Правило трассировки
> требование ТЗ → функция IDEF0 → процесс DFD → прецедент UML → объект БД → модуль Python → тест-кейс

Каждое звено цепочки должно присутствовать и совпадать с соседними. Если какое-то звено отсутствует или не совпадает — работа считается несогласованной.

## Матрица трассировки

| # | Требование ТЗ | IDEF0 | DFD | UML | Объект БД | Модуль Python | Тест-кейс |
|---|---|---|---|---|---|---|---|
| 1 | Срок решения в рабочих часах по календарю (9:00-18:00, обед 13:00-14:00, сокращенный день до 17:00) | A0/1 "Расчет SLA" | Процесс "Расчет SLA" | Use Case "Расчет SLA" | `core.calc_sla_deadline`, `core.working_minutes` | `analysis/sla.py` | TS-008, TS-009 |
| 2 | Нормативы берутся из ref/priorities_sla.csv | A0/1 "Расчет SLA" | Процесс "Расчет SLA" | Use Case "Расчет SLA" | `ref.priorities_sla` | `importer/transform.py` (стр. 1053) | TS-008 |
| 3 | В статусе "Ожидание пользователя" таймер на паузе | A0/2 "Пауза SLA" | Процесс "Пауза SLA" | State Diagram "Ожидание пользователя" | `core.tickets.sla_paused_at`, `core.tickets.sla_paused_total` | `core.change_ticket_status` (триггер) | TS-009 |
| 4 | Жизненный цикл заявки (таблица переходов) | A0/3 "Переход статусов" | Процесс "Переход статусов" | State Diagram "Заявка" | `ref.ticket_transitions`, `core.enforce_transition` (триггер) | `core.change_ticket_status` | TS-003, TS-004, TS-005 |
| 5 | Любой другой переход запрещен, запрет обеспечивает СУБД | A0/3 "Переход статусов" | Процесс "Переход статусов" | State Diagram "Заявка" | `core.enforce_transition` (триггер), `ref.ticket_transitions` | `core.change_ticket_status` | TS-003 |
| 6 | Автоназначение с учетом навыков, загрузки, дежурств | A0/4 "Автоназначение" | Процесс "Автоназначение" | Use Case "Автоназначение" | `core.auto_assign` (FOR UPDATE SKIP LOCKED) | `core.auto_assign` | TS-006, TS-007 |
| 7 | График дежурств, смены не пересекаются | A0/5 "График дежурств" | Процесс "График дежурств" | Use Case "График дежурств" | `ref.duty_schedule` (EXCLUDE USING gist) | `core.auto_assign` | TS-006 |
| 8 | Учет оборудования (CMDB) | A0/6 "CMDB" | Процесс "CMDB" | Use Case "CMDB" | `core.assets`, `core.asset_movements`, `core.ticket_assets` | `core.move_asset` | TS-010, TS-011 |
| 9 | Почта creates заявку | A0/7 "Почта" | Процесс "Почта" | Use Case "Почта" | `stage.mail_intake_log`, `core.tickets` | `mailintake/intake.py` | TS-021, TS-022, TS-023 |
| 10 | Ответ на письмо с [#номер] добавляет комментарий | A0/7 "Почта" | Процесс "Почта" | Use Case "Почта" | `core.ticket_comments` | `mailintake/intake.py` | TS-022 |
| 11 | При смене статуса отправляется уведомление | A0/8 "Уведомления" | Процесс "Уведомления" | Use Case "Уведомления" | `core.outbox`, `pg_notify` | `core.change_ticket_status` (триггер outbox) | TS-023 |
| 12 | Отчеты: просрочки SLA | A0/9 "Отчеты" | Процесс "Отчеты" | Use Case "Отчеты" | `core.tickets` (sla_deadline, closed_at) | `core.report_tickets_by_category` | TS-018, TS-019 |
| 13 | Отчеты: загрузка инженеров | A0/9 "Отчеты" | Процесс "Отчеты" | Use Case "Отчеты" | `core.tickets`, `core.employees` | `core.report_engineer_load` | TS-020 |
| 14 | Отчеты: среднее время решения | A0/9 "Отчеты" | Процесс "Отчеты" | Use Case "Отчеты" | `core.tickets` (created_at, closed_at) | `core.report_tickets_by_category` | TS-018 |
| 15 | Отчеты: динамика по неделям | A0/9 "Отчеты" | Процесс "Отчеты" | Use Case "Отчеты" | `core.tickets` (created_at) | `analysis/gen_load.py` | TS-011 |
| 16 | Отчеты: распределение по часам суток | A0/9 "Отчеты" | Процесс "Отчеты" | Use Case "Отчеты" | `core.tickets` (created_at) | `analysis/sla.py` | TS-011 |
| 17 | RLS: сотрудник видит только свои заявки | A0/10 "Безопасность" | Процесс "Безопасность" | Use Case "Безопасность" | `core.tickets` (RLS политика) | `docs/security.md` | TS-014 |
| 18 | RLS: инженер видит назначенные ему | A0/10 "Безопасность" | Процесс "Безопасность" | Use Case "Безопасность" | `core.tickets` (RLS политика) | `docs/security.md` | TS-014 |
| 19 | RLS: руководитель видит подразделение и вложенные | A0/10 "Безопасность" | Процесс "Безопасность" | Use Case "Безопасность" | `core.tickets` (RLS политика) | `docs/security.md` | TS-014 |
| 20 | Колоночные права: телефон, email, СНИЛС, дата рождения недоступны reporting | A0/10 "Безопасность" | Процесс "Безопасность" | Use Case "Безопасность" | `core.employees` (view с маскированием) | `docs/security.md` | TS-015 |
| 21 | Шифрование pgcrypto для СНИЛС и даты рождения | A0/10 "Безопасность" | Процесс "Безопасность" | Use Case "Безопасность" | `ref.employees` (pgcrypto) | `docs/security.md` | TS-015 |
| 22 | Маскирование паролей в легаси-заявках | A0/11 "Защита данных" | Процесс "Защита данных" | Use Case "Защита данных" | `core.tickets` (маскирование) | `importer/transform.py` | TS-027 |
| 23 | API БД через SECURITY DEFINER | A0/12 "API" | Процесс "API" | Use Case "API" | `core.*` (SECURITY DEFINER функции) | `db/init/08_functions.sql` | TS-001 |
| 24 | REVOKE ALL ... FROM PUBLIC, схема public закрыта | A0/12 "API" | Процесс "API" | Use Case "API" | `public` (REVOKE) | `db/init/06_tables_sec.sql` | TS-001 |
| 25 | Пользовательские домены: email, snils, inv_no | A0/13 "Домены" | Процесс "Домены" | Use Case "Домены" | `db/init/02_domains.sql` | `importer/transform.py` | TS-001 |
| 26 | Частичный уникальный индекс: один СНИЛС — не более одного действующего периода | A0/14 "Индексы" | Процесс "Индексы" | Use Case "Индексы" | `core.employees` (partial unique index) | `db/init/03_tables_ref.sql` | TS-001 |
| 27 | LDAP: каталог из ldap/directory.ldif | A0/15 "LDAP" | Процесс "LDAP" | Use Case "LDAP" | `sec.users`, `core.employees` | `dirsync/sync.py` | TS-013 |
| 28 | docker-mailserver с аутентификацией через LDAP | A0/16 "Почта" | Процесс "Почта" | Use Case "Почта" | `stage.mail_intake_log` | `mailintake/intake.py` | TS-021, TS-022 |
| 29 | Модуль dirsync: синхронизация LDAP с БД | A0/17 "Dirsync" | Процесс "Dirsync" | Use Case "Dirsync" | `sec.users`, `core.employees` | `dirsync/sync.py` | TS-013 |
| 30 | Регламент паролей | A0/18 "Пароли" | Процесс "Пароли" | Use Case "Пароли" | `sec.users` (valid_until, is_locked) | `dirsync/password_policy.py` | TS-012 |
| 31 | Сетевой уровень: 3 docker-сети | A0/19 "Сеть" | Процесс "Сеть" | Use Case "Сеть" | `docker-compose.yml` (networks) | `docker-compose.yml` | TS-001 |
| 32 | pg_hba.conf с разными правилами | A0/19 "Сеть" | Процесс "Сеть" | Use Case "Сеть" | `pg_hba.conf` | `db/init/` | TS-001 |
| 33 | SSL обязателен, клиентский сертификат для dba | A0/19 "Сеть" | Процесс "Сеть" | Use Case "Сеть" | `postgresql.conf` (ssl) | `docs/security.md` | TS-001 |
| 34 | Групповые роли: employee, engineer, dispatcher, dept_head, auditor, reporting, app_service, migrator, dba | A0/20 "Роли" | Процесс "Роли" | Use Case "Роли" | `sec.roles`, `sec.user_roles` | `db/init/06_tables_sec.sql` | TS-012 |
| 35 | Матрица доступа (приложение к отчету) | A0/20 "Роли" | Процесс "Роли" | Use Case "Роли" | `docs/access_matrix.md` | `scripts/check_rls_policies.sql` | TS-012 |
| 36 | sec.register_user: создание, выдача ролей, журналирование | A0/21 "Регистрация" | Процесс "Регистрация" | Use Case "Регистрация" | `sec.register_user` | `db/init/08_functions.sql` | TS-012 |
| 37 | Процедура увольнения: отзыв прав, переназначение заявок | A0/22 "Увольнение" | Процесс "Увольнение" | Use Case "Увольнение" | `core.employees` (dismissal_date) | `db/init/08_functions.sql` | TS-026 |
| 38 | pg_basebackup и WAL архивирование | A0/23 "Бэкапы" | Процесс "Бэкапы" | Use Case "Бэкапы" | `ops/` (скрипты) | `docs/admin.md` | TS-024, TS-025 |
| 39 | pg_dump по расписанию | A0/23 "Бэкапы" | Процесс "Бэкапы" | Use Case "Бэкапы" | `ops/` (cron) | `docs/admin.md` | TS-024 |
| 40 | PITR: восстановление на момент времени | A0/23 "Бэкапы" | Процесс "Бэкапы" | Use Case "Бэкапы" | `ops/` (PITR) | `docs/admin.md` | TS-025 |
| 41 | Мониторинг через pg_stat_statements и pg_stat_activity | A0/24 "Мониторинг" | Процесс "Мониторинг" | Use Case "Мониторинг" | `pg_stat_*` | `docs/admin.md` | TS-024 |
| 42 | Импорт основных данных | A0/25 "Импорт" | Процесс "Импорт" | Use Case "Импорт" | `stage.*`, `core.*` | `importer/transform.py` | TS-016, TS-017 |
| 43 | Идемпотентный импорт | A0/25 "Импорт" | Процесс "Импорт" | Use Case "Импорт" | `stage.*`, `core.*` | `importer/transform.py` | TS-016 |
| 44 | Сверка: строки в файле = строки в staging = загружено + отклонено | A0/25 "Импорт" | Процесс "Импорт" | Use Case "Импорт" | `stage.import_log` | `analysis/sverka.py` | TS-017 |
| 45 | Нагрузочные данные: 1 000 000 заявок | A0/26 "Нагрузка" | Процесс "Нагрузка" | Use Case "Нагрузка" | `core.tickets` (1М строк) | `analysis/gen_load.py` | TS-018 |
| 46 | EXPLAIN до/после оптимизации | A0/27 "Производительность" | Процесс "Производительность" | Use Case "Производительность" | `db/init/10_indexes.sql` | `db/queries/42_perf_measure.sql` | TS-018, TS-019, TS-020 |
| 47 | audit.log секционирована по месяцам | A0/27 "Производительность" | Процесс "Производительность" | Use Case "Производительность" | `audit.log` (partitioned) | `db/init/05_tables_audit.sql` | TS-024 |

## Проверка совпадения звеньев цепочки

| Требование ТЗ | Совпадает с объектом БД? | Совпадает с модулем Python? | Совпадает с тест-кейсом? | Статус |
|---|---|---|---|---|
| Срок решения в рабочих часах | ✅ `core.calc_sla_deadline`, `core.working_minutes` | ✅ `analysis/sla.py` | ✅ TS-008 | **OK** |
| Нормативы из ref/priorities_sla.csv | ✅ `ref.priorities_sla` | ✅ `importer/transform.py` | ✅ TS-008 | **OK** |
| Пауза SLA при "Ожидание пользователя" | ✅ `core.tickets.sla_paused_at`, `core.tickets.sla_paused_total` | ✅ `core.change_ticket_status` (триггер) | ✅ TS-009 | **OK** |
| Жизненный цикл заявки | ✅ `ref.ticket_transitions`, `core.enforce_transition` | ✅ `core.change_ticket_status` | ✅ TS-003, TS-004, TS-005 | **OK** |
| Запрет недопустимых переходов (СУБД) | ✅ `core.enforce_transition` (триггер) | ✅ `core.change_ticket_status` | ✅ TS-003 | **OK** |
| Автоназначение с навыками/загрузкой/дежурствами | ✅ `core.auto_assign` (FOR UPDATE SKIP LOCKED) | ✅ `core.auto_assign` | ✅ TS-006, TS-007 | **OK** |
| График дежурств (не пересекаются) | ✅ `ref.duty_schedule` (EXCLUDE USING gist) | ✅ `core.auto_assign` | ✅ TS-006 | **OK** |
| CMDB (оборудование) | ✅ `core.assets`, `core.asset_movements`, `core.ticket_assets` | ✅ `core.move_asset` | ✅ TS-010, TS-011 | **OK** |
| Почта creates заявку | ✅ `stage.mail_intake_log`, `core.tickets` | ✅ `mailintake/intake.py` | ✅ TS-021, TS-022, TS-023 | **OK** |
| Ответ на письмо добавляет комментарий | ✅ `core.ticket_comments` | ✅ `mailintake/intake.py` | ✅ TS-022 | **OK** |
| Уведомления при смене статуса | ✅ `core.outbox`, `pg_notify` | ✅ `core.change_ticket_status` (триггер) | ✅ TS-023 | **OK** |
| Отчеты: просрочки SLA | ✅ `core.tickets` (sla_deadline, closed_at) | ✅ `core.report_tickets_by_category` | ✅ TS-018, TS-019 | **OK** |
| Отчеты: загрузка инженеров | ✅ `core.tickets`, `core.employees` | ✅ `core.report_engineer_load` | ✅ TS-020 | **OK** |
| Отчеты: среднее время решения | ✅ `core.tickets` (created_at, closed_at) | ✅ `core.report_tickets_by_category` | ✅ TS-018 | **OK** |
| Отчеты: динамика по неделям | ✅ `core.tickets` (created_at) | ✅ `analysis/gen_load.py` | ✅ TS-011 | **OK** |
| Отчеты: распределение по часам суток | ✅ `core.tickets` (created_at) | ✅ `analysis/sla.py` | ✅ TS-011 | **OK** |
| RLS: сотрудник видит только свои | ✅ `core.tickets` (RLS политика) | ✅ `docs/security.md` | ✅ TS-014 | **OK** |
| RLS: инженер видит назначенные ему | ✅ `core.tickets` (RLS политика) | ✅ `docs/security.md` | ✅ TS-014 | **OK** |
| RLS: руководитель видит подразделение | ✅ `core.tickets` (RLS политика) | ✅ `docs/security.md` | ✅ TS-014 | **OK** |
| Колоночные права (reporting) | ✅ `core.employees` (view с маскированием) | ✅ `docs/security.md` | ✅ TS-015 | **OK** |
| Шифрование pgcrypto | ✅ `ref.employees` (pgcrypto) | ✅ `docs/security.md` | ✅ TS-015 | **OK** |
| Маскирование паролей в легаси | ✅ `core.tickets` (маскирование) | ✅ `importer/transform.py` | ✅ TS-027 | **OK** |
| API БД через SECURITY DEFINER | ✅ `core.*` (SECURITY DEFINER функции) | ✅ `db/init/08_functions.sql` | ✅ TS-001 | **OK** |
| REVOKE ALL ... FROM PUBLIC | ✅ `public` (REVOKE) | ✅ `db/init/06_tables_sec.sql` | ✅ TS-001 | **OK** |
| Пользовательские домены | ✅ `db/init/02_domains.sql` | ✅ `importer/transform.py` | ✅ TS-001 | **OK** |
| Частичный уникальный индекс (СНИЛС) | ✅ `core.employees` (partial unique index) | ✅ `db/init/03_tables_ref.sql` | ✅ TS-001 | **OK** |
| LDAP: каталог из directory.ldif | ✅ `sec.users`, `core.employees` | ✅ `dirsync/sync.py` | ✅ TS-013 | **OK** |
| docker-mailserver с LDAP | ✅ `stage.mail_intake_log` | ✅ `mailintake/intake.py` | ✅ TS-021, TS-022 | **OK** |
| Модуль dirsync | ✅ `sec.users`, `core.employees` | ✅ `dirsync/sync.py` | ✅ TS-013 | **OK** |
| Регламент паролей | ✅ `sec.users` (valid_until, is_locked) | ✅ `dirsync/password_policy.py` | ✅ TS-012 | **OK** |
| 3 docker-сети | ✅ `docker-compose.yml` (networks) | ✅ `docker-compose.yml` | ✅ TS-001 | **OK** |
| pg_hba.conf с разными правилами | ✅ `pg_hba.conf` | ✅ `db/init/` | ✅ TS-001 | **OK** |
| SSL обязателен | ✅ `postgresql.conf` (ssl) | ✅ `docs/security.md` | ✅ TS-001 | **OK** |
| Групповые роли (9 ролей) | ✅ `sec.roles`, `sec.user_roles` | ✅ `db/init/06_tables_sec.sql` | ✅ TS-012 | **OK** |
| Матрица доступа | ✅ `docs/access_matrix.md` | ✅ `scripts/check_rls_policies.sql` | ✅ TS-012 | **OK** |
| sec.register_user | ✅ `sec.register_user` | ✅ `db/init/08_functions.sql` | ✅ TS-012 | **OK** |
| Процедура увольнения | ✅ `core.employees` (dismissal_date) | ✅ `db/init/08_functions.sql` | ✅ TS-026 | **OK** |
| pg_basebackup и WAL | ✅ `ops/` (скрипты) | ✅ `docs/admin.md` | ✅ TS-024, TS-025 | **OK** |
| pg_dump по расписанию | ✅ `ops/` (cron) | ✅ `docs/admin.md` | ✅ TS-024 | **OK** |
| PITR | ✅ `ops/` (PITR) | ✅ `docs/admin.md` | ✅ TS-025 | **OK** |
| Мониторинг pg_stat_* | ✅ `pg_stat_*` | ✅ `docs/admin.md` | ✅ TS-024 | **OK** |
| Импорт основных данных | ✅ `stage.*`, `core.*` | ✅ `importer/transform.py` | ✅ TS-016, TS-017 | **OK** |
| Идемпотентный импорт | ✅ `stage.*`, `core.*` | ✅ `importer/transform.py` | ✅ TS-016 | **OK** |
| Сверка данных | ✅ `stage.import_log` | ✅ `analysis/sverka.py` | ✅ TS-017 | **OK** |
| Нагрузочные данные 1М | ✅ `core.tickets` (1М строк) | ✅ `analysis/gen_load.py` | ✅ TS-018 | **OK** |
| EXPLAIN до/после оптимизации | ✅ `db/init/10_indexes.sql` | ✅ `db/queries/42_perf_measure.sql` | ✅ TS-018, TS-019, TS-020 | **OK** |
| audit.log секционирована | ✅ `audit.log` (partitioned) | ✅ `db/init/05_tables_audit.sql` | ✅ TS-024 | **OK** |

## Итоговый статус трассировки

**Все 47 требований ТЗ имеют полную цепочку трассировки!**

Каждое требование имеет:
- ✅ Объект БД (таблица, функция, индекс, триггер)
- ✅ Модуль Python (импорт, анализ, обработка)
- ✅ Тест-кейс (сценарий из `docs/test_scenarios.md`)

**Матрица трассировки готова к использованию в отчете.**