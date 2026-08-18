#!/usr/bin/env python3
"""Build an xdelta3 (VCDIFF) patch: clean 8 MiB original → current 16 MiB main TIP.

Unlike IPS, VCDIFF can COPY from the source ROM. The stock 8 MiB half is therefore
not embedded in the patch file. That is the distribution format for this prepend
expansion (IPS would re-ship almost the entire original image).

Requires xdelta3 (pinned Windows 3.2.0 is fetched into ``tools/vendor``).
Verify with ``tools/apply_main_tip_xdelta.py``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import ROM_SIZE, ROM_SIZE_16MB, find_rom  # noqa: E402
from xdelta3_tool import (  # noqa: E402
    DEFAULT_APP_HEADER,
    XdeltaError,
    decode_xdelta,
    encode_xdelta,
    identity,
    print_delta_info,
    resolve_xdelta3,
    sha256_bytes,
    sha256_file,
)

DEFAULT_TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT_DIR = ROOT / "out/dist"
DEFAULT_NAME = "monoeye_ko_expanded_v1.3"
DEFAULT_IPS = ROOT / "out/dist/monoeye_ko_expanded.ips"
# Fail closed if the encoder forgot ``-s`` and compressed the whole TIP.
MAX_PATCH_BYTES = 2 * 1024 * 1024
RELEASE_VERSION = "1.3"
RELEASE_TYPE = "release"
RELEASE_BASE_VERSION = "1.2"


def _rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, default=None, help="Clean 8 MiB .wsc")
    parser.add_argument("--tip", type=Path, default=DEFAULT_TIP, help="Current main TIP")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--name", type=str, default=DEFAULT_NAME)
    parser.add_argument("--xdelta3", type=Path, default=None)
    parser.add_argument(
        "--armor",
        action="store_true",
        help="Keep xdelta 3.2 BLAKE3 armor (breaks older Delta Patcher builds)",
    )
    parser.add_argument(
        "--skip-roundtrip",
        action="store_true",
        help="Do not re-apply the patch for SHA verification",
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

    try:
        xdelta3 = resolve_xdelta3(args.xdelta3)
    except XdeltaError as exc:
        raise SystemExit(str(exc)) from exc

    args.out_dir.mkdir(parents=True, exist_ok=True)
    patch_path = args.out_dir / f"{args.name}.xdelta"
    try:
        encode_xdelta(
            xdelta3,
            original_path,
            tip_path,
            patch_path,
            armor=args.armor,
        )
    except XdeltaError as exc:
        raise SystemExit(str(exc)) from exc

    patch_prefix = patch_path.read_bytes()[:5]
    if len(patch_prefix) < 5 or patch_prefix[:4] != bytes.fromhex("D6C3C400"):
        raise SystemExit(f"invalid VCDIFF header in {patch_path}")
    vcd_header_indicator = patch_prefix[4]
    if vcd_header_indicator & 0x01:
        raise SystemExit(
            "xdelta compatibility regression: VCD_SECONDARY is set; "
            "xdeltaUI/older xdelta3 decoders may reject this patch"
        )
    if vcd_header_indicator & 0x04:
        raise SystemExit(
            "xdelta compatibility regression: VCD_APPHEADER is set; older xdeltaUI "
            "may misread filename metadata as an external-compression ID"
        )

    patch_size = patch_path.stat().st_size
    if patch_size > MAX_PATCH_BYTES:
        raise SystemExit(
            f"xdelta unexpectedly large ({patch_size} bytes); refuse to keep "
            f"{patch_path} (source-copy may have failed)"
        )

    hdr = ""
    try:
        hdr = print_delta_info(xdelta3, patch_path)
    except XdeltaError:
        hdr = ""

    roundtrip_ok = None
    if not args.skip_roundtrip:
        handle = tempfile.NamedTemporaryFile(prefix="monoeye_xdelta_rt_", suffix=".wsc", delete=False)
        rebuilt_path = Path(handle.name)
        handle.close()
        try:
            decode_xdelta(xdelta3, original_path, patch_path, rebuilt_path)
            rebuilt = rebuilt_path.read_bytes()
            roundtrip_ok = rebuilt == tip
            if not roundtrip_ok:
                if len(rebuilt) != len(tip):
                    raise SystemExit(
                        f"xdelta round-trip size mismatch: got {len(rebuilt)} expected {len(tip)}"
                    )
                for index, (left, right) in enumerate(zip(rebuilt, tip)):
                    if left != right:
                        raise SystemExit(
                            f"xdelta round-trip mismatch at {index:#x}: "
                            f"got {left:02X} expected {right:02X}"
                        )
                raise SystemExit("xdelta round-trip mismatch")
        except XdeltaError as exc:
            raise SystemExit(str(exc)) from exc
        finally:
            try:
                os.unlink(rebuilt_path)
            except OSError:
                pass

    comparison: dict[str, Any] | None = None
    if DEFAULT_IPS.is_file():
        ips_size = DEFAULT_IPS.stat().st_size
        comparison = {
            "ips_path": _rel(DEFAULT_IPS),
            "ips_size": ips_size,
            "xdelta_size": patch_size,
            "saved_bytes": ips_size - patch_size,
            "xdelta_over_ips": round(patch_size / ips_size, 6) if ips_size else None,
            "note": (
                "IPS prepend encoding embeds the patched stock ROM; "
                "xdelta COPYs the original and does not."
            ),
        }

    original_sha = sha256_bytes(original)
    tip_sha = sha256_bytes(tip)
    patch_sha = sha256_file(patch_path)
    meta = {
        "schema_version": 1,
        "generated_by": "tools/make_main_tip_xdelta.py",
        "name": args.name,
        "release": {
            "version": RELEASE_VERSION,
            "type": RELEASE_TYPE,
            "base_version": RELEASE_BASE_VERSION,
        },
        "method": "xdelta3_vcdiff_from_8mib_original_to_16mib_tip",
        "xdelta3": {
            "path": _rel(xdelta3) if xdelta3.resolve().is_relative_to(ROOT.resolve()) else str(xdelta3),
            "armor": args.armor,
            "app_header": DEFAULT_APP_HEADER,
            "secondary": "disabled",
            "vcd_header_indicator": f"0x{vcd_header_indicator:02X}",
            "compatibility": "plain VCDIFF: no secondary compression and no application header; xdeltaUI/older xdelta3 friendly",
            "level": 9,
            "source_window": 8 * 1024 * 1024,
            "target_window": 16 * 1024 * 1024,
            "legacy_app_header": False,
        },
        "original": identity(original_path, original),
        "main_tip": identity(tip_path, tip),
        "xdelta": {
            "path": _rel(patch_path),
            "size": patch_size,
            "sha256": patch_sha,
        },
        "embeds_original_rom": False,
        "roundtrip_matches_main_tip": roundtrip_ok,
        "comparison_with_ips": comparison,
        "printhdrs": hdr.strip() or None,
        "apply": [
            "Use only a legally owned Japanese original 8 MiB ROM, and keep a clean backup.",
            f"Verify original SHA-256 == {original_sha}",
            "Apply with Delta Patcher (xdelta3) or:",
            "python tools/apply_main_tip_xdelta.py --original <original.wsc> "
            f"--xdelta {_rel(patch_path)} --out <output.wsc>",
            f"Expected output SHA-256 == {tip_sha}",
        ],
    }
    meta_path = args.out_dir / f"{args.name}_xdelta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ips_line = ""
    if comparison:
        ips_mib = comparison["ips_size"] / (1024 * 1024)
        xd_mib = comparison["xdelta_size"] / (1024 * 1024)
        ips_line = (
            f"- IPS 대비: `{comparison['ips_size']}` bytes ({ips_mib:.2f} MiB) → "
            f"`{comparison['xdelta_size']}` bytes ({xd_mib:.2f} MiB), "
            f"{comparison['saved_bytes']} bytes 감소"
        )

    readme_path = args.out_dir / f"{args.name}_XDELTA_README.md"
    readme_path.write_text(
        "\n".join(
            [
                f"# {args.name} xdelta",
                "",
                f"- 릴리스: **v{RELEASE_VERSION} ({RELEASE_TYPE})**",
                f"- 기준 버전: **v{RELEASE_BASE_VERSION}**",
                "",
                "**합법적으로 소유한 일본판 원본 8 MiB WonderSwan ROM**에 적용하면 **16 MiB** 메인 TIP이 됩니다.",
                "",
                "이 프로젝트는 확장 8 MiB를 **앞에 붙입니다**. IPS는 삽입이 없어",
                "패치된 스톡 ROM 거의 전체를 패치 파일에 다시 넣게 되므로 배포용으로",
                "쓰지 않습니다. xdelta3(VCDIFF)는 원본을 소스로 COPY하므로 원본 데이터가",
                "패치에 들어가지 않습니다.",
                "",
                "## 입력",
                "",
                f"- 원본: `{original_path.name}` · SHA-256 `{original_sha}`",
                f"- 메인 TIP: `{tip_path.name}` · SHA-256 `{tip_sha}`",
                "",
                "## 패치",
                "",
                f"- 파일: `{patch_path.name}`",
                f"- xdelta SHA-256: `{patch_sha}`",
                f"- 크기: **{patch_size}** bytes",
                f"- 원본 ROM 포함: **아니오** (`embeds_original_rom: false`)",
                f"- 8 MiB→16 MiB 라운드트립: **{roundtrip_ok}**",
                *([ips_line] if ips_line else []),
                "",
                "## 적용",
                "",
                "### GUI (Delta Patcher 등 xdelta3 프론트엔드)",
                "",
                "1. 합법적으로 소유한 일본판 원본 8 MiB ROM 준비 및 백업",
                "2. Original file = 합법적으로 소유한 일본판 원본 `.wsc`, XDelta patch = "
                f"`{patch_path.name}`, Output = 새 16 MiB `.wsc`",
                f"3. 결과 SHA-256이 `{tip_sha}`인지 확인",
                "",
                "xdelta **3.2 armor(BLAKE3)**, **VCDIFF secondary compression**,",
                "**application header**를 모두 끄고 plain VCDIFF로 인코딩했습니다. xdeltaUI 및",
                "구버전 xdelta3 프론트엔드 호환성을 우선한 배포 형식입니다.",
                "",
                "### CLI",
                "",
                "```bash",
                f"python tools/apply_main_tip_xdelta.py --original \"{original_path.name}\" "
                f"--xdelta out/dist/{patch_path.name} --out out/dist/monoeye_ko_from_xdelta.wsc",
                "```",
                "",
                "또는:",
                "",
                "```bash",
                f"xdelta3 -d -f -s \"{original_path.name}\" out/dist/{patch_path.name} "
                "out/dist/monoeye_ko_from_xdelta.wsc",
                "```",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"Wrote {patch_path}")
    print(f"Wrote {meta_path}")
    print(f"Wrote {readme_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
