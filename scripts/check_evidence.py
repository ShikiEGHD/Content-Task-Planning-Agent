"""Validate UTF-8 JSON outputs and match them to the latest evidence manifest."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from run_evidence import ROOT, output_hash, source_hash, verify_run, write


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revalidate", action="store_true",
                        help="Recheck existing immutable outputs and preserve the initial manifest before updating verification flags")
    args = parser.parse_args()
    outputs = sorted((ROOT / "examples/outputs").glob("*.json"))
    for path in outputs:
        text = path.read_text(encoding="utf-8")
        assert "\ufffd" not in text, path
        json.loads(text)
    checked = 0
    for mode in ("mock", "cloud"):
        path = ROOT / "examples/test-results" / (mode + "-evidence.json")
        if not path.exists():
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        scheme = manifest.get("source_hash_scheme")
        candidates = [source_hash(scheme)] if scheme else [source_hash("raw-v1"), source_hash()]
        assert manifest["source_sha256"] in candidates, "Source changed after evidence run: " + mode
        if args.revalidate:
            original = ROOT / "examples/test-results" / Path(manifest["runtime_root"]).name / "initial-manifest.json"
            if not original.exists():
                original.parent.mkdir(parents=True, exist_ok=True)
                original.write_bytes(path.read_bytes())
        else:
            assert manifest["all_verified"] is True
        for run in manifest["runs"]:
            output = ROOT / run["output"]
            assert output_hash(output, manifest.get("output_hash_scheme", "raw-v1")) == run["output_sha256"], output
            if run.get("trace"):
                for line in (ROOT / run["trace"]).read_text(encoding="utf-8").splitlines():
                    json.loads(line)
            result = json.loads(output.read_text(encoding="utf-8"))
            assert run["exit_code"] == run["expected_exit_code"]
            verify_run(result, "completed" if run["expected_exit_code"] == 0 else "incomplete", run["name"], mode)
            if args.revalidate:
                run["verified"] = True
                run["output_sha256"] = output_hash(output)
            else:
                assert run["verified"]
            checked += 1
        if args.revalidate:
            manifest["all_verified"] = True
            manifest["source_sha256"] = source_hash()
            manifest["source_hash_scheme"] = "normalized-v2"
            manifest["output_hash_scheme"] = "utf8-lf-v1"
            manifest["revalidated_at"] = datetime.now(timezone.utc).isoformat()
            manifest["revalidation_note"] = "Offline verification of original output hashes and current source; safety-limit termination is a valid incomplete result, not model-initiated recovery. No API calls or output edits."
            write(path, manifest)
    print(f"UTF-8 JSON outputs: {len(outputs)}; verified manifest cases: {checked}")


if __name__ == "__main__":
    main()
