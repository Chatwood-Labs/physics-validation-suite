"""Turn unavailable capabilities into failures in explicitly strict CI lanes."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-no-skips",
        action="store_true",
        default=False,
        help="Fail qualification when any collected or executed test is skipped.",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--require-no-skips"):
        config.pluginmanager.register(_NoSkips(), "pvs-no-skips")


class _NoSkips:
    def __init__(self) -> None:
        self.skipped: set[str] = set()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.skipped:
            self.skipped.add(report.nodeid)

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.skipped:
            self.skipped.add(report.nodeid)

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if self.skipped and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter: object) -> None:
        if self.skipped:
            terminalreporter.write_sep("=", "Required capability qualification contains skips")
            for nodeid in sorted(self.skipped):
                terminalreporter.write_line(nodeid)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "symlink_factory" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.windows_filesystem)
        if item.get_closest_marker("windows_filesystem") is not None:
            item.user_properties.append(("pvs_capability", "windows_filesystem"))
        if "case_sensitive_filesystem" in getattr(item, "fixturenames", ()):
            item.user_properties.append(("pvs_capability", "case_sensitive_filesystem"))
