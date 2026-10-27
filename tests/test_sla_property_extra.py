"""Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹Рµ property-based С‚РµСЃС‚С‹ SLA (3 С‚РµСЃС‚Р°)."""
from hypothesis import given, strategies as st, assume
import psycopg


@given(
    st.datetimes(
        min_value="2024-01-01T00:00:00+03:00",
        max_value="2026-12-31T23:59:59+03:00",
    ),
    st.integers(min_value=1, max_value=4),
)
def test_sla_deadline_monotonic(dt, priority):
    """Р‘РѕР»РµРµ РІС‹СЃРѕРєРёР№ РїСЂРёРѕСЂРёС‚РµС‚ (РјРµРЅСЊС€РёР№ РЅРѕРјРµСЂ) РґР°РµС‚ Р±РѕР»РµРµ СЂР°РЅРЅРёР№ deadline."""
    assume(priority in [1, 2, 3, 4])
    conn = psycopg.connect(
        host="localhost", port=5432, user="dba_admin",
        password="118100", dbname="servicedesk")
    cur = conn.cursor()
    try:
        cur.execute("SELECT core.calc_sla_deadline(%s::timestamptz, %s)", (dt.strftime("%Y-%m-%d %H:%M%z"), priority))
        r1 = cur.fetchone()[0]
        cur.execute("SELECT core.calc_sla_deadline(%s::timestamptz, %s)", (dt.strftime("%Y-%m-%d %H:%M%z"), 4))
        r4 = cur.fetchone()[0]
        if r1 is None or r4 is None:
            return
        # РџСЂРёРѕСЂРёС‚РµС‚ 1, 2, 3 РґРѕР»Р¶РЅС‹ РґР°РІР°С‚СЊ deadline <= РїСЂРёРѕСЂРёС‚РµС‚Сѓ 4
        assert r1 <= r4, f"Priority {priority} deadline {r1} > priority 4 deadline {r4}"
    finally:
        cur.close()
        conn.close()


@given(
    st.datetimes(
        min_value="2024-01-01T00:00:00+03:00",
        max_value="2026-12-31T23:59:59+03:00",
    ),
)
def test_sla_weekend_no_deadline_on_weekend(dt):
    """SLA deadline РЅРµ РґРѕР»Р¶РµРЅ РїР°РґР°С‚СЊ РЅР° РІС‹С…РѕРґРЅС‹Рµ РґР»СЏ РїСЂРёРѕСЂРёС‚РµС‚Р° 1-2."""
    from datetime import datetime
    assume(dt.weekday() == 4)  # РџСЏС‚РЅРёС†Р°
    assume(dt.hour >= 16)  # РџРѕСЃР»Рµ 16:00
    conn = psycopg.connect(
        host="localhost", port=5432, user="dba_admin",
        password="118100", dbname="servicedesk")
    cur = conn.cursor()
    try:
        cur.execute("SELECT core.calc_sla_deadline(%s::timestamptz, %s)", (dt.strftime("%Y-%m-%d %H:%M%z"), 1))
        result = cur.fetchone()[0]
        if result is None:
            return
        result_dt = datetime.fromisoformat(result.replace("+03:00", ""))
        # Deadline РЅРµ РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ РІ СЃСѓР±Р±РѕС‚Сѓ РёР»Рё РІРѕСЃРєСЂРµСЃРµРЅСЊРµ
        assert result_dt.weekday() not in (5, 6), f"Deadline falls on weekend: {result}"
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
def test_sla_deadline_positive(dt, priority):
    """SLA deadline РІСЃРµРіРґР° РІ Р±СѓРґСѓС‰РµРј РѕС‚РЅРѕСЃРёС‚РµР»СЊРЅРѕ created_at."""
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
        dt_obj = datetime.fromisoformat(dt.replace("+03:00", ""))
        result_dt = datetime.fromisoformat(result.replace("+03:00", ""))
        assert result_dt > dt_obj, f"Deadline {result} is not after created_at {dt}"
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    print("Р—Р°РїСѓСЃРє property-based С‚РµСЃС‚РѕРІ SLA...")

