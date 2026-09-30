"""``bmcval`` command line: the single entry point Jenkins stages call."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import fetch, report
from .checks import CheckSuite
from .gpu import health, xid
from .profiles import available_profiles, load_profile
from .qemu import BootError, QemuBmc, find_image
from .redfish import RedfishClient
from .security import audit, cve_diff, squashfs

EXIT_OK, EXIT_GATE_FAILED, EXIT_INFRA = 0, 1, 2


def _client(profile) -> RedfishClient:
    ep, cred = profile.endpoints, profile.credentials
    return RedfishClient(
        base_url=f"https://{os.environ.get('BMC_HOST', ep['host'])}:"
        f"{os.environ.get('BMC_HTTPS_PORT', ep['https_port'])}",
        username=os.environ.get("BMC_USERNAME", cred["username"]),
        password=os.environ.get("BMC_PASSWORD", cred["password"]),
    )


def wait_redfish(profile, timeout: float) -> float:
    """Ready means: sessions can be created *and* the manager resource is populated.

    bmcweb answers /redfish/v1 long before D-Bus services finish starting, so
    the service root alone is not a reliable readiness signal.
    """
    client = _client(profile)
    start = time.monotonic()

    def ready() -> bool:
        client.login()
        try:
            managers = client.get_json("/redfish/v1/Managers")
            return managers.get("Members@odata.count", 0) > 0
        finally:
            client.logout()

    client.wait_until(ready, timeout=timeout, poll=10, what="Redfish readiness")
    return time.monotonic() - start


def _write_suite(suite: CheckSuite, args) -> int:
    if getattr(args, "junit", None):
        suite.write_junit(Path(args.junit))
    if getattr(args, "json", None):
        suite.write_json(Path(args.json))
    summary = {k: v for k, v in suite.to_dict()["summary"].items() if v}
    print(f"{suite.name}: {summary}")
    return EXIT_GATE_FAILED if suite.failed else EXIT_OK


# ------------------------------------------------------------------ commands
def cmd_profiles(args) -> int:
    for name in available_profiles():
        p = load_profile(name)
        print(f"{name:24} {p.description}")
    return EXIT_OK


def cmd_fetch(args) -> int:
    kinds = set(args.kinds.split(","))
    out = fetch.download(args.jenkins, args.machine, args.build, Path(args.dest), kinds)
    print(out)
    return EXIT_OK


def cmd_boot(args) -> int:
    profile = load_profile(args.profile)
    image = (
        Path(args.image)
        if args.image
        else find_image(Path(args.image_dir), profile.qemu["image_glob"])
    )
    bmc = QemuBmc(profile, image, Path(args.workdir))
    pid = bmc.start()
    print(f"QEMU pid {pid}: {' '.join(bmc.command())}")
    try:
        t_console = bmc.wait_for_console(profile.timeout("boot", 900))
        print(f"console login prompt after {t_console:.0f}s")
        if not args.no_wait_redfish:
            t_rf = wait_redfish(profile, profile.timeout("redfish_ready", 600))
            print(f"Redfish ready after a further {t_rf:.0f}s")
            (Path(args.workdir) / "boot-metrics.json").write_text(
                json.dumps({"console_s": round(t_console), "redfish_s": round(t_rf)})
            )
    except (BootError, TimeoutError) as exc:
        print(f"BOOT FAILED: {exc}", file=sys.stderr)
        print(f"last console lines:\n{_tail(bmc.console_log)}", file=sys.stderr)
        bmc.stop()
        return EXIT_INFRA
    return EXIT_OK


def cmd_wait(args) -> int:
    profile = load_profile(args.profile)
    try:
        print(f"Redfish ready after {wait_redfish(profile, args.timeout):.0f}s")
    except TimeoutError as exc:
        print(exc, file=sys.stderr)
        return EXIT_INFRA
    return EXIT_OK


def cmd_stop(args) -> int:
    profile = load_profile(args.profile)
    bmc = QemuBmc(profile, Path("/dev/null"), Path(args.workdir))
    bmc.stop()
    return EXIT_OK


def cmd_extract_rootfs(args) -> int:
    image = Path(args.image)
    hits = squashfs.find_squashfs(image)
    if not hits:
        print(f"no SquashFS found in {image}", file=sys.stderr)
        return EXIT_INFRA
    for h in hits:
        print(f"squashfs @0x{h.offset:08x} size={h.size} block={h.block_size} comp={h.compression}")
    rootfs = max(hits, key=lambda h: h.size)  # the rootfs dwarfs any other squashfs blob
    out = Path(args.out)
    blob = squashfs.carve(image, rootfs, out.with_suffix(".squashfs"))
    print(f"carved rootfs -> {blob}")
    if shutil.which("unsquashfs"):
        if out.exists():
            shutil.rmtree(out)
        subprocess.run(["unsquashfs", "-q", "-no-xattrs", "-d", str(out), str(blob)], check=True)
        print(f"unpacked -> {out}")
    else:
        print("unsquashfs not installed; leaving the carved blob only", file=sys.stderr)
    return EXIT_OK


def cmd_cve_diff(args) -> int:
    policy = cve_diff.Policy.load(Path(args.policy) if args.policy else None)
    baseline = cve_diff.load_grype(Path(args.baseline))
    candidate = cve_diff.load_grype(Path(args.candidate))
    excluded: dict[str, int] = {}
    if args.baseline_manifest:
        baseline, _ = cve_diff.shipped_only(
            baseline, cve_diff.load_manifest(Path(args.baseline_manifest))
        )
    if args.candidate_manifest:
        candidate, excluded = cve_diff.shipped_only(
            candidate, cve_diff.load_manifest(Path(args.candidate_manifest))
        )
    result = cve_diff.diff(baseline, candidate, policy)
    result.excluded = excluded
    md = cve_diff.to_markdown(result, args.baseline_label, args.candidate_label)
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(md)
    print(md.split("## Blocking")[0])
    return _write_suite(cve_diff.to_suite(result), args)


def cmd_cis(args) -> int:
    code = EXIT_OK
    if args.xccdf:
        sev = set(args.fail_severity.split(",")) if args.fail_severity else None
        s = audit.xccdf_results(Path(args.xccdf), severities=sev)
        code = max(code, _write_suite(s, argparse.Namespace(junit=args.junit, json=args.json)))
    if args.lynis:
        s = audit.lynis_report(Path(args.lynis), args.min_hardening_index)
        junit = Path(args.junit).with_name("lynis.xml") if args.junit else None
        code = max(code, _write_suite(s, argparse.Namespace(junit=junit, json=None)))
    return code


def cmd_xid(args) -> int:
    events = xid.parse_file(Path(args.log))
    suite = xid.to_suite(events)
    print(f"node action: {suite.properties['node_action']}")
    return _write_suite(suite, args)


def cmd_gpu_health(args) -> int:
    return _write_suite(health.nvml_health(Path(args.input)), args)


def cmd_dcgm(args) -> int:
    return _write_suite(health.dcgm_diag(Path(args.input)), args)


def cmd_report(args) -> int:
    summary = report.build_summary(Path(args.reports), args.build, args.sha, args.firmware)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    history_dir = Path(args.history)
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / f"{args.build}.json").write_text(json.dumps(summary))
    history = report.load_history(history_dir)
    (out / "index.html").write_text(report.render_html(summary, history))
    print(f"report -> {out / 'index.html'} ({'PASS' if summary['ok'] else 'FAIL'})")
    return EXIT_OK if summary["ok"] else EXIT_GATE_FAILED


def _tail(path: Path, n: int = 40) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except FileNotFoundError:
        return "(no console log)"


# -------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bmcval", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("profiles", help="list bundled platform profiles").set_defaults(fn=cmd_profiles)

    f = sub.add_parser("fetch", help="download a firmware build from upstream OpenBMC Jenkins")
    f.add_argument("--machine", default="romulus")
    f.add_argument("--build", default="lastSuccessfulBuild", help="build number or alias")
    f.add_argument("--jenkins", default=fetch.DEFAULT_JENKINS)
    f.add_argument("--dest", default="build/images")
    f.add_argument("--kinds", default="mtd,spdx,manifest,update")
    f.set_defaults(fn=cmd_fetch)

    b = sub.add_parser("boot", help="boot a firmware image under QEMU and wait until usable")
    b.add_argument("--profile", default="qemu-romulus")
    b.add_argument("--image", help="explicit .static.mtd path")
    b.add_argument("--image-dir", default="build/images")
    b.add_argument("--workdir", default="build/qemu")
    b.add_argument("--no-wait-redfish", action="store_true")
    b.set_defaults(fn=cmd_boot)

    w = sub.add_parser("wait", help="wait for Redfish readiness on an already-running BMC")
    w.add_argument("--profile", default="qemu-romulus")
    w.add_argument("--timeout", type=float, default=600)
    w.set_defaults(fn=cmd_wait)

    s = sub.add_parser("stop", help="stop the QEMU instance started by 'boot'")
    s.add_argument("--profile", default="qemu-romulus")
    s.add_argument("--workdir", default="build/qemu")
    s.set_defaults(fn=cmd_stop)

    e = sub.add_parser("extract-rootfs", help="carve and unpack the SquashFS rootfs")
    e.add_argument("image")
    e.add_argument("--out", required=True, help="directory to unpack into")
    e.set_defaults(fn=cmd_extract_rootfs)

    c = sub.add_parser("cve-diff", help="gate a candidate firmware on its CVE delta")
    c.add_argument("--baseline", required=True, help="grype JSON of the released firmware")
    c.add_argument("--candidate", required=True, help="grype JSON of the build under test")
    c.add_argument("--baseline-label", default="baseline")
    c.add_argument("--candidate-label", default="candidate")
    c.add_argument("--policy", help="YAML policy with fail_on and waivers")
    c.add_argument("--baseline-manifest", help="image manifest: gate only shipped packages")
    c.add_argument("--candidate-manifest", help="image manifest: gate only shipped packages")
    c.add_argument("--markdown")
    c.add_argument("--junit")
    c.add_argument("--json")
    c.set_defaults(fn=cmd_cve_diff)

    a = sub.add_parser("cis", help="convert OpenSCAP / Lynis results to JUnit")
    a.add_argument("--xccdf")
    a.add_argument("--lynis")
    a.add_argument("--fail-severity", default="high,medium", help="XCCDF severities that fail")
    a.add_argument("--min-hardening-index", type=int, default=70)
    a.add_argument("--junit")
    a.add_argument("--json")
    a.set_defaults(fn=cmd_cis)

    x = sub.add_parser("xid", help="triage NVIDIA Xid events in a kernel log")
    x.add_argument("log")
    x.add_argument("--junit")
    x.add_argument("--json")
    x.set_defaults(fn=cmd_xid)

    g = sub.add_parser("gpu-health", help="convert gpu-health JSON to JUnit")
    g.add_argument("input")
    g.add_argument("--junit")
    g.add_argument("--json")
    g.set_defaults(fn=cmd_gpu_health)

    d = sub.add_parser("dcgm", help="convert 'dcgmi diag -j' output to JUnit")
    d.add_argument("input")
    d.add_argument("--junit")
    d.add_argument("--json")
    d.set_defaults(fn=cmd_dcgm)

    r = sub.add_parser("report", help="aggregate JUnit results into a dashboard")
    r.add_argument("--reports", default="reports")
    r.add_argument("--history", default="build/history")
    r.add_argument("--out", default="reports/dashboard")
    r.add_argument("--build", default=os.environ.get("BUILD_NUMBER", "local"))
    r.add_argument("--sha", default=os.environ.get("GIT_COMMIT", "unknown"))
    r.add_argument("--firmware", default=os.environ.get("FIRMWARE_LABEL", "unknown"))
    r.set_defaults(fn=cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
