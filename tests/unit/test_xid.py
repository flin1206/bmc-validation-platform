from bmcval.checks import Status
from bmcval.gpu import xid

KERN_LOG = """\
[  12.001] nvidia 0000:3b:00.0: enabling device (0000 -> 0003)
[ 812.345] NVRM: Xid (PCI:0000:3b:00): 13, pid=4242, name=python3, Graphics Exception: ESR 0x404600=0x80000002
[ 812.346] NVRM: Xid (PCI:0000:3b:00): 45, pid=4242, name=python3, Ch 00000008
[ 900.100] NVRM: Xid (PCI:0000:5e:00): 79, pid=0, GPU has fallen off the bus.
[ 901.000] NVRM: Xid (PCI:0000:AF:00): 48, An uncorrectable double bit error (DBE) has been detected
[ 950.000] NVRM: Xid (PCI:0000:af:00): 999, pid=1, something new
"""


def test_parse_formats():
    events = xid.parse(KERN_LOG.splitlines())
    assert [e.code for e in events] == [13, 45, 79, 48, 999]
    first = events[0]
    assert first.bdf == "0000:3b:00"
    assert first.pid == "4242"
    assert first.process == "python3"
    assert first.message.startswith("Graphics Exception")
    old_style = events[3]  # no pid/name fields
    assert old_style.pid is None
    assert old_style.bdf == "0000:af:00"  # normalised to lower case


def test_triage_takes_worst_action_per_gpu():
    actions = xid.triage(xid.parse(KERN_LOG.splitlines()))
    assert actions["0000:3b:00"] is xid.Action.CHECK_APP
    assert actions["0000:5e:00"] is xid.Action.DRAIN_NODE
    assert actions["0000:af:00"] is xid.Action.RESET_GPU  # 48 and unknown 999


def test_suite_statuses_and_node_action():
    suite = xid.to_suite(xid.parse(KERN_LOG.splitlines()))
    by_name = {c.name: c.status for c in suite.checks}
    assert by_name["Xid 13 on 0000:3b:00"] is Status.WARN
    assert by_name["Xid 45 on 0000:3b:00"] is Status.PASS
    assert by_name["Xid 79 on 0000:5e:00"] is Status.FAIL
    assert suite.properties["node_action"] == "DRAIN_NODE"
    assert suite.failed


def test_clean_log():
    suite = xid.to_suite([])
    assert not suite.failed
    assert suite.properties["node_action"] == "NONE"
