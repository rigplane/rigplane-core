import tomllib
from pathlib import Path


def test_wheel_includes_built_frontend_static() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"][
        "force-include"
    ]

    assert force_include["frontend/dist"] == "rigplane/web/static"
