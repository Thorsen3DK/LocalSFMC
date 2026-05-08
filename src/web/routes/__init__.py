"""
Web routes package — organizes Flask blueprints by tool.

The main blueprint is assembled here by importing all tool sub-packages.
"""

from flask import Blueprint

bp = Blueprint("main", __name__)

# Import tool packages to register their endpoints on the blueprint
from web.routes import dashboard   # noqa: E402, F401
from web.routes import html2pdf    # noqa: E402, F401
from web.routes import search      # noqa: E402, F401
from web.routes import journey     # noqa: E402, F401
from web.routes import blockname   # noqa: E402, F401
