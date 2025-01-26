import os
import pytest

def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="run integration tests that require real iCloud credentials"
    )

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: mark test as requiring iCloud credentials"
    )

def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-integration"):
        skip_integration = pytest.mark.skip(reason="need --run-integration option to run")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration) 