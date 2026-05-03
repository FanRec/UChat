from __future__ import annotations

from pathlib import Path
from string import Formatter
from typing import Any


class PromptError(RuntimeError):
    pass


class PromptManager:
    def __init__(self, root_dir: str | Path = "prompts", locale: str = "zh-CN", version: str = "v0"):
        self.root_dir = Path(root_dir)
        self.locale = locale
        self.version = version
        self._formatter = Formatter()

    def render(self, prompt_name: str, **values: Any) -> str:
        template = self.load(prompt_name)
        required = {
            field_name
            for _, field_name, _, _ in self._formatter.parse(template)
            if field_name is not None and field_name != ""
        }
        missing = sorted(field for field in required if field not in values)
        if missing:
            raise PromptError(f"prompt '{prompt_name}' missing values: {', '.join(missing)}")
        return template.format(**{key: str(value) for key, value in values.items()})

    def load(self, name: str) -> str:
        path = self.resolve_path(name)
        if not path.exists():
            raise PromptError(f"prompt file not found: {path}")
        template = path.read_text(encoding="utf-8")
        if not template.strip():
            raise PromptError(f"prompt file is empty: {path}")
        return template

    def resolve_path(self, name: str) -> Path:
        return self.root_dir / self.locale / f"{name}.prompt"
