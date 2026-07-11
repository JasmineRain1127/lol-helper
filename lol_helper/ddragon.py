from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any

from .config import ROOT


BASE_URL = "https://ddragon.leagueoflegends.com"
CACHE_DIR = ROOT / "cache" / "ddragon"


def _plain_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return unescape(value).strip()


@dataclass(frozen=True, slots=True)
class ChampionSummary:
    champion_id: int
    alias: str
    name: str
    title: str
    tags: tuple[str, ...]
    blurb: str
    image_file: str


class DataDragon:
    def __init__(self, locale: str = "zh_CN", timeout: float = 8.0):
        self.locale = locale
        self.timeout = timeout
        self.version = ""
        self.by_id: dict[int, ChampionSummary] = {}

    def _download(self, url: str, destination: Path) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": "LOL-Helper/0.4"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = response.read()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{threading.get_ident()}.tmp")
        temporary.write_bytes(data)
        temporary.replace(destination)
        return data

    def _json(self, url: str, destination: Path, refresh: bool = False) -> Any:
        if destination.exists() and not refresh:
            data = destination.read_bytes()
        else:
            data = self._download(url, destination)
        return json.loads(data.decode("utf-8"))

    def load(self) -> dict[int, ChampionSummary]:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        versions_path = CACHE_DIR / "versions.json"
        cache_is_fresh = versions_path.exists() and time.time() - versions_path.stat().st_mtime < 86_400
        try:
            versions = self._json(
                f"{BASE_URL}/api/versions.json", versions_path, refresh=not cache_is_fresh
            )
        except Exception:
            versions = self._json(f"{BASE_URL}/api/versions.json", versions_path)
        self.version = str(versions[0])
        catalog_path = CACHE_DIR / self.version / self.locale / "champion.json"
        catalog = self._json(
            f"{BASE_URL}/cdn/{self.version}/data/{self.locale}/champion.json",
            catalog_path,
        )
        result: dict[int, ChampionSummary] = {}
        for raw in catalog.get("data", {}).values():
            try:
                champion_id = int(raw["key"])
                result[champion_id] = ChampionSummary(
                    champion_id=champion_id,
                    alias=str(raw["id"]),
                    name=str(raw["name"]),
                    title=str(raw.get("title", "")),
                    tags=tuple(str(tag) for tag in raw.get("tags", [])),
                    blurb=_plain_text(str(raw.get("blurb", ""))),
                    image_file=str(raw["image"]["full"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
        self.by_id = result
        return result

    def portrait(self, champion_id: int) -> Path:
        champion = self.by_id[champion_id]
        path = CACHE_DIR / self.version / "img" / "champion" / champion.image_file
        if not path.exists():
            self._download(f"{BASE_URL}/cdn/{self.version}/img/champion/{champion.image_file}", path)
        return path

    def preload_portraits(self, workers: int = 10) -> tuple[int, int]:
        """Warm the complete square-portrait cache without blocking the UI thread."""
        champion_ids = list(self.by_id)
        completed = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ddragon-preload") as pool:
            futures = [pool.submit(self.portrait, champion_id) for champion_id in champion_ids]
            for future in as_completed(futures):
                try:
                    future.result()
                    completed += 1
                except Exception:
                    pass
        return completed, len(champion_ids)

    def details(self, champion_id: int) -> dict[str, Any]:
        champion = self.by_id[champion_id]
        path = CACHE_DIR / self.version / self.locale / "champion" / f"{champion.alias}.json"
        raw = self._json(
            f"{BASE_URL}/cdn/{self.version}/data/{self.locale}/champion/{champion.alias}.json",
            path,
        )
        detail = raw.get("data", {}).get(champion.alias, {})
        passive = detail.get("passive", {})
        spells = detail.get("spells", [])
        return {
            "id": champion_id,
            "name": champion.name,
            "title": champion.title,
            "tags": champion.tags,
            "blurb": champion.blurb,
            "passive": {
                "name": str(passive.get("name", "")),
                "description": _plain_text(str(passive.get("description", ""))),
            },
            "spells": [
                {
                    "key": key,
                    "name": str(spell.get("name", "")),
                    "description": _plain_text(str(spell.get("description", ""))),
                }
                for key, spell in zip(("Q", "W", "E", "R"), spells)
            ],
        }
