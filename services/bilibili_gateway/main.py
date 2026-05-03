from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

import uvicorn

from services.bilibili_gateway.app import app
from services.bilibili_gateway.config import load_service_config

def main() -> None:
    config_path = Path(__file__).resolve().parent / "config" / "service.toml"
    config = load_service_config(config_path)
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        uvicorn.run(
            "services.bilibili_gateway.main:app",
            host=config.service.listen_host,
            port=config.service.listen_port,
            reload=False,
            access_log=False,
            log_level=str(config.observability.log_level).lower(),
        )
        return
    print(f"bilibili_gateway service ready: {config_path}")
    print(
        "run with: uv run uvicorn services.bilibili_gateway.main:app "
        f"--host {config.service.listen_host} --port {config.service.listen_port}"
    )


if __name__ == "__main__":
    main()
