"""Assemble the standalone WebGL scene.

three.js is vendored locally rather than pulled from a CDN, because the page has
to run from a file:// path with no network and the artifact host blocks external
requests outright.

Run:  python build_scene.py
"""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
TWIN = HERE.parent
PROJECT = TWIN.parent
THREE_LOCAL = HERE / "three-0.149.0.min.js"
OUT = TWIN / "coventg_evening_scene.html"


def public_safe_text(value):
    """Remove personal names that add nothing to the published evidence trail."""
    if isinstance(value, dict):
        return {key: public_safe_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_safe_text(item) for item in value]
    if not isinstance(value, str):
        return value
    value = re.sub(r"\bWill's\b", "the site owner's", value)
    value = re.sub(r"\bWill\b", "the site owner", value)
    value = re.sub(r"\bAlex's\b", "the resident's", value)
    return re.sub(r"\bAlex\b", "the resident", value)


def image_data_uri(path: Path, max_edge: int = 1400, quality: int = 82) -> str:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def main() -> None:
    if not THREE_LOCAL.exists():
        raise FileNotFoundError("The vendored three.js build is missing")

    geo = json.loads((TWIN / "site_geometry.json").read_text(encoding="utf-8"))
    traffic = json.loads(
        (PROJECT / "monitoring-2026/outputs/traffic_hourly.json").read_text(encoding="utf-8")
    )
    # The real record: what each instrument measured every five minutes,
    # with the wind that was actually blowing. Built by
    # monitoring-2026/scripts/build_observations.py.
    obs_path = HERE / "observations.json"
    obs = json.loads(obs_path.read_text(encoding="utf-8")) if obs_path.exists() else None
    current_aq_path = HERE / "airgradient_current.json"
    current_aq = (
        json.loads(current_aq_path.read_text(encoding="utf-8"))
        if current_aq_path.exists()
        else None
    )
    weather_path = HERE / "tempest_weather.json"
    weather = json.loads(weather_path.read_text(encoding="utf-8")) if weather_path.exists() else None

    # the scene only needs a subset; drop the long provenance prose to keep it lean
    slim = {
        k: geo[k]
        for k in (
            "origin", "heights", "home", "buildings", "roads", "sources",
            "weather_stations", "receptor_homes", "monitors", "monitor_states",
        )
    }
    for s in slim["sources"]:
        s.pop("position_provenance", None)
    for m in slim["monitors"]:
        m.pop("placement_confidence", None)
        m.pop("role", None)
        # Instrument identity is needed by the local registry/collector, not by
        # the browser.  Keep serials out of the standalone HTML.
        m.pop("serial", None)
    slim = public_safe_text(slim)

    html = (HERE / "shell.html").read_text(encoding="utf-8")
    html = html.replace("__GEO_JSON__", json.dumps(slim, separators=(",", ":")))
    html = html.replace("__TRAFFIC_JSON__", json.dumps(traffic, separators=(",", ":")))
    html = html.replace("__OBS_JSON__", json.dumps(obs, separators=(",", ":")))
    html = html.replace(
        "__CURRENT_AQ_JSON__", json.dumps(current_aq, separators=(",", ":"))
    )
    html = html.replace("__WEATHER_JSON__", json.dumps(weather, separators=(",", ":")))
    lid = HERE / "lidar_field.json"
    html = html.replace("__LIDAR_JSON__",
                        lid.read_text(encoding="utf-8") if lid.exists() else "null")
    photos = [
        {
            "short": "Weather station",
            "title": "Tempest weather station installed 12 August 2026",
            "caption": (
                "Supplied installation photograph showing the complete sensor, open-sky "
                "exposure, mast, bracket and nearby roof context. The site owner confirmed the head "
                "is approximately five feet above the terrace on 13 August 2026. The "
                "universal mount is now shown projecting from the western wall section "
                "between the greenhouse and south wall, north of and aligned with unit 1."
            ),
            "data_uri": image_data_uri(
                PROJECT / "Pics" / "WhatsApp Image 2026-08-12 at 16.48.05.jpeg"
            ),
        },
        {
            "short": "Installed · 1 + 4",
            "title": "Units 1 and 4 installed together beside Tempest",
            "caption": (
                "Supplied 19 August installation photograph. The hand-labelled units 1 "
                "and 4 share the timber cross-rail beside the Tempest weather station. "
                "Their retained pairing provides a continuous on-site comparability check during deployment."
            ),
            "data_uri": image_data_uri(
                PROJECT / "Pics" / "WhatsApp Image 2026-08-19 at 15.08.45 (9).jpeg"
            ),
        },
        {
            "short": "Installed · unit 2",
            "title": "Unit 2 installed at 81a County Street",
            "caption": (
                "Supplied 19 August installation photograph. Unit 2 is fixed to the brown "
                "bamboo screen at the farther 81a plot, beyond the intervening house. The "
                "screen stands on the ridge of a pitched roof whose panels are fixed flush "
                "to it, so the array carries the roof's own angle. The raised garden is behind, "
                "and the run stops at the light-brick gabled house on its left. That house now "
                "meets the adjoining western house and has a simple two-window rear facade; the "
                "window positions, plan position and height remain approximate."
            ),
            "data_uri": image_data_uri(
                PROJECT / "Pics" / "WhatsApp Image 2026-08-19 at 15.08.45 (6).jpeg"
            ),
        },
        {
            "short": "Installed · unit 3",
            "title": "Unit 3 installed on the communal access gallery",
            "caption": (
                "Supplied 19 August installation photograph. Unit 3 is cable-tied to the "
                "east-facing metal safety rail of the communal deck-access gallery, a single "
                "north-south run south of the adjacent private home. The site owner's exact 21 August "
                "GPS fix places the rail there, south of the private terrace and not on it. The "
                "gallery is level, with rails rather than steps."
            ),
            "data_uri": image_data_uri(
                PROJECT / "Pics" / "WhatsApp Image 2026-08-19 at 15.08.45 (12).jpeg"
            ),
        },
        {
            "short": "Intervening neighbour",
            "title": "The intervening house east of 81a",
            "caption": (
                "Supplied 19 August site photograph used with OSM and LiDAR to rebuild "
                "the pale-render and light-brick house between 74–75 and the farther 81a "
                "roof garden. It partly hides unit 2 from the home site and must not be "
                "read as the instrumented 81a property. This is a visual reconstruction, "
                "not photogrammetry or an architectural survey."
            ),
            "data_uri": image_data_uri(
                PROJECT / "Pics" / "WhatsApp Image 2026-08-19 at 15.08.43.jpeg"
            ),
        },
        {
            "short": "Roofscape · 9 Aug",
            "title": "Rear roofscape photographed 9 August 2026",
            "caption": (
                "Supplied site photograph. This is the visual reference for roof form, "
                "brickwork, windows and the observed outlets; the LIDAR layer is context massing."
            ),
            "data_uri": image_data_uri(PROJECT / "Pics" / "WhatsApp Image 2026-08-09 at 17.52.54 (14).jpeg"),
        },
        {
            "short": "Visible plume",
            "title": "Documented visible plume in the supplied record",
            "caption": (
                "Supplied evidence photograph. The scene draws visible material only for "
                "173 because this is the outlet for which visible-plume evidence is held."
            ),
            "data_uri": image_data_uri(PROJECT / "Pics" / "IMG_1228.jpeg"),
        },
        {
            "short": "Western outlet",
            "title": "Western stainless-steel outlet in the supplied roofscape record",
            "caption": (
                "Supplied site photograph (WhatsApp 17.52.54 (4)). It documents the "
                "outlet form and surrounding roof/windows clearly. The current scene "
                "keeps the point provisional because this photograph alone does not "
                "survey its coordinates, height or premise connection."
            ),
            "data_uri": image_data_uri(
                PROJECT / "Pics" / "WhatsApp Image 2026-08-09 at 17.52.54 (4).jpeg"
            ),
        },
        {
            "short": "Co-location",
            "title": "Four-unit outdoor co-location on the south-facing balustrade",
            "caption": (
                "Supplied 9 August photograph showing the four outdoor units side-by-side "
                "beside the rear/south-facing outlook, with their inlets at approximately "
                "five feet. From the right-hand end of the primary terrace view the order "
                "is 1, 2, 3, 4. The row is constrained to the south-wing footprint so all "
                "four remain visible. Unit 5 remained indoors; spacing is still illustrative."
            ),
            "data_uri": image_data_uri(
                PROJECT / "Pics" / "WhatsApp Image 2026-08-09 at 17.52.52.jpeg"
            ),
        },
        {
            "short": "Side passage",
            "title": "Side passage and courtyard towards County Street",
            "caption": (
                "Supplied elevated view of the gap/courtyard between the properties. "
                "It remains useful architectural and source-receptor context; the final "
                "outdoor deployment no longer places a unit in this courtyard."
            ),
            "data_uri": image_data_uri(
                PROJECT / "Pics" / "WhatsApp Image 2026-08-09 at 17.52.53.jpeg"
            ),
        },
        {
            "short": "House context",
            "title": "Brick, slate, windows and courtyard context",
            "caption": (
                "Supplied site photograph used to guide the hybrid house rendering. "
                "The procedural brickwork/windows communicate form; they are not a "
                "photogrammetric facade survey."
            ),
            "data_uri": image_data_uri(
                PROJECT / "Pics" / "WhatsApp Image 2026-08-09 at 17.52.53 (5).jpeg"
            ),
        },
        {
            "short": "Greenhouse",
            "title": "Roof greenhouse and planted terrace",
            "caption": (
                "Supplied terrace photograph. The scene now uses the smaller domestic "
                "greenhouse footprint confirmed on 13 August 2026 and places it slightly "
                "behind the monitor and Tempest line. The two former large planting blobs "
                "over that instrument line have been removed."
            ),
            "data_uri": image_data_uri(
                PROJECT / "Pics" / "WhatsApp Image 2026-08-09 at 17.52.54 (9).jpeg"
            ),
        },
    ]
    html = html.replace(
        "__PHOTO_JSON__", json.dumps(public_safe_text(photos), separators=(",", ":"))
    )
    html = html.replace("__THREE__", THREE_LOCAL.read_text(encoding="utf-8"))
    html = html.replace("__SCENE__", (HERE / "scene.js").read_text(encoding="utf-8"))

    OUT.write_text(html, encoding="utf-8")
    kb = len(html.encode()) / 1024
    print(f"Wrote {OUT.name}  {kb:,.0f} KB")
    if lid.exists():
        import json as _j
        L = _j.loads(lid.read_text(encoding="utf-8"))
        print(f"  LIDAR nDSM {L['cols']}x{L['rows']} m at {L['cell_m']} m")
    print(f"  {len(slim['buildings'])} buildings, {len(slim['sources'])} sources, "
          f"{len(slim['monitors'])} monitors, {len(slim['weather_stations'])} weather station, "
          f"{len(slim['roads'])} roads")
    print(f"  traffic: {traffic['aadf_2025']:,} AADF, "
          f"{len(traffic['counted_hours'])} counted hours from {traffic['counted_day']}")


if __name__ == "__main__":
    main()
