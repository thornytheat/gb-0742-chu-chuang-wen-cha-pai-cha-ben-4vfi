from concurrent.futures import ThreadPoolExecutor

from app.modules.archive.pipeline import evaluate
from app.modules.coverage.summary import compare, conclusion, episodes_compare
from app.modules.intake.catalog import load_sample


def versions(client, sample):
    response = client.post("/api/runs", json={"sample": sample})
    assert response.status_code in (200, 201), response.text
    return response.json()["manifest"], f'/api/runs/{response.json()["manifest"]["id"]}'


def test_conclusion_separates_paired_from_gradable(snapshot):
    report = conclusion(snapshot)
    assert (report["expected"], report["paired"], report["gradable"], report["ungradable"]) == (20, 17, 16, 4)
    assert report["paired_ungradable"] == 1  # CV-17 03:00 paired but confidence interval crosses threshold
    assert report["gaps"] == {"MISSING_UPPER": 2, "NO_COMPATIBLE_LOWER": 1, "LOWER_CONTENTION": 0}
    assert report["alignment"]["aligned"]


def test_gap_reasons_name_each_unpaired_cabinet_hour(snapshot):
    report = conclusion(snapshot)
    reasons = {(slot["cabinet"], slot["start"]): slot["reason"] for slot in report["slots"] if slot["reason"]}
    assert reasons == {("CV-17", "2026-11-20T06:00:00Z"): "NO_COMPATIBLE_LOWER",
                       ("CV-17", "2026-11-20T09:00:00Z"): "MISSING_UPPER",
                       ("CV-21", "2026-11-20T01:00:00Z"): "MISSING_UPPER"}
    paired_gap = next(slot for slot in report["slots"] if slot["state"] == "MISSING" and slot["paired"])
    assert (paired_gap["grade_code"], paired_gap["gradable"]) == ("CI_OVERLAP", False)


def test_conclusion_reuses_grade_episode_and_audit_keys(snapshot):
    report = conclusion(snapshot)
    keys = {(slot["cabinet"], slot["start"]) for slot in report["slots"]}
    assert keys == {(slot["cabinet"], slot["start"]) for slot in snapshot["grades"]}
    traced = {(item["cabinet"], proof["start"]) for item in snapshot["episodes"] for proof in item["evidence"]}
    assert traced <= keys
    assert report["alignment"]["checks"]["episode_hours_match_evidence"]


def test_supplement_run_is_new_version_that_supersedes_and_reconciles(client):
    first, base = versions(client, "shift")
    assert first["version"] == 1 and first["supersedes"] is None
    second, base2 = versions(client, "supplement")
    assert second["version"] == 2 and second["supersedes"] == first["id"]
    result = client.get(base2 + "/coverage").json()
    assert (result["coverage"]["gradable"], result["coverage"]["ungradable"]) == (18, 2)
    diff = result["comparison"]
    converted = {(slot["cabinet"], slot["start"]): slot for slot in diff["slots"]["converted"]}
    assert set(converted) == {("CV-17", "2026-11-20T06:00:00Z"), ("CV-17", "2026-11-20T09:00:00Z")}
    assert converted[("CV-17", "2026-11-20T06:00:00Z")]["reason_before"] == "NO_COMPATIBLE_LOWER"
    assert converted[("CV-17", "2026-11-20T09:00:00Z")]["reason_before"] == "MISSING_UPPER"
    assert converted[("CV-17", "2026-11-20T09:00:00Z")]["state"] == "ABNORMAL"
    assert diff["slots"]["regressed"] == []
    assert [(slot["cabinet"], slot["reason"]) for slot in diff["slots"]["still_gap"]] == [("CV-21", "MISSING_UPPER")]
    assert diff["gaps_before"] == {"MISSING_UPPER": 2, "NO_COMPATIBLE_LOWER": 1, "LOWER_CONTENTION": 0}
    assert diff["gaps_after"] == {"MISSING_UPPER": 1, "NO_COMPATIBLE_LOWER": 0, "LOWER_CONTENTION": 0}


def test_supplement_recomputes_episode_while_keeping_earlier_segments(client):
    versions(client, "shift")
    _, base = versions(client, "supplement")
    episodes = client.get(base + "/coverage").json()["comparison"]["episodes"]
    assert [item["id"] for item in episodes["retained"]] == ["CV-17-A01", "CV-17-A02"]
    assert [(item["before"]["id"], item["before"]["end"], item["after"]["end"]) for item in episodes["extended"]] == [
        ("CV-17-A03", "2026-11-20T09:00:00Z", "2026-11-20T10:00:00Z")]
    assert episodes["dropped"] == [] and episodes["added"] == []


def test_same_shift_rerun_reuses_version_and_keeps_receipt_cutoff(client):
    first, base = versions(client, "shift")
    again = client.post("/api/runs", json={"sample": "shift"})
    assert again.status_code == 200 and again.json()["reused"] is True
    assert again.json()["manifest"] == first
    assert [item["id"] for item in client.get("/api/runs").json()] == [first["id"]]
    assert client.get(base + "/coverage").json()["versions"][0]["version"] == 1


