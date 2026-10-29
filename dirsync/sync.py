# -*- coding: utf-8 -*-
"""
Пункт 4.2 ТЗ — МОДУЛЬ `dirsync`.

Синхронизация каталога LDAP -> база данных и login-роли PostgreSQL.
"""
import argparse
import os
import secrets
import string
import sys
import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "importer"))
import load_stage  # noqa: E402

import ldap3  # noqa: E402
from ldap3 import Server, Connection, ALL, SUBTREE  # noqa: E402
import psycopg  # noqa: E402
from psycopg import sql as psql  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def env():
    return load_stage.load_env(os.path.join(ROOT, ".env"))


def ldap_connect(e):
    server = Server(e.get("LDAP_URL", "ldap://127.0.0.1:389"), get_info=ALL)
    conn = Connection(
        server,
        user=e.get("LDAP_BIND_DN", "cn=admin,dc=corp,dc=local"),
        password=e["LDAP_ADMIN_PASSWORD"],
        auto_bind=True,
    )
    return conn


def pg_connect(e):
    dsn = load_stage.dsn_from_env(e)
    return psycopg.connect(dsn, autocommit=False)


def fetch_ldap_users(conn, base_dn):
    people_ou = "ou=people," + base_dn
    conn.search(people_ou, "(objectClass=inetOrgPerson)", SUBTREE,
                attributes=["uid", "employeeNumber", "mail", "cn", "userPassword",
                            "departmentNumber", "title"])
    out = []
    for entry in conn.entries:
        uid = str(entry.uid) if entry.uid else None
        en = str(entry.employeeNumber) if entry.employeeNumber else None
        has_pw = bool(entry.userPassword.value) if entry.userPassword else False
        out.append({
            "uid": uid,
            "employee_number": en,
            "mail": str(entry.mail) if entry.mail else None,
            "cn": str(entry.cn) if entry.cn else None,
            "department": str(entry.departmentNumber) if entry.departmentNumber else None,
            "title": str(entry.title) if entry.title else None,
            "has_password": has_pw,
        })
    return out


def fetch_hr(conn_pg):
    with conn_pg.cursor() as cur:
        cur.execute("""
            SELECT employee_id, tab_number, dismissal_date,
                   (dismissal_date IS NULL) AS is_active
            FROM core.employees
        """)
        out = {}
        for eid, tab, disp, active in cur.fetchall():
            out[str(tab)] = {"employee_id": eid, "dismissal_date": disp, "is_active": active}
        return out


def fetch_existing_users(conn_pg):
    with conn_pg.cursor() as cur:
        cur.execute("SELECT user_id, login_name, employee_id FROM sec.users")
        out = {}
        for uid, login, eid in cur.fetchall():
            out[login] = {"user_id": uid, "employee_id": eid}
        return out


def gen_password():
    alphabet = string.ascii_letters + string.digits + "!@#$%&*"
    return "".join(secrets.choice(alphabet) for _ in range(16))


def collision_free_login(desired, existing_logins):
    if desired not in existing_logins:
        return desired
    i = 2
    while f"{desired}{i}" in existing_logins:
        i += 1
    return f"{desired}{i}"


def ensure_pg_role(cur, login, password, valid_until, connection_limit, nologin=False):
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (login,))
    exists = cur.fetchone() is not None
    login_sql = "NOLOGIN" if nologin else "LOGIN"
    pwd_lit = psql.Literal(password)
    vu_lit = psql.Literal(valid_until)
    cl = int(connection_limit)
    role_id = psql.Identifier(login)
    if exists:
        cur.execute(psql.SQL('ALTER ROLE {} WITH {} PASSWORD {} '
                             'VALID UNTIL {} CONNECTION LIMIT {}').format(
            role_id, psql.SQL(login_sql), pwd_lit, vu_lit, psql.SQL(str(cl))))
    else:
        cur.execute(psql.SQL('CREATE ROLE {} WITH {} PASSWORD {} '
                             'VALID UNTIL {} CONNECTION LIMIT {}').format(
            role_id, psql.SQL(login_sql), pwd_lit, vu_lit, psql.SQL(str(cl))))
    cur.execute(psql.SQL('ALTER ROLE {} SET search_path = core, ref, sec, audit, public').format(role_id))


