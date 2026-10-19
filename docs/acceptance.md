# Пятый раздел ТЗ: Тестирование и приёмка (4.5)

## 1. Тест-сценарии (Test Scenarios)

### 1.1 Функциональные тесты

#### 1.1.1 Сценарий создания заявки (Happy Path)
1. Зайти в систему под любым сотрудником
2. Нажать "Создать заявку"
3. Заполнить поле "Тема", "Категория", "Приоритет"
4. Нажать "Сохранить"
5. Проверить:
   - Заявка создана с номером `SD-2026-XXXX`
   - Статус изменился на "Назначена"
   - `sla_deadline` заполнился автоматически через функцию `calc_sla_deadline`
   - `sla_spent_min` = 0 (ещё не закрыта)
   - `created_at` = текущее время
   - Уведомление отправлено исполнителю

#### 1.1.2 Сценарий смены статуса (Transition)
1. Заявка в статусе "Назначена"
2. Нажать "Перевести в работу"
3. Проверить:
   - Статус изменился на "В работе"
   - В `core.ticket_status_history` добавлена новая строка
   - `sla_paused_at` сброшен (пауза остановлена)
   - `version` incremented
   - Запись в `core.outbox` о событии "status_change"

#### 1.1.3 Запрещённый переход
1. Заявка в статусе "Новая"
2. Попытаться перевести статус в "Закрыта"
3. Проверить:
   - Ошибка: "Недопустимый переход статуса заявки: Новая -> Закрыта"
   - Заявка осталась в статусе "Новая"
   - Сработал триггер `check_transition`

#### 1.1.4 Переоткрытие заявки (в течение 72 ч)
1. Заclosure заявки
2. В течение 72 часов нажать "Переоткрыть"
3. Проверить:
   - Статус изменился на "Переоткрыта"
   - `sla_paused_total` скорректирован
   - История содержит запись о переоткрытии

### 1.2 Нефункциональные тесты

#### 1.2.1 Производительность запросов
- Запустить `EXPLAIN (ANALYZE, BUFFERS)` для 15 запросов из `41_queries.sql`
- Убедиться, что планы используют индексы из `10_indexes.sql`
- Время выполнения не превышает установленных SLA (например, топ-3 по категории < 1 сек)

#### 1.2.2 Инtegrity checks
```sql
-- Проверить целостность внешних ключей
SELECT count(*) FROM core.tickets t
WHERE t.category_id NOT IN (SELECT category_id FROM ref.categories);

-- Проверить referential integrity
SELECT count(*) FROM core.tickets t
LEFT JOIN core.employees e ON t.executor_id = e.employee_id
WHERE e.employee_id IS NULL AND t.executor_id IS NOT NULL;
```

#### 1.2.3 Тест нагрузки (Load Test)
```bash
# Запуск генератора (если не был запущен ранее)
bash analysis/gen_load.sh 10000  # 10 000 заявок для теста

# Проверка производительности после генерации
psql -U dba_admin -d servicedesk -f db/queries/41_queries.sql
```

## 2. Сценарии приёмки (Acceptance Criteria)

### 2.1 Clean Start Acceptance Test
Этот тест проверяет, что система работает с нуля:

```bash
# 1. УдалитьVolume с данными
docker compose down -v

# 2. Запустить с нуля
docker compose up -d

# 3. Подождать запуска всех контейнеров (health checks)
#    (ждём статус healthy для sd-postgres, sd-ldap, sd-mail)

# 4. Запустить импорт старых данных
bash importer/load_stage.sh  # 150 716 строк staging data

# 5. Запустить трансформацию
python3 importer/transform.py  # загружает в core

# 6. Запустить почтовый интейк
python3 mailintake/intake.py  # извлекает письма

# 7. Генерация нагрузки (не более 100 000 заявок для скорости)
python3 analysis/gen_load.py 100000

# 8. Проверить основные метрики:
#    - core.tickets count = 150716 + 19 + 100000
#    - Все заявки имеют ticket_number
#    - 11 функций работают (test function calls)
#    - 17 триггеров сработали (проверить audit.log)
#    - SLA пересчитан (sla_deadline filled for % of tickets)
#    - 15 запросов из 41_queries.sql отработали без ошибок
```

