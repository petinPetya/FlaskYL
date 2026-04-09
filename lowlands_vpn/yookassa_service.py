from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class YooKassaPaymentResult:
    payment_id: str
    confirmation_url: str


def create_yookassa_payment(
    *,
    shop_id: str,
    secret_key: str,
    amount_rub: str,
    description: str,
    return_url: str,
    metadata: dict,
) -> YooKassaPaymentResult:
    from yookassa import Configuration, Payment

    Configuration.account_id = shop_id
    Configuration.secret_key = secret_key

    payload = {
        "amount": {"value": amount_rub, "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": description,
        "metadata": metadata,
    }

    payment = Payment.create(payload, str(uuid.uuid4()))
    confirmation_url = (
        payment.confirmation.get("confirmation_url") if payment.confirmation else None
    )
    if not confirmation_url:
        raise ValueError("YooKassa did not return confirmation_url.")

    return YooKassaPaymentResult(payment_id=payment.id, confirmation_url=confirmation_url)

