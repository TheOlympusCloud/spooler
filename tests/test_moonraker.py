import types
import unittest

from plugin_loader import load

sp, orca = load()


def spool(spool_id, fid, name, material, colour, vendor="SUNLU", tag=None, remaining=250.0):
    return {"id": spool_id, "archived": False, "remaining_weight": remaining, "initial_weight": 1000,
            "extra": {"tag": f'"{tag}"'} if tag else {},
            "filament": {"id": fid, "name": name, "material": material, "color_hex": colour,
                         "vendor": {"name": vendor}, "weight": 1000}}


SPOOLS = [spool(1, 10, "Black", "PETG", "000000", tag="04A1B2C3D4E5F6"),
          spool(2, 11, "Red", "PLA", "FF0000"),
          spool(3, 12, "White", "PLA", "FFFFFF")]

U1_STATUS = {
    "print_task_config": {
        "filament_exist": [True, True, False, True],
        "filament_type": ["PETG", "PLA", "", "PLA"],
        "filament_sub_type": ["Basic", "Basic", "", "Silk"],
        "filament_vendor": ["Generic", "SUNLU", "", "Acme"],
        "filament_color_rgba": ["010101FF", "FE0101FF", "", "00FF00FF"],
    },
    "filament_detect": {"info": [{"CARD_UID": "04A1B2C3D4E5F6"}, {"CARD_UID": "NONE"}, {}, {}]},
}


class BaseTest(unittest.TestCase):
    def test_moon_base(self):
        self.assertEqual(sp.moon_base("192.168.1.5"), "http://192.168.1.5")
        self.assertEqual(sp.moon_base("u1.lan:7125/"), "http://u1.lan:7125")
        self.assertEqual(sp.moon_base("https://p.example/path"), "https://p.example")
        self.assertEqual(sp.moon_base(""), "")
        self.assertEqual(sp.moon_base("ftp://x"), "")
        self.assertEqual(sp.moon_base("bad host"), "")


class HostsTest(unittest.TestCase):
    def test_one_printer_per_host(self):
        def preset(name, host):
            return types.SimpleNamespace(name=name, label=lambda raw: name,
                                         config_value=lambda key: host if key == "print_host" else None)

        presets = [preset("Snapmaker U1 (0.4 nozzle)", "192.168.1.20"),
                   preset("My U1 copy", "192.168.1.20"),
                   preset("Sovol SV06 ACE (0.4 nozzle)", "sv06.lan"),
                   preset("Bambu X1C", None)]
        printers = types.SimpleNamespace(size=lambda: len(presets), preset=lambda i: presets[i])
        found = sp.printer_hosts(types.SimpleNamespace(printers=printers))
        self.assertEqual([p["name"] for p in found], ["Snapmaker U1", "Sovol SV06 ACE"])
        self.assertEqual(found[0]["id"], "auto-192-168-1-20")
        self.assertTrue(all(p["kind"] == "moonraker" and p["auto"] for p in found))

    def test_auto_printers_are_never_saved(self):
        self.assertEqual(sp.parse_printers([{"id": "auto-x", "auto": True, "name": "U1"}]), [])


class U1Test(unittest.TestCase):
    def setUp(self):
        sp._profiles["names"] = {10: "SUNLU PETG Black", 11: "SUNLU PLA Red"}

    def test_heads(self):
        doc, heads = sp.moon_report(U1_STATUS, None, SPOOLS)
        self.assertTrue(heads)
        trays = doc["print"]["ams"]["ams"][0]["tray"]
        self.assertEqual(len(trays), 4)
        # Head 1: matched by RFID tag even though the printer says "Generic".
        self.assertEqual(trays[0]["spool_id"], 1)
        self.assertEqual(trays[0]["profile"], "_local/spooler/SUNLU PETG Black")
        self.assertEqual(trays[0]["remain"], 25.0)
        # Head 2: no tag, matched by vendor, material and close colour.
        self.assertEqual(trays[1]["spool_id"], 2)
        # Head 3: empty.
        self.assertEqual(trays[2], {"id": "2"})
        # Head 4: nothing in Spoolman, shown as the printer reports it.
        self.assertIsNone(trays[3]["spool_id"])
        self.assertEqual(trays[3]["tray_sub_brands"], "Acme PLA Silk")
        self.assertEqual(trays[3]["profile"], "")

    def test_live_state_shows_heads(self):
        doc, heads = sp.moon_report(U1_STATUS, None, SPOOLS)
        sp._live["printers"].clear()
        sp.live_apply("auto-u1", "U1", doc, link="auto-u1")
        sp._live["printers"]["auto-u1"]["heads"] = heads
        snap = sp.live_snapshot()["printers"][0]
        self.assertTrue(snap["heads"])
        trays = snap["units"][0]["trays"]
        self.assertEqual(trays[0]["spool"], 1)
        self.assertTrue(trays[0]["rfid"])
        self.assertTrue(trays[2]["empty"])


class SingleTest(unittest.TestCase):
    def setUp(self):
        sp._profiles["names"] = {12: "SUNLU PLA White"}

    def test_active_spool(self):
        doc, heads = sp.moon_report({}, 3, SPOOLS)
        self.assertFalse(heads)
        ext = doc["print"]["vt_tray"]
        self.assertEqual(ext["spool_id"], 3)
        self.assertEqual(ext["tray_color"], "FFFFFFFF")
        self.assertEqual(ext["profile"], "_local/spooler/SUNLU PLA White")

    def test_no_active_spool(self):
        doc, _ = sp.moon_report({}, None, SPOOLS)
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
    def test_load_selects_profiles(self):
        calls = []

        class Tx:
            def __init__(self, slot, name):
                calls.append((slot, name))

            def commit(self):
                if calls[-1][0] == 2:
                    raise ValueError("bad")

        orca.host.preset.filament = types.SimpleNamespace(begin_update=Tx)
        originals = (sp.get_spools, sp.profile_sync, sp.moon_poll)
        sp.get_spools = lambda url: {"ok": True, "spools": SPOOLS}
        sp.profile_sync = lambda spools, snap: {"error": "", "names": {10: "SUNLU PETG Black", 11: "SUNLU PLA Red"}}
        sp.moon_poll = lambda base: sp.moon_report(U1_STATUS, None, SPOOLS)
        sp._profiles["names"] = {10: "SUNLU PETG Black", 11: "SUNLU PLA Red"}
        try:
            text = sp.load_printer_filaments({"host": "http://u1", "slots": 4, "names": set()})
        finally:
            sp.get_spools, sp.profile_sync, sp.moon_poll = originals
        self.assertEqual(calls, [(1, "_local/spooler/SUNLU PETG Black"), (2, "_local/spooler/SUNLU PLA Red")])
        self.assertIn("Loaded 1 filament", text)
        self.assertIn("slot(s) 4.", text)
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
        names = {"_local/spooler/A", "B"}
        plan, missing = sp.slot_plan(trays, 3, names)
        self.assertEqual(plan, [(1, "_local/spooler/A")])
        self.assertEqual(missing, [2])
        plan, _ = sp.slot_plan(trays, 4, names)
        self.assertEqual(plan[-1], (4, "B"))


if __name__ == "__main__":
    unittest.main()
