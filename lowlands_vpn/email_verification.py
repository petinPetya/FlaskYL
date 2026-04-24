from __future__ import annotations

from flask import current_app


def is_email_verification_enabled() -> bool:
    return bool(current_app.config.get("EMAIL_VERIFICATION_ENABLED", True))


def is_email_verification_required() -> bool:
    return bool(
        is_email_verification_enabled()
        and current_app.config.get("EMAIL_VERIFICATION_REQUIRED", False)
    )
