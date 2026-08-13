#!/usr/bin/env python3
"""Refresh observed weather/AQ and rebuild the standalone digital twin.

The command is deliberately manual. It always refreshes Tempest unless run with
``--offline``. Once ``.airgradient.env`` exists, the default online refresh also
syncs/exports AirGradient. ``--airgradient`` remains as an explicit force and
compatibility option; ``--tempest-only`` skips the AirGradient API. Credentials
remain in their ignored local dotenv files and are never embedded in the twin.

Run from monitoring-2026:

    python scripts/update_tempest_twin.py

Use ``--offline`` to rebuild from the existing exported CSV files without
contacting either provider.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
TWIN_SCENE = ROOT.parent / "digital-twin-v4" / "scene"
AIRGRADIENT_ENV = ROOT / ".airgradient.env"


def run(*arguments: str, cwd: Path = ROOT) -> None:
    subprocess.run([sys.executable, *arguments], cwd=cwd, check=True)


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use existing CSV files instead of syncing either API first.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--airgradient",
        action="store_true",
        help=(
            "Force AirGradient sync/export even if its local config file is absent. "
            "Normally it is included automatically once configured."
        ),
    )
    mode.add_argument(
        "--tempest-only",
        action="store_true",
        help="Skip AirGradient sync/export for this online refresh.",
    )
    arguments = parser.parse_args(argv)

    airgradient_configured = AIRGRADIENT_ENV.is_file()
    include_airgradient = (
        not arguments.tempest_only
        and (arguments.airgradient or airgradient_configured)
    )

    if not arguments.offline:
        run("-m", "tempest", "sync")
        run("-m", "tempest", "export")
        if include_airgradient:
            run("-m", "airgradient", "sync")
            run("-m", "airgradient", "export")
        elif arguments.tempest_only:
            print("AirGradient API: skipped by --tempest-only")
        else:
            print(
                "AirGradient API: not configured; skipping sync/export "
                "(.airgradient.env is absent)"
            )
    else:
        print("Offline rebuild: skipped both Tempest and AirGradient API calls")
    run(str(ROOT / "scripts" / "build_airgradient_observations.py"))
    run(str(ROOT / "scripts" / "build_tempest_weather.py"))
    run(str(TWIN_SCENE / "build_scene.py"), cwd=TWIN_SCENE)
    print(f"Updated standalone twin: {TWIN_SCENE.parent / 'coventg_evening_scene.html'}")


if __name__ == "__main__":
    main()
