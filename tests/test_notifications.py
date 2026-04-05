"""Tests for the notification/email infrastructure."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from aquifer.strata.notifications import EmailConfig, send_notification


class TestEmailConfig:
    def test_default_disabled(self):
        cfg = EmailConfig()
        assert cfg.enabled is False
        assert cfg.smtp_host == ""
        assert cfg.smtp_port == 587
        assert cfg.from_address == "noreply@aquifer.health"

    @patch.dict("os.environ", {
        "AQUIFER_SMTP_HOST": "smtp.example.com",
        "AQUIFER_SMTP_PORT": "465",
        "AQUIFER_SMTP_USER": "user@example.com",
        "AQUIFER_SMTP_PASSWORD": "secret",
        "AQUIFER_SMTP_FROM": "hello@aquifer.health",
    })
    def test_from_env_enabled(self):
        cfg = EmailConfig.from_env()
        assert cfg.enabled is True
        assert cfg.smtp_host == "smtp.example.com"
        assert cfg.smtp_port == 465
        assert cfg.smtp_user == "user@example.com"
        assert cfg.smtp_password == "secret"
        assert cfg.from_address == "hello@aquifer.health"

    @patch.dict("os.environ", {}, clear=True)
    def test_from_env_disabled_without_host(self):
        cfg = EmailConfig.from_env()
        assert cfg.enabled is False

    @patch.dict("os.environ", {
        "AQUIFER_SMTP_HOST": "smtp.example.com",
    }, clear=True)
    def test_from_env_disabled_without_user(self):
        cfg = EmailConfig.from_env()
        assert cfg.enabled is False


class TestSendNotification:
    def test_disabled_config_returns_false(self):
        cfg = EmailConfig(enabled=False)
        result = send_notification(cfg, "to@example.com", "Subject", "Body")
        assert result is False

    @patch("aquifer.strata.notifications.smtplib.SMTP")
    def test_sends_email_when_enabled(self, mock_smtp_class):
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        cfg = EmailConfig(
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_user="user@test.com",
            smtp_password="pass",
            from_address="from@test.com",
            enabled=True,
        )
        result = send_notification(cfg, "to@test.com", "Test Subject", "Test Body")
        assert result is True
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user@test.com", "pass")
        mock_server.send_message.assert_called_once()

    @patch("aquifer.strata.notifications.smtplib.SMTP")
    def test_returns_false_on_smtp_error(self, mock_smtp_class):
        mock_smtp_class.side_effect = ConnectionRefusedError("connection refused")

        cfg = EmailConfig(
            smtp_host="bad.host",
            smtp_port=587,
            smtp_user="user@test.com",
            smtp_password="pass",
            enabled=True,
        )
        result = send_notification(cfg, "to@test.com", "Subject", "Body")
        assert result is False
