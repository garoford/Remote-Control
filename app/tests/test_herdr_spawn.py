import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from remote_control import agents


def pane(pid, tab, ws="w1"):
    return {"pane_id": pid, "tab_id": tab, "workspace_id": ws}


def tab(tid, count, number, ws="w1", label=""):
    return {"tab_id": tid, "pane_count": count, "number": number, "workspace_id": ws, "label": label or str(number)}


class PlanSlotTests(unittest.TestCase):
    def test_own_tab_while_it_has_room(self) -> None:
        tabs = [tab("w1:t1", 4, 1)]
        self.assertEqual(agents.plan_slot("w1:p1", tabs, [pane("w1:p1", "w1:t1")], set()), ("split", "w1:t1"))

    def test_full_tab_opens_a_new_one_in_the_same_workspace(self) -> None:
        tabs = [tab("w1:t1", 5, 1), tab("w2:t1", 1, 1, ws="w2")]
        self.assertEqual(agents.plan_slot("w1:p1", tabs, [pane("w1:p1", "w1:t1")], set()), ("new", "w1"))

    def test_user_tabs_with_room_are_never_filled(self) -> None:
        tabs = [tab("w1:t1", 5, 1), tab("w1:t2", 1, 2)]
        self.assertEqual(agents.plan_slot("w1:p1", tabs, [pane("w1:p1", "w1:t1")], set()), ("new", "w1"))

    def test_agents_tabs_fill_in_order_up_to_five(self) -> None:
        tabs = [tab("w1:t1", 5, 1), tab("w1:t3", 5, 3), tab("w1:t4", 2, 4), tab("w9:t1", 1, 1, ws="w9")]
        ours = {"w1:t3", "w1:t4", "w9:t1"}
        self.assertEqual(agents.plan_slot("w1:p1", tabs, [pane("w1:p1", "w1:t1")], ours), ("split", "w1:t4"))

    def test_unknown_pane(self) -> None:
        self.assertEqual(agents.plan_slot("w1:p9", [], [], set()), ("", ""))


class BiggestPaneTests(unittest.TestCase):
    def test_wide_pane_splits_right_tall_one_down(self) -> None:
        layout = {"panes": [
            {"pane_id": "a", "rect": {"width": 74, "height": 35}},
            {"pane_id": "b", "rect": {"width": 148, "height": 20}},
        ]}
        self.assertEqual(agents.biggest_pane(layout), ("b", "right"))
        layout = {"panes": [{"pane_id": "a", "rect": {"width": 60, "height": 35}}]}
        self.assertEqual(agents.biggest_pane(layout), ("a", "down"))
        self.assertEqual(agents.biggest_pane({}), ("", ""))


class FakeHerdr:
    """A tiny herdr: tabs with panes, split and tab create."""

    def __init__(self) -> None:
        self.tabs = {"w1:t1": ["w1:p1"]}
        self.labels = {"w1:t1": "1"}
        self.next = 2
        self.ran = []

    def _new_pane(self) -> str:
        pid = f"w1:p{self.next}"
        self.next += 1
        return pid

    def __call__(self, *args):
        if args[:2] == ("pane", "list"):
            return {"panes": [{"pane_id": p, "tab_id": t, "workspace_id": "w1", "agent": "claude", "agent_status": "idle"}
                              for t, ps in self.tabs.items() for p in ps]}
        if args[:2] == ("tab", "list"):
            return {"tabs": [{"tab_id": t, "pane_count": len(ps), "number": i + 1, "workspace_id": "w1", "label": self.labels[t]}
                             for i, (t, ps) in enumerate(self.tabs.items())]}
        if args[:2] == ("pane", "layout"):
            t = next(t for t, ps in self.tabs.items() if args[3] in ps)
            return {"layout": {"panes": [{"pane_id": p, "rect": {"width": 100, "height": 30}} for p in self.tabs[t]]}}
        if args[:2] == ("pane", "split"):
            t = next(t for t, ps in self.tabs.items() if args[2] in ps)
            pid = self._new_pane()
            self.tabs[t].append(pid)
            return {"pane": {"pane_id": pid, "tab_id": t}}
        if args[:2] == ("tab", "create"):
            t = f"w1:t{len(self.tabs) + 1}"
            pid = self._new_pane()
            self.tabs[t] = [pid]
            self.labels[t] = args[args.index("--label") + 1]
            return {"tab": {"tab_id": t}, "root_pane": {"pane_id": pid}}
        if args[:2] == ("pane", "run"):
            self.ran.append(args[2:])
            return {}
        if args[:2] == ("pane", "close"):
            for ps in self.tabs.values():
                if args[2] in ps:
                    ps.remove(args[2])
            self.tabs = {t: ps for t, ps in self.tabs.items() if ps}
            return {}
        return None


class SpawnHerdrTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.herdr = FakeHerdr()
        for p in (
            mock.patch.object(agents, "primary_home", lambda: Path(self.tmp.name)),
            mock.patch.object(agents, "_herdr", self.herdr),
            mock.patch.object(agents, "_herdr_ready", lambda pane, cli: "idle"),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.me = {"profile": "", "pane": "w1:p1"}

    def test_twelve_agents_plus_the_caller_make_tabs_of_five_at_most(self) -> None:
        for i in range(12):
            agent, status = agents.spawn_herdr("claude", [], self.me, "/tmp")
            self.assertEqual(status, "idle")
        sizes = {t: len(ps) for t, ps in self.herdr.tabs.items()}
        self.assertEqual(sizes, {"w1:t1": 5, "w1:t2": 5, "w1:t3": 3})
        self.assertEqual([self.herdr.labels[t] for t in ("w1:t2", "w1:t3")], ["agentes 1", "agentes 2"])
        self.assertEqual(self.herdr.ran[0], ("w1:p2", "claude"))

    def test_close_frees_a_slot_that_is_reused(self) -> None:
        made = [agents.spawn_herdr("claude", [], self.me, "/tmp")[0]["id"] for _ in range(6)]
        self.assertEqual(len(self.herdr.tabs["w1:t2"]), 2)
        self.assertEqual(agents.close_spawned(made[0], self.me), "")
        self.assertEqual(agents.close_spawned(made[0], self.me), "it was not opened with `rc spawn`; ask your user to close it")
        self.assertEqual(agents.close_spawned(made[1], {"profile": "luis", "pane": "w1:p1"}), "another user opened it")
        # A slot in the caller's tab again: the next one goes there.
        agents.spawn_herdr("claude", [], self.me, "/tmp")
        self.assertEqual(len(self.herdr.tabs["w1:t1"]), 5)

    def test_user_panes_are_not_closed(self) -> None:
        self.assertIn("not opened with `rc spawn`", agents.close_spawned("w1:p1", {"profile": "", "pane": "w1:p9"}))
        self.assertEqual(agents.close_spawned("w1:p1", self.me), "that is you")


if __name__ == "__main__":
    unittest.main()
