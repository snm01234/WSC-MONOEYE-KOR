#!/usr/bin/env python3
"""Mixed Korean/Japanese residual localization workflow (task 7.1).

Three subcommands, with strictly separated authority:

``discover``
    Read-only.  Locks every input identity, enumerates the Original-derived
    proven population, classifies residuals and publishes the Target Manifest.

``plan``
    Read-only.  Validates the reviewed translation catalog against the manifest,
    builds the Original+Working reference union, and chooses a storage strategy
    per target (ext3 → true-free → curated pair-steal) or marks it unresolved.

``apply-and-verify``
    The only command that may produce a ROM.  It applies the plan to an
    in-memory scratch copy, proves the result (record structure, rendered
    Korean, approved-extent confinement), writes a *temporary* candidate, runs
    every mandatory static gate on it, writes the aggregate acceptance report,
    and renames the temporary into the final candidate path only when every gate
    passed.  A rejected run leaves the reports and no publishable ROM.

Input ROMs are opened read-only in every subcommand.  The Original, the
Accepted_Baseline and the Working ROM are never written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta  # noqa: E402
from mixed_residual_discovery import generate_target_manifest  # noqa: E402
from mixed_residual_gate_runner import (  # noqa: E402
    GateInputs,
    build_acceptance_report,
    run_static_gates,
)
from mixed_residual_models import (  # noqa: E402
    DiscoveryInputIdentities,
    identify_evidence,
    identify_rom,
)
from mixed_residual_planner import (  # noqa: E402
    build_plan,
    load_pair_manifest,
)
from mixed_residual_reference_union import (  # noqa: E402
    build_free_slot_inventory,
    build_reference_union,
    make_shared_token_short_record_reason,
)
from mixed_residual_transaction import (  # noqa: E402
    LocalizationTransaction,
    TransactionAbort,
)
from mixed_residual_translations import validate_catalog_files  # noqa: E402
from monoeye_rom import Tbl, load_rom  # noqa: E402
from verify_all_stages_smoke import make_smoke_dictionary  # noqa: E402

DEFAULT_ORIGINAL_ROM = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_WORKING_ROM = ROOT / "out/patch/mixed_residual_working_terminators_repaired.wsc"
DEFAULT_PRE_EXT3_ROM = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_BLOCKS = ROOT / "out/script/aux_text_blocks.json"
DEFAULT_PREFIX_EVIDENCE = ROOT / "out/patch/mixed_residual_prefix_evidence.json"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
DEFAULT_EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_BASELINE_META = ROOT / "out/patch/p0_baseline_manifest.json"
DEFAULT_SHEET = ROOT / "out/script/translations_quality.json"
DEFAULT_MANIFEST = ROOT / "out/patch/mixed_residual_target_manifest.json"
DEFAULT_TRANSLATIONS = ROOT / "data/mixed_residual_translations.json"
DEFAULT_PLAN = ROOT / "out/patch/mixed_residual_plan.json"
DEFAULT_CANDIDATE = ROOT / "out/patch/mixed_residual_candidate.wsc"
DEFAULT_REPORT = ROOT / "out/patch/mixed_korean_japanese_residual_localization_report.json"
#: Read by ``verify_nondialogue_text.load_name75_rewrites`` so the deliberately
#: rewritten aux/name75 records are approved by evidence, not by a hard-coded
#: exception. Length and terminator preservation is still enforced there.
APPLY_REPORT_NAME = "mixed_residual_localization_report.json"

AUX_EVIDENCE_PRODUCER = "tools/find_aux_text_blocks.py"
PREFIX_EVIDENCE_PRODUCER = "tools/build_mixed_residual_prefix_evidence.py"
PREFIX_EVIDENCE_KIND = "record_prefixes"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else Path(path).read_bytes()
    return {
        "path": str(Path(path).resolve()),
        "size": len(payload),
        "sha256": _sha256(payload),
    }


def _discovery_inputs(
    original: Path, working: Path, blocks: Path, prefix_evidence: Path
) -> DiscoveryInputIdentities:
    original_identity = identify_rom(original, "original")
    return DiscoveryInputIdentities(
        original_rom=original_identity,
        working_rom=identify_rom(working, "working"),
        evidence=(
            identify_evidence(
                blocks,
                kind="aux_text_blocks",
                generated_by=AUX_EVIDENCE_PRODUCER,
                original_rom=original_identity,
            ),
            identify_evidence(
                prefix_evidence,
                kind=PREFIX_EVIDENCE_KIND,
                generated_by=PREFIX_EVIDENCE_PRODUCER,
                original_rom=original_identity,
            ),
        ),
    )


def cmd_discover(args: argparse.Namespace) -> int:
    if args.out_manifest.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — discovery is read-only")
    original = bytes(load_rom(args.original_rom))
    working = bytes(load_rom(args.working_rom))
    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    # The Shared_Token_Short_Record rule needs the Original+Working reference
    # union and the free-slot inventory; discovery takes them as evidence rather
    # than deriving a capacity rule of its own.
    union = build_reference_union(
        original, working, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    inventory = build_free_slot_inventory(
        working, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    manifest = generate_target_manifest(
        _discovery_inputs(
            args.original_rom, args.working_rom, args.blocks, args.prefix_evidence
        ),
        Tbl.load(args.tbl),
        args.out_manifest,
        dictionary_factory=make_smoke_dictionary,
        working_dictionary_name="verify_all_stages_smoke.make_smoke_dictionary",
        prefix_evidence_kinds=(PREFIX_EVIDENCE_KIND,),
        storage_capacity=make_shared_token_short_record_reason(
            original,
            union=union,
            two_byte_free=tuple(inventory.stock_free) + tuple(inventory.ext_free),
        ),
    )
    counts = manifest.get("population", {}).get("counts", {})
    print(
        json.dumps(
            {
                "manifest": str(args.out_manifest),
                "manifest_sha256": manifest.get("manifest_sha256"),
                "counts": counts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _load_plan(args: argparse.Namespace) -> tuple[Any, Mapping[str, Any], Any, bytes, bytes]:
    original = bytes(load_rom(args.original_rom))
    working = bytes(load_rom(args.working_rom))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    validation = validate_catalog_files(args.manifest, args.translations)
    union = build_reference_union(
        original,
        working,
        ext_meta=load_ext_meta(args.ext_meta),
        ext3_meta=load_ext_meta(args.ext3_meta),
    )
    plan = build_plan(
        original_rom=original,
        working_rom=working,
        manifest=manifest,
        validation=validation,
        tbl=Tbl.load(args.tbl),
        ext_meta=load_ext_meta(args.ext_meta),
        ext3_meta=load_ext_meta(args.ext3_meta),
        union=union,
        pair_manifest=load_pair_manifest(args.pair_manifest),
        allow_ext3_reclaim=not args.no_ext3_reclaim,
        inputs={
            "original_rom": _identity(args.original_rom, original),
            "working_rom": _identity(args.working_rom, working),
            "manifest": _identity(args.manifest),
            "translations": _identity(args.translations),
            "tbl": _identity(args.tbl),
            "ext_meta": _identity(args.ext_meta),
            "ext3_meta": _identity(args.ext3_meta),
        },
    )
    return plan, validation, union, original, working


def cmd_plan(args: argparse.Namespace) -> int:
    if args.out_plan.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — planning is read-only")
    plan, validation, _union, _original, _working = _load_plan(args)
    document = plan.to_json_data()
    document["translation_validation"] = validation.to_json_data()["counts"]
    args.out_plan.parent.mkdir(parents=True, exist_ok=True)
    args.out_plan.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"ok": plan.ok, "counts": document["counts"], "plan": str(args.out_plan)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if plan.ok else 1


def cmd_apply_and_verify(args: argparse.Namespace) -> int:
    if args.candidate_rom.suffix.lower() != ".wsc":
        raise SystemExit("the candidate ROM path must be a .wsc")
    plan, validation, union, _original, working = _load_plan(args)
    plan_document = plan.to_json_data()
    validation_document = {
        "accepted": validation.accepted,
        "unresolved_count": validation.unresolved_count,
        "catalog_path": validation.catalog_path,
        "manifest_sha256": validation.manifest_sha256,
    }
    args.out_plan.parent.mkdir(parents=True, exist_ok=True)
    args.out_plan.write_text(
        json.dumps(plan_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    baseline_path = args.baseline_rom or args.working_rom
    baseline = bytes(load_rom(baseline_path))

    if not plan.ok:
        report = {
            "schema_version": 1,
            "generated_by": "tools/localize_mixed_residuals.py",
            "status": "rejected",
            "accepted": False,
            "reason": "plan_has_unresolved_targets",
            "unresolved_count": plan.unresolved_count,
            "counts": plan_document["counts"],
            "plan": str(args.out_plan),
            "translation_validation": validation_document,
            "candidate_rom": None,
            "emulator_follow_up": {"status": "pending", "blocking": False},
        }
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({k: report[k] for k in ("status", "unresolved_count")}, indent=2))
        return 1

    transaction = LocalizationTransaction(
        working_rom=working,
        baseline_rom=baseline,
        plan=plan,
        union=union,
        tbl=Tbl.load(args.tbl),
        ext_meta=load_ext_meta(args.ext_meta),
        ext3_meta=load_ext_meta(args.ext3_meta),
    )
    temporary = args.candidate_rom.with_name(f".{args.candidate_rom.name}.tmp")
    reusable_candidate_sha256: str | None = None
    reusable_not_before_ns: int | None = None
    reusable_source = (
        temporary
        if temporary.is_file()
        else args.candidate_rom
        if args.candidate_rom.is_file()
        else None
    )
    if args.reuse_gate_reports and reusable_source is not None:
        reusable_candidate_sha256 = _sha256(reusable_source.read_bytes())
        reusable_not_before_ns = reusable_source.stat().st_mtime_ns
    try:
        transaction.apply_to_scratch()
        precommit = transaction.precommit_verify()
        if not precommit.ok:
            raise TransactionAbort("; ".join(precommit.failures[:6]))
        apply_report_path = args.ui_report_dir / APPLY_REPORT_NAME
        apply_report_path.parent.mkdir(parents=True, exist_ok=True)
        apply_report_path.write_text(
            json.dumps(transaction.apply_report(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        candidate_identity = transaction.write_temporary(temporary)
    except TransactionAbort as exc:
        temporary.unlink(missing_ok=True)
        report = {
            "schema_version": 1,
            "generated_by": "tools/localize_mixed_residuals.py",
            "status": "rejected",
            "accepted": False,
            "reason": f"transaction_aborted: {exc}",
            "counts": plan_document["counts"],
            "journal": transaction.journal,
            "candidate_rom": None,
            "emulator_follow_up": {"status": "pending", "blocking": False},
        }
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"REJECTED transaction_aborted: {exc}", file=sys.stderr)
        return 2

    gate_inputs = GateInputs(
        original_rom=args.original_rom,
        pre_ext3_rom=args.pre_ext3_rom,
        baseline_rom=baseline_path,
        candidate_rom=temporary,
        blocks=args.blocks,
        prefix_evidence=args.prefix_evidence,
        tbl=args.tbl,
        ext_meta=args.ext_meta,
        ext3_meta=args.ext3_meta,
        sheet=args.sheet,
        ui_report_dir=args.ui_report_dir,
        out_dir=args.gate_dir,
        baseline_meta=args.baseline_meta,
    )
    reuse_reports = bool(
        args.reuse_gate_reports
        and reusable_candidate_sha256 == candidate_identity.get("sha256")
        and reusable_not_before_ns is not None
    )
    reuse_env = "MONOEYE_GATE_REUSE_NOT_BEFORE_NS"
    prior_reuse_env = os.environ.get(reuse_env)
    if reuse_reports:
        os.environ[reuse_env] = str(reusable_not_before_ns)
    else:
        os.environ.pop(reuse_env, None)
    try:
        gates, runs = run_static_gates(
            gate_inputs,
            plan_document=plan_document,
            validation=validation_document,
            precommit=precommit.as_dict(),
        )
    finally:
        if prior_reuse_env is None:
            os.environ.pop(reuse_env, None)
        else:
            os.environ[reuse_env] = prior_reuse_env
    gates_ok = all(result.ok for result in gates.values())
    published = transaction.publish(temporary, args.candidate_rom, gates_ok=gates_ok)
    if published.get("published"):
        candidate_identity = {
            "path": published["path"],
            "size": published["size"],
            "sha256": published["sha256"],
        }
    else:
        candidate_identity = {}

    report = build_acceptance_report(
        inputs=gate_inputs,
        plan_document=plan_document,
        validation=validation_document,
        precommit=precommit.as_dict(),
        gates=gates,
        runs=runs,
        apply_report=transaction.apply_report(),
        candidate_identity=candidate_identity,
    )
    report["published"] = bool(published.get("published"))
    report["journal"] = transaction.journal
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "published": report["published"],
                "candidate_rom": candidate_identity,
                "gates": {
                    name: {"ok": result.ok, "status": result.status}
                    for name, result in gates.items()
                },
                "report": str(args.report),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["accepted"] else 1


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--original-rom", type=Path, default=DEFAULT_ORIGINAL_ROM)
    parser.add_argument("--working-rom", type=Path, default=DEFAULT_WORKING_ROM)
    parser.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    parser.add_argument("--blocks", type=Path, default=DEFAULT_BLOCKS)
    parser.add_argument("--prefix-evidence", type=Path, default=DEFAULT_PREFIX_EVIDENCE)


def _add_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--translations", type=Path, default=DEFAULT_TRANSLATIONS)
    parser.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    parser.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    parser.add_argument("--pair-manifest", type=Path, default=None)
    parser.add_argument("--no-ext3-reclaim", action="store_true")
    parser.add_argument("--out-plan", type=Path, default=DEFAULT_PLAN)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser("discover", help="read-only target discovery")
    _add_common(discover)
    discover.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    discover.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    discover.add_argument("--out-manifest", type=Path, default=DEFAULT_MANIFEST)
    discover.set_defaults(func=cmd_discover)

    plan = sub.add_parser("plan", help="read-only guarded planning")
    _add_common(plan)
    _add_plan_args(plan)
    plan.set_defaults(func=cmd_plan)

    apply = sub.add_parser(
        "apply-and-verify", help="scratch apply, mandatory gates, atomic publish"
    )
    _add_common(apply)
    _add_plan_args(apply)
    apply.add_argument("--baseline-rom", type=Path, default=None)
    apply.add_argument(
        "--baseline-meta", type=Path, default=DEFAULT_BASELINE_META
    )
    apply.add_argument("--pre-ext3-rom", type=Path, default=DEFAULT_PRE_EXT3_ROM)
    apply.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    apply.add_argument("--ui-report-dir", type=Path, default=ROOT / "out/patch")
    apply.add_argument("--gate-dir", type=Path, default=ROOT / "out/patch")
    apply.add_argument(
        "--reuse-gate-reports",
        action="store_true",
        help=(
            "reuse fresh gate JSON only when a prior temporary or published "
            "candidate exists and its SHA-256 matches the newly rebuilt candidate"
        ),
    )
    apply.add_argument("--candidate-rom", type=Path, default=DEFAULT_CANDIDATE)
    apply.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    apply.set_defaults(func=cmd_apply_and_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
