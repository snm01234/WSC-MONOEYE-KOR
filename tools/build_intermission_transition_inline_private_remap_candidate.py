#!/usr/bin/env python3
"""Keep the final intermission wrapper and remap transitional BG writes inline.

The final guarded wrapper is the intended clean rearranged UI.  During entry,
the stock BG renderer rewrites 35 wrapper-owned cells over three frames before
the wrapper runs again.  This candidate hooks the two stock 0x3800 tilemap
stores and substitutes the wrapper's private entry for those exact cells while
all eight intermission anchors match.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from monoeye_rom import stock_base, update_ws_checksum, ws_header  # noqa: E402
from trace_intermission_focus_sprites import WSRAM_BYTES, WSRAM_CORE_OFFSET  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SOURCE_REPORT = PATCH / "intermission_advance_left_residue_clear_build_report.json"
TRACE_DIR = PATCH / "intermission_transition_live_trace_current/dynamic_write_states"
OUT_DIR = PATCH / "intermission_transition_inline_private_remap_candidate"
OUT_ROM = OUT_DIR / "intermission_transition_inline_private_remap_candidate.wsc"
OUT_SAVE = ROOT / "sram/intermission_transition_inline_private_remap_candidate.sav"
REPORT = OUT_DIR / "build_report.json"
ZSTD_DLL = ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll"

EXPECTED_MAIN_SHA256 = "163e8e6e4984e866b1a64d92f44765197df30c6281c92adf75acd6e552ad928a"
EXPECTED_SOURCE_REPORT_SHA256 = "d47978126327635fb906c45f1f6d935ba6e5b580c9d34084e8c03c77927c8137"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
RENDER_FINAL_CALL = 0x789C4D
WRAPPER_ROM = 0x78FCD3
PRIVATE_PAYLOAD_ROM = 0x79FA8F
PRIVATE_PAYLOAD_BYTES = 832
REMAPPER_ROM = PRIVATE_PAYLOAD_ROM + PRIVATE_PAYLOAD_BYTES
REMAPPER_BANK_END = 0x7A0000
STORE_DX = 0x78A06E
STORE_SI = 0x78A0EB
STORE_DX_EXPECTED = bytes.fromhex("26 89 97 00 38")
STORE_SI_EXPECTED = bytes.fromhex("26 89 B7 00 38")
CURRENT_WRAPPER_CALL = bytes.fromhex("9A D3 FC 00 80")


class BuildError(RuntimeError):
    pass


class Code:
    def __init__(self, origin: int):
        self.origin = origin
        self.data = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str, int]] = []

    @property
    def logical(self) -> int:
        return self.origin + len(self.data)

    def emit(self, value: bytes) -> None:
        self.data += value

    def label(self, name: str) -> None:
        if name in self.labels:
            raise BuildError(f"duplicate label: {name}")
        self.labels[name] = self.logical

    def rel8(self, opcode: int, label: str) -> None:
        self.data += bytes((opcode, 0))
        self.fixups.append((len(self.data) - 1, label, 1))

    def rel16(self, opcode: int, label: str) -> None:
        self.data += bytes((opcode, 0, 0))
        self.fixups.append((len(self.data) - 2, label, 2))

    def resolve(self) -> bytes:
        for at, label, size in self.fixups:
            target = self.labels[label]
            after = self.origin + at + size
            displacement = target - after
            if size == 1:
                if not -128 <= displacement <= 127:
                    raise BuildError(f"rel8 overflow to {label}: {displacement}")
                self.data[at] = displacement & 0xFF
            else:
                if not -0x8000 <= displacement <= 0x7FFF:
                    raise BuildError(f"rel16 overflow to {label}: {displacement}")
                self.data[at : at + 2] = (displacement & 0xFFFF).to_bytes(2, "little")
        return bytes(self.data)


def digest(value: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(value)).hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, value: bytes | None = None) -> dict:
    payload = path.read_bytes() if value is None else value
    return {"path": relative(path), "size": len(payload), "sha256": digest(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def far_call(target: int) -> bytes:
    segment = ((target >> 16) - 0x70 + 0x00) << 12
    if not 0 <= segment <= 0xFFFF:
        raise BuildError("far-call segment overflow")
    return b"\x9A" + (target & 0xFFFF).to_bytes(2, "little") + segment.to_bytes(2, "little")


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    result = []
    start = None
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = index
        elif left == right and start is not None:
            result.append((start, index))
            start = None
    if start is not None:
        result.append((start, len(before)))
    return result


def emit_remapper(report: dict) -> tuple[bytes, dict]:
    wrapper = report["renderer_wrapper"]
    anchors = wrapper["guard_anchors"]
    patches = report["static_atlas"]["tilemap_patches"]
    if len(anchors) != 8 or len(patches) != 35:
        raise BuildError("source wrapper contract drifted")

    # DX/SI entry stubs preserve the source value; common code substitutes only
    # the exact 35 wrapper-owned destinations while all eight anchors identify
    # this intermission map.  Otherwise it performs the original store exactly.
    # This code lives after the existing private payload in bank 79 so the
    # already-verified final wrapper remains byte-for-byte and cycle-for-cycle
    # unchanged.
    code = Code(REMAPPER_ROM)
    code.label("store_dx")
    code.emit(bytes.fromhex("9C 50 8B C2"))  # pushf; push ax; mov ax,dx
    code.rel8(0xEB, "store_common")
    code.label("store_si")
    code.emit(bytes.fromhex("9C 50 8B C6"))  # pushf; push ax; mov ax,si
    code.label("store_common")
    code.emit(bytes.fromhex("51 56 1E 50"))  # save cx,si,ds and input value
    for row in anchors:
        code.emit(b"\x26\x81\x3E")
        code.emit(int(row["wsram_offset"]).to_bytes(2, "little"))
        code.emit(int(row["entry"]).to_bytes(2, "little"))
        code.rel8(0x75, "guard_fail")
    code.rel8(0xEB, "search")
    code.label("guard_fail")
    code.rel16(0xE9, "store_original")
    code.label("search")
    code.emit(bytes.fromhex("0E 1F"))  # push cs; pop ds
    code.emit(b"\xBE\x00\x00")
    table_pointer_at = len(code.data) - 2
    code.emit(b"\xB9" + len(patches).to_bytes(2, "little"))
    code.label("search_loop")
    code.emit(b"\xAD\x3B\xC3")  # lodsw; cmp ax,bx
    code.rel8(0x74, "found")
    code.emit(bytes.fromhex("83 C6 02"))
    code.rel8(0xE2, "search_loop")
    code.rel8(0xEB, "store_original")
    code.label("found")
    code.emit(b"\xAD")
    code.emit(bytes.fromhex("83 C4 02"))  # discard saved input
    code.emit(bytes.fromhex("26 89 87 00 38"))  # es:[bx+3800] = mapped ax
    code.rel8(0xEB, "store_cleanup")
    code.label("store_original")
    code.emit(b"\x58")
    code.emit(bytes.fromhex("26 89 87 00 38"))  # original input store
    code.label("store_cleanup")
    code.emit(bytes.fromhex("1F 5E 59 58 9D CB"))
    code.label("remap_table")
    code.data[table_pointer_at : table_pointer_at + 2] = (
        code.labels["remap_table"] & 0xFFFF
    ).to_bytes(2, "little")
    for row in sorted(patches, key=lambda item: int(item["wsram_offset"])):
        relative_offset = int(row["wsram_offset"]) - 0x3800
        code.emit(relative_offset.to_bytes(2, "little"))
        code.emit(int(row["new_entry"]).to_bytes(2, "little"))

    blob = code.resolve()
    if REMAPPER_ROM + len(blob) > REMAPPER_BANK_END:
        raise BuildError(
            f"remapper exceeds bank: {len(blob)} > {REMAPPER_BANK_END - REMAPPER_ROM}"
        )
    metadata = {
        "bytes": len(blob),
        "start_logical": f"{REMAPPER_ROM:06X}",
        "end_logical": f"{REMAPPER_ROM + len(blob):06X}",
        "sha256": digest(blob),
        "store_dx_entry": f"{code.labels['store_dx']:06X}",
        "store_si_entry": f"{code.labels['store_si']:06X}",
        "remap_table": f"{code.labels['remap_table']:06X}",
        "remap_rows": len(patches),
        "guard_anchors": anchors,
    }
    return blob, metadata


def main() -> int:
    parent = MAIN.read_bytes()
    saveram = MAIN_SAVE.read_bytes()
    source_report_bytes = SOURCE_REPORT.read_bytes()
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("main ROM identity drifted")
    if len(saveram) != SAVE_SIZE:
        raise BuildError("main SaveRAM size drifted")
    if digest(source_report_bytes) != EXPECTED_SOURCE_REPORT_SHA256:
        raise BuildError("source wrapper report identity drifted")
    source = json.loads(source_report_bytes.decode("utf-8"))
    base = stock_base(parent)
    body = parent[base : base + 0x800000]
    old_wrapper_bytes = int(source["renderer_wrapper"]["wrapper_bytes"])
    old_wrapper = body[WRAPPER_ROM : WRAPPER_ROM + old_wrapper_bytes]
    if digest(old_wrapper) != source["renderer_wrapper"]["wrapper_sha256"]:
        raise BuildError("current wrapper bytes drifted")
    if body[RENDER_FINAL_CALL : RENDER_FINAL_CALL + 5] != CURRENT_WRAPPER_CALL:
        raise BuildError("current wrapper call drifted")
    if body[STORE_DX : STORE_DX + 5] != STORE_DX_EXPECTED:
        raise BuildError("DX tilemap store drifted")
    if body[STORE_SI : STORE_SI + 5] != STORE_SI_EXPECTED:
        raise BuildError("SI tilemap store drifted")
    payload = body[PRIVATE_PAYLOAD_ROM : PRIVATE_PAYLOAD_ROM + PRIVATE_PAYLOAD_BYTES]
    if digest(payload) != source["renderer_wrapper"]["private_payload_sha256"]:
        raise BuildError("private payload drifted")

    free_tail = body[REMAPPER_ROM:REMAPPER_BANK_END - 2]
    if not free_tail or any(byte != 0xFF for byte in free_tail):
        raise BuildError("bank 79 post-payload tail is no longer free")

    remapper, remapper_meta = emit_remapper(source)
    candidate = bytearray(parent)
    remapper_file = base + REMAPPER_ROM
    candidate[remapper_file : remapper_file + len(remapper)] = remapper
    dx_entry = int(remapper_meta["store_dx_entry"], 16)
    si_entry = int(remapper_meta["store_si_entry"], 16)
    candidate[base + STORE_DX : base + STORE_DX + 5] = far_call(dx_entry)
    candidate[base + STORE_SI : base + STORE_SI + 5] = far_call(si_entry)
    checksum = update_ws_checksum(candidate)
    output = bytes(candidate)

    # Every collected transitional state has all eight anchors, so the inline
    # remapper is active during each of the three stock redraw phases.
    zstd = Zstd(ZSTD_DLL)
    anchor_rows = []
    for state in sorted(TRACE_DIR.glob("*.State")):
        core, _ = read_state_core(state, zstd)
        ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
        matches = [
            int.from_bytes(
                ram[int(row["wsram_offset"]) : int(row["wsram_offset"]) + 2], "little"
            )
            == int(row["entry"])
            for row in source["renderer_wrapper"]["guard_anchors"]
        ]
        anchor_rows.append({"state": relative(state), "all_eight_match": all(matches)})

    runs = diff_runs(parent, output)
    allow = [
        (base + REMAPPER_ROM, base + REMAPPER_ROM + len(remapper)),
        (base + STORE_DX, base + STORE_DX + 5),
        (base + STORE_SI, base + STORE_SI + 5),
        (len(output) - 2, len(output)),
    ]
    outside = [
        (lo, hi) for lo, hi in runs if not any(a <= lo and hi <= b for a, b in allow)
    ]
    checks = {
        "current_tip_hash_bound": digest(parent) == EXPECTED_MAIN_SHA256,
        "source_wrapper_report_bound": digest(source_report_bytes) == EXPECTED_SOURCE_REPORT_SHA256,
        "existing_final_wrapper_retained": output[
            base + RENDER_FINAL_CALL : base + RENDER_FINAL_CALL + 5
        ] == CURRENT_WRAPPER_CALL,
        "existing_final_wrapper_byte_identical": output[
            base + WRAPPER_ROM : base + WRAPPER_ROM + old_wrapper_bytes
        ] == old_wrapper,
        "private_payload_byte_identical": output[
            base + PRIVATE_PAYLOAD_ROM : base + PRIVATE_PAYLOAD_ROM + PRIVATE_PAYLOAD_BYTES
        ] == payload,
        "all_transition_states_match_all_eight_guards": bool(anchor_rows)
        and all(row["all_eight_match"] for row in anchor_rows),
        "post_payload_tail_was_free": bool(free_tail) and all(byte == 0xFF for byte in free_tail),
        "remapper_fits_bank_79_tail": REMAPPER_ROM + len(remapper) <= REMAPPER_BANK_END - 2,
        "both_stock_tilemap_stores_far_hooked": output[base + STORE_DX] == 0x9A
        and output[base + STORE_SI] == 0x9A,
        "diffs_bounded_to_remapper_store_hooks_and_checksum": not outside,
        "wonder_swan_checksum_valid": int(ws_header(output)["checksum"])
        == (sum(output[:-2]) & 0xFFFF),
        "candidate_saveram_will_copy_current_snapshot": True,
        "main_tip_unchanged_by_builder": MAIN.read_bytes() == parent,
        "main_saveram_unchanged_by_builder": MAIN_SAVE.read_bytes() == saveram,
    }
    if not all(checks.values()):
        raise BuildError(f"build checks failed: {checks}, outside={outside}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_bytes(OUT_ROM, output)
    temporary_save = OUT_SAVE.with_name(f".{OUT_SAVE.name}.{os.getpid()}.tmp")
    shutil.copyfile(MAIN_SAVE, temporary_save)
    os.replace(temporary_save, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_intermission_transition_inline_private_remap_candidate.py",
        "ok": True,
        "published": False,
        "status": "static_verified_pending_quicksave5_runtime_trace",
        "diagnosis": (
            "The final private-tile wrapper is correct. The stock BG renderer rewrites "
            "its 35 cells over three entry frames before the wrapper runs again."
        ),
        "fix": (
            "Keep the final wrapper and inline-remap the two stock 0x3800 tilemap stores "
            "for the exact wrapper-owned cells while all eight intermission anchors match. "
            "Place the remapper after the private payload so the final wrapper remains "
            "byte-for-byte unchanged."
        ),
        "parent": identity(MAIN, parent),
        "main_saveram": identity(MAIN_SAVE, saveram),
        "source_report": identity(SOURCE_REPORT, source_report_bytes),
        "candidate": identity(OUT_ROM, output),
        "candidate_saveram": identity(OUT_SAVE),
        "existing_wrapper": {
            "logical": f"{WRAPPER_ROM:06X}",
            "bytes": old_wrapper_bytes,
            "sha256": digest(old_wrapper),
            "byte_identical_to_parent": output[
                base + WRAPPER_ROM : base + WRAPPER_ROM + old_wrapper_bytes
            ] == old_wrapper,
        },
        "remapper": remapper_meta,
        "store_hooks": {
            "dx": {
                "logical": f"{STORE_DX:06X}",
                "before": STORE_DX_EXPECTED.hex(),
                "after": output[base + STORE_DX : base + STORE_DX + 5].hex(),
            },
            "si": {
                "logical": f"{STORE_SI:06X}",
                "before": STORE_SI_EXPECTED.hex(),
                "after": output[base + STORE_SI : base + STORE_SI + 5].hex(),
            },
        },
        "transition_anchor_evidence": anchor_rows,
        "diff": {
            "changed_runs": len(runs),
            "changed_bytes_including_checksum": sum(hi - lo for lo, hi in runs),
            "outside_allowlist": outside,
            "checksum": f"{checksum:04X}",
        },
        "checks": checks,
        "runtime_gate": [
            "Load the unmodified pre-intermission QuickSave5 fixture.",
            "Require all 35 private map entries to remain stable from first visible frame 1848 through 2000.",
            "Require the final settled frame to be pixel-identical to the restored main wrapper output.",
            "Replay all twelve intermission focus fixtures.",
        ],
    }
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
