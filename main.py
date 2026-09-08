"""Entry point for the bedis server."""

import logging

from app.server import Server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Server().serve_forever()


if __name__ == "__main__":
    main()
