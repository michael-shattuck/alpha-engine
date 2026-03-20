import argparse
import logging
import uvicorn

from server.config import WEB_API_HOST, WEB_API_PORT, DEFAULT_MODE
from server.web_api import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def main():
    parser = argparse.ArgumentParser(description="Alpha Engine")
    parser.add_argument("--capital", type=float, default=100.0, help="Starting capital in USD")
    parser.add_argument("--mode", choices=["paper", "live"], default=DEFAULT_MODE)
    parser.add_argument("--host", default=WEB_API_HOST)
    parser.add_argument("--port", type=int, default=WEB_API_PORT)
    args = parser.parse_args()

    app.state.capital = args.capital
    app.state.mode = args.mode

    logging.info(f"Alpha Engine starting: mode={args.mode}, capital=${args.capital:.2f}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
