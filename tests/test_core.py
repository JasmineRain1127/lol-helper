import json
import logging
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from pathlib import Path
from uuid import uuid4

from lol_helper.automation import (
    AutomationEngine,
    bench_champion_ids,
    bench_ids,
    card_ids,
    current_champion_id,
    current_pick_action,
    pickable_ids,
    redact_session,
)
from lol_helper.config import ROOT, Settings
from lol_helper.ddragon import ChampionSummary, DataDragon
from lol_helper.lcu import LCUNotRunning, LCUResponseError, Credentials, _credentials_from_lockfile, _extract_credentials
from lol_helper.ui import App, bench_slot_layout, should_show_bar
from lol_helper.window_docking import top_bar_geometry, sync_bar_z_order, SWP_NOACTIVATE
from lol_helper import window_docking


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

    def test_preserves_bench_order_and_deduplicates(self):
        session = {
            "benchChampions": [
                {"championId": 81},
                {"championId": "22"},
                {"championId": 81},
                {"championId": 0},
                {"championId": "invalid"},
                145,
            ]
        }
        self.assertEqual(bench_champion_ids(session), [81, 22, 145])
        self.assertEqual(bench_ids(session), {81, 22, 145})

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
    def test_invalid_shapes_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            for raw in ([], None, 42, "settings", {"poll_interval_ms": "fast"}):
                with self.subTest(raw=raw):
                    path.write_text(json.dumps(raw), encoding="utf-8")
                    self.assertEqual(Settings.load(path), Settings())

    def test_validates_fields_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({
                "auto_accept": False, "auto_pick": "false",
                "poll_interval_ms": 1,
                "preferred_champions": [81, 81, True, -1, "22", 22],
            }), encoding="utf-8")
            self.assertEqual(Settings.load(path), Settings(False, True, True, [81, 22], 150))

    def test_snapshot_preserves_disabled_automation(self):
        app = SimpleNamespace(settings=Settings(False, False, False, [81], 500))
        snapshot = App._settings_snapshot(app)
        self.assertEqual(snapshot, app.settings)
        snapshot.preferred_champions.clear()
        self.assertEqual(app.settings.preferred_champions, [81])

    def test_failed_replace_preserves_existing_config(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            Settings(auto_accept=False).save(path)
            original = path.read_bytes()
            with patch.object(Path, "replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    Settings().save(path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(folder).iterdir()), [path])

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


class AutomationEngineTests(unittest.TestCase):
    def test_connection_failure_does_not_kill_monitor(self):
        for error in (LCUNotRunning("offline"), LCUResponseError(401, "expired")):
            with self.subTest(error=error):
                events = []
                engine = AutomationEngine(Settings, lambda *event: events.append(event), logging.getLogger())
                engine.set_target_champion(22)
                engine._picked_session = True
                engine._swap_attempt[22] = 100
                engine._last_session = {"benchChampions": [22]}
                client = Mock()
                client.get.side_effect = error
                engine._stop = Mock()
                engine._stop.is_set.side_effect = [False, False, True]
                with patch("lol_helper.automation.LCUClient", return_value=client) as factory:
                    engine._run()
                self.assertEqual(factory.call_count, 2)
                self.assertIsNone(engine._target())
                self.assertIsNone(engine._last_session)
                self.assertFalse(engine._picked_session)
                self.assertEqual(engine._swap_attempt, {})
                self.assertEqual(sum(level == "target_cleared" for level, _ in events), 1)

    def test_disabled_auto_accept_sends_no_action(self):
        engine = AutomationEngine(lambda: Settings(auto_accept=False), lambda *_: None, logging.getLogger())
        engine._client = Mock()
        engine._client.get.return_value = "ReadyCheck"
        engine._tick()
        engine._client.post.assert_not_called()

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


class DataDragonTests(unittest.TestCase):
    def test_preloads_all_available_portrait_paths(self):
        dragon = DataDragon()
        dragon.by_id = {
            22: ChampionSummary(22, "艾希", "Ashe.png"),
            81: ChampionSummary(81, "伊泽瑞尔", "Ezreal.png"),
        }

        paths = {
            22: Path("cache/Ashe.png"),
            81: Path("cache/Ezreal.png"),
        }
        dragon.portrait = lambda champion_id: paths[champion_id]

        self.assertEqual(
            dragon.preload_portraits(workers=2),
            {22: str(paths[22]), 81: str(paths[81])},
        )


class AutomationSwapTests(unittest.TestCase):
    def test_champ_select_keeps_bench_order_and_swaps_target(self):
        class Client:
            posts: list[str] = []

            def get(self, path):
                self.assert_session_path(path)
                return {
                    "benchChampions": [
                        {"championId": 145},
                        {"championId": 22},
                        {"championId": 81},
                    ],
                    "localPlayerCellId": 1,
                    "myTeam": [{"cellId": 1, "championId": 99}],
                }

            def post(self, path, _data=None):
                self.posts.append(path)

            @staticmethod
            def assert_session_path(path):
                if path != "/lol-champ-select/v1/session":
                    raise AssertionError(path)

        events = []
        client = Client()
        engine = AutomationEngine(
            lambda: Settings(),
            lambda level, payload: events.append((level, payload)),
            logging.getLogger(),
        )
        engine._client = client
        engine.set_target_champion(22)
        engine._champ_select(Settings())

        payload = next(payload for level, payload in events if level == "champ_select")
        self.assertEqual(payload["bench"], [145, 22, 81])
        self.assertEqual(
            client.posts,
            ["/lol-champ-select/v1/session/bench/swap/22"],
        )


class WindowDockingTests(unittest.TestCase):
    def test_z_order_follows_client_without_activation(self):
        for foreground, predecessor, expected in ((10, 30, 0), (20, 30, 0), (30, 40, 40), (30, 20, None)):
            with self.subTest(foreground=foreground, predecessor=predecessor):
                native = Mock()
                native.IsWindow.return_value = True
                native.IsIconic.return_value = False
                native.GetForegroundWindow.return_value = foreground
                native.GetWindow.return_value = predecessor
                native.GetWindowLongW.return_value = 0
                with patch("lol_helper.window_docking.user32", native), patch(
                    "lol_helper.window_docking.native_window_handle", return_value=20
                ):
                    sync_bar_z_order(200, 10)
                if expected is None:
                    native.SetWindowPos.assert_not_called()
                else:
                    args = native.SetWindowPos.call_args.args
                    self.assertEqual(args[:2], (20, expected))
                    self.assertTrue(args[-1] & SWP_NOACTIVATE)

    def test_topmost_predecessor_does_not_promote_overlay(self):
        native = Mock()
        native.IsWindow.return_value = True
        native.IsIconic.return_value = False
        native.GetForegroundWindow.return_value = 30
        native.GetWindow.return_value = 30
        native.GetWindowLongW.return_value = 0x8
        with patch("lol_helper.window_docking.user32", native), patch(
            "lol_helper.window_docking.native_window_handle", return_value=20
        ):
            sync_bar_z_order(200, 10)
        self.assertEqual(native.SetWindowPos.call_args.args[1], -2)

    def test_top_bar_sits_above_client_top_edge(self):
        self.assertEqual(top_bar_geometry((100, 150, 1380, 870)), "1280x72+100+78")

    def test_top_bar_supports_negative_monitor_coordinates(self):
        self.assertEqual(top_bar_geometry((-1920, 100, 0, 1180)), "1920x72-1920+28")

    def test_top_bar_uses_requested_height_independent_of_client_height(self):
        self.assertEqual(top_bar_geometry((0, 100, 800, 140)), "800x72+0+28")


class TopBarUiTests(unittest.TestCase):
    def test_click_selects_then_cancels_with_immediate_feedback(self):
        app = App.__new__(App)
        app.session_state = {"bench": [22], "target": None}
        app.engine = Mock()
        app._render_session = Mock()
        app._animate_click = Mock()
        app.feedback = Mock()
        app._choose(22)
        self.assertEqual(app.session_state["target"], 22)
        app.engine.set_target_champion.assert_called_with(22)
        app._animate_click.assert_called_with(22)
        app._choose(22)
        self.assertIsNone(app.session_state["target"])
        app.feedback.configure.assert_called_with(text="已取消目标 · 点击头像重新选择")

    def test_selection_feedback_distinguishes_target_from_success(self):
        app = App.__new__(App)
        app.summaries = {22: ChampionSummary(22, "艾希", "Ashe.png")}
        app.session_state = {"current": 81}
        self.assertIn("已选 艾希", app._selection_text(22))
        app.session_state["current"] = 22
        self.assertIn("已换到 艾希", app._selection_text(22))

    def test_bench_slots_match_1280_client_design_coordinates(self):
        self.assertEqual(
            bench_slot_layout(1280, 3),
            [(352, 50), (410, 50), (469, 50)],
        )

    def test_bench_slots_scale_to_users_125_percent_client(self):
        self.assertEqual(
            bench_slot_layout(1600, 3),
            [(440, 62), (513, 62), (586, 62)],
        )

    def test_slot_size_is_capped_without_losing_center_alignment(self):
        self.assertEqual(bench_slot_layout(1920, 1), [(535, 62)])

    def test_bar_requires_champ_select_bench_client_and_portraits(self):
        rect = (100, 50, 1380, 770)
        self.assertTrue(should_show_bar("ChampSelect", [81, 22], rect, {81, 22}))
        self.assertFalse(should_show_bar("Lobby", [81, 22], rect, {81, 22}))
        self.assertFalse(should_show_bar("ChampSelect", [], rect, set()))
        self.assertFalse(should_show_bar("ChampSelect", [81], None, {81}))
        self.assertFalse(should_show_bar("ChampSelect", [81, 22], rect, {81}))


class WindowDockingTests(unittest.TestCase):
    def test_taskbar_style_update_passes_all_set_window_pos_arguments(self):
        class User32:
            def GetWindowLongW(self, _hwnd, _index):
                return 0

            def SetWindowLongW(self, _hwnd, _index, _style):
                return 0

            def SetWindowPos(self, *args):
                self.args = args
                return 1

        fake = User32()
        previous = window_docking.user32
        try:
            window_docking.user32 = fake
            with patch("lol_helper.window_docking.native_window_handle", return_value=123):
                window_docking.show_in_taskbar(456)
        finally:
            window_docking.user32 = previous
        self.assertEqual(len(fake.args), 7)


if __name__ == "__main__":
    unittest.main()
