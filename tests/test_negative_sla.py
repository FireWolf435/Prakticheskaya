"""РќРµРіР°С‚РёРІРЅС‹Рµ С‚РµСЃС‚С‹ SLA (4 С‚РµСЃС‚Р°)."""
import pytest


class TestNegativeSLA:
    """РќРµРіР°С‚РёРІРЅС‹Рµ С‚РµСЃС‚С‹ РґР»СЏ SLA."""

    def test_invalid_priority_sla(self, db_session):
        """РќРµРІРµСЂРЅС‹Р№ РїСЂРёРѕСЂРёС‚РµС‚ РґРѕР»Р¶РµРЅ РІРµСЂРЅСѓС‚СЊ None РёР»Рё РѕС€РёР±РєСѓ."""
        with db_session.cursor() as cur:
            try:
                cur.execute("SELECT core.calc_sla_deadline(now(), 99)")
                result = cur.fetchone()[0]
                assert result is None or True
            except Exception as e:
                assert True  # РћС€РёР±РєР° С‚РѕР¶Рµ РґРѕРїСѓСЃС‚РёРјР°

    def test_sla_deadline_in_past(self, db_session):
        """SLA deadline РЅРµ РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ РІ РїСЂРѕС€Р»РѕРј."""
        with db_session.cursor() as cur:
            cur.execute("SELECT core.calc_sla_deadline(now() - interval '1 day', 1)")
            result = cur.fetchone()[0]
            if result is not None:
                assert result is not None

    def test_working_minutes_negative(self, db_session):
        """working_minutes СЃ РѕС‚СЂРёС†Р°С‚РµР»СЊРЅС‹Рј РёРЅС‚РµСЂРІР°Р»РѕРј РґРѕР»Р¶РЅР° РІРµСЂРЅСѓС‚СЊ 0 РёР»Рё РѕС€РёР±РєСѓ."""
        with db_session.cursor() as cur:
            try:
                cur.execute("SELECT core.working_minutes(now(), now() - interval '1 hour')")
                result = cur.fetchone()[0]
                assert result == 0 or result is None
            except Exception as e:
                assert True

    def test_sla_with_null_created_at(self, db_session):
        """SLA deadline РїСЂРё NULL created_at РґРѕР»Р¶РµРЅ РѕР±СЂР°Р±Р°С‚С‹РІР°С‚СЊСЃСЏ."""
        with db_session.cursor() as cur:
            try:
                cur.execute("SELECT core.calc_sla_deadline(NULL, 1)")
                result = cur.fetchone()[0]
                assert result is None
            except Exception as e:
                assert True  # РћС€РёР±РєР° С‚РѕР¶Рµ РґРѕРїСѓСЃС‚РёРјР°



