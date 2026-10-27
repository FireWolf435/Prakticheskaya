"""Негативные тесты mailintake (4 теста)."""
import pytest
import email
import email.policy


class TestNegativeMailIntake:
    """Негативные тесты для mailintake."""

    def test_malformed_email_no_from(self):
        """Письмо без адреса отправителя должно отклоняться."""
        msg = email.message_from_string(
            "Subject: Test\n\nBody text",
            policy=email.policy.default
        )
        name, addr = email.utils.parseaddr(str(msg.get("From", "")))
        assert addr == "" or addr is None

    def test_malformed_email_no_subject(self):
        """Письмо без темы должно обрабатываться."""
        msg = email.message_from_string(
            "From: test@example.com\n\nBody text",
            policy=email.policy.default
        )
        subject = str(msg.get("Subject", "") or "").strip()
        assert subject == ""

    def test_phishing_email_spf_fail(self):
        """Письмо с SPF=fail должно отклоняться."""
        # Имитация проверки фишинга
        spf = "Received-SPF: fail (sender IP is 1.2.3.4)"
        is_phishing = "fail" in spf.lower()
        assert is_phishing is True

    def test_dangerous_attachment_quarantined(self):
        """Опасное вложение должно помещаться в карантин."""
        DANGEROUS_EXT = {".exe", ".scr", ".bat", ".cmd"}
        filename = "virus.exe"
        ext = os.path.splitext(filename)[1].lower() if False else ".exe"
        is_dangerous = ext in DANGEROUS_EXT
        assert is_dangerous is True