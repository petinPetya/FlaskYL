from flask import Flask, render_template


app = Flask(__name__)
app.config["SECRET_KEY"] = "PetinPetya + senamorsin"


@app.route("/")
def index():
    plans = [
        {
            "name": "Starter",
            "price": "199",
            "period": "30 дней",
            "description": "Быстрый старт для одного устройства",
            "features": [
                "1 устройство",
                "Сервер в Нидерландах",
                "Поддержка популярных клиентов",
                "Инструкции по подключению",
            ],
        },
        {
            "name": "Family",
            "price": "449",
            "period": "90 дней",
            "description": "Для нескольких устройств дома и в поездках",
            "features": [
                "До 3 устройств",
                "Стабильный канал без рекламы",
                "Приоритетная поддержка",
                "Автопродление по желанию",
            ],
        },
        {
            "name": "Pro",
            "price": "1490",
            "period": "365 дней",
            "description": "Максимальная выгода для постоянного использования",
            "features": [
                "До 5 устройств",
                "Годовая подписка со скидкой",
                "Помощь с переносом настроек",
                "Быстрый доступ к новым локациям",
            ],
        },
    ]

    return render_template("index.html", plans=plans)


@app.route("/login")
def login():
    return render_template("login.html")


@app.route("/register")
def register():
    return render_template("register.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


if __name__ == "__main__":
    app.run(debug=True, host='127.0.0.1', port=5000)
