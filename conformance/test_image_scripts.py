"""Runs build-image's skip guard and merge step, and promote-release's promote step, against a fake docker and gh."""
import json, os, pathlib, re, stat, subprocess, tempfile, unittest

WF = pathlib.Path(__file__).parent.parent / ".github/workflows"
SHA = "a" * 40
OTHER = "b" * 40
REF = "ghcr.io/o/r/backend"
TAG = f"{REF}:sha-{SHA[:7]}"
D_AMD, D_ARM, D_IDX = ("sha256:" + c * 64 for c in "123")

# Only what the scripts call: `buildx imagetools inspect REF --format FMT` and `buildx imagetools create`.
# Responses are files named after the ref; create copies its single source, or `merged.*` for several.
FAKE_DOCKER = """#!/bin/bash
k() { printf '%s' "$1" | tr '/:@' '___'; }
[ "$1 $2" = "buildx imagetools" ] || { echo "unexpected docker $*" >&2; exit 2; }
case $3 in
  inspect)
    case $6 in '{{json .Manifest}}') f=manifest;; '{{json .Image}}'|'{{json .Image.Config.Labels}}') f=image;; *) echo "unexpected format $6" >&2; exit 2;; esac
    p=$FAKE_DIR/$(k "$4").$f
    [ -f "$p" ] || { echo "ERROR: $4: not found" >&2; exit 1; }
    # Like buildx: on an index .Image is a platform -> config map, so .Image.Config.Labels is null.
    if [ "$6" = '{{json .Image.Config.Labels}}' ]; then jq -c '.config.Labels' "$p"; else cat "$p"; fi;;
  create)
    echo "$*" >> "$FAKE_DIR/create.log"
    shift 3; srcs=()
    while [ $# -gt 0 ]; do case $1 in -t) t=$2; shift 2;; --*) shift;; *) srcs+=("$1"); shift;; esac; done
    for f in image manifest; do
      if [ ${#srcs[@]} -eq 1 ]; then s=$FAKE_DIR/$(k "${srcs[0]}").$f; else s=$FAKE_DIR/merged.$f; fi
      [ ! -f "$s" ] || cp "$s" "$FAKE_DIR/$(k "$t").$f"
    done;;
  *) echo "unexpected docker $*" >&2; exit 2;;
esac
"""
FAKE_GH = """#!/bin/bash
echo "commit $FAKE_REL"
"""


def script(wf, marker):
    m = re.search(re.escape(marker) + r"\n(?:.*\n)*?        run: \|\n((?:          .*\n|\n)+)", (WF / wf).read_text())
    return "\n".join(l[10:] for l in m.group(1).splitlines())


def img(arch, rev=SHA, version="1.2.3", title="r/backend", os="linux"):
    labels = {"org.opencontainers.image.revision": rev, "org.opencontainers.image.version": version,
              "org.opencontainers.image.title": title}
    return {"os": os, "architecture": arch, "config": {"Labels": labels}}


def index(*imgs, arm="linux/arm64"):
    return {arm if i["architecture"] == "arm64" else f"linux/{i['architecture']}": i for i in imgs}


def run(wf, marker, refs, env=None, digests=None, merged=None):
    """refs: {ref: (image_json, manifest_digest)}. Returns (exit code, GITHUB_OUTPUT text, create calls)."""
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for name, body in (("docker", FAKE_DOCKER), ("gh", FAKE_GH)):
            (d / name).write_text(body)
            (d / name).chmod(0o755)
        k = lambda r: re.sub(r"[/:@]", "_", r)
        for r, (image, digest) in refs.items():
            (d / f"{k(r)}.image").write_text(json.dumps(image))
            (d / f"{k(r)}.manifest").write_text(json.dumps({"digest": digest}))
        if merged:
            (d / "merged.image").write_text(json.dumps(merged))
            (d / "merged.manifest").write_text(json.dumps({"digest": D_IDX}))
        (d / "digests").mkdir()
        for arch, digest in (digests or {}).items():
            (d / "digests" / arch).write_text(digest + "\n")
        out = d / "out"
        out.touch()
        e = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}", FAKE_DIR=str(d), RUNNER_TEMP=str(d), GITHUB_SHA=SHA,
                 GITHUB_OUTPUT=str(out), GITHUB_REPOSITORY="o/r", REF=REF, TAG=TAG, TITLE="r/backend", **(env or {}))
        # The runner's `shell: bash` is `bash -eo pipefail`.
        p = subprocess.run(["bash", "-eo", "pipefail", "-c", script(wf, marker)], env=e, capture_output=True, text=True)
        log = d / "create.log"
        return p.returncode, out.read_text(), log.read_text().splitlines() if log.exists() else []


def guard(image):
    return run("build-image.yml", "- id: guard", {TAG: (image, D_IDX)} if image else {})


def merge(digests, refs, merged):
    return run("build-image.yml", "- name: Merge the platform images", refs, digests=digests, merged=merged)