def test_first_version_diff_is_empty_and_chain_lists_versions(client):
    first, base = versions(client, "shift")
    result = client.get(base + "/coverage").json()
    assert result["comparison"]["from"] is None and result["comparison"]["slots"]["converted"] == []
    versions(client, "supplement")
    chain = client.get(base + "/coverage").json()["versions"]
    assert [(item["version"], item["sample"]) for item in chain] == [(1, "shift"), (2, "supplement")]
    assert chain[0]["comparison"]["from"] is None and chain[1]["comparison"]["from"] == first["id"]


def test_original_archive_stays_replayable_after_supplement(client):
    first, base = versions(client, "shift")
    versions(client, "supplement")
    assert client.post(base + "/rerun").json()["verified"] is True
    assert client.get(base).json() == first
    assert client.get(base + "/export").status_code == 200


def test_readings_after_cutoff_are_deferred_not_silently_dropped(package):
    early = package.spec.model_copy(update={"receipt_cutoff": "2026-11-20T18:00:00+08:00"})
    snapshot = evaluate(type(package)(early, package.blob))
    deferred = [row for row in snapshot["rows"] if row["disposition"] == "deferred"]
    assert [row["observed_utc"] for row in deferred] == ["2026-11-20T10:00:00Z"]
    assert all(row["code"] == "RECEIPT_AFTER_CUTOFF" for row in deferred)
    assert snapshot["audit"]["row_audit"]["balanced"]
    assert snapshot["audit"]["row_audit"]["deferred"] == 1


def test_compare_only_reports_paired_transitions_and_state_changes():
    base = {"slots": [{"cabinet": "C", "start": "S1", "end": "E", "paired": False, "reason": "MISSING_UPPER",
                       "state": "MISSING", "grade_code": None, "gradable": False},
                      {"cabinet": "C", "start": "S2", "end": "E", "paired": True, "reason": None,
                       "state": "STABLE", "grade_code": None, "gradable": True}]}
    after = {"slots": [{**base["slots"][0], "paired": True, "reason": None, "state": "STABLE", "gradable": True},
                       {**base["slots"][1], "state": "MISSING", "grade_code": "CI_OVERLAP", "gradable": False}]}
    diff = compare(base, after)
    assert [slot["start"] for slot in diff["converted"]] == ["S1"]
    assert [slot["start"] for slot in diff["state_changes"]] == ["S2"]
    assert diff["still_gap"] == [] and diff["regressed"] == []


def test_episode_compare_keeps_number_identity_across_merged_end():
    before = [{"id": "C-A01", "cabinet": "C", "start": "S1", "end": "E1", "hours": 1, "kind": "SUSTAINED", "axes": ["humidity"]},
              {"id": "C-A02", "cabinet": "C", "start": "S2", "end": "E2", "hours": 1, "kind": "TRANSIENT", "axes": ["temperature"]}]
    after = [{**before[1]}, {**before[0], "id": "C-A01", "end": "E3", "hours": 2}]
    diff = episodes_compare(before, after)
    assert [item["id"] for item in diff["retained"]] == ["C-A02"]
    assert [(item["before"]["id"], item["after"]["end"]) for item in diff["extended"]] == [("C-A01", "E3")]
    assert diff["changed"] == [] and diff["added"] == [] and diff["dropped"] == []


def test_failed_supplement_recheck_keeps_previous_version(client):
    first, base = versions(client, "shift")
    store = client.app.state.archives.store
    with store.connect() as db:
        db.execute("UPDATE shift_archives SET evidence = '{}' WHERE id = ?", (first["id"],))
    refused = client.post("/api/runs", json={"sample": "supplement"})
    assert (refused.status_code, refused.json()["code"]) == (409, "ARCHIVE_DAMAGED")
    assert client.get("/api/runs").json() == [first]
    assert [item[0]["id"] for item in store.versions("N26-1120")] == [first["id"]]


def test_same_shift_concurrent_submissions_assign_no_duplicate_version(client):
    service = client.app.state.archives
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(service.run, ["shift"] * 4 + ["supplement"] * 4))
    assert sum(not item["reused"] for item in outcomes) == 2
    versions = service.store.versions("N26-1120")
    assert [item[0]["version"] for item in versions] == [1, 2]
    assert [item[0]["supersedes"] for item in versions] == [None, versions[0][0]["id"]]
    assert len(service.store.history()) == 2


def test_cutoff_and_version_are_exported_with_the_original_archive(client):
    first, base = versions(client, "shift")
    versions(client, "supplement")
    package = client.get(base + "/export").json()
    assert package["manifest"]["version"] == 1
    assert package["manifest"]["cutoff_utc"] == "2026-11-20T10:02:00Z"
    assert package["evidence"]["cutoff"] == first["cutoff"]
