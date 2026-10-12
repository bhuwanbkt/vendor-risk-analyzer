"""Single source for the application version (pyproject.toml)."""

from importlib.metadata import PackageNotFoundError, version


def get_version() -> str:
    try:
        return version("vendor-risk-analyzer")
    except PackageNotFoundError:
        return "unknown"


APP_VERSION = get_version()
