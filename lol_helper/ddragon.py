from __future__ import annotations

import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ROOT


BASE_URL = "https://ddragon.leagueoflegends.com"
CACHE_DIR = ROOT / "cache" / "ddragon"


@dataclass(frozen=True, slots=True)
class ChampionSummary:
    champion_id: int
    name: str
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
                    name=str(raw["name"]),
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

    def preload_portraits(self, workers: int = 10) -> dict[int, str]:
        """Warm every portrait and return paths ready for immediate UI use."""
        champion_ids = list(self.by_id)
        completed: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ddragon-preload") as pool:
            futures = {
                pool.submit(self.portrait, champion_id): champion_id
                for champion_id in champion_ids
            }
            for future in as_completed(futures):
                try:
                    completed[futures[future]] = str(future.result())
                except Exception:
                    pass
        return completed