def terminate_sessions(cur, login):
    cur.execute("""
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE usename = %s AND pid <> pg_backend_pid()
    """, (login,))


def lock_pg_role(cur, login):
    cur.execute(psql.SQL('ALTER ROLE {} WITH NOLOGIN').format(psql.Identifier(login)))
    terminate_sessions(cur, login)


def upsert_sec_user(cur, employee_id, login_name, password_hash, valid_until,
                    connection_limit, is_locked, locked_reason):
    cur.execute("""
        INSERT INTO sec.users (employee_id, login_name, password_hash,
                               password_changed_at, valid_until, connection_limit,
                               is_locked, locked_at, locked_reason, created_at, updated_at)
        VALUES (%s, %s, %s, now(), %s, %s, %s,
                CASE WHEN %s THEN now() ELSE NULL END, %s, now(), now())
        ON CONFLICT (login_name) DO UPDATE SET
            employee_id = EXCLUDED.employee_id,
            password_hash = EXCLUDED.password_hash,
            valid_until = EXCLUDED.valid_until,
            connection_limit = EXCLUDED.connection_limit,
            is_locked = EXCLUDED.is_locked,
            locked_at = CASE WHEN EXCLUDED.is_locked THEN now() ELSE sec.users.locked_at END,
            locked_reason = EXCLUDED.locked_reason,
            updated_at = now()
    """, (employee_id, login_name, password_hash, valid_until, connection_limit,
          is_locked, is_locked, locked_reason))


def sync_users(e, dry_run=False, default_password=None, quiet=False):
    conn_ldap = ldap_connect(e)
    conn_pg = pg_connect(e)
    base_dn = e.get("LDAP_BASE_DN", "dc=corp,dc=local")
    hr = fetch_hr(conn_pg)
    existing = fetch_existing_users(conn_pg)

    ldap_users = fetch_ldap_users(conn_ldap, base_dn)
    ldap_users = [u for u in ldap_users if u["employee_number"]]

    generated = []
    existing_logins = set(existing.keys())
    stats = {"created": 0, "updated": 0, "locked": 0, "skipped": 0}

    valid_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=90)

    for u in ldap_users:
        tab = u["employee_number"]
        info = hr.get(tab)
        if not info:
            if not quiet:
                print(f"[skip] {u['uid']}: таб.№ {tab} не найден в core.employees")
            stats["skipped"] += 1
            continue

        employee_id = info["employee_id"]
        dismissed = not info["is_active"]
        desired_login = u["uid"]
        login = collision_free_login(desired_login, existing_logins)
        existing_logins.add(login)

        password = default_password or gen_password()
        locked_reason = "dismissed" if dismissed else None

        if dry_run:
            if not quiet:
                print(f"[dry-run] {login}: employee_id={employee_id}, dismissed={dismissed}, "
                      f"collision={'yes' if login != desired_login else 'no'}")
            if dismissed:
                stats["locked"] += 1
            else:
                stats["created"] += 1
            continue

        with conn_pg.cursor() as cur:
            ensure_pg_role(cur, login, password, valid_until.isoformat(), 5, nologin=dismissed)
            if dismissed:
                lock_pg_role(cur, login)
                terminate_sessions(cur, login)
            upsert_sec_user(cur, employee_id, login, f"ldap-ssha:{u['uid']}",
                            valid_until, 5, dismissed, locked_reason)
            conn_pg.commit()

        if not dismissed:
            generated.append((login, password))
            stats["created"] += 1
        else:
            stats["locked"] += 1
            if not quiet:
                print(f"[locked] {login}: уволенный сотрудник (таб.№ {tab})")

    conn_ldap.unbind()
    conn_pg.close()

    if not quiet:
        print("\n=== ИТОГ СИНХРОНИЗАЦИИ ===")
        print(f"  создано/обновлено: {stats['created']}")
        print(f"  заблокировано:     {stats['locked']}")
        print(f"  пропущено:         {stats['skipped']}")
        if generated:
            print("\n=== СГЕНЕРИРОВАННЫЕ ПАРОЛИ (показываются один раз!) ===")
            for login, pw in generated:
                print(f"  {login}: {pw}")
            print("\nСохраните пароли в защищённом хранилище. В БД хранится только SCRAM-хеш.")

    return stats


