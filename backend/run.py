"""Development server entrypoint.

Run from the project root:

    python -m backend.run

The previous version used `from config import settings` and `"main:app"`, which
only resolve when the CWD happens to be backend/. Absolute package paths make
it work from anywhere.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402

from backend.config.config import settings  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        # The default access log is superseded by RequestContextMiddleware,
        # which records the user and department too.
        access_log=False,
        log_config=None,
    )
