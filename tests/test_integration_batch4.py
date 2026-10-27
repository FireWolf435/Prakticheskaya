"""Интеграционные тесты (батч 4: 2 теста)."""
import pytest


class TestIntegrationBatch4:
    """Батч 4 интеграционных тестов."""

    def test_department_hierarchy_traversal(self, db_session):
        """Обход иерархии подразделений."""
        with db_session.cursor() as cur:
            cur.execute(
                "WITH RECURSIVE dept_tree AS ("
                "  SELECT department_id, parent_id, name, 1 AS depth FROM ref.departments WHERE parent_id IS NULL"
                "  UNION ALL"
                "  SELECT d.department_id, d.parent_id, d.name, dt.depth + 1 "
                "  FROM ref.departments d JOIN dept_tree dt ON dt.department_id = d.parent_id"
                ") SELECT count(*) FROM dept_tree")
            count = cur.fetchone()[0]
            assert count >= 1

    def test_asset_movement_history(self, db_session):
        """История перемещений оборудования."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.assets (inv_no, name, category_id, location_id, is_active) "
                "VALUES ('TEST-ASSET-4', 'Тестовое оборудование', 1, 1, true) "
                "ON CONFLICT (inv_no) DO NOTHING RETURNING asset_id")
            result = cur.fetchone()
            if result:
                asset_id = result[0]
                cur.execute(
                    "INSERT INTO core.asset_movements (asset_id, from_location, to_location, moved_at, movement_type) "
                    "VALUES (%s, 1, 2, now(), 'перемещение') RETURNING movement_id",
                    (asset_id,))
                movement_id = cur.fetchone()[0]
                assert movement_id is not None