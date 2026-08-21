"""Derive the published, identity-free site geometry from the private build.

The public geometry is not a copy. Provider serials, raw GPS fixes, premise
identities, photo stacks and personal names never leave the private project, so
this drops those branches and scrubs first names before writing. Run it after
build_site_geometry.py; the tests check that the published file is clean.
"""

import json
import re
import sys
from pathlib import Path

PRIVATE_DEFAULT = Path(
    "/mnt/c/Users/willh/Documents/Air Aware Labs/Projects/CoventG"
    "/digital-twin-v4/site_geometry.json"
)
PUBLIC = Path(__file__).resolve().parent / "site_geometry.json"

# Whole branches that carry identity or provenance the public page never needs.
DROP_TOP = {
    "front_facade_bearing_deg", "grid_rotation_note", "height_provenance",
    "legacy_monitor", "photo_stacks", "place_labels", "premises",
    "provenance", "traffic",
}
DROP_MONITOR = {
    "placement_confidence", "raw_gps_latitude", "raw_gps_longitude",
    # The local-metre twins of those fixes are just as re-projectable, and phone
    # GPS in this courtyard was poor: units 1, 2 and 4 sit 11-26 m from their own
    # 10 August fix, and unit 2's lands on a neighbouring building entirely.
    "raw_gps_east_m", "raw_gps_north_m",
    # "position" names a raw GPS fix in the private build. Unit 2's fix is 26 m
    # from where it was installed, so publishing the key invites the reader to
    # treat it as the pod's location. The scene coordinates are the location.
    "position", "role", "serial",
}
NAMES = [
    (r"\bWill's\b", "the site owner's"), (r"\bWill\b", "the site owner"),
    (r"\bAlex's\b", "the resident's"), (r"\bAlex\b", "the resident"),
    (r"\bGrant and Nigel's\b", "the residents'"),
    (r"\bGrant\b", "the resident"), (r"\bNigel\b", "the neighbour"),
]


def scrub(value):
    if isinstance(value, str):
        for pattern, replacement in NAMES:
            value = re.sub(pattern, replacement, value)
        return value
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    return value


def main() -> int:
    private = Path(sys.argv[1]) if len(sys.argv) > 1 else PRIVATE_DEFAULT
    geometry = json.loads(private.read_text(encoding="utf-8"))
    public = {k: v for k, v in geometry.items() if k not in DROP_TOP}
    public["monitors"] = [
        {k: v for k, v in monitor.items() if k not in DROP_MONITOR}
        for monitor in public.get("monitors", [])
    ]
    public = scrub(public)

    leaked = sorted(
        name for name in ("Will", "Alex", "Grant", "Nigel")
        if re.search(rf"\b{name}\b", json.dumps(public))
    )
    if leaked:
        raise SystemExit(f"refusing to write: names still present {leaked}")

    PUBLIC.write_text(
        json.dumps(public, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {PUBLIC.name}: {len(public['monitors'])} monitors, "
          f"{len(public.get('receptor_homes', []))} receptor settings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
