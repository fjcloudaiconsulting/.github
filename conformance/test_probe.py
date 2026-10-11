import base64
import json
import os
import re
import unittest

import probe

R = "o/r"
SHA = "a" * 40
SNAP = "b" * 40
SHARED = "fjcloudaiconsulting/.github/.github/workflows/%s.yml@v1"
CI = "jobs:\n  x:\n" + "".join("    uses: %s\n" % (SHARED % n) for n in ("pr-title", "build-image", "release")) + \
    "  y:\n    steps:\n      - uses: fjcloudaiconsulting/.github/actions/no-docker-hub@v1\n"
CHECKOUT = "      - uses: actions/checkout@%s # v7.0.1\n        with:\n          persist-credentials: false\n" % ("a" * 40)
FRONTEND = {"frontend/package.json": '{"packageManager": "pnpm@10.0.0"}', ".nvmrc": "22\n"}
ORG = {"actions/checkout": "7"}
CHECKS = ["Backend Checks", "Frontend Checks"]
ALWAYS = ["Backend Checks"]
GOOD = {
    ".github/workflows/ci.yml": CI + CHECKOUT,
    "release-please-config.json": json.dumps({"release-type": "simple", "bump-minor-pre-major": True}),
    "version.txt": "0.1.0\n",
    ".release-please-manifest.json": "{}",
    "CHANGELOG.md": "x",
    ".env.example": "x",
    "renovate.json": '{"extends": ["github>fjcloudaiconsulting/.github#v1"]}',
}
PROTECTED = {"protected": True, "protection": {"required_status_checks": {"contexts": CHECKS}}}


def run(files=None, branch=PROTECTED, rules=(), ref="main", errors=None, calls=None, org=ORG):
    """dict-backed fake: errors maps a path substring to a status code."""
    files = {**GOOD, **(files or {})}
    routes = {"/repos/o/r/branches/main": branch if isinstance(branch, str) else json.dumps(branch), "/repos/o/r/rules/branches/main": json.dumps(list(rules)),
              "/repos/o/r/commits/" + ref: json.dumps({"sha": SNAP})}
    for p, text in files.items():
        if text is not None:
            routes["/repos/o/r/contents/" + p] = text
    for d in (".github/workflows", ".github/actions", "frontend"):
        names = sorted({p[len(d) + 1:].split("/")[0] for p, t in files.items() if p.startswith(d + "/") and t is not None})
        if names:
            routes["/repos/o/r/contents/" + d] = json.dumps([{"name": n} for n in names])

    def fetch(path):
        if calls is not None:
            calls.append(path)
        for sub, code in (errors or {}).items():
            if sub in path:
                return code, ""
        key = path.split("?")[0]
        return (200, routes[key]) if key in routes else (404, "")

    return probe.check(R, ref, ALWAYS, fetch, org)


def wf(*uses):
    return {".github/workflows/ci.yml": CI + "".join("      - uses: %s\n" % u for u in uses)}


