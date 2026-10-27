# Матрица доступа (ПМ.11, п. 4.4)

## Матрица «роль × объект × операция»

**Роли системы:** employee, engineer, dispatcher, dept_head, auditor, reporting, app_service, migrator, dba

### Легенда:
- ✅ — разрешено
- ❌ — запрещено
- R — read (чтение)
- W — write (запись)
- D — delete (удаление)
- X — execute (выполнение функций)

---

### Схема `ref` (справочники)

| Объект | employee | engineer | dispatcher | dept_head | auditor | reporting | app_service | migrator | dba |
|---|---|---|---|---|---|---|---|---|---|
| `ref.departments` | R | R | R | R/W | R | R | R | W | R/W/D |
| `ref.employees` | R (свои) | R | R | R/W | R | R (маска) | R | W | R/W/D |
| `ref.categories` | R | R | R | R | R | R | R | W | R/W/D |
| `ref.priorities_sla` | R | R | R | R | R | R | R | W | R/W/D |
| `ref.locations` | R | R | R | R | R | R | R | W | R/W/D |
| `ref.assets` | R | R | R | R | R | R | R | W | R/W/D |
| `ref.engineer_skills` | - | R/W | R | R | R | R | R | W | R/W/D |
| `ref.duty_schedule` | - | R | R | R | R | R | R | W | R/W/D |
| `ref.production_calendar` | R | R | R | R | R | R | R | W | R/W/D |
| `ref.ticket_transitions` | R | R | R | R | R | R | R | W | R/W/D |
| `ref.ticket_status` | R | R | R | R | R | R | R | W | R/W/D |

### Схема `core` (предметная область)

| Объект | employee | engineer | dispatcher | dept_head | auditor | reporting | app_service | migrator | dba |
|---|---|---|---|---|---|---|---|---|---|
| `core.tickets` | R/W (свои) | R/W (назначенные) | R/W (все) | R/W (свои + подразделение) | R | R (маска) | R/W | W | R/W/D |
| `core.ticket_status_history` | R (свои) | R (назначенные) | R (все) | R (свои + подразделение) | R | R (маска) | R | W | R/W/D |
| `core.ticket_comments` | R/W (свои) | R/W (назначенные) | R/W (все) | R (свои + подразделение) | R | R (маска) | R | W | R/W/D |
| `core.ticket_attachments` | R/W (свои) | R/W (назначенные) | R/W (все) | R (свои + подразделение) | R | R (маска) | R | W | R/W/D |
| `core.ticket_assets` | R (свои) | R (назначенные) | R (все) | R (свои + подразделение) | R | R (маска) | R | W | R/W/D |
| `core.assets` | R | R | R | R | R | R | R | W | R/W/D |
| `core.asset_movements` | R | R | R | R | R | R | R | W | R/W/D |
| `core.outbox` | - | - | - | - | R | - | R/W | W | R/W/D |

### Схема `audit` (журналы)

| Объект | employee | engineer | dispatcher | dept_head | auditor | reporting | app_service | migrator | dba |
|---|---|---|---|---|---|---|---|---|---|
| `audit.log` | - | - | - | - | R | - | R | W | R/W/D |
| `audit.ddl_log` | - | - | - | - | R | - | R | W | R/W/D |
| `audit.security_events` | - | - | - | - | R | - | R | W | R/W/D |

### Схема `sec` (безопасность)

| Объект | employee | engineer | dispatcher | dept_head | auditor | reporting | app_service | migrator | dba |
|---|---|---|---|---|---|---|---|---|---|
| `sec.users` | R (свои) | - | - | - | R | - | R/W | W | R/W/D |
| `sec.roles` | - | - | - | - | R | - | R | W | R/W/D |
| `sec.user_roles` | - | - | - | - | R | - | R | W | R/W/D |

### Схема `stage` (импорт)

| Объект | employee | engineer | dispatcher | dept_head | auditor | reporting | app_service | migrator | dba |
|---|---|---|---|---|---|---|---|---|---|
| `stage.*` | - | - | - | - | R | - | R | W | R/W/D |

