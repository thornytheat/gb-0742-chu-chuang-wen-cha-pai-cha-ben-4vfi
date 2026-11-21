from collections import Counter

from app.modules.assignment.windows import DeploymentIndex, assign
from app.modules.audit.reconcile import reconcile
from app.modules.episodes.segment import segment
from app.modules.gradient.grade import grade
from app.modules.intake.readings import deduplicate, read
from app.modules.pairing.schedule import schedule
from app.reasons import mark
from app.timebase import seconds


def cutoff_of(spec):
    return seconds(spec.receipt_cutoff) if spec.receipt_cutoff else None


def defer_late(rows, cutoff):
    """Hold back readings that were not yet received at the publishing cutoff."""
    if cutoff is None:
        return 0
    held = [row for row in rows if row["disposition"] == "accepted" and row["received"] > cutoff]
    for row in held:
        mark(row, "deferred", "RECEIPT_AFTER_CUTOFF")
    return len(held)


def evaluate(package):
    spec = package.spec
    cutoff = cutoff_of(spec)
    rows, intake = read(package)
    deferred = defer_late(rows, cutoff)
    deduplicate(rows)
    index = DeploymentIndex(spec)
    assign(spec, rows, index)
    pairs, unused = schedule(spec, rows, index)
    grades = grade(spec, pairs, rows)
    episodes = segment(grades, spec.sustained_hours)
    audit = reconcile(spec, rows, intake, grades, episodes)
    return {"cutoff": cutoff, "deferred": deferred, "intake": intake, "rows": rows,
            "deployments": index.windows, "deployment_conflicts": index.conflicts,
            "pairs": pairs, "unused": unused, "grades": grades, "episodes": episodes, "audit": audit,
            "isolation": dict(sorted(Counter(row["code"] for row in rows if row["disposition"] == "isolated").items()))}
