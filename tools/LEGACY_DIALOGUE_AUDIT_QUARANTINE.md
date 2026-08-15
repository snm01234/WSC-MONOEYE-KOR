# Legacy dialogue audit quarantine

The only promotion-authoritative dialogue audit is:

```text
python tools/audit_dialogue_runtime_safety_gate.py --target <rom> --out <json>
```

That command regenerates `out/script/dialogue_runtime_contracts.json` from the
exact target and audits `tools/dialogue_runtime_contracts.py`. It does not load
or fall back to the retired prefix/metadata heuristics.

The following entry points are deliberately fail-closed and must not be used
by a build or promotion:

- `tools/audit_dialogue_20cell_candidate.py`
- `tools/audit_dialogue_runtime_evidence_matrix.py`

Historical promotion/build scripts that import either quarantined module are
also retired. Their import must fail instead of silently authorizing a ROM with
the old 20-cell or first-code-unit model.

Legacy reports such as `*_20cell.json`, `*_20cell.csv`, and the former runtime
evidence matrix are diagnostic history only. They are not promotion inputs.
Unresolved roles belong in the contract manifest as `quarantine` and remain
byte-exact.
