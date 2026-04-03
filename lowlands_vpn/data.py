PLANS = [
    {
        "name": "Starter",
        "price": "199",
        "price_cents": 19900,
        "period": "30 дней",
        "days_valid": 30,
        "traffic_limit_bytes": 214748364800,
        "description": "Быстрый старт для одного устройства",
        "features": [
            "1 устройство",
            "Сервер в Нидерландах",
            "Поддержка популярных клиентов",
            "Инструкции по подключению",
        ],
        "is_popular": False,
    },
    {
        "name": "Family",
        "price": "449",
        "price_cents": 44900,
        "period": "90 дней",
        "days_valid": 90,
        "traffic_limit_bytes": 644245094400,
        "description": "Для нескольких устройств дома и в поездках",
        "features": [
            "До 3 устройств",
            "Стабильный канал без рекламы",
            "Приоритетная поддержка",
            "Автопродление по желанию",
        ],
        "is_popular": True,
    },
    {
        "name": "Pro",
        "price": "1490",
        "price_cents": 149000,
        "period": "365 дней",
        "days_valid": 365,
        "traffic_limit_bytes": None,
        "description": "Максимальная выгода для постоянного использования",
        "features": [
            "До 5 устройств",
            "Годовая подписка со скидкой",
            "Помощь с переносом настроек",
            "Быстрый доступ к новым локациям",
        ],
        "is_popular": False,
    },
]

PLAN_CHOICES = [(plan["name"], f"{plan['name']} - {plan['period']}") for plan in PLANS]
