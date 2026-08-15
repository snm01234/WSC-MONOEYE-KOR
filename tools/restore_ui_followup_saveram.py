#!/usr/bin/env python3
"""Deprecated no-op: live SaveRAM must never be restored by a pinned hash.

``sram/monoeye_ko_expanded.sav`` is the user's mutable validation SaveRAM.
Its current contents are always treated as the latest source of truth.  Test ROM
builders copy that live file to the matching test-ROM stem immediately before a
run.  This old recovery command remains only so stale notes or shell history do
not silently restore an obsolete backup.
"""
from __future__ import annotations


def main() -> int:
    print(
        "SaveRAM restore skipped: monoeye_ko_expanded.sav is mutable live test data; "
        "the current file is authoritative."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
