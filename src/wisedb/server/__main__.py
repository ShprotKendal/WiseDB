"""Запуск API WiseDB через Uvicorn."""

from __future__ import annotations


def main() -> None:
    import uvicorn

    uvicorn.run(
        "wisedb.server.app:app",
        host="0.0.0.0",
        port=8000,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
