from datetime import datetime, timezone
from time import perf_counter

from app.config import POLICY
from app.errors import FieldError
from app.manifest import identify, json_sha, preserve
from app.modules.archive.pipeline import evaluate
from app.modules.archive.proof import verify
from app.modules.coverage.summary import compare, conclusion, episodes_compare
from app.modules.intake.catalog import load_sample
from app.timebase import seconds, utc


class _Build:
    """Build the manifest and the before/after diff against the prior version."""

    def __init__(self, spec, evidence, key, hashes, elapsed):
        self.shift_key = spec.shift
        self.spec, self.evidence, self.key, self.hashes, self.elapsed = spec, evidence, key, hashes, elapsed

    def __call__(self, previous):
        spec, evidence = self.spec, self.evidence
        current = conclusion(evidence)
        if previous is None:
            comparison = {"from": None, "cutoff_before": None, "cutoff_after": current["cutoff"],
                          "gaps_before": {name: 0 for name in current["gaps"]}, "gaps_after": current["gaps"],
                          "gradable_before": 0, "gradable_after": current["gradable"],
                          "slots": compare(current, current), "episodes": episodes_compare([], evidence["episodes"])}
        else:
            old_manifest, old_evidence, old_input = previous
            verify(previous)
            before = conclusion(old_evidence)
            comparison = {"from": old_manifest["id"], "cutoff_before": before["cutoff"], "cutoff_after": current["cutoff"],
                          "gaps_before": before["gaps"], "gaps_after": current["gaps"],
                          "gradable_before": before["gradable"], "gradable_after": current["gradable"],
                          "alignment_before": before["alignment"], "alignment_after": current["alignment"],
                          "slots": compare(before, current),
                          "episodes": episodes_compare(old_evidence["episodes"], evidence["episodes"])}
        comparison["to"] = self.key
        manifest = {"id": self.key, "sample": spec.sample, "label": spec.label, "shift": spec.shift,
                    "shift_key": spec.shift, "policy": POLICY,
                    "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "cutoff": evidence["cutoff"], "cutoff_utc": None if evidence["cutoff"] is None else utc(evidence["cutoff"]),
                    "supersedes": comparison["from"],
                    "window": [utc(seconds(spec.start)), utc(seconds(spec.end))],
                    "cabinets": [cabinet.model_dump(mode="json") for cabinet in spec.cabinet_hours],
                    "tolerance_seconds": spec.tolerance_seconds, "sustained_hours": spec.sustained_hours,
                    "file": spec.csv, "hashes": self.hashes, "result_sha256": json_sha(evidence),
                    "elapsed_ms": self.elapsed, "summary": evidence["audit"]["hours"]}
        return manifest, comparison


class ArchiveService:
    def __init__(self, store, loader=load_sample):
        self.store = store
        self.loader = loader

    def run(self, sample):
        start = perf_counter()
        package = self.loader(sample)
        evidence = evaluate(package)
        key, hashes = identify(package)
        elapsed = round(1000 * (perf_counter() - start))
        build = _Build(package.spec, evidence, key, hashes, elapsed)
        record, created = self.store.insert(key, build, evidence, preserve(package))
        if record["result_sha256"] != json_sha(evidence):
            raise FieldError("RULE_DRIFT", "同版结果摘要不一致", 409)
        return {"manifest": record, "reused": not created}

    def replay(self, run_id):
        archive = self.store.fetch(run_id)
        manifest = archive[0]
        if manifest["policy"] != POLICY:
            raise FieldError("OLD_POLICY", "当前规则无法复算此归卷", 409)
        pinned = verify(archive)
        if json_sha(evaluate(pinned)) != manifest["result_sha256"]:
            raise FieldError("RULE_DRIFT", "复算结果摘要不一致", 409)
        return {"manifest": manifest, "verified": True, "reused": True}
