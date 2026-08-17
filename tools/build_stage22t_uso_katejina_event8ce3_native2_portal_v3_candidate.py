#!/usr/bin/env python3
"""Build the STAGE22t v3 candidate using a globally-unowned 2-byte portal.

v2 proved the runtime design and nested-native-only helper are correct, but its
portal E5 1B collides with two reachable native dictionary phrases.  v3 keeps
all successful v2 mechanics and changes only the portal identity to E5 1D,
which must pass a fresh union ownership scan inside the shared builder.
"""
from __future__ import annotations

from pathlib import Path

import build_stage22t_uso_katejina_event8ce3_native2_portal_candidate as base

MAGIC = bytes.fromhex("E51D")

base.MAGIC2 = MAGIC
base.TARGET_AFTER = bytes.fromhex("173418F191E51D")
base.OUT_ROM = base.PATCH / "stage22t_uso_katejina_event8ce3_native2_portal_v3_candidate.wsc"
base.OUT_SAVE = base.ROOT / "sram/stage22t_uso_katejina_event8ce3_native2_portal_v3_candidate.sav"
base.OUT_REPORT = base.PATCH / "stage22t_uso_katejina_event8ce3_native2_portal_v3_report.json"


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
