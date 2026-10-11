"""Conformance probe for RELEASE_CONTRACT.md. Prints a JSON plan; python3 stdlib only.

Usage: probe.py [owner/repo[@ref] ...]   (default: every target in TARGETS)
Known limits: a `uses:` line (or a `node-version:` line) inside a `run: |` block is a false positive; a repo
before its first release lacks CHANGELOG.md and is reported; `/* */` comments in a Renovate config are not
skipped. The step-block scan for `persist-credentials` is indentation based (no YAML parser); action majors
are compared only for actions this repo itself pins with a `# vX` comment, and an app pin without that
comment is not compared.
"""
import glob
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
PIN = re.compile(r"uses:\s*[\"']?([\w.-]+/[^@\s\"']+)@[0-9a-f]{40}[\"']?\s*#\s*v(\d+)")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
NO_DOCKER_HUB = "fjcloudaiconsulting/.github/actions/no-docker-hub@v1"
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


def org_majors(root=ROOT):
    """{action: major} from this repo's own SHA pins with a `# vX` comment (workflows and composites)."""
    majors = {}
    for pat in (".github/workflows/*.yml", "actions/*/action.yml"):
        for f in sorted(glob.glob(os.path.join(root, pat))):
            with open(f) as fh:
                for line in fh:
                    m = PIN.search(line) if not line.lstrip().startswith("#") else None
                    if m:
                        majors[m.group(1)] = m.group(2)
    return majors


def checkout_steps(text):
    """Yield the lines of every step that uses actions/checkout (indentation based)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not re.match(r"^\s*-?\s*uses:\s*[\"']?actions/checkout@", line):
            continue
        key = len(line) - len(line.lstrip(" -"))
        start = i
        while start > 0 and not (re.match(r"^\s*- ", lines[start]) and lines[start].index("-") < key):
            start -= 1
        d = lines[start].index("-")
        end = i + 1
        while end < len(lines) and not (lines[end].strip() and (len(lines[end]) - len(lines[end].lstrip()) < d or
                                        (re.match(r"^\s*- ", lines[end]) and lines[end].index("-") <= d))):
            end += 1
        yield lines[start:end]


def check(repo, ref, required_checks, fetch, org=None):
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
    org = org_majors() if org is None else org
    for f in files:
        text = read(f) or ""
        for line in text.splitlines():
            m = USES.match(line)
            if m:
                v = re.sub(r"\s+#.*$", "", m.group(1)).strip().strip("\"'")
                used.append(v)
                if not any(p.fullmatch(v) for p in OK):
                    findings.append("%s: disallowed uses: %s" % (f, v))
            m = PIN.search(line) if m else None
            if m and org.get(m.group(1), m.group(2)) != m.group(2):
                findings.append("%s: %s is v%s, org pins v%s" % (f, m.group(1), m.group(2), org[m.group(1)]))
            if re.match(r"^\s*(-\s*)?(node|python)-version:", line):
                findings.append("%s: literal `%s`, use %s-file" % (f, line.strip(), line.split("-version")[0].strip(" -")))
        findings += ["%s: actions/checkout without persist-credentials: false" % f
                     for step in checkout_steps(text) if not any(re.match(r"\s*(- )?persist-credentials:\s*[\"']?false\b", l) for l in step)]
    # The shared release workflow calls promote-release and smoke itself.
    findings += ["no call to shared workflow " + SHARED % n + (" (direct promote-release/smoke calls are no longer accepted)" if n == "release" else "")
                 for n in ("pr-title", "build-image", "release") if SHARED % n not in used]
    findings += ["direct %s: use %s" % (u.split("@")[0], via) for u, via in (("astral-sh/setup-uv", "actions/uv-sync@v1"),
                 ("googleapis/release-please-action", "the shared release.yml")) for v in used if v.startswith(u + "@")]
    if NO_DOCKER_HUB not in used:
        findings.append("no use of " + NO_DOCKER_HUB)
    if read(".github/workflows/ci.yml") is None:
        findings.append(".github/workflows/ci.yml missing")

    # 3b. version files
    if (pkg := read("frontend/package.json")) is not None:
        if read(".nvmrc") is None:
            findings.append(".nvmrc missing")
        try:
            if not json.loads(pkg).get("packageManager"):
                findings.append("frontend/package.json has no packageManager")
        except (ValueError, AttributeError):
            findings.append("frontend/package.json is not valid JSON")
    if read("backend/pyproject.toml") is not None:
        if read("backend/.python-version") is None:
            findings.append("backend/.python-version missing")
        tv = read("backend/.tool-versions")
        if tv is None:
            findings.append("backend/.tool-versions missing")
        elif len([l for l in tv.splitlines() if re.match(r"uv\s", l)]) != 1:
            findings.append("backend/.tool-versions must have exactly one `uv ` line")

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
