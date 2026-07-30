from radar.core.eval.idempotency import compare_snapshots


def test_two_equivalent_passes_have_identical_projection():
    rows = {table: [{"id": "1", "content_hash": "abc"}] for table in ("entities", "entity_relationships", "match_chunks", "edital_chunks", "discovered_opportunities")}
    result = compare_snapshots(rows, rows)
    assert all(value["equal"] and value["before"] == value["after"] for value in result.values())
