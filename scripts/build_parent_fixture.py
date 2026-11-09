"""Build tests/fixtures/orca_parents.json from an OrcaSlicer checkout (resolving inherits).

    python scripts/build_parent_fixture.py <OrcaSlicer checkout> tests/fixtures/orca_parents.json
"""
import glob
import json
import os
import sys

root, out = sys.argv[1], sys.argv[2]
presets = {}
for path in glob.glob(os.path.join(root, "resources/profiles/*/filament/**/*.json"), recursive=True):
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        continue
    if d.get("name"):
        presets[d["name"]] = d
def get(name, key, depth=0):
    d = presets.get(name)
    if d is None or depth > 20:
        return None
    return d[key] if key in d else get(d.get("inherits", ""), key, depth + 1)
targets = {"Snapmaker U1 (0.4 nozzle)", "Sovol SV06 0.4 nozzle"}
rows = []
for name, d in sorted(presets.items()):
    if d.get("instantiation") != "true":
        continue
    printers = get(name, "compatible_printers") or []
    cond = get(name, "compatible_printers_condition") or ""
    if printers and not (set(printers) & targets):
        continue
    if not printers and cond:
        continue
    vendor = (get(name, "filament_vendor") or [""])[0]
    ftype = (get(name, "filament_type") or [""])[0]
    rows.append({"name": name, "vendor": vendor, "type": ftype,
                 "printers": sorted(set(printers) & targets), "condition": cond})
json.dump(rows, open(out, "w"), indent=0, ensure_ascii=False)
print(len(presets), "presets,", len(rows), "candidates for", targets)
