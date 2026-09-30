def pytest_addoption(parser):
    group = parser.getgroup("bmcval")
    group.addoption(
        "--profile",
        default="qemu-romulus",
        help="platform profile name or YAML path (see `bmcval profiles`)",
    )
    group.addoption(
        "--run-destructive",
        action="store_true",
        help="run tests that change persistent BMC state (power, firmware, accounts)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "requires(*capabilities): skip unless the platform profile declares them"
    )
