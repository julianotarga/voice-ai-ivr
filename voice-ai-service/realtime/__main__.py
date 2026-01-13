"""
Realtime package entrypoint.

Why this exists:
- Running `python -m realtime.server` can emit a noisy `RuntimeWarning` from runpy in some environments.
- Running `python -m realtime` avoids that, while keeping the package layout intact.
"""

import asyncio
import logging
import os

from .server import run_server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    host = os.getenv("REALTIME_HOST", "0.0.0.0")
    port = int(os.getenv("REALTIME_PORT", "8085"))

    asyncio.run(run_server(host=host, port=port))


if __name__ == "__main__":
    main()

