import logging
import unittest
from pathlib import Path
from uuid import uuid4

from lol_helper.automation import (
    AutomationEngine,
    bench_ids,
    card_ids,
    current_champion_id,
    current_pick_action,
    pickable_ids,
    redact_session,
)
from lol_helper.config import ROOT, Settings
from lol_helper.ddragon import _plain_text
from lol_helper.lcu import Credentials, _credentials_from_lockfile, _extract_credentials


class CredentialsTests(unittest.TestCase):
    def test_extracts_equals_arguments(self):
        value = _extract_credentials('LeagueClientUx.exe --app-port=12345 --remoting-auth-token="secret"')
        self.assertEqual(value, Credentials(12345, "secret"))

    def test_extracts_space_arguments(self):
        value = _extract_credentials("x --app-port 54321 --remoting-auth-token abc")
        self.assertEqual(value, Credentials(54321, "abc"))

    def test_reads_standard_lockfile(self):
        folder = ROOT / ".test-tmp" / uuid4().hex
        path = folder / "lockfile"
        try:
            folder.mkdir(parents=True)
            path.write_text("LeagueClient:123:45678:token:https", encoding="utf-8")
            self.assertEqual(_credentials_from_lockfile(path), Credentials(45678, "token"))
        finally:
            if path.exists():
                path.unlink()
            if folder.exists():
                folder.rmdir()
            parent = folder.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()


class SessionTests(unittest.TestCase):
    def test_finds_local_pick(self):
        session = {"localPlayerCellId": 2, "actions": [[
            {"id": 7, "actorCellId": 2, "type": "pick", "isInProgress": True, "completed": False}
        ]]}
        self.assertEqual(current_pick_action(session)["id"], 7)

    def test_extracts_bench_and_card_ids(self):
        session = {
            "benchChampions": [{"championId": 81}, {"championId": 22}],
            "championCards": {"championIds": [145, 157]},
        }
        self.assertEqual(bench_ids(session), {81, 22})
        self.assertEqual(card_ids(session), {145, 157})
        self.assertEqual(pickable_ids(session), {81, 22, 145, 157})

    def test_extracts_current_champion(self):
        session = {
            "localPlayerCellId": 3,
            "myTeam": [
                {"cellId": 1, "championId": 22},
                {"cellId": 3, "championId": 81},
            ],
        }
        self.assertEqual(current_champion_id(session), 81)

    def test_redacts_identity(self):
        value = redact_session({"puuid": "private", "nested": [{"summonerId": 42}], "championId": 81})
        self.assertEqual(value["puuid"], "<redacted>")
        self.assertEqual(value["nested"][0]["summonerId"], "<redacted>")
        self.assertEqual(value["championId"], 81)


class SettingsTests(unittest.TestCase):
    def test_roundtrip(self):
        folder = ROOT / ".test-tmp" / uuid4().hex
        path = folder / "settings.json"
        try:
            expected = Settings(True, True, True, [81, 145], 250)
            expected.save(path)
            self.assertEqual(Settings.load(path), expected)
        finally:
            if path.exists():
                path.unlink()
            if folder.exists():
                folder.rmdir()
            parent = folder.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()


class DataDragonTests(unittest.TestCase):
    def test_strips_html_from_skill_text(self):
        self.assertEqual(_plain_text("造成 <b>魔法伤害</b><br />并减速。"), "造成 魔法伤害\n并减速。")


class AutomationEngineTests(unittest.TestCase):
    def test_accepts_once_per_ready_check(self):
        class Client:
            phase = "ReadyCheck"
            posts: list[str] = []

            def get(self, path):
                if path == "/lol-gameflow/v1/gameflow-phase":
                    return self.phase
                if path == "/lol-matchmaking/v1/ready-check":
                    return {"state": "InProgress", "timer": 8}
                raise AssertionError(path)

            def post(self, path, _data=None):
                self.posts.append(path)

        events = []
        client = Client()
        engine = AutomationEngine(Settings, lambda level, payload: events.append((level, payload)), logging.getLogger())
        engine._client = client

        engine._tick()
        engine._tick()
        self.assertEqual(len(client.posts), 1)
        self.assertEqual(sum(level == "status" for level, _payload in events), 1)

        client.phase = "Lobby"
        engine._tick()
        client.phase = "ReadyCheck"
        engine._tick()
        self.assertEqual(len(client.posts), 2)


if __name__ == "__main__":
    unittest.main()
