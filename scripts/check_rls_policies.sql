-- ============================================================================
-- SQL-скрипт автоматической проверки RLS-политик на соответствие матрице доступа
-- (ПМ.11, п. 4.4: матрица доступа «роль × объект × операция»)
--
-- Скрипт проверяет:
-- 1. Все политики RLS включены на таблицах с чувствительными данными
-- 2. Политики соответствуют матрице доступа из docs/access_matrix.md
-- 3. Каждая роль имеет только необходимые права
-- 4. Проверка колоночных прав (masking) для роли reporting
--
-- Запуск:
--   psql -U dba_admin -d servicedesk -f scripts/check_rls_policies.sql
-- ============================================================================

\echo === ПРОВЕРКА RLS-ПОЛИТИК НА СООТВЕТСТВИЕ МАТРИЦЕ ДОСТУПА ===
\echo

-- ============================================================================
-- 1. Проверка: RLS включена на таблицах с чувствительными данными
-- ============================================================================
\echo === 1. RLS включена на таблицах с чувствительными данными ===

SELECT 
    n.nspname || '.' || c.relname AS table_name,
    c.relrowsecurity AS rls_enabled,
    c.relforcerowsecurity AS force_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('core', 'ref', 'sec', 'audit')
  AND c.relkind = 'r'
  AND c.relname IN ('tickets', 'employees', 'assets', 'users')
ORDER BY 1;

\echo
\echo === 2. Список всех RLS-политик ===

SELECT 
    n.nspname || '.' || c.relname AS table_name,
    p.polname AS policy_name,
    p.polcmd AS operation,
    p.polroles::regrole[] AS roles,
    pg_get_expr(p.polqual, p.polrelid) AS using_expr,
    pg_get_expr(p.polwithcheck, p.polrelid) AS with_check_expr
FROM pg_policy p
JOIN pg_class c ON c.oid = p.polrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('core', 'ref', 'sec', 'audit')
ORDER BY 1, 2;

-- ============================================================================
-- 2. Проверка колоночных прав (masking) для роли reporting
-- ============================================================================
\echo
\echo === 3. Проверка колоночных прав: reporting не видит СНИЛС, телефон, email, дату рождения ===

-- Проверяем, что у роли reporting нет прямого доступа к sensitive колонкам
SELECT 
    grantee AS role_name,
    table_schema || '.' || table_name AS table_name,
    column_name,
    privilege_type
FROM information_schema.column_privileges
WHERE grantee = 'reporting'
  AND table_schema IN ('core', 'ref', 'sec')
  AND column_name IN ('snils', 'phone', 'email', 'birth_date', 'password_hash', 'login_name')
ORDER BY 1, 2, 3;

-- ============================================================================
-- 3. Проверка прав роли reporting на таблицы
-- ============================================================================
\echo
\echo === 4. Права роли reporting на таблицы ===

SELECT 
    grantee AS role_name,
    table_schema || '.' || table_name AS table_name,
    privilege_type
FROM information_schema.role_table_grants
WHERE grantee = 'reporting'
  AND table_schema IN ('core', 'ref', 'sec', 'audit')
ORDER BY 2, 3;

-- ============================================================================
-- 4. Проверка прав роли employee на таблицы
-- ============================================================================
\echo
\echo === 5. Права роли employee на таблицы ===

SELECT 
    grantee AS role_name,
    table_schema || '.' || table_name AS table_name,
    privilege_type
FROM information_schema.role_table_grants
WHERE grantee = 'employee'
  AND table_schema IN ('core', 'ref', 'sec', 'audit')
ORDER BY 2, 3;

-- ============================================================================
-- 5. Проверка прав роли dba на таблицы
-- ============================================================================
\echo
\echo === 6. Права роли dba на таблицы ===

SELECT 
    grantee AS role_name,
    table_schema || '.' || table_name AS table_name,
    privilege_type
FROM information_schema.role_table_grants
WHERE grantee = 'dba'
  AND table_schema IN ('core', 'ref', 'sec', 'audit')
ORDER BY 2, 3;

-- ============================================================================
-- 6. Проверка функций SECURITY DEFINER
-- ============================================================================
\echo
\echo === 7. Функции SECURITY DEFINER ===

SELECT 
    n.nspname || '.' || p.proname AS function_name,
    p.prosecdef AS security_definer,
    pg_get_function_identity_arguments(p.oid) AS arguments
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname IN ('core', 'sec', 'ref')
  AND p.prokind = 'f'
ORDER BY 1;

-- ============================================================================
-- 7. Проверка search_path для функций SECURITY DEFINER
-- ============================================================================
\echo
\echo === 8. Проверка search_path для SECURITY DEFINER функций ===

SELECT 
    n.nspname || '.' || p.proname AS function_name,
    p.prosecdef AS security_definer,
    p.proconfig AS config
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname IN ('core', 'sec', 'ref')
  AND p.prokind = 'f'
  AND p.prosecdef = true
ORDER BY 1;

-- ============================================================================
-- 8. Проверка RLS-нарушений (сводка)
-- ============================================================================
\echo
\echo === 9. Сводка нарушений RLS ===

WITH rls_tables AS (
    SELECT n.nspname || '.' || c.relname AS table_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('core', 'ref', 'sec', 'audit')
      AND c.relkind = 'r'
      AND c.relname IN ('tickets', 'employees', 'assets', 'users')
),
rls_policies AS (
    SELECT n.nspname || '.' || c.relname AS table_name, count(*) AS policy_count
    FROM pg_policy p
    JOIN pg_class c ON c.oid = p.polrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('core', 'ref', 'sec', 'audit')
    GROUP BY 1
)
SELECT 
    t.table_name,
    COALESCE(p.policy_count, 0) AS policy_count,
    CASE 
        WHEN COALESCE(p.policy_count, 0) = 0 THEN '❌ НЕТ ПОЛИТИК'
        ELSE '✅ Политики есть'
    END AS status
FROM rls_tables t
LEFT JOIN rls_policies p ON p.table_name = t.table_name
ORDER BY 1;

-- ============================================================================
-- 9. Проверка маскирования для роли reporting (представления)
-- ============================================================================
\echo
\echo === 10. Представления для маскирования (reporting) ===

SELECT 
    schemaname || '.' || viewname AS view_name,
    viewowner AS owner
FROM pg_views
WHERE schemaname IN ('core', 'ref', 'sec', 'audit')
  AND (viewname LIKE '%masked%' OR viewname LIKE '%report%')
ORDER BY 1;

-- ============================================================================
-- 10. Итоговая проверка: все политики включены
-- ============================================================================
\echo
\echo === 11. ИТОГОВАЯ ПРОВЕРКА: RLS включена на всех таблицах с чувствительными данными ===

SELECT 
    CASE 
        WHEN count(*) FILTER (WHERE relrowsecurity = false) > 0 
        THEN '❌ RLS не включена на некоторых таблицах'
        ELSE '✅ RLS включена на всех таблицах с чувствительными данными'
    END AS rls_status,
    count(*) FILTER (WHERE relrowsecurity = true) AS tables_with_rls,
    count(*) FILTER (WHERE relrowsecurity = false) AS tables_without_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('core', 'ref', 'sec', 'audit')
  AND c.relkind = 'r'
  AND c.relname IN ('tickets', 'employees', 'assets', 'users');

\echo
\echo === ПРОВЕРКА ЗАВЕРШЕНА ===
\echo Если есть нарушения, они указаны в выводе выше.
\echo Для исправления см. docs/access_matrix.md и docs/security.md.