class Probe(unittest.TestCase):
    def test_good_baseline_clean(self):
        self.assertEqual(run(), [])

    def test_tag_pinned_third_party_flagged(self):  # fence: allow-all
        f = run(wf("actions/checkout@v7"))
        self.assertTrue(any("actions/checkout@v7" in x for x in f), f)

    def test_allowed_forms(self):  # guard: over-strict regex
        self.assertEqual(run(wf("./x", "docker://a/b@sha256:" + "0" * 64, "a/b/c@" + SHA, SHARED % "x",
                                "fjcloudaiconsulting/.github/actions/uv-sync@v1")), [])

    def test_shared_action_off_major_flagged(self):
        f = run(wf("fjcloudaiconsulting/.github/actions/uv-sync@main"))
        self.assertTrue(any("uv-sync@main" in x for x in f), f)

    def test_shared_workflow_on_branch_flagged(self):  # fence: prefix whitelist
        f = run(wf("fjcloudaiconsulting/.github/.github/workflows/x.yml@main"))
        self.assertTrue(any("@main" in x for x in f), f)

    def test_shared_workflow_v10_flagged(self):
        f = run(wf(SHARED % "x" + "0"))
        self.assertTrue(any("x.yml@v10" in x for x in f), f)

    def test_commented_uses_ignored(self):  # fence: false drift
        self.assertEqual(run(wf("a/b@" + SHA) | {".github/workflows/c.yml": "  # uses: a/b@v1\n    # - uses: c/d@v2\n"}), [])

    def test_action_yml_scanned(self):
        f = run({".github/actions/z/action.yaml": "    - uses: a/b@v1\n"})
        self.assertTrue(any("a/b@v1" in x for x in f), f)

    def test_comments_and_quotes_stripped(self):  # guard
        self.assertEqual(run(wf("a/b@%s # v4.2.2" % SHA, '"./x"', "'./y' # c")), [])

    def test_readme_snippets_clean(self):  # guard: README matches the probe
        here = os.path.dirname(__file__)
        text = open(os.path.join(here, "..", "README.md")).read()
        snippets = "\n".join(re.findall(r"```yaml\n(.*?)```", text, re.S))
        self.assertIn("uses:", snippets)
        self.assertEqual(run({".github/workflows/ci.yml": snippets + "- uses: fjcloudaiconsulting/.github/actions/no-docker-hub@v1\n"}), [])  # README shows no-docker-hub elsewhere

    def test_comment_does_not_hide_tag(self):  # fence: dropping lines containing #
        f = run(wf("actions/checkout@v7 # v7"))
        self.assertTrue(any("actions/checkout@v7" in x for x in f), f)

    def test_required_checks_from_ruleset_only(self):  # fence: classic-only read
        rule = {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": c} for c in CHECKS]}}
        self.assertEqual(run(branch={"protected": False}, rules=[rule]), [])

    def test_checks_array_context(self):
        branch = {"protected": True, "protection": {"required_status_checks": {"contexts": [], "checks": [{"context": c} for c in CHECKS]}}}
        self.assertEqual(run(branch=branch), [])

    def test_one_of_two_missing(self):  # fence: any()
        branch = {"protected": True, "protection": {"required_status_checks": {"contexts": ["Backend Checks"]}}}
        f = run(FRONTEND, branch=branch)
        self.assertEqual(len(f), 1, f)
        self.assertIn("Frontend Checks", f[0])

    def test_backend_only_repo_needs_only_backend(self):  # fence: always-both
        branch = {"protected": True, "protection": {"required_status_checks": {"contexts": ["Backend Checks"]}}}
        self.assertEqual(run(branch=branch), [])

    def test_frontend_dir_requires_frontend_check(self):  # fence: never-frontend
        branch = {"protected": True, "protection": {"required_status_checks": {"contexts": ["Backend Checks"]}}}
        f = run(FRONTEND, branch=branch)
        self.assertEqual(f, ["required check missing: Frontend Checks"])

    def test_frontend_lookup_error_could_not_run(self):  # guard
        with self.assertRaises(probe.CouldNotRun):
            run(errors={"contents/frontend": 500})

    def test_unprotected(self):
        self.assertTrue(run(branch={"protected": False}))

    def test_rules_only_with_protected_false_is_protected(self):  # fence: classic-only
        rule = {"type": "pull_request", "parameters": {}}
        f = run(branch={"protected": False}, rules=[rule])
        self.assertFalse(any("protect" in x for x in f), f)

    def test_non_protecting_rules_ignored(self):  # fence: any rule counts
        f = run(branch={"protected": False}, rules=[{"type": "creation"}, {"type": "required_signatures"}])
        self.assertTrue(any("protect" in x for x in f), f)
        for t in ("pull_request", "required_status_checks", "non_fast_forward", "deletion"):
            self.assertFalse(any("not protected" in x for x in run(branch={"protected": False}, rules=[{"type": t}])), t)

    def test_no_actions_dir_is_not_a_finding(self):  # guard
        self.assertEqual(run(errors={"contents/.github/actions": 404}), [])

    def test_http_errors_could_not_run(self):  # fence: swallowing errors
        for code in (403, 429, 500, 502):
            for where in ("branches/main", "rules/branches", "version.txt", "contents/.github/workflows", "commits/"):
                with self.assertRaises(probe.CouldNotRun, msg=(code, where)):
                    run(errors={where: code})

    def test_bad_json_could_not_run(self):
        with self.assertRaises(probe.CouldNotRun):
            run(branch="not json")

    def test_missing_file_after_ref_is_finding(self):
        f = run({"version.txt": None})
        self.assertTrue(any("version.txt" in x for x in f), f)

    def test_missing_branch_or_ref_could_not_run(self):  # fence: bogus drift
        for where, code in (("branches/main", 404), ("commits/", 404), ("commits/", 422)):
            with self.assertRaises(probe.CouldNotRun, msg=(where, code)):
                run(errors={where: code})

    def test_protection_reads_main_files_read_resolved_sha(self):  # one snapshot
        calls = []
        run(ref="feat/x", calls=calls)
        self.assertTrue(any("/contents/" in c for c in calls))
        self.assertTrue(all("ref=" + SNAP in c for c in calls if "/contents/" in c), calls)
        self.assertTrue(all("feat" not in c for c in calls if "branches/main" in c), calls)

    def test_release_keys_at_root_clean(self):  # fence: package-only read
        cfg = {"release-type": "simple", "bump-minor-pre-major": True, "packages": {".": {}}}
        self.assertEqual(run({"release-please-config.json": json.dumps(cfg)}), [])

    def test_release_keys_in_package(self):
        cfg = {"packages": {".": {"release-type": "node", "bump-minor-pre-major": True}}}
        f = run({"release-please-config.json": json.dumps(cfg)})
        self.assertTrue(any("release-type" in x for x in f), f)

    def test_prerelease_version_no_crash(self):
        self.assertEqual(run({"version.txt": "1.0.0-rc.1\n"} | {"release-please-config.json": json.dumps({"release-type": "simple"})}), [])

    def test_bump_pre_major(self):  # fence: missing bump below 1.0
        cfg = {"release-type": "simple"}
        f = run({"version.txt": "0.20.0", "release-please-config.json": json.dumps(cfg)})
        self.assertTrue(any("bump-minor-pre-major" in x for x in f), f)
        self.assertEqual(run({"version.txt": "1.2.0", "release-please-config.json": json.dumps(cfg)}), [])

    def test_renovate_main_flagged(self):  # fence
        f = run({"renovate.json": '{"extends": ["github>fjcloudaiconsulting/.github#main"]}'})
        self.assertTrue(any("enovate" in x for x in f), f)

    def test_renovate_v10_and_commented_flagged(self):
        for body in ('"github>fjcloudaiconsulting/.github#v10"', '// "github>fjcloudaiconsulting/.github#v1"'):
            self.assertTrue(run({"renovate.json5": body, "renovate.json": None}), body)

    def test_renovate_single_quote(self):
        self.assertEqual(run({"renovate.json": "extends: ['github>fjcloudaiconsulting/.github#v1']"}), [])

    def test_renovate_json5_found(self):
        self.assertEqual(run({"renovate.json": None, ".github/renovate.json5": '  "github>fjcloudaiconsulting/.github#v1",'}), [])

    def test_missing_shared_call(self):  # fence
        f = run({".github/workflows/ci.yml": CI.replace(SHARED % "build-image", "./x") + CHECKOUT})
        self.assertEqual(len(f), 1, f)
        self.assertIn("build-image", f[0])

    def test_direct_promote_and_smoke_no_longer_accepted(self):
        ci = CI.replace(SHARED % "release", SHARED % "promote-release") + "    uses: %s\n" % (SHARED % "smoke")
        f = run({".github/workflows/ci.yml": ci + CHECKOUT})
        self.assertTrue(any("release.yml@v1 (direct promote-release/smoke" in x for x in f), f)

    def test_checkout_persist_credentials(self):
        bad = CI + "      - uses: actions/checkout@%s # v7.0.1\n" % SHA
        f = run({".github/workflows/ci.yml": bad})
        self.assertEqual(len(f), 1, f)
        self.assertIn("persist-credentials", f[0])
        # step with persist-credentials in a neighbouring step still fails; "- name:" first form passes
        nb = CI + "      - uses: actions/checkout@%s # v7.0.1\n      - run: x\n        with:\n          persist-credentials: false\n" % SHA
        self.assertEqual(len(run({".github/workflows/ci.yml": nb})), 1)
        named = CI + "      - name: c\n        uses: actions/checkout@%s # v7.0.1\n        with:\n          persist-credentials: false\n" % SHA
        self.assertEqual(run({".github/workflows/ci.yml": named}), [])

    def test_action_major_must_match_org(self):
        f = run(wf("actions/checkout@%s # v6.2.0" % SHA))
        self.assertTrue(any("actions/checkout is v6, org pins v7" in x for x in f), f)
        self.assertFalse(any("org pins" in x for x in run(wf("actions/checkout@%s # v7.9.9" % SHA) , )), "same major, other minor")
        self.assertFalse(any("org pins" in x for x in run(wf("other/x@%s # v1" % SHA))), "action the org does not use")
        self.assertFalse(any("org pins" in x for x in run(wf("actions/checkout@%s # v99.0.0" % SHA))), "app ahead of the org")

    def test_version_expression_is_not_a_literal(self):
        lit = CI + "      - uses: actions/setup-node@%s # v7.1.0\n        with:\n          node-version: 24\n" % SHA
        expr = CI + "      - uses: actions/setup-node@%s # v7.1.0\n        with:\n          node-version: ${{ matrix.node }}\n" % SHA
        self.assertTrue(any("literal" in x for x in run({".github/workflows/ci.yml": lit})))
        self.assertFalse(any("literal" in x for x in run({".github/workflows/ci.yml": expr})))

    def test_org_majors_read_from_repo(self):  # guard: the probe reads this repo's real pins
        m = probe.org_majors()
        self.assertRegex(m["actions/checkout"], r"^\d+$")
        self.assertIn("astral-sh/setup-uv", m)

    def test_direct_setup_uv_and_release_please_flagged(self):
        f = run(wf("astral-sh/setup-uv@%s # v10" % SHA, "googleapis/release-please-action@%s # v5" % SHA))
        self.assertTrue(any("direct astral-sh/setup-uv" in x for x in f), f)
        self.assertTrue(any("direct googleapis/release-please-action" in x for x in f), f)

    def test_no_docker_hub_required(self):
        f = run({".github/workflows/ci.yml": CI.replace("no-docker-hub@v1", "uv-sync@v1") + CHECKOUT})
        self.assertEqual(f, ["no use of fjcloudaiconsulting/.github/actions/no-docker-hub@v1"])

    def test_ci_yml_required(self):
        f = run({".github/workflows/ci.yml": None, ".github/workflows/other.yml": CI + CHECKOUT})
        self.assertEqual(f, [".github/workflows/ci.yml missing"])

    def test_literal_tool_versions_flagged(self):
        for line in ("node-version: 22", "python-version: '3.13'"):
            f = run(wf("./x") | {".github/workflows/c.yml": "      - uses: ./x\n        with:\n          %s\n" % line})
            self.assertEqual(len(f), 1, f)
        self.assertEqual(run({".github/workflows/c.yml": "          node-version-file: .nvmrc\n          python-version-file: x\n"}), [])

    def test_frontend_version_files(self):
        pkg = FRONTEND
        self.assertEqual(run(pkg, branch=PROTECTED | {"protection": {"required_status_checks": {"contexts": CHECKS}}}), [])
        self.assertEqual(run(pkg | {".nvmrc": None}), [".nvmrc missing"])
        self.assertEqual(run(pkg | {"frontend/package.json": "{}"}), ["frontend/package.json has no packageManager"])

    def test_backend_version_files(self):
        be = {"backend/pyproject.toml": "x", "backend/.python-version": "3.13\n", "backend/.tool-versions": "# c\nuv 0.9.0\n"}
        self.assertEqual(run(be), [])
        self.assertEqual(run(be | {"backend/.python-version": None}), ["backend/.python-version missing"])
        for tv in (None, "# uv 0.9\n", "uv 1\nuv 2\n"):
            self.assertEqual(len(run(be | {"backend/.tool-versions": tv})), 1, tv)


class Main(unittest.TestCase):
    def plan(self, argv, fetch=None):
        import contextlib, io
        out = io.StringIO()
        old = probe.real_fetch
        probe.real_fetch = fetch or (lambda p: (_ for _ in ()).throw(AssertionError("no fetch expected")))
        try:
            with contextlib.redirect_stdout(out):
                probe.main(argv)
        finally:
            probe.real_fetch = old
        return json.loads(out.getvalue())

    def test_invalid_targets_could_not_run_without_fetch(self):
        for t in ("evil/other", "fjcloudaiconsulting/", "fjcloudaiconsulting/a/b", "fjcloudaiconsulting/x y",
                  "fjcloudaiconsulting/a@../b", "fjcloudaiconsulting/a@x?y"):
            entry = self.plan([t])[t.split("@")[0]]
            self.assertEqual(entry["verdict"], "could-not-run", t)
            self.assertIn("invalid target", " ".join(entry["findings"]), t)

    def test_unexpected_error_is_per_repo_could_not_run(self):  # one bad file must not empty the plan
        def fetch(path):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
        plan = self.plan(["fjcloudaiconsulting/a", "evil/other"], fetch)
        self.assertEqual({k: v["verdict"] for k, v in plan.items()}, {"fjcloudaiconsulting/a": "could-not-run", "evil/other": "could-not-run"})


if __name__ == "__main__":
    unittest.main()
