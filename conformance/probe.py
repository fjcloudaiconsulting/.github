"""Conformance probe for RELEASE_CONTRACT.md. Prints a JSON plan; python3 stdlib only.

Usage: probe.py [owner/repo[@ref] ...]   (default: every target in TARGETS)
Known limits: a `uses:` line inside a `run: |` block is a false positive; a repo before its first
release lacks CHANGELOG.md and is reported; `/* */` comments in a Renovate config are not skipped.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

TARGETS = ["fjcloudaiconsulting/ziftbook", "fjcloudaiconsulting/tbd"]
CHECKS = ["Backend Checks"]  # always required; "Frontend Checks" is added when the repo has frontend/
TARGET = re.compile(r"fjcloudaiconsulting/[A-Za-z0-9._-]+(@(?!.*\.\.)[A-Za-z0-9._/-]+)?")
SHARED = "fjcloudaiconsulting/.github/.github/workflows/%s.yml@v1"
PROTECTING = {"pull_request", "required_status_checks", "non_fast_forward", "deletion"}
USES = re.compile(r"^\s*-?\s*uses:(.*)$")
OK = [re.compile(p) for p in (
    r"\./.*",
    r"docker://\S+@sha256:[0-9a-f]{64}",
    r"fjcloudaiconsulting/\.github/\.github/workflows/[^/@\s]+\.yml@v1",
    r"fjcloudaiconsulting/\.github/actions/[\w-]+@v1",
    r"[\w.-]+/[\w.-]+(/[^@\s]+)?@[0-9a-f]{40}")]
RENOVATE = ["renovate.json", "renovate.json5", ".github/renovate.json", ".github/renovate.json5",
            ".renovaterc", ".renovaterc.json", ".renovaterc.json5"]


class CouldNotRun(Exception):
    pass


def real_fetch(path):
    req = urllib.request.Request("https://api.github.com" + path, headers={
        "Accept": "application/vnd.github.raw+json", "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": "Bearer " + os.environ.get("GH_TOKEN", "")})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""
    except OSError as e:
        raise CouldNotRun("network: %s" % e)


def check(repo, ref, required_checks, fetch):
    def get(path, ok=(200,)):
        status, text = fetch(path)
        if status in ok or status == 404:
            return status, text
        raise CouldNotRun("%s: HTTP %s" % (path, status))

    def getjson(path, missing=None):
        status, text = get(path)
        if status == 404:
            if missing is None:
                raise CouldNotRun("%s: not found" % path)
            return missing
        try:
            return json.loads(text)
        except ValueError:
            raise CouldNotRun("%s: unparseable JSON" % path)

    base = "/repos/" + repo
    branch = getjson(base + "/branches/main")
    rules = getjson(base + "/rules/branches/main", [])
    status, text = get(base + "/commits/" + ref)  # a missing ref is 422 (rejected by get) or 404 (empty body, fails the parse)
    try:
        snap = json.loads(text)["sha"]
    except (ValueError, KeyError, TypeError):
        raise CouldNotRun("commits/%s: unparseable" % ref)

    def read(path):
        status, text = get("%s/contents/%s?ref=%s" % (base, path, snap))
        return text if status == 200 else None

    def listing(path):
        text = read(path)
        try:
            return [e["name"] for e in json.loads(text)] if text else []
        except (ValueError, KeyError, TypeError):
            raise CouldNotRun("%s: unparseable listing" % path)

    findings = []

    # 1. protection
    if not (branch.get("protected") or any(r.get("type") in PROTECTING for r in rules)):
        findings.append("main is not protected")
    rsc = (branch.get("protection") or {}).get("required_status_checks") or {}
    have = set(rsc.get("contexts") or []) | {c["context"] for c in rsc.get("checks") or []}
    for r in rules:
        if r.get("type") == "required_status_checks":
            have |= {c["context"] for c in r.get("parameters", {}).get("required_status_checks", [])}
    status, _ = get("%s/contents/frontend?ref=%s" % (base, snap))
    need = required_checks + (["Frontend Checks"] if status == 200 else [])
    findings += ["required check missing: " + c for c in need if c not in have]

    # 2 + 3. actions
    files = [".github/workflows/" + n for n in listing(".github/workflows") if re.search(r"\.ya?ml$", n)]
    for d in listing(".github/actions"):
        files += [".github/actions/%s/action.%s" % (d, e) for e in ("yml", "yaml")]
    used = []
    for f in files:
        for line in (read(f) or "").splitlines():
            m = USES.match(line)
            if m:
                v = re.sub(r"\s+#.*$", "", m.group(1)).strip().strip("\"'")
                used.append(v)
                if not any(p.fullmatch(v) for p in OK):
                    findings.append("%s: disallowed uses: %s" % (f, v))
    # The shared release workflow calls promote-release and smoke itself.
    released = SHARED % "release" in used
    findings += ["no call to shared workflow " + SHARED % n for n in ("pr-title", "build-image", "promote-release", "smoke")
                 if SHARED % n not in used and not (released and n in ("promote-release", "smoke"))]

    # 4. release-please
    version = read("version.txt")
    text = read("release-please-config.json")
    if text is None:
        findings.append("release-please-config.json missing")
    else:
        try:
            cfg = json.loads(text)
            pkg = cfg.get("packages", {}).get(".", {})
            val = lambda k: pkg.get(k, cfg.get(k))
            if val("release-type") != "simple":
                findings.append("release-please release-type is not simple")
            if version is not None and version.strip().split(".")[0].isdigit() and int(version.strip().split(".")[0]) < 1 \
                    and val("bump-minor-pre-major") is not True:
                findings.append("release-please bump-minor-pre-major must be true below 1.0")
        except (ValueError, AttributeError):
            findings.append("release-please-config.json is not a valid config")

    # 5. files
    findings += [f + " missing" for f, t in (("version.txt", version), (".release-please-manifest.json", read(".release-please-manifest.json")),
                 ("CHANGELOG.md", read("CHANGELOG.md")), (".env.example", read(".env.example"))) if t is None]

    # 6. renovate
    for f in RENOVATE:
        text = read(f)
        if text is not None:
            if not any(re.search(r"github>fjcloudaiconsulting/\.github#v1[\"']", l) for l in text.splitlines() if not l.lstrip().startswith("//")):
                findings.append("%s does not extend github>fjcloudaiconsulting/.github#v1" % f)
            break
    else:
        findings.append("no Renovate config found")
    return sorted(set(findings))


def main(argv):
    plan = {}
    for t in argv or TARGETS:
        repo, _, ref = t.partition("@")
        try:
            if not TARGET.fullmatch(t):
                raise CouldNotRun("invalid target %r" % t)
            f = check(repo, ref or "main", CHECKS, real_fetch)
            plan[repo] = {"verdict": "drift" if f else "clean", "findings": f}
        except Exception as e:  # one bad repo must not empty the plan
            plan[repo] = {"verdict": "could-not-run", "findings": [str(e) or type(e).__name__]}
    print(json.dumps(plan, indent=2, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1:])
