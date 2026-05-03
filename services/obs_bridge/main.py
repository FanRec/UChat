from __future__ import annotations

import sys
from pathlib import Path

# 注入项目根目录到 sys.path，确保绝对导入可用
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.obs_bridge.config import ObsBridgeConfig  # noqa: E402
from services.obs_bridge.service import ObsBridgeService  # noqa: E402
from services.obs_bridge.app import create_app  # noqa: E402


def default_config_path() -> Path:
    return Path(__file__).resolve().parent / "config" / "service.toml"


def run_server(config_path: str | Path | None = None) -> None:
    import uvicorn

    config = ObsBridgeConfig.load(config_path or default_config_path())
    service = ObsBridgeService(config)
    app = create_app(service)
    uvicorn.run(
        app,
        host=config.service.listen_host,
        port=config.service.listen_port,
        log_level="info",
    )


def main() -> None:
    if "--serve" in sys.argv:
        config_path = None
        if "--config" in sys.argv:
            idx = sys.argv.index("--config")
            if idx + 1 < len(sys.argv):
                config_path = sys.argv[idx + 1]
        run_server(config_path)
        return
    config_path = default_config_path()
    print(f"obs_bridge ready: {config_path}")


app = None
try:
    _config = ObsBridgeConfig.load()
    _service = ObsBridgeService(_config)
    app = create_app(_service)
except Exception:
    pass

if __name__ == "__main__":
    main()
