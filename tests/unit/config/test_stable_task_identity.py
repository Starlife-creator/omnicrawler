from omnicrawler.gui.core.config_model import CrawlConfig
from omnicrawler.gui.core.config_serializer import from_yaml, to_yaml


def test_task_identity_survives_comment_free_yaml_and_rename():
    config = CrawlConfig(task_id="stable-task-A", project_name="before")
    serialized = to_yaml(config)
    uncommented = "\n".join(line for line in serialized.splitlines() if not line.startswith("#"))
    restored = from_yaml(uncommented)
    assert restored.task_id == config.task_id
    restored.project_name = "after"
    assert from_yaml(to_yaml(restored)).task_id == config.task_id
