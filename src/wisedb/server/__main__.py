"""CLI entrypoint for wisedb-server."""

from __future__ import annotations

import uvicorn

from wisedb.server.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "wisedb.server.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
