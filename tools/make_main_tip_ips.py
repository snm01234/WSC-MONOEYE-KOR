#!/usr/bin/env python3
"""Build an IPS that turns the clean 8 MiB original into the current 16 MiB main TIP.

WonderSwan expansion in this project *prepends* 8 MiB, so a naive same-layout IPS
cannot be applied to the retail 8 MiB image.  This encoder instead diffs against:

    baseline = original_8MiB + (8 MiB of 0x00)

Applying that IPS to the 8 MiB original works because records:

1. Overwrite ``[0, 8MiB)`` with the expanded-bank half of the main TIP.
2. Extend the file and fill ``[8MiB, 16MiB)`` with the patched stock half.

A Lunar IPS-style 3-byte truncate trailer (``0x1000000``) is appended after ``EOF``
so patchers that honour it force the output size to 16 MiB.

Verify with ``tools/apply_main_tip_ips.py`` (applies directly to the 8 MiB ROM).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import ROM_SIZE, ROM_SIZE_16MB, find_rom  # noqa: E402

DEFAULT_TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT_DIR = ROOT / "out/dist"
DEFAULT_NAME = "monoeye_ko_expanded"
IPS_FORBIDDEN_OFFSET = 0x454F46  # ASCII "EOF"


class IpsError(RuntimeError):
    pass


def sha256_bytes(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha256_bytes(payload),
    }


def build_direct_baseline(original_8mb: bytes) -> bytes:
    """Baseline that matches '8 MiB ROM + zero-filled extension' apply semantics."""
    if len(original_8mb) != ROM_SIZE:
        raise IpsError(f"original must be 8 MiB, got {len(original_8mb)}")
    return bytes(original_8mb) + (b"\x00" * ROM_SIZE)


def iter_changed_runs(original: bytes, patched: bytes) -> Iterable[tuple[int, bytes]]:
    if len(original) != len(patched):
        raise IpsError("IPS encoding requires equal ROM sizes")
    index = 0
    size = len(original)
    while index < size:
        if original[index] == patched[index]:
            index += 1
            continue
        start = index
        while index < size and original[index] != patched[index]:
            index += 1
            if index - start >= 0xFFFF:
                break
        yield start, bytes(patched[start:index])


def _write_ips_record(handle, offset: int, data: bytes) -> tuple[int, int, int]:
    """Write one IPS record; returns (records, changed_bytes, rle_records)."""
    if not data:
        return 0, 0, 0
    if offset > 0xFFFFFF:
        raise IpsError(f"IPS offset too large: {offset:#x}")
    if offset == IPS_FORBIDDEN_OFFSET:
        raise IpsError("refusing IPS record whose offset is ASCII EOF (0x454F46)")
    if len(data) >= 3 and len(set(data)) == 1:
        handle.write(struct.pack(">I", offset)[1:])
        handle.write(b"\x00\x00")
        handle.write(struct.pack(">H", len(data)))
        handle.write(data[:1])
        return 1, len(data), 1
    handle.write(struct.pack(">I", offset)[1:])
    handle.write(struct.pack(">H", len(data)))
    handle.write(data)
    return 1, len(data), 0


def _avoid_eof_record_offset(offset: int, data: bytes, patched: bytes) -> tuple[int, bytes]:
    """Lunar IPS treats offset 0x454F46 as the EOF trailer; never start a record there.

    If a run would begin at that address, prepend the previous patched byte so the
    record offset is 0x454F45 and the forbidden byte is still rewritten.
    """
    if not data or offset != IPS_FORBIDDEN_OFFSET:
        return offset, data
    if offset == 0:
        raise IpsError("cannot encode change at IPS-forbidden offset 0")
    return offset - 1, bytes([patched[offset - 1]]) + data


def _emit_run(
    handle,
    offset: int,
    data: bytes,
    baseline: bytes,
    patched: bytes,
) -> tuple[int, int, int]:
    del baseline  # baseline is only used by callers for diffing; keep signature stable
    records = changed = rle = 0
    if not data:
        return records, changed, rle

    offset, data = _avoid_eof_record_offset(offset, data, patched)

    # Chunk at 0xFFFF, and never let a subsequent chunk begin at 0x454F46.
    pos = 0
    while pos < len(data):
        chunk_off = offset + pos
        remaining = len(data) - pos
        take = min(remaining, 0xFFFF)
        next_off = chunk_off + take
        if next_off == IPS_FORBIDDEN_OFFSET and pos + take < len(data):
            # Absorb the forbidden-start byte into this chunk when possible.
            if take < 0xFFFF:
                take += 1
            else:
                take -= 1  # leave at least the previous byte for the next chunk
        if chunk_off == IPS_FORBIDDEN_OFFSET:
            chunk_off, chunk = _avoid_eof_record_offset(
                chunk_off, data[pos : pos + take], patched
            )
            # Prepend shifts start back; only consume the original `take` bytes from `data`.
            written, payload, rle_n = _write_ips_record(handle, chunk_off, chunk)
            records += written
            changed += payload
            rle += rle_n
            pos += take
            continue
        chunk = data[pos : pos + take]
        written, payload, rle_n = _write_ips_record(handle, chunk_off, chunk)
        records += written
        changed += payload
        rle += rle_n
        pos += take

    return records, changed, rle


def write_ips(
    baseline: bytes,
    patched: bytes,
    out_path: Path,
    *,
    truncate_size: int | None = ROM_SIZE_16MB,
) -> dict[str, Any]:
    """Encode IPS with optional RLE and optional Lunar-style truncate trailer."""
    records = changed = rle_records = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as handle:
        handle.write(b"PATCH")
        for offset, data in iter_changed_runs(baseline, patched):
            written, payload, rle_n = _emit_run(handle, offset, data, baseline, patched)
            records += written
            changed += payload
            rle_records += rle_n
        handle.write(b"EOF")
        if truncate_size is not None:
            if not 0 < truncate_size <= 0xFFFFFF:
                # 16 MiB is 0x1000000 which needs 24-bit... 0x1000000 == 16777216
                # 24-bit max is 0xFFFFFF (16MiB-1). Lunar truncate is 3 bytes so max
                # expressible size is 16MiB-1. For exact 16MiB, rely on extending writes
                # instead of truncate, OR write 0x1000000 truncated to 3 bytes which
                # becomes 0x000000 (wrong!).
                #
                # Practical approach used by many 16MB patches: omit truncate and let
                # writes past EOF grow the file to the highest touched offset+1.
                # Highest offset we write is tip end-1 = 0xFFFFFF, so length becomes
                # 0x1000000 automatically when the last byte is written.
                if truncate_size == ROM_SIZE_16MB:
                    truncate_size = None
                elif truncate_size > 0xFFFFFF:
                    raise IpsError(f"truncate size exceeds 24-bit IPS limit: {truncate_size:#x}")
            if truncate_size is not None:
                handle.write(struct.pack(">I", truncate_size)[1:])
    return {
        "path": str(out_path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": out_path.stat().st_size,
        "sha256": sha256_file(out_path),
        "records": records,
        "rle_records": rle_records,
        "changed_bytes": changed,
        "truncate_size": truncate_size,
        "grows_from_8mib_via_high_offset_writes": True,
    }


def apply_ips(base: bytes | bytearray, ips_data: bytes) -> bytearray:
    """Apply PATCH…EOF[+optional truncate] IPS, extending the image with 0x00 as needed."""
    if not ips_data.startswith(b"PATCH"):
        raise IpsError("IPS missing PATCH header")
    # Trailer may be EOF or EOF + 3-byte truncate.
    eof_at = ips_data.rfind(b"EOF")
    if eof_at < 5:
        raise IpsError("IPS missing EOF trailer")
    body = ips_data[5:eof_at]
    trailer = ips_data[eof_at + 3 :]
    truncate_size: int | None = None
    if len(trailer) == 3:
        truncate_size = int.from_bytes(trailer, "big")
    elif len(trailer) != 0:
        raise IpsError(f"unexpected IPS trailer length: {len(trailer)}")

    output = bytearray(base)

    def ensure_size(size: int) -> None:
        if size > len(output):
            output.extend(b"\x00" * (size - len(output)))

    cursor = 0
    while cursor < len(body):
        if cursor + 5 > len(body):
            raise IpsError("truncated IPS record header")
        offset = int.from_bytes(body[cursor : cursor + 3], "big")
        length = int.from_bytes(body[cursor + 3 : cursor + 5], "big")
        cursor += 5
        if length == 0:
            if cursor + 3 > len(body):
                raise IpsError("truncated IPS RLE record")
            rle_length = int.from_bytes(body[cursor : cursor + 2], "big")
            value = body[cursor + 2]
            cursor += 3
            end = offset + rle_length
            ensure_size(end)
            output[offset:end] = bytes([value]) * rle_length
        else:
            end = cursor + length
            if end > len(body):
                raise IpsError("truncated IPS payload")
            ensure_size(offset + length)
            output[offset : offset + length] = body[cursor:end]
            cursor = end

    if truncate_size is not None:
        if truncate_size < len(output):
            del output[truncate_size:]
        else:
            ensure_size(truncate_size)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, default=None, help="Clean 8 MiB .wsc")
    parser.add_argument("--tip", type=Path, default=DEFAULT_TIP, help="Current main TIP")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--name", type=str, default=DEFAULT_NAME)
    parser.add_argument(
        "--skip-roundtrip",
        action="store_true",
        help="Do not re-apply the IPS for SHA verification",
    )
    args = parser.parse_args()

    original_path = args.original or find_rom(ROOT)
    tip_path = args.tip
    if not original_path.is_file():
        raise SystemExit(f"original ROM missing: {original_path}")
    if not tip_path.is_file():
        raise SystemExit(f"main TIP missing: {tip_path}")

    original = original_path.read_bytes()
    tip = tip_path.read_bytes()
    if len(original) != ROM_SIZE:
        raise SystemExit(f"original size must be {ROM_SIZE}, got {len(original)}")
    if len(tip) != ROM_SIZE_16MB:
        raise SystemExit(f"main TIP size must be {ROM_SIZE_16MB}, got {len(tip)}")

    baseline = build_direct_baseline(original)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    ips_path = args.out_dir / f"{args.name}.ips"
    ips_info = write_ips(baseline, tip, ips_path)

    roundtrip_ok = None
    if not args.skip_roundtrip:
        rebuilt = apply_ips(original, ips_path.read_bytes())
        if len(rebuilt) < ROM_SIZE_16MB:
            # Ensure final size if last bytes matched baseline zeros and were omitted.
            rebuilt.extend(b"\x00" * (ROM_SIZE_16MB - len(rebuilt)))
        roundtrip_ok = bytes(rebuilt) == tip
        if not roundtrip_ok:
            # Diagnose first mismatch for debugging.
            for index, (a, b) in enumerate(zip(rebuilt, tip)):
                if a != b:
                    raise SystemExit(
                        f"IPS round-trip mismatch at {index:#x}: "
                        f"got {a:02X} expected {b:02X} (out_len={len(rebuilt)})"
                    )
            raise SystemExit(
                f"IPS round-trip size mismatch: got {len(rebuilt)} expected {len(tip)}"
            )

    meta = {
        "schema_version": 1,
        "generated_by": "tools/make_main_tip_ips.py",
        "name": args.name,
        "method": "direct_ips_from_8mib_original_with_file_growth",
        "original": identity(original_path, original),
        "main_tip": identity(tip_path, tip),
        "encode_baseline": {
            "size": len(baseline),
            "sha256": sha256_bytes(baseline),
            "note": "original 8 MiB + 8 MiB 0x00; matches patcher growth semantics",
        },
        "ips": ips_info,
        "roundtrip_matches_main_tip": roundtrip_ok,
        "apply": [
            "Keep a clean backup of the original 8 MiB ROM.",
            f"Verify original SHA-256 == {sha256_bytes(original)}",
            "Apply the IPS directly to the 8 MiB original with Floating IPS / Lunar IPS "
            "(the patch grows the image to 16 MiB), or:",
            "python tools/apply_main_tip_ips.py --original <original.wsc> "
            f"--ips {ips_info['path']} --out <output.wsc>",
            f"Expected output SHA-256 == {sha256_bytes(tip)}",
        ],
    }
    meta_path = args.out_dir / f"{args.name}_ips.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    readme_path = args.out_dir / f"{args.name}_IPS_README.md"
    readme_path.write_text(
        "\n".join(
            [
                f"# {args.name} IPS",
                "",
                "원본 **8 MiB** WonderSwan ROM에 그대로 적용하면 **16 MiB** 메인 TIP이 됩니다.",
                "",
                "**배포에는 IPS를 쓰지 마세요.** 프리펜드 확장 때문에 패치된 스톡 ROM",
                "(원본 데이터 거의 전부)이 IPS 안에 들어갑니다. 배포 포맷은",
                "`tools/make_main_tip_xdelta.py` → `out/dist/monoeye_ko_expanded.xdelta` 입니다.",
                "",
                "IPS는 파일 중간에 8 MiB를 ‘삽입’할 수 없습니다. 대신",
                "`[0, 8MiB)`에는 확장 뱅크를 덮어쓰고, `[8MiB, 16MiB)`에는 패치된 스톡 반쪽을",
                "써서 파일을 늘리는 방식으로 프리펜드 확장을 표현합니다.",
                "",
                "## 입력",
                "",
                f"- 원본: `{original_path.name}` · SHA-256 `{sha256_bytes(original)}`",
                f"- 메인 TIP: `{tip_path.name}` · SHA-256 `{sha256_bytes(tip)}`",
                "",
                "## 패치",
                "",
                f"- 파일: `{ips_path.name}`",
                f"- IPS SHA-256: `{ips_info['sha256']}`",
                f"- 변경 바이트: **{ips_info['changed_bytes']}** / 레코드 **{ips_info['records']}**"
                f" (RLE {ips_info['rle_records']})",
                f"- 8 MiB→16 MiB 라운드트립: **{roundtrip_ok}**",
                "",
                "## 적용",
                "",
                "### GUI (Floating IPS / Lunar IPS 등)",
                "",
                "1. 원본 8 MiB ROM 백업",
                f"2. `{ips_path.name}`를 원본에 적용 (출력은 16 MiB)",
                f"3. 결과 SHA-256이 `{sha256_bytes(tip)}`인지 확인",
                "",
                "### CLI",
                "",
                "```bash",
                f"python tools/apply_main_tip_ips.py --original \"{original_path.name}\" "
                f"--ips out/dist/{ips_path.name} --out out/dist/monoeye_ko_from_ips.wsc",
                "```",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"Wrote {ips_path}")
    print(f"Wrote {meta_path}")
    print(f"Wrote {readme_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
