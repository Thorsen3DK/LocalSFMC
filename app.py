"""
LocalSFMC — Local SFMC Content Builder.

A Flask web app that renders HTML email templates with AMPScript,
using Excel files as Data Extensions.
"""

import os
import sys

# Add src/ to Python path so packages are importable without prefix
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from dotenv import load_dotenv
from flask import Flask

load_dotenv()


def create_app() -> Flask:
    base_dir = os.path.dirname(os.path.abspath(__file__))

    app = Flask(
        __name__,
        template_folder=os.path.join(base_dir, "src", "templates"),
        static_folder=os.path.join(base_dir, "static"),
    )

    app.config["EMAILS_DIR"] = os.path.join(base_dir, "content", "emails")
    app.config["DATA_EXTENSIONS_DIR"] = os.path.join(base_dir, "content", "data_extensions")
    app.config["OUTPUT_DIR"] = os.path.join(base_dir, "content", "output")
    app.config["JOURNEY_DATA_DIR"] = os.path.join(base_dir, "data", "journey")
    app.config["BLOCKNAME_DATA_DIR"] = os.path.join(base_dir, "data", "blockname")
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB upload limit
    app.secret_key = "localsfmc-dev-key"

    # Ensure directories exist
    for d in ("content/emails", "content/data_extensions", "content/output", "data/journey", "data/blockname"):
        os.makedirs(os.path.join(base_dir, d), exist_ok=True)

    from web.routes import bp
    app.register_blueprint(bp)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)
