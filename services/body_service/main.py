from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.body_service.app import create_app  # noqa: E402
from services.body_service.config import BodyServiceConfig, default_config_path  # noqa: E402
from services.body_service.service import BodyService  # noqa: E402


def run_server(config_path: str | Path | None = None) -> None:
    import uvicorn

    config = BodyServiceConfig.load(config_path or default_config_path())
    service = BodyService(config)
    app = create_app(service)
    uvicorn.run(app, host=config.service.listen_host, port=config.service.listen_port, log_level="info")


def main() -> None:
    if "--serve" in sys.argv:
        config_path = None
        if "--config" in sys.argv:
            idx = sys.argv.index("--config")
            if idx + 1 < len(sys.argv):
                config_path = sys.argv[idx + 1]
        run_server(config_path)
        return
    print(f"body_service ready: {default_config_path()}")


app = None
try:
    _config = BodyServiceConfig.load()
    _service = BodyService(_config)
    app = create_app(_service)
except Exception:
    pass


if __name__ == "__main__":
    main()