### Функции и процедуры

| Функция/Процедура | employee | engineer | dispatcher | dept_head | auditor | reporting | app_service | migrator | dba |
|---|---|---|---|---|---|---|---|---|---|
| `core.calc_sla_deadline` | X | X | X | X | X | X | X | X | X |
| `core.calc_sla_spent` | X | X | X | X | X | X | X | X | X |
| `core.change_ticket_status` | - | X | X | X | - | - | X | X | X |
| `core.auto_assign` | - | - | X | - | - | - | X | X | X |
| `core.reopen_ticket` | X | - | X | - | - | - | X | X | X |
| `core.move_asset` | - | - | X | - | - | - | X | X | X |
| `sec.register_user` | - | - | - | - | - | - | X | X | X |
| `core.report_tickets_by_category` | - | - | - | - | - | X | X | X | X |
| `core.report_engineer_load` | - | - | - | - | - | X | X | X | X |
| `core.touch_row` | - | - | X | - | - | - | X | X | X |
| `core.before_ticket_insert` | - | - | - | - | - | - | X | X | X |

### Триггеры

| Триггер | employee | engineer | dispatcher | dept_head | auditor | reporting | app_service | migrator | dba |
|---|---|---|---|---|---|---|---|---|---|
| `audit.log` (jsonb diff) | - | - | - | - | R | - | R | W | R/W |
| `core.enforce_transition` | - | - | - | - | - | - | X | X | X |
| `core.sla_pause` | - | - | - | - | - | - | X | X | X |
| `core.outbox_notify` | - | - | - | - | - | - | X | X | X |
| `core.touch_row` | - | - | - | - | - | - | X | X | X |
| `audit.log_ddl` (event trigger) | - | - | - | - | R | - | R | W | R/W |

### Представления (Views)

| View | employee | engineer | dispatcher | dept_head | auditor | reporting | app_service | migrator | dba |
|---|---|---|---|---|---|---|---|---|---|
| `v_tickets_masked` (маскирование) | R | R | R | R | R | R | R | R | R |
| `v_engineer_load` | - | R (свои) | R (все) | R (свои) | R | R | R | R | R |
| `v_asset_location` | R | R | R | R | R | R | R | R | R |

---

## Проверка соответствия матрицы и фактических прав

Скрипт автоматической проверки: `scripts/check_rls_policies.sql`

### Сводка по ролям

| Роль | Основные права | Ограничения |
|---|---|---|
| `employee` | R/W свои заявки, R справочники | Нет доступа к чужим заявкам |
| `engineer` | R/W назначенные заявки, R справочники | Нет доступа к чужим заявкам |
| `dispatcher` | R/W все заявки, R справочники | Полный доступ к заявкам |
| `dept_head` | R/W свои + подразделение | Нет доступа к другим подразделениям |
| `auditor` | R всех таблиц, R журналов | Только чтение |
| `reporting` | R с маскированием | Нет доступа к СНИЛС, телефону, email, дате рождения |
| `app_service` | R/W заявки, R справочники | Только через функции SECURITY DEFINER |
| `migrator` | W на все таблицы | Только миграции, нет чтения |
| `dba` | R/W/D все таблицы | Полный доступ |

### Принципы безопасности

1. **Минимальные привилегии:** Каждая роль имеет только необходимые права
2. **RLS на уровне строк:** Сотрудники видят только свои заявки
3. **Колоночные права:** Чувствительные данные маскируются для роли `reporting`
4. **Шифрование:** СНИЛС и дата рождения шифруются через `pgcrypto`
5. **Аудит:** Все изменения логируются в `audit.log` и `audit.ddl_log`
6. **SECURITY DEFINER:** Изменения выполняются только через функции с явным `search_path`

---

*Матрица доступа подготовлена для п. 4.4 ТЗ. Используется как приложение к отчёту и для автоматической проверки прав (см. `scripts/check_rls_policies.sql`).*