### 2.2 Чек-лист приёмки (Sign-Off Checklist)

| # | Критерий | Статус |
|---|---|---|
| 1 | `docker compose up` запускает все 3 контейнера (healthy) | |
| 2 | `load_stage.py` загружает 150 716 строк без ошибок | |
| 3 | `transform.py` загружает данные в core без потерь | |
| 4 | 11 функций работают без сбоев (список в `db/init/08_functions.sql`) | |
| 5 | 17 триггеров активны (проверка через `SELECT tgname FROM pg_trigger`) | |
| 6 | 15 запросов из `41_queries.sql` отрабатывают без ошибок | |
| 7 | SLA пересчитан для ≥ 95% заявок (sla_deadline NOT NULL) | |
| 7 | Нарушения переходов статусов блокируются триггерами | |
| 8 | Аудит	log записывает все DDL и изменения данных | |
| 9 | RLS политики включены и не ломают обычные запросы | |
| 10 | Резервное копирование работает (pg_dump прошел успешно) | |
| 11 | LDAP-импорт выполнен (если предусмотрен в конфиге) | |
| 12 | Производительность запросов в Norm (сравнение с perf_queries_report.txt) | |

### 2.3 Отказные случаи (Rollback Criteria)

Система должна позволять откатывать изменения в случае критических сбоев:

```bash
# Полный откат к предыдущему состоянию
docker compose down -v
docker compose up -d

# Или частичный откат через SQL-тулзы:
psql -U dba_admin -d servicedesk < backup_20261020.sql
```

## 3. Регрессионные проверки

### 3.1 Проверка после миграции
После любого изменения схемы (добавление индекса, смена колонки, смена политики) выполняем:

```sql
-- 1. Проверить, что старые запросы все еще работают
EXPLAIN (ANALYZE, BUFFERS) SELECT ... FROM core.tickets WHERE ...;

-- 2. Проверить, что триггеры сработали
SELECT * FROM audit.ddl_log WHERE command_type = 'INSERT' LIMIT 5;

-- 3. Проверить, что данные не повреждены
SELECT count(*) FROM core.tickets WHERE ticket_id <= 0;  -- должно быть 0
```

### 3.2 Автоматический регрессионный тест
```bash
# Скрипт проверяет 10 критических сценариев
# и возвращает код возврата 0 если все прошли
for scenario in $(cat test_scenarios.txt); do
    result=$(run_test "$scenario")
    if [ "$result" != "PASS" ]; then
        echo "FAIL: $scenario"
        exit 1
    fi
done
echo "All tests passed"
exit 0
```

## 4. Финальная верификация (Final Verification)

### 4.1 Full Cycle Verification
После завершения всех работ (4.1–4.5) выполняем:

```bash
# 1. Фиксация итогового состояния
docker compose exec -T postgres pg_dump -U dba_admin -d servicedesk > /final_dump.sql

# 2. Генерация отчета по estado actual
psql -U dba_admin -d servicedesk -c "
SELECT 'total_tickets' as metric, count(*) as value FROM core.tickets
UNION ALL
SELECT 'with_sla_deadline', count(*) FROM core.tickets WHERE sla_deadline IS NOT NULL
UNION ALL
SELECT 'with_closed_at', count(*) FROM core.tickets WHERE closed_at IS NOT NULL
UNION ALL
SELECT 'with_spent_time', count(*) FROM core.tickets WHERE sla_spent_min IS NOT NULL
UNION ALL
SELECT 'overdue_closed', count(*) FROM core.tickets 
    WHERE status = 'Закрыта' AND closed_at > sla_deadline;
"

# 2. Сравнение с базовой линией (baseline)
#    Сравниваем количество заявок, среднее время решения, покрытие индексами

# 3. Формирование отчета для преподавателя
#    - Краткий отчет (3 параграфа)
#    - Приложения (матрицы, журналы, диаграммы)
#    - Шпаргалка для защиты
```