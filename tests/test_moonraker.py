import json
import types
import unittest

from fake_bundle import FIXTURES, SV06, U1
from plugin_loader import load

sp, orca = load()

SPOOLS = json.loads((FIXTURES / "spoolman_spools.json").read_text(encoding="utf-8"))
# Captured from a Snapmaker U1: all four heads hold Snapmaker SnapSpeed PLA with RFID tags,
# and the firmware reports the Spoolman spool in each head (filament_spool_id).
U1_STATUS = json.loads((FIXTURES / "u1_status.json").read_text(encoding="utf-8"))


def u1_status(**changes):
    status = json.loads(json.dumps(U1_STATUS))
    status["print_task_config"].update(changes)
    return status


def names():
    found = {}
    for f in sp.stocked_filaments(SPOOLS):
        found[(f["id"], U1)] = f"{sp.profile_name(f)} @Snapmaker U1"
        found[(f["id"], SV06)] = f"{sp.profile_name(f)} @Sovol SV06"
    return found


class BaseTest(unittest.TestCase):
    def test_moon_base(self):
        self.assertEqual(sp.moon_base("10.25.1.83"), "http://10.25.1.83")
        self.assertEqual(sp.moon_base("u1.lan:7125/"), "http://u1.lan:7125")
        self.assertEqual(sp.moon_base("https://p.example/path"), "https://p.example")
        self.assertEqual(sp.moon_base(""), "")
        self.assertEqual(sp.moon_base("ftp://x"), "")
        self.assertEqual(sp.moon_base("bad host"), "")
        self.assertEqual(sp.moon_base("user:pw@host"), "")

    def test_auto_printers_are_never_saved(self):
        self.assertEqual(sp.parse_printers([{"id": "auto-x", "auto": True, "name": "U1"}]), [])


class U1Test(unittest.TestCase):
    def setUp(self):
        sp._profiles["names"] = names()

    def trays(self, status, spools=SPOOLS):
        doc, heads = sp.moon_report(status, None, spools, U1)
        self.assertTrue(heads)
        return doc["print"]["ams"]["ams"][0]["tray"]

    def test_heads_use_the_spool_id_the_printer_reports(self):
        trays = self.trays(U1_STATUS)
        self.assertEqual([t["spool_id"] for t in trays], [3, 4, 6, 5])
        self.assertEqual(trays[0]["profile"], "_local/spooler/Snapmaker PLA SnapSpeed Yellow @Snapmaker U1")
        self.assertEqual(trays[0]["tray_color"], "F4C032FF")
        self.assertEqual(trays[0]["tag_uid"], "60827A6A")
        spool = next(s for s in SPOOLS if s["id"] == 3)
        self.assertAlmostEqual(trays[0]["remain"], spool["remaining_weight"] / spool["initial_weight"] * 100)

    def test_rfid_tag_when_no_spool_id(self):
        trays = self.trays(u1_status(filament_spool_id=[0, 0, 0, 0]))
        self.assertEqual([t["spool_id"] for t in trays], [3, 4, 6, 5])

    def test_colour_when_no_id_or_tag(self):
        status = u1_status(filament_spool_id=[0, 0, 0, 0])
        status["filament_detect"] = {"info": [{}, {}, {}, {}]}
        trays = self.trays(status)
        # Yellow F4C032 only matches the yellow SnapSpeed spool.
        self.assertEqual(trays[0]["spool_id"], 3)

    def test_unknown_spool_id_falls_back(self):
        trays = self.trays(u1_status(filament_spool_id=[999, 4, 6, 5]))
        self.assertEqual(trays[0]["spool_id"], 3)

    def test_empty_head(self):
        trays = self.trays(u1_status(filament_exist=[True, False, True, True]))
        self.assertEqual(trays[1], {"id": "1"})

    def test_nothing_in_spoolman(self):
        status = u1_status(filament_spool_id=[0, 0, 0, 0])
        status["filament_detect"] = {"info": [{}, {}, {}, {}]}
        trays = self.trays(status, spools=[])
        self.assertIsNone(trays[3]["spool_id"])
        self.assertEqual(trays[3]["tray_sub_brands"], "Snapmaker PLA SnapSpeed")
        self.assertEqual(trays[3]["profile"], "")

    def test_live_state_shows_heads(self):
        doc, heads = sp.moon_report(U1_STATUS, None, SPOOLS, U1)
        sp._live["printers"].clear()
        sp.live_apply("auto-u1", "U1", doc, link="auto-u1")
        sp._live["printers"]["auto-u1"]["heads"] = heads
        snap = sp.live_snapshot()["printers"][0]
        self.assertTrue(snap["heads"])
        trays = snap["units"][0]["trays"]
        self.assertEqual([t["spool"] for t in trays], [3, 4, 6, 5])
        self.assertTrue(trays[0]["rfid"])
        self.assertTrue(trays[0]["profile"].endswith("@Snapmaker U1"))
        # A head emptied later is cleared, not left stale.
        doc, _ = sp.moon_report(u1_status(filament_exist=[False, True, True, True]), None, SPOOLS, U1)
        sp.live_apply("auto-u1", "U1", doc, link="auto-u1")
        self.assertTrue(sp.live_snapshot()["printers"][0]["units"][0]["trays"][0]["empty"])
        sp._live["printers"].clear()


