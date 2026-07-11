from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
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
            allowed = {name for name in cls.__dataclass_fields__}
            return cls(**{key: value for key, value in raw.items() if key in allowed})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