def promote(image):
    refs = {f"{REF}:sha-{SHA[:7]}": (image, D_IDX), f"{REF}@{D_IDX}": (image, D_IDX)} if image else {}
    return run("promote-release.yml", "- name: Promote the release commit's images", refs,
               env={"VERSION": "1.2.3", "IMAGES": "backend", "FAKE_REL": SHA})


class Guard(unittest.TestCase):
    def test_not_found_builds(self):
        self.assertEqual((0, "skip=false\n"), guard(None)[:2])

    def test_full_index_skips(self):
        self.assertEqual((0, "skip=true\n"), guard(index(img("amd64"), img("arm64")))[:2])

    def test_arm64_v8_index_skips(self):
        self.assertEqual((0, "skip=true\n"), guard(index(img("amd64"), img("arm64"), arm="linux/arm64/v8"))[:2])

    def test_single_platform_rebuilds(self):
        self.assertEqual((0, "skip=false\n"), guard(img("amd64"))[:2])

    def test_index_missing_arch_rebuilds(self):
        self.assertEqual((0, "skip=false\n"), guard(index(img("amd64"), img("s390x")))[:2])

    def test_wrong_revision_fails(self):
        self.assertNotEqual(0, guard(index(img("amd64"), img("arm64", rev=OTHER)))[0])

    def test_single_wrong_revision_fails(self):
        self.assertNotEqual(0, guard(img("amd64", rev=OTHER))[0])


GOOD = {f"{REF}@{D_AMD}": (img("amd64"), D_AMD), f"{REF}@{D_ARM}": (img("arm64"), D_ARM)}
DIGESTS = {"amd64": D_AMD, "arm64": D_ARM}


class Merge(unittest.TestCase):
    def test_both_arches_tagged(self):
        rc, _, created = merge(DIGESTS, GOOD, index(img("amd64"), img("arm64")))
        self.assertEqual(0, rc)
        self.assertEqual([f"buildx imagetools create -t {TAG} {REF}@{D_AMD} {REF}@{D_ARM}"], created)

    def test_arm64_v8_index_tagged(self):
        self.assertEqual(0, merge(DIGESTS, GOOD, index(img("amd64"), img("arm64"), arm="linux/arm64/v8"))[0])

    def test_wrong_os_never_tagged(self):
        refs = dict(GOOD, **{f"{REF}@{D_ARM}": (img("arm64", os="windows"), D_ARM)})
        self.assertEqual((1, []), merge(DIGESTS, refs, index(img("amd64"), img("arm64")))[::2])

    def test_wrong_arch_never_tagged(self):
        refs = dict(GOOD, **{f"{REF}@{D_ARM}": (img("amd64"), D_ARM)})
        self.assertEqual((1, []), merge(DIGESTS, refs, index(img("amd64"), img("arm64")))[::2])

    def test_wrong_revision_never_tagged(self):
        refs = dict(GOOD, **{f"{REF}@{D_AMD}": (img("amd64", rev=OTHER), D_AMD)})
        self.assertEqual((1, []), merge(DIGESTS, refs, index(img("amd64"), img("arm64")))[::2])

    def test_wrong_title_never_tagged(self):
        refs = dict(GOOD, **{f"{REF}@{D_AMD}": (img("amd64", title="r/frontend"), D_AMD)})
        self.assertEqual((1, []), merge(DIGESTS, refs, index(img("amd64"), img("arm64")))[::2])

    def test_missing_digest_never_tagged(self):
        self.assertEqual((1, []), merge({"amd64": D_AMD}, GOOD, index(img("amd64"), img("arm64")))[::2])

    def test_digest_not_found_never_tagged(self):
        refs = {f"{REF}@{D_AMD}": GOOD[f"{REF}@{D_AMD}"]}
        self.assertEqual((1, []), merge(DIGESTS, refs, index(img("amd64"), img("arm64")))[::2])

    def test_wrong_title_after_create_fails(self):
        self.assertNotEqual(0, merge(DIGESTS, GOOD, index(img("amd64"), img("arm64", title="r/frontend")))[0])

    def test_bad_index_after_create_fails(self):
        self.assertNotEqual(0, merge(DIGESTS, GOOD, index(img("amd64"), img("s390x")))[0])


class Promote(unittest.TestCase):
    def created(self, image):
        rc, _, created = promote(image)
        return rc, len(created)

    def test_index_promoted(self):
        self.assertEqual((0, 1), self.created(index(img("amd64"), img("arm64"))))

    def test_single_platform_promoted(self):
        self.assertEqual((0, 1), self.created(img("amd64")))

    def test_index_wrong_revision_refused(self):
        self.assertEqual((1, 0), self.created(index(img("amd64"), img("arm64", rev=OTHER))))

    def test_index_wrong_version_refused(self):
        self.assertEqual((1, 0), self.created(index(img("amd64", version="1.2.2"), img("arm64"))))

    def test_single_wrong_revision_refused(self):
        self.assertEqual((1, 0), self.created(img("amd64", rev=OTHER)))

    def test_not_found_refused(self):
        self.assertEqual((1, 0), self.created(None))


if __name__ == "__main__":
    unittest.main()
