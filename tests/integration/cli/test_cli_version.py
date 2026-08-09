import pytest

from omnicrawl import __version__
from omnicrawl.cli import build_parser


def test_version_flag_does_not_require_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"omnicrawl {__version__}"
