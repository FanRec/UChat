from __future__ import annotations

from pathlib import Path


def main() -> None:
    config_path = Path(__file__).resolve().parent / "config" / "service.toml"
    print(f"body_service skeleton ready: {config_path}")


if __name__ == "__main__":
    main()
