"""Runs the 'Release commit gate' step of release.yml against a fake gh."""
import json, os, pathlib, re, stat, subprocess, tempfile, unittest

WF = pathlib.Path(__file__).parent.parent / ".github/workflows/release.yml"
FAKE_GH = """#!/bin/bash
if [ "$1" = pr ]; then printf '%s\\n' $FAKE_PRS; exit 0; fi
sha=$(sed -E 's#.*/commits/([^/]+)/check-runs.*#\\1#' <<<"$2")
cat "$FAKE_DIR/$sha.json"
"""


def gate_script():
    m = re.search(r"- name: Release commit gate\n(?:.*\n)*?        run: \|\n((?:          .*\n|\n)+)", WF.read_text())
    return "\n".join(l[10:] for l in m.group(1).splitlines())


def run(prs, runs_by_sha, head="head"):
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        gh = d / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        for sha, runs in runs_by_sha.items():
            (d / f"{sha}.json").write_text("\n".join(json.dumps(r) for r in runs))
        env = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}", FAKE_DIR=str(d), FAKE_PRS=" ".join(prs),
                   GITHUB_SHA=head, GITHUB_REPOSITORY="o/r")
        return subprocess.run(["bash", "-c", gate_script()], env=env, capture_output=True, text=True).returncode


def cr(name, id, conclusion):
    return {"name": name, "id": id, "conclusion": conclusion}


class ReleaseGate(unittest.TestCase):
    def test_all_success(self):
        self.assertEqual(0, run(["r1"], {"r1": [cr("Backend Checks", 1, "success"), cr("Frontend Checks", 2, "success")]}))

    def test_backend_only_success(self):
        self.assertEqual(0, run(["r1"], {"r1": [cr("Backend Checks", 1, "success")]}))

    def test_backend_missing(self):
        self.assertNotEqual(0, run(["r1"], {"r1": [cr("Frontend Checks", 2, "success")]}))

    def test_frontend_failure(self):
        self.assertNotEqual(0, run(["r1"], {"r1": [cr("Backend Checks", 1, "success"), cr("Frontend Checks", 2, "failure")]}))

    def test_rerun_newer_success_wins(self):
        self.assertEqual(0, run(["r1"], {"r1": [cr("Backend Checks", 1, "failure"), cr("Backend Checks", 3, "success")]}))

    def test_rerun_newer_failure_wins(self):
        self.assertNotEqual(0, run(["r1"], {"r1": [cr("Backend Checks", 1, "success"), cr("Backend Checks", 3, "failure")]}))

    def test_own_commit_skipped(self):
        self.assertEqual(0, run(["head"], {}))

    def test_no_pending_pr(self):
        self.assertEqual(0, run([], {}))


if __name__ == "__main__":
    unittest.main()
