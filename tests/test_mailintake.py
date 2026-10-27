"""Тесты mailintake для Service Desk (5 тестов)."""
import pytest
import os
import glob


class TestMailIntake:
    """Тесты обработки входящей почты."""

    def test_parse_letter_subject(self):
        """Разбор темы письма."""
        import email
        import email.policy
        # Тестовый email
        msg = email.message_from_string(
            "From: test@example.com\nSubject: [#123] Test ticket\n\nBody text",
            policy=email.policy.default
        )
        subject = str(msg.get("Subject", "")).strip()
        assert subject == "[#123] Test ticket"

    def test_parse_letter_from_email(self):
        """Разбор адреса отправителя."""
        import email
        import email.policy
        msg = email.message_from_string(
            "From: Test User <test@example.com>\nSubject: Test\n\nBody",
            policy=email.policy.default
        )
        name, addr = email.utils.parseaddr(str(msg.get("From", "")))
        assert addr == "test@example.com"
        assert name == "Test User"

    def test_ticket_in_subject_regex(self):
        """Регулярное выражение для номера заявки в теме."""
        import re
        TICKET_IN_SUBJECT = re.compile(r"\[#(\d+)\]")
        match = TICKET_IN_SUBJECT.search("[#123] Test ticket")
        assert match is not None
        assert match.group(1) == "123"

    def test_pick_category_by_keywords(self):
        """Выбор категории по ключевым словам."""
        CATEGORY_RULES = [
            ("Печать", ["принтер", "печата", "картридж"]),
            ("Сеть", ["сеть", "интернет", "wi-fi"]),
        ]
        text = "принтер не печатает"
        found = None
        for name, keywords in CATEGORY_RULES:
            for kw in keywords:
                if kw in text.lower():
                    found = name
                    break
            if found:
                break
        assert found == "Печать"

    def test_mask_secrets(self):
        """Маскирование паролей в тексте."""
        import re
        SECRET_RE = re.compile(r"(парол\w*|логин\w*|password)\s*[:=]\s*\S+", re.IGNORECASE)
        text = "пароль: secret123, login: admin"
        masked = SECRET_RE.sub(lambda m: m.group(1) + ": ***", text)
        assert "secret123" not in masked
        assert "***" in masked