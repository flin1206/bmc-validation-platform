from bmcval.checks import Status
from bmcval.security import audit

XCCDF = """<?xml version="1.0"?>
<Benchmark xmlns="http://checklists.nist.gov/xccdf/1.2" id="xccdf_org.ssgproject.content_benchmark_UBUNTU2404">
  <Rule id="xccdf_org.ssgproject.content_rule_sshd_disable_root_login" severity="medium">
    <title>Disable SSH Root Login</title>
  </Rule>
  <TestResult id="r1">
    <profile idref="xccdf_org.ssgproject.content_profile_cis_level1_server"/>
    <rule-result idref="xccdf_org.ssgproject.content_rule_sshd_disable_root_login" severity="medium">
      <result>fail</result>
    </rule-result>
    <rule-result idref="xccdf_org.ssgproject.content_rule_package_telnet_removed" severity="high">
      <result>pass</result>
    </rule-result>
    <rule-result idref="xccdf_org.ssgproject.content_rule_banner_etc_issue" severity="low">
      <result>fail</result>
    </rule-result>
    <rule-result idref="xccdf_org.ssgproject.content_rule_grub2_password" severity="high">
      <result>notapplicable</result>
    </rule-result>
    <rule-result idref="xccdf_org.ssgproject.content_rule_unselected" severity="low">
      <result>notselected</result>
    </rule-result>
    <score system="urn:xccdf:scoring:default" maximum="100">71.5</score>
  </TestResult>
</Benchmark>
"""


def test_xccdf_severity_gating(tmp_path):
    path = tmp_path / "results.xml"
    path.write_text(XCCDF)
    suite = audit.xccdf_results(path, severities={"high", "medium"})
    by_name = {c.name: c for c in suite.checks}
    assert by_name["sshd_disable_root_login"].status is Status.FAIL
    assert by_name["sshd_disable_root_login"].message == "Disable SSH Root Login"
    assert by_name["banner_etc_issue"].status is Status.WARN  # low severity -> warn only
    assert by_name["grub2_password"].status is Status.SKIP
    assert "unselected" not in by_name
    assert suite.properties["score"] == 71.5
    assert suite.properties["profile"].endswith("cis_level1_server")


def test_lynis_report(tmp_path):
    path = tmp_path / "lynis-report.dat"
    path.write_text(
        "# Lynis Report\nhardening_index=64\n"
        "warning[]=SSH-7408|Root can log in via SSH|-|-|\n"
        "suggestion[]=AUTH-9230|Configure password hashing rounds|-|-|\n"
    )
    suite = audit.lynis_report(path, min_hardening_index=70)
    by_name = {c.name: c.status for c in suite.checks}
    assert by_name["hardening_index"] is Status.FAIL
    assert by_name["warning SSH-7408"] is Status.WARN
    assert suite.properties == {"hardening_index": 64, "suggestions": 1}
