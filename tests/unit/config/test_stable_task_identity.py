from omnicrawler.gui.core.config_model import CrawlConfig
from omnicrawler.gui.core.config_serializer import from_yaml, load_yaml, save_yaml, to_yaml


def test_task_identity_survives_comment_free_yaml_and_rename():
    config = CrawlConfig(task_id="stable-task-A", project_name="before")
    serialized = to_yaml(config)
    uncommented = "\n".join(line for line in serialized.splitlines() if not line.startswith("#"))
    restored = from_yaml(uncommented)
    assert restored.task_id == config.task_id
    restored.project_name = "after"
    assert from_yaml(to_yaml(restored)).task_id == config.task_id


def test_legacy_file_reopen_retains_identity_without_mutating_source(tmp_path):
    source = tmp_path / "旧任务.yaml"
    source.write_text("project:\n  name: before\n", encoding="utf8")
    original = source.read_bytes()
    first = load_yaml(source)
    source.write_text("project:\n  name: after\n", encoding="utf8")
    assert load_yaml(source).task_id == first.task_id
    source.write_bytes(original)
    assert load_yaml(source).task_id == first.task_id
    assert source.read_bytes() == original
    assert first.passthrough["project"]["identity_origin"] == "legacy_config_path"
    different = tmp_path / "independent.yaml"
    different.write_bytes(original)
    assert load_yaml(different).task_id != first.task_id
    save_yaml(first, different)
    assert load_yaml(different).task_id == first.task_id


def test_legacy_comment_identity_is_not_replaced_by_path_identity(tmp_path):
    source = tmp_path / "old.yaml"
    source.write_text("# task_id: abc123-456\nproject:\n  name: old\n", encoding="utf8")
    assert load_yaml(source).task_id == "abc123-456"


def test_legacy_comment_after_config_is_recognized_consistently(tmp_path):
    source = tmp_path / "tail-comment.yaml"
    source.write_text("project: {name: old}\n# task_id: abc123-456\n", encoding="utf8")
    assert from_yaml(source.read_text(encoding="utf8")).task_id == "abc123-456"
    assert load_yaml(source).task_id == "abc123-456"
    assert load_yaml(source).task_id == load_yaml(source).task_id


def test_incidental_comment_does_not_claim_persisted_identity(tmp_path):
    source = tmp_path / "example.yaml"
    source.write_text("# example task_id: abc123-456\nproject: {name: old}\n", encoding="utf8")
    restored = load_yaml(source)
    assert restored.task_id != "abc123-456"
    assert restored.passthrough["project"]["identity_origin"] == "legacy_config_path"