def lock_dismissed_only(e, quiet=False):
    conn_pg = pg_connect(e)
    with conn_pg.cursor() as cur:
        cur.execute("""
            SELECT u.login_name
            FROM sec.users u
            JOIN core.employees emp ON emp.employee_id = u.employee_id
            WHERE emp.dismissal_date IS NOT NULL AND u.is_locked = false
        """)
        to_lock = [r[0] for r in cur.fetchall()]
    conn_pg.close()

    if not to_lock:
        if not quiet:
            print("Уволенных для блокировки не найдено.")
        return []

    conn_pg = pg_connect(e)
    with conn_pg.cursor() as cur:
        for login in to_lock:
            lock_pg_role(cur, login)
            terminate_sessions(cur, login)
            cur.execute("""
                UPDATE sec.users SET is_locked = true, locked_at = now(),
                       locked_reason = 'dismissed', updated_at = now()
                WHERE login_name = %s
            """, (login,))
            if not quiet:
                print(f"[locked] {login}")
        conn_pg.commit()
    conn_pg.close()
    return to_lock


def sync_service_accounts(e, dry_run=False, quiet=False):
    base_dn = e.get("LDAP_BASE_DN", "dc=corp,dc=local")
    conn_ldap = ldap_connect(e)
    conn_ldap.search("ou=service," + base_dn, "(objectClass=inetOrgPerson)", SUBTREE,
                     attributes=["uid"])
    svc_uids = [str(entry.uid) for entry in conn_ldap.entries]
    conn_ldap.unbind()

    env_map = {
        "svc-notifier": "SVC_NOTIFIER_PASSWORD",
        "svc-mailintake": "SVC_MAILINTAKE_PASSWORD",
        "svc-dirsync": "SVC_DIRSYNC_PASSWORD",
    }

    conn_pg = pg_connect(e)
    valid_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
    for uid in svc_uids:
        pw = e.get(env_map.get(uid, ""), None)
        if not pw:
            if not quiet:
                print(f"[warn] {uid}: пароль не задан в .env ({env_map.get(uid)}) — роль не создана")
            continue
        if dry_run:
            if not quiet:
                print(f"[dry-run] service role: {uid}")
            continue
        with conn_pg.cursor() as cur:
            ensure_pg_role(cur, uid, pw, valid_until.isoformat(), 10, nologin=False)
            cur.execute("""
                INSERT INTO sec.users (login_name, password_hash, valid_until,
                                       connection_limit, is_locked, created_at, updated_at)
                VALUES (%s, %s, %s, 10, false, now(), now())
                ON CONFLICT (login_name) DO UPDATE SET
                    valid_until = EXCLUDED.valid_until, updated_at = now()
            """, (uid, f"svc:{uid}", valid_until))
            conn_pg.commit()
        if not quiet:
            print(f"[service] {uid}: роль создана/обновлена")
    conn_pg.close()


def main():
    parser = argparse.ArgumentParser(description="dirsync — синхронизация LDAP -> PostgreSQL")
    parser.add_argument("--dry-run", action="store_true", help="показать, что будет сделано, без изменений")
    parser.add_argument("--lock-dismissed", action="store_true", help="только заблокировать уволенных")
    parser.add_argument("--service-only", action="store_true", help="только служебные учётки")
    parser.add_argument("--quiet", action="store_true", help="тихий режим (для cron)")
    parser.add_argument("--default-password", help="пароль по умолчанию для тестового стенда")
    args = parser.parse_args()

    e = env()

    if args.lock_dismissed:
        lock_dismissed_only(e, quiet=args.quiet)
        return
    if args.service_only:
        sync_service_accounts(e, dry_run=args.dry_run, quiet=args.quiet)
        return

    sync_users(e, dry_run=args.dry_run, default_password=args.default_password, quiet=args.quiet)
    sync_service_accounts(e, dry_run=args.dry_run, quiet=args.quiet)


if __name__ == "__main__":
    main()
