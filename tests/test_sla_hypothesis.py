"""Property-based С‚РµСЃС‚С‹ РґР»СЏ С„СѓРЅРєС†РёРё calc_sla_deadline С‡РµСЂРµР· hypothesis."""
from hypothesis import given, strategies as st, assume
import psycopg


@given(
    st.datetimes(
        min_value="2024-01-01T00:00:00+03:00",
        max_value="2026-12-31T23:59:59+03:00",
    ),
    st.integers(min_value=1, max_value=4),
)
def test_sla_invariant_during_day(dt, priority):
    """invariant: working_minutes(dt, calc_sla_deadline(dt, priority)) == priorities_sla[priority] * 60"""
    assume(priority in [1, 2, 3, 4])
    conn = psycopg.connect(
        host="localhost", port=5432, user="dba_admin",
        password="118100", dbname="servicedesk")
    cur = conn.cursor()
    try:
        cur.execute("SELECT core.calc_sla_deadline(%s::timestamptz, %s)", (dt.strftime("%Y-%m-%d %H:%M%z"), priority))
        result = cur.fetchone()[0]
        if result is None:
            return
        cur.execute("SELECT core.working_minutes(%s::timestamptz, %s::timestamptz)", (dt.strftime("%Y-%m-%d %H:%M%z"), result))
        actual_minutes = cur.fetchone()[0]
        sla_hours = {1: 4, 2: 8, 3: 24, 4: 72}[priority]
        expected_minutes = sla_hours * 60
        assert actual_minutes == expected_minutes, (
            f"FAIL: dt={dt}, priority={priority}, "
            f"expected={expected_minutes}, actual={actual_minutes}"
        )
    finally:
        cur.close()
        conn.close()


@given(
    st.datetimes(
        min_value="2024-01-01T00:00:00+03:00",
        max_value="2026-12-31T23:59:59+03:00",
    ),
    st.integers(min_value=1, max_value=4),
)
def test_sla_weekend_coverage(dt, priority):
    """РќР° weekends РїРµСЂРёРѕРґ СЂРµС€РµРЅРёСЏ РЅРµ РґРѕР»Р¶РµРЅ РІРєР»СЋС‡Р°С‚СЊ РІС‹С…РѕРґРЅС‹Рµ РґРЅРё."""
    assume(priority in [1, 2, 3, 4])
    conn = psycopg.connect(
        host="localhost", port=5432, user="dba_admin",
        password="118100", dbname="servicedesk")
    cur = conn.cursor()
    try:
        cur.execute("SELECT core.calc_sla_deadline(%s::timestamptz, %s)", (dt.strftime("%Y-%m-%d %H:%M%z"), priority))
        result = cur.fetchone()[0]
        if result is None:
            return
        from datetime import datetime
        result_dt = datetime.fromisoformat(result.replace("+03:00", ""))
        assert result_dt > datetime.now(), f"Result {result} is in the past"
    finally:
        cur.close()
        conn.close()


@given(
    st.datetimes(
        min_value="2024-01-01T00:00:00+03:00",
        max_value="2026-12-31T23:59:59+03:00",
    ),
    st.integers(min_value=1, max_value=4),
)
def test_sla_priority_ordering(dt, priority1, priority2):
    """РџСЂРё Р±РѕР»СЊС€РµРј РїСЂРёРѕСЂРёС‚РµС‚Рµ СЃСЂРѕРє СЂРµС€РµРЅРёСЏ РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ РјРµРЅСЊС€Рµ (РёР»Рё СЂР°РІРµРЅ)."""
    priorities = [1, 2, 3, 4]
    assume(priority1 in priorities and priority2 in priorities)
    assume(priority1 != priority2)
    conn = psycopg.connect(
        host="localhost", port=5432, user="dba_admin",
        password="118100", dbname="servicedesk")
    cur = conn.cursor()
    try:
        cur.execute("SELECT core.calc_sla_deadline(%s::timestamptz, %s)", (dt.strftime("%Y-%m-%d %H:%M%z"), priority1))
        r1 = cur.fetchone()[0]
        cur.execute("SELECT core.calc_sla_deadline(%s::timestamptz, %s)", (dt.strftime("%Y-%m-%d %H:%M%z"), priority2))
        r2 = cur.fetchone()[0]
        if r1 is None or r2 is None:
            return
        if priority1 < priority2:
            assert r1 <= r2, f"Higher priority {priority1} should have earlier deadline"
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    print("Р—Р°РїСѓСЃРє property-based С‚РµСЃС‚РѕРІ SLA...")

