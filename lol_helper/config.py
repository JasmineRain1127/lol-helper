from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from tempfile import NamedTemporaryFile
from pathlib import Path


if getattr(sys, "frozen", False):
    ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LOL Helper"
else:
    ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "settings.json"


@dataclass(slots=True)
class Settings:
    auto_accept: bool = True
    auto_pick: bool = True
    auto_bench_swap: bool = True
    preferred_champions: list[int] = field(default_factory=list)
    poll_interval_ms: int = 350

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Settings":
        if not path.exists():
            value = cls()
            value.save(path)
            return value
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return cls()
            value = cls()
            for name in ("auto_accept", "auto_pick", "auto_bench_swap"):
                if type(raw.get(name)) is bool:
                    setattr(value, name, raw[name])
            interval = raw.get("poll_interval_ms")
            if type(interval) is int:
                value.poll_interval_ms = max(150, min(interval, 2000))
            preferred = raw.get("preferred_champions")
            if isinstance(preferred, list):
                value.preferred_champions = list(dict.fromkeys(
                    item for item in preferred if type(item) is int and item > 0
                ))
            return value
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                    prefix=f".{path.name}.", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                file.write(json.dumps(asdict(self), ensure_ascii=False, indent=2))
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
