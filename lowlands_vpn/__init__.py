from pathlib import Path

from flask import Flask

from lowlands_vpn.config import BASE_DIR, Config
from lowlands_vpn.extensions import csrf, db, login_manager


def create_app() -> Flask:
    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    app.config.from_object(Config)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    from lowlands_vpn.routes import main_bp

    app.register_blueprint(main_bp)

    with app.app_context():
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
        db.create_all()

    return app
