import argparse
import logging

import uvicorn

from .settings import settings


def main():
    parser = argparse.ArgumentParser(description="SignalGraph: news-to-stock signal dashboard")
    parser.add_argument("--demo", action="store_true", help="offline demo with sample data")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args()
    if args.demo:
        settings.mode = "demo"
        settings.database_url = settings.database_url.replace("signalgraph.db", "demo.db")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("signalgraph.api.app:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
