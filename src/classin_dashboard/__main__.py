"""Run the dashboard: `classin-dash` or `python -m classin_dashboard`."""

from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "classin_dashboard.web.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        root_path=settings.root_path,
    )


if __name__ == "__main__":
    main()
