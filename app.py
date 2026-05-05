"""
LocalSFMC — Local SFMC Content Builder.

A Flask web app that renders HTML email templates with AMPScript,
using Excel files as Data Extensions.
"""

import os
from dotenv import load_dotenv
from flask import Flask

load_dotenv()


def create_app() -> Flask:
    base_dir = os.path.dirname(os.path.abspath(__file__))

    app = Flask(
        __name__,
        template_folder=os.path.join(base_dir, "templates"),
        static_folder=os.path.join(base_dir, "static"),
    )

    app.config["EMAILS_DIR"] = os.path.join(base_dir, "emails")
    app.config["DATA_EXTENSIONS_DIR"] = os.path.join(base_dir, "data_extensions")
    app.config["OUTPUT_DIR"] = os.path.join(base_dir, "output")
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB upload limit
    app.secret_key = "localsfmc-dev-key"

    # Ensure directories exist
    for d in ("emails", "data_extensions", "output"):
        os.makedirs(os.path.join(base_dir, d), exist_ok=True)

    from web.routes import bp
    app.register_blueprint(bp)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)
