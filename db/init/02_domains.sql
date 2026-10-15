-- ============================================================================
-- Пользовательские домены (custom domains)
-- email   — на основе citext (регистронезависимый email)
-- snils   — с проверкой контрольной суммы СНИЛС
-- inv_no  — инвентарный номер оборудования
-- ============================================================================

-- Расширение citext для регистронезависимого сравнения email
CREATE EXTENSION IF NOT EXISTS citext;

-- ============================================================================
-- Домен email: citext с базовой проверкой формата
-- ============================================================================
CREATE DOMAIN ref.email AS citext
CHECK (VALUE ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$');

COMMENT ON DOMAIN ref.email IS 'Электронная почта (регистронезависимая, citext)';

-- ============================================================================
-- Функция проверки контрольной суммы СНИЛС
-- Алгоритм:
--   1. Берём 9 первых цифр СНИЛС (без разделителей).
--   2. Умножаем их на веса 9,8,7,6,5,4,3,2,1 и суммируем (S).
--   3. Если S < 100 — контрольное число = S.
--      Если S = 100 — контрольное число = 0.
--      Если S > 100 — берём S % 101; если результат = 100 — контрольное число 0.
--   4. Сравниваем с последними двумя цифрами.
-- ============================================================================
CREATE FUNCTION ref.snils_valid(snils text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    SELECT
        CASE
            WHEN s < 100 THEN s
            WHEN s % 101 = 100 THEN 0
            ELSE s % 101
        END = substring(regexp_replace(snils, '\D', '', 'g') from 10 for 2)::int
    FROM (
        SELECT
            substring(regexp_replace(snils, '\D', '', 'g') from 1 for 1)::int * 9 +
            substring(regexp_replace(snils, '\D', '', 'g') from 2 for 1)::int * 8 +
            substring(regexp_replace(snils, '\D', '', 'g') from 3 for 1)::int * 7 +
            substring(regexp_replace(snils, '\D', '', 'g') from 4 for 1)::int * 6 +
            substring(regexp_replace(snils, '\D', '', 'g') from 5 for 1)::int * 5 +
            substring(regexp_replace(snils, '\D', '', 'g') from 6 for 1)::int * 4 +
            substring(regexp_replace(snils, '\D', '', 'g') from 7 for 1)::int * 3 +
            substring(regexp_replace(snils, '\D', '', 'g') from 8 for 1)::int * 2 +
            substring(regexp_replace(snils, '\D', '', 'g') from 9 for 1)::int * 1 AS s
    ) t;
$$;

COMMENT ON FUNCTION ref.snils_valid(text) IS 'Проверка контрольной суммы СНИЛС';

-- ============================================================================
-- Домен snils: формат XXX-XXX-XXX XX + проверка контрольной суммы
-- ============================================================================
CREATE DOMAIN ref.snils AS text
CHECK (
    VALUE ~ '^\d{3}-\d{3}-\d{3} \d{2}$'
    AND ref.snils_valid(VALUE)
);

COMMENT ON DOMAIN ref.snils IS 'СНИЛС с проверкой контрольной суммы (формат XXX-XXX-XXX XX)';

-- ============================================================================
-- Домен inv_no: инвентарный номер (буквы, цифры, дефисы, не пустой)
-- ============================================================================
CREATE DOMAIN ref.inv_no AS text
CHECK (
    VALUE ~ '^[A-Za-z0-9][A-Za-z0-9\-]{0,49}$'
    AND length(VALUE) <= 50
);

COMMENT ON DOMAIN ref.inv_no IS 'Инвентарный номер оборудования (до 50 символов)';

-- ============================================================================
-- Дополнительные полезные домены
-- ============================================================================

-- Телефон (разрешаем цифры, плюс, скобки, дефисы, пробелы)
CREATE DOMAIN ref.phone AS text
CHECK (
    VALUE IS NULL OR VALUE ~ '^[\+]?[0-9\s\-\(\)]{7,25}$'
);

COMMENT ON DOMAIN ref.phone IS 'Телефонный номер';

-- Дата рождения (не в будущем, не раньше 1900)
CREATE DOMAIN ref.birth_date AS date
CHECK (
    VALUE >= DATE '1900-01-01'
    AND VALUE <= CURRENT_DATE
);

COMMENT ON DOMAIN ref.birth_date IS 'Дата рождения (не в будущем)';

-- Статус заявки (из допустимого множества)
CREATE DOMAIN ref.ticket_status AS text
CHECK (
    VALUE IN ('Новая', 'Назначена', 'В работе', 'Ожидание пользователя', 'Решена', 'Закрыта', 'Переоткрыта')
);

COMMENT ON DOMAIN ref.ticket_status IS 'Статус заявки (строго из допустимого набора)';

-- Приоритет заявки (1..5)
CREATE DOMAIN ref.ticket_priority AS int
CHECK (VALUE >= 1 AND VALUE <= 5);

COMMENT ON DOMAIN ref.ticket_priority IS 'Приоритет заявки (1 — высший, 5 — низкий)';
