# Четвертый раздел ТЗ: Безопасность базы данных (4.4)

## 1. RLS (Row-Level Security) политики

### 1.1 Зачем нужно
RLS контролирует, какие строки таблицы видит пользователь. Важно для изоляции данных между подразделениями.

### 1.2 Настройка RLS на core.tickets
```sql
ALTER TABLE core.tickets ENABLE ROW LEVEL SECURITY;

CREATE POLICY tickets_author_select ON core.tickets
    FOR SELECT
    USING (author_id = current_setting('app.user_id')::integer);

CREATE POLICY tickets_dept_select ON core.tickets
    FOR SELECT
    USING (department_id = (
        SELECT department_id FROM core.employees 
        WHERE employee_id = current_setting('app.user_id')::integer
    ));

CREATE POLICy tickets_admin_all ON core.tickets
    FOR ALL
    USING (current_setting('app.is_admin')::boolean = true);
```

### 1.3 Политики для других таблиц
```sql
-- core.employees
ALTER TABLE core.employees ENABLE ROW LEVEL SECURITY;
CREATE POLICy employees_self_select ON core.employees
    FOR SELECT
    USING (employee_id = current_setting('app.user_id')::integer);

-- core.assets
CREATE POLICy assets_dept_select ON core.assets
    FOR SELECT
    USING (location_id IN (
        SELECT location_id FROM core.employees 
        WHERE employee_id = current_setting('app.user_id')::integer
    ));
```

## 2. Маскирование и защита данных

### 2.1 Маскирование паролей в аудите
Пароли уже маскируются функцией `audit.log_ddl` (replace password_hash на `***`).

### 2.2 Проверка утечек данных
```sql
SELECT sql_command, count(*) 
FROM audit.ddl_log 
WHERE sql_command LIKE '%password%' 
GROUP BY sql_command;
```

### 2.3 Шифрование соединений (SSL/TLS)
```bash
openssl req -new -x509 -keyout server.key -out server.crt -days 365 -nodes \
  -subj "/C=RU/ST=Moscow/L=Moscow/O=ServiceDesk/CN=postgres.service.desk"

# В postgresql.conf:
ssl = on
ssl_cert_file = '/etc/ssl/postgres/server.crt'
ssl_key_file = '/etc/ssl/postgres/server.key'

# В pg_hba.conf:
host    servicedesk     all             127.0.0.1/32  md5  ssl
```

### 2.3 Политика паролей
```sql
ALTER USER dba_admin PASSWORD '<new-password>';
```
*Рекомендация: не менее 8 символов,混合大小写, цифр и спецсимволов.*

## 3. Аудит безопасности

### 3.1 Безопасные события
```sql
CREATE TABLE IF NOT EXISTS audit.security_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type varchar(50) NOT NULL,
    event_timestamp timestamptz DEFAULT now(),
    related_entity varchar(100),
    related_id bigint,
    user_action text,
    details jsonb,
    alert_level varchar(20) DEFAULT 'info'
);

INSERT INTO audit.security_events (event_type, related_entity, user_action, details, alert_level)
VALUES ('multiple_failed_logins', 'admin', '3 attempts in 1 minute', 
        '{"ip": "192.168.1.100", "time_range": "5 min"}', 'warning');
```

### 3.2 Мониторинг аномалий
```sql
SELECT e.user_action, count(*) as cnt, e.event_timestamp
FROM audit.security_events e
WHERE e.event_timestamp >= now() - interval '1 hour'
GROUP BY e.user_action, e.event_timestamp
HAVING count(*) > 10;
```

## 4. Бэкап политик безопасности

### 4.1 Сохранение RLS политий
```sql
SELECT pg_get_policydef(oid) AS policy_definition
FROM pg_policy 
WHERE polrelid = 'core.tickets'::regclass;
```

### 4.2 Восстановление после сброса настроек
```bash
psql -U dba_admin -d servicedesk < policies_backup.sql
```
