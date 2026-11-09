import json
import tempfile
import unittest
from pathlib import Path

from plugin_loader import load

sp, orca = load()

NAMES = {"Generic PLA @System", "Generic PETG @System", "Generic PLA Silk @System",
         "SUNLU PETG @System", "Generic TPU @System", "Generic PA-CF @System"}


def spool(spool_id, filament, archived=False):
    return {"id": spool_id, "archived": archived, "remaining_weight": 500, "filament": filament}


def filament(fid, name, material, vendor="SUNLU", colour="1A2B3C", **extra):
    return {"id": fid, "name": name, "material": material, "vendor": {"name": vendor},
            "color_hex": colour, **extra}


class ParentTest(unittest.TestCase):
    def test_prefers_vendor_preset(self):
        self.assertEqual(sp.profile_parent("SUNLU", "PETG", NAMES), "SUNLU PETG @System")

    def test_generic_by_material(self):
        self.assertEqual(sp.profile_parent("Acme", "PETG", NAMES), "Generic PETG @System")

    def test_alias(self):
        self.assertEqual(sp.profile_parent("Acme", "Silk PLA", NAMES), "Generic PLA Silk @System")
        self.assertEqual(sp.profile_parent("Acme", "TPU 95A", NAMES), "Generic TPU @System")

    def test_base_material_of_variant(self):
        self.assertEqual(sp.profile_parent("Acme", "PLA Glow", NAMES), "Generic PLA @System")

    def test_unknown_falls_back_to_pla(self):
        self.assertEqual(sp.profile_parent("Acme", "Unobtainium", NAMES), "Generic PLA @System")


class NameTest(unittest.TestCase):
    def test_adds_vendor_and_material(self):
        self.assertEqual(sp.profile_name(filament(1, "Black", "PETG")), "SUNLU PETG Black")

    def test_keeps_name_that_has_both(self):
        self.assertEqual(sp.profile_name(filament(1, "SUNLU PLA+ Red", "PLA+")), "SUNLU PLA+ Red")

    def test_strips_path_characters(self):
        self.assertEqual(sp.profile_name(filament(1, "Red/Blue: dual", "PLA")), "SUNLU PLA Red Blue dual")


class PresetTest(unittest.TestCase):
    def test_fields(self):
        f = filament(7, "Black", "PETG", colour="#0a0b0c", settings_extruder_temp=235,
                     settings_bed_temp=80, density=1.27, diameter=1.75, price=20, weight=1000,
                     spool_weight=180)
        p = sp.profile_preset(f, "SUNLU PETG Black", "SUNLU PETG @System")
        self.assertEqual(p["filament_colour"], ["#0A0B0C"])
        self.assertEqual(p["filament_id"], "SPLR00007")
        self.assertEqual(p["inherits"], "SUNLU PETG @System")
        self.assertEqual(p["nozzle_temperature"], ["235"])
        self.assertEqual(p["hot_plate_temp"], ["80"])
        self.assertEqual(p["filament_density"], ["1.27"])
        self.assertEqual(p["filament_cost"], ["20"])
        self.assertEqual(p["filament_vendor"], ["SUNLU"])
        self.assertEqual(p["compatible_printers"], [])

    def test_missing_values_are_left_to_parent(self):
        p = sp.profile_preset(filament(1, "x", "PLA", colour=None), "x", "Generic PLA @System")
        self.assertNotIn("nozzle_temperature", p)
        self.assertNotIn("filament_cost", p)
        self.assertEqual(p["filament_colour"], ["#FFFFFF"])

    def test_multi_colour_uses_first(self):
        f = filament(1, "x", "PLA", colour=None, multi_color_hexes="ff0000,00ff00")
        self.assertEqual(sp.profile_colour(f), "FF0000")


class FilesTest(unittest.TestCase):
    def test_one_preset_per_stocked_filament(self):
        black = filament(1, "Black", "PETG")
        spools = [spool(1, black), spool(2, black), spool(3, filament(2, "Gone", "PLA"), archived=True),
                  spool(4, filament(3, "Black", "PETG", vendor="SUNLU"))]
        files, by_id = sp.profile_files(spools, NAMES)
        self.assertEqual(sorted(files), ["SUNLU PETG Black #3.json", "SUNLU PETG Black.json"])
        self.assertEqual(by_id, {1: "SUNLU PETG Black", 3: "SUNLU PETG Black #3"})


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        sp._orca_data_dir = lambda: self.root
        orca.reloads.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_reloads_and_skips_when_unchanged(self):
        spools = [spool(1, filament(1, "Black", "PETG"))]
        snap = {"names": NAMES, "folder": "123"}
        state = sp.profile_sync(spools, snap)
        bundle = self.root / "user" / "123" / "_local" / "spooler"
        self.assertEqual(state["count"], 1)
        self.assertEqual(state["error"], "")
        self.assertEqual(orca.reloads, ["spooler"])
        preset = json.loads((bundle / "filament" / "SUNLU PETG Black.json").read_text())
        self.assertEqual(preset["name"], "SUNLU PETG Black")
        meta = json.loads((bundle / "bundle_metadata.json").read_text())
        self.assertEqual(meta["id"], "spooler")
        sp.profile_sync(spools, snap)
        self.assertEqual(orca.reloads, ["spooler"])
        self.assertEqual(sp.profile_for_spool(spools[0]), "_local/spooler/SUNLU PETG Black")

    def test_removes_stale_presets(self):
        snap = {"names": NAMES, "folder": "123"}
        sp.profile_sync([spool(1, filament(1, "Black", "PETG"))], snap)
        sp.profile_sync([spool(2, filament(2, "White", "PLA"))], snap)
        folder = self.root / "user" / "123" / "_local" / "spooler" / "filament"
        self.assertEqual([p.name for p in folder.glob("*.json")], ["SUNLU PLA White.json"])

    def test_failed_reload_is_retried(self):
        snap = {"names": NAMES, "folder": "123"}
        spools = [spool(1, filament(1, "Black", "PETG"))]
        original = orca.host.preset.bundles.reload_local

        def refuse(bundle_id):
            raise RuntimeError("invalid preset")

        orca.host.preset.bundles.reload_local = refuse
        try:
            state = sp.profile_sync(spools, snap)
        finally:
            orca.host.preset.bundles.reload_local = original
        self.assertIn("invalid preset", state["error"])
        sp.profile_sync(spools, snap)
        self.assertEqual(orca.reloads, ["spooler"])


if __name__ == "__main__":
    unittest.main()
