"""DO NOT MERGE: MOR-4924 acceptance demo. A deliberately failing test whose
name must appear in the uploaded shard artifact."""


def test_mor4924_deliberate_failure_is_named_in_the_artifact() -> None:
    assert False, "MOR-4924 demo: this failure must be named in the uploaded report"
