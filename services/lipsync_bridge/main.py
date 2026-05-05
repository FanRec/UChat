from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.lipsync_bridge.service import parse_args, run_server


def main() -> None:
    config_path = Path(__file__).resolve().parent / "config" / "service.toml"
    args = parse_args()
    if args.serve:
        run_server(args.config)
        return
    print(f"lipsync_bridge ready: {config_path}")


if __name__ == "__main__":
    main()
