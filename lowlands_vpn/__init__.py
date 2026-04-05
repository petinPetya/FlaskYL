import logging

from flask import Flask, render_template
from flask_wtf.csrf import CSRFError

from lowlands_vpn.config import BASE_DIR, Config
from lowlands_vpn.database import init_database
from lowlands_vpn.extensions import csrf, db, login_manager


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    configure_logging(app)

    from lowlands_vpn.routes import main_bp

    app.register_blueprint(main_bp)
    register_error_handlers(app)

    with app.app_context():
        init_database(app.instance_path)

    return app


def configure_logging(app: Flask) -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=getattr(logging, app.config["LOG_LEVEL"], logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    if app.config["SECRET_KEY"] == "change-me-in-production":
        app.logger.warning(
            "Using the default SECRET_KEY. Set SECRET_KEY in the environment."
        )


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        return (
            render_template(
                "csrf_error.html",
                error_message=error.description or "Недействительный CSRF-токен.",
            ),
            400,
        )
