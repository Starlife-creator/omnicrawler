"""S2.5.39：SDK/CLI/GUI 三入口 require_sample_match 默认值一致。"""

from __future__ import annotations

import inspect

from omnicrawl.sdk import run as sdk_run
from omnicrawl.services.application_service import ApplicationService
from omnicrawl.services.controllers import RunController


def test_sdk_default_matches_service_default() -> None:
    sdk_default = inspect.signature(sdk_run).parameters["require_sample_match"].default
    service_default = inspect.signature(ApplicationService.run).parameters["require_sample_match"].default
    controller_default = inspect.signature(RunController.run).parameters["require_sample_match"].default
    assert sdk_default is False
    assert service_default is False
    assert controller_default is False
