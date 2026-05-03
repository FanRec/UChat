from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.tts_bridge.service import parse_args, run_server, run_streaming_diagnostic


def main() -> None:
    config_path = Path(__file__).resolve().parent / "config" / "service.toml"
    args = parse_args()
    if args.diagnose_streaming:
        result = run_streaming_diagnostic(args.config, text=args.text)
        print(result)
        return
    if args.serve:
        run_server(args.config)
        return
    print(f"tts_bridge ready: {config_path}")


if __name__ == "__main__":
    main()
