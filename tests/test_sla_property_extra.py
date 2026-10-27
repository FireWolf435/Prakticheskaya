"""Дополнительные property-based тесты SLA (3 теста)."""
from hypothesis import given, strategies as st, assume
from core.calc_sla_deadline import calc_sla_deadline
from core.working_minutes import working_minutes
from core.priorities_sla import priorities_sla


@given(
    st.datetimes(
        min_value="2024-01-01T00:00:00+03:00",
        max_value="2026-12-31T23:59:59+03:00",
    ),
    st.integers(min_value=1, max_value=4),
)
def test_sla_deadline_monotonic(dt, priority):
    """Более высокий приоритет (меньший номер) дает более ранний deadline."""
    assume(priority in priorities_sla)
    # Сравниваем с приоритетом 4 (самый низкий)
    r1 = calc_sla_deadline(dt.strftime("%Y-%m-%d %H:%M%z"), priority)
    r4 = calc_sla_deadline(dt.strftime("%Y-%m-%d %H:%M%z"), 4)
    if r1 is None or r4 is None:
        return
    # Приоритет 1, 2, 3 должны давать deadline <= приоритету 4
    assert r1 <= r4, f"Priority {priority} deadline {r1} > priority 4 deadline {r4}"


@given(
    st.datetimes(
        min_value="2024-01-01T00:00:00+03:00",
        max_value="2026-12-31T23:59:59+03:00",
    ),
)
def test_sla_weekend_no_deadline_on_weekend(dt):
    """SLA deadline не должен падать на выходные для приоритета 1-2."""
    # Проверяем, что если created_at в пятницу вечером, deadline сдвигается на понедельник
    from datetime import datetime
    assume(dt.weekday() == 4)  # Пятница
    assume(dt.hour >= 16)  # После 16:00
    result = calc_sla_deadline(dt.strftime("%Y-%m-%d %H:%M%z"), 1)
    if result is None:
        return
    result_dt = datetime.fromisoformat(result.replace("+03:00", ""))
    # Deadline не должен быть в субботу или воскресенье
    assert result_dt.weekday() not in (5, 6), f"Deadline falls on weekend: {result}"


@given(
    st.datetimes(
        min_value="2024-01-01T00:00:00+03:00",
        max_value="2026-12-31T23:59:59+03:00",
    ),
    st.integers(min_value=1, max_value=4),
)
def test_sla_deadline_positive(dt, priority):
    """SLA deadline всегда в будущем относительно created_at."""
    assume(priority in priorities_sla)
    result = calc_sla_deadline(dt.strftime("%Y-%m-%d %H:%M%z"), priority)
    if result is None:
        return
    from datetime import datetime
    dt_obj = datetime.fromisoformat(dt.replace("+03:00", ""))
    result_dt = datetime.fromisoformat(result.replace("+03:00", ""))
    assert result_dt > dt_obj, f"Deadline {result} is not after created_at {dt}"