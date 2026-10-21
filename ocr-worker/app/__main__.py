from __future__ import annotations

import uvicorn

from .config import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.service_port,
        workers=1,
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