class SingleTest(unittest.TestCase):
    def setUp(self):
        sp._profiles["names"] = names()

    def test_active_spool(self):
        doc, heads = sp.moon_report({}, 6, SPOOLS, SV06)
        self.assertFalse(heads)
        ext = doc["print"]["vt_tray"]
        self.assertEqual(ext["spool_id"], 6)
        self.assertEqual(ext["tray_color"], "E2DEDBFF")
        self.assertEqual(ext["profile"], "_local/spooler/Snapmaker PLA SnapSpeed White @Sovol SV06")

    def test_no_active_spool(self):
        doc, _ = sp.moon_report({}, None, SPOOLS, SV06)
        self.assertEqual(doc["print"]["vt_tray"], {"id": "254"})


class ProbeTest(unittest.TestCase):
    def test_is_moonraker(self):
        original = sp._moon_get
        try:
            sp._moon_get = lambda base, path: {"klippy_state": "ready"}
            self.assertTrue(sp.is_moonraker("http://x"))
            sp._moon_get = lambda base, path: {"name": "PrusaLink"}
            self.assertFalse(sp.is_moonraker("http://x"))

            def missing(base, path):
                raise sp.urllib.error.HTTPError(base + path, 404, "Not Found", {}, None)

            sp._moon_get = missing
            self.assertFalse(sp.is_moonraker("http://x"))
        finally:
            sp._moon_get = original

    def test_not_moonraker_printers_are_hidden(self):
        sp._auto_printers[:] = [{"id": "auto-a", "name": "A", "units": [], "ip": "http://a",
                                 "serial": "", "kind": "moonraker", "auto": True}]
        sp._live["links"]["auto-a"] = "none"
        try:
            self.assertEqual(sp.printer_state()["printers"], [])
        finally:
            sp._live["links"].pop("auto-a")
            sp._auto_printers.clear()


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.originals = (sp.get_spools, sp.profile_sync, sp.moon_poll)
        sp._ui_snap.clear()
        sp._ui_snap.update(printers=[{"target": U1, "label": "Snapmaker U1", "hosts": ["http://u1"]}])

    def tearDown(self):
        sp.get_spools, sp.profile_sync, sp.moon_poll = self.originals
        sp._ui_snap.clear()

    def test_load_selects_profiles(self):
        calls = []

        class Tx:
            def __init__(self, slot, name):
                calls.append((slot, name))

            def commit(self):
                if calls[-1][0] == 2:
                    raise ValueError("bad")

        orca.host.preset.filament = types.SimpleNamespace(begin_update=Tx)
        sp.get_spools = lambda url: {"ok": True, "spools": SPOOLS}
        sp.profile_sync = lambda spools, snap: {"error": "", "names": names()}
        sp._profiles["names"] = names()
        status = u1_status(filament_exist=[True, True, True, False])
        sp.moon_poll = lambda base: sp.moon_report(status, None, SPOOLS, U1)
        text = sp.load_printer_filaments({"host": "http://u1", "slots": 4, "names": set()})
        self.assertEqual([c[0] for c in calls], [1, 2, 3])
        self.assertTrue(calls[0][1].endswith("Snapmaker PLA SnapSpeed Yellow @Snapmaker U1"))
        self.assertIn("Loaded 2 filament", text)
        self.assertIn("Could not change slot(s) 2", text)

    def test_needs_snapshot_and_host(self):
        with self.assertRaises(RuntimeError):
            sp.load_printer_filaments({})
        with self.assertRaises(RuntimeError):
            sp.load_printer_filaments({"host": "", "slots": 1, "names": set()})


class PlanTest(unittest.TestCase):
    def test_plan(self):
        trays = [{"profile": "_local/spooler/A", "tray_type": "PLA"},
                 {"profile": "", "tray_type": "PLA"},
                 {"id": "2"},
                 {"profile": "_local/spooler/B", "tray_type": "PLA"}]
        plan, missing = sp.slot_plan(trays, 3, {"_local/spooler/A", "B"})
        self.assertEqual(plan, [(1, "_local/spooler/A")])
        self.assertEqual(missing, [2])
        plan, _ = sp.slot_plan(trays, 4, {"_local/spooler/A", "B"})
        self.assertEqual(plan[-1], (4, "B"))


if __name__ == "__main__":
    unittest.main()
