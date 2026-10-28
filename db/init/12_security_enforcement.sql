-- Реальная защита строк и персональных данных (ПМ.11, п. 4.3).
-- Ключ pgcrypto передаётся приложением через SET LOCAL app.pgcrypto_key.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'employee') THEN CREATE ROLE employee NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engineer') THEN CREATE ROLE engineer NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dispatcher') THEN CREATE ROLE dispatcher NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dept_head') THEN CREATE ROLE dept_head NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'auditor') THEN CREATE ROLE auditor NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reporting') THEN CREATE ROLE reporting NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_service') THEN CREATE ROLE app_service NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'migrator') THEN CREATE ROLE migrator NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dba') THEN CREATE ROLE dba NOLOGIN; END IF;
END $$;

ALTER TABLE core.tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.tickets FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tickets_employee_select ON core.tickets;
CREATE POLICY tickets_employee_select ON core.tickets
    FOR SELECT TO employee
    USING (author_id = NULLIF(current_setting('app.employee_id', true), '')::integer);

DROP POLICY IF EXISTS tickets_employee_write ON core.tickets;
CREATE POLICY tickets_employee_write ON core.tickets
    FOR INSERT TO employee
    WITH CHECK (author_id = NULLIF(current_setting('app.employee_id', true), '')::integer);

DROP POLICY IF EXISTS tickets_engineer_select ON core.tickets;
CREATE POLICY tickets_engineer_select ON core.tickets
    FOR SELECT TO engineer
    USING (executor_id = NULLIF(current_setting('app.employee_id', true), '')::integer);

DROP POLICY IF EXISTS tickets_dispatcher_all ON core.tickets;
CREATE POLICY tickets_dispatcher_all ON core.tickets
    FOR ALL TO dispatcher USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS tickets_dept_head_select ON core.tickets;
CREATE POLICY tickets_dept_head_select ON core.tickets
    FOR SELECT TO dept_head
    USING (department_id = NULLIF(current_setting('app.department_id', true), '')::smallint);

DROP POLICY IF EXISTS tickets_reporting_select ON core.tickets;
CREATE POLICY tickets_reporting_select ON core.tickets
    FOR SELECT TO reporting USING (true);

DROP POLICY IF EXISTS tickets_app_service_all ON core.tickets;
CREATE POLICY tickets_app_service_all ON core.tickets
    FOR ALL TO app_service USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS tickets_dba_all ON core.tickets;
CREATE POLICY tickets_dba_all ON core.tickets
    FOR ALL TO dba USING (true) WITH CHECK (true);

-- Шифрованные копии персональных данных. Ключ не хранится в БД.
ALTER TABLE core.employees ADD COLUMN IF NOT EXISTS snils_encrypted bytea;
ALTER TABLE core.employees ADD COLUMN IF NOT EXISTS birth_date_encrypted bytea;

CREATE OR REPLACE FUNCTION sec.encrypt_employee_pii(
    p_employee_id integer,
    p_snils text,
    p_birth_date date
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = sec, core, public, pg_catalog
AS $$
DECLARE
    v_key text := current_setting('app.pgcrypto_key', true);
BEGIN
    IF v_key IS NULL OR v_key = '' THEN
        RAISE EXCEPTION 'app.pgcrypto_key is not set';
    END IF;
    UPDATE core.employees
       SET snils_encrypted = CASE WHEN p_snils IS NULL THEN NULL ELSE public.pgp_sym_encrypt(p_snils, v_key) END,
           birth_date_encrypted = CASE WHEN p_birth_date IS NULL THEN NULL ELSE public.pgp_sym_encrypt(p_birth_date::text, v_key) END
     WHERE employee_id = p_employee_id;
END $$;

CREATE OR REPLACE FUNCTION sec.decrypt_employee_snils(p_cipher bytea)
RETURNS text LANGUAGE sql SECURITY DEFINER
SET search_path = sec, public, pg_catalog
AS $$ SELECT public.pgp_sym_decrypt(p_cipher, current_setting('app.pgcrypto_key')) $$;

CREATE OR REPLACE VIEW sec.employee_pii_masked AS
SELECT employee_id, tab_number, first_name, last_name, department_id,
       CASE WHEN current_user IN ('dba', 'auditor') THEN snils_encrypted ELSE NULL END AS snils_encrypted,
       CASE WHEN current_user IN ('dba', 'auditor') THEN birth_date_encrypted ELSE NULL END AS birth_date_encrypted
FROM core.employees;

REVOKE ALL ON core.employees FROM PUBLIC;
REVOKE ALL ON core.tickets FROM PUBLIC;
GRANT SELECT ON sec.employee_pii_masked TO reporting, auditor, dba;
GRANT SELECT ON core.tickets TO employee, engineer, dispatcher, dept_head, reporting, app_service, dba;
GRANT INSERT ON core.tickets TO employee, dispatcher, app_service, dba;

COMMENT ON FUNCTION sec.encrypt_employee_pii(integer, text, date) IS
'Шифрует СНИЛС и дату рождения ключом app.pgcrypto_key, который передаётся приложением.';
