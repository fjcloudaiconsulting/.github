"""Runs release.yml's 'Heal an unpromoted release' step against a fake gh and docker."""
import base64, json, os, pathlib, re, subprocess, tempfile, unittest

WF = pathlib.Path(__file__).parent.parent / ".github/workflows/release.yml"
R = "c" * 40
# gh: the manifest, the release tag (FAKE_TAG=0 answers 404, FAKE_TAG=err any other error), its commit, its check runs.
FAKE_GH = """#!/bin/bash
case $2 in
  */contents/.release-please-manifest.json*) printf '%s\\n' "$FAKE_MANIFEST_B64";;
  */git/ref/tags/*)
    case $FAKE_TAG in 1) echo '{}';; 0) echo 'gh: Not Found (HTTP 404)' >&2; exit 1;; *) echo 'gh: Server Error (HTTP 502)' >&2; exit 1;; esac;;
  */commits/refs/tags/v*) echo "$FAKE_R";;
  */check-runs*) cat "$FAKE_DIR/runs.jsonl";;
  *) echo "unexpected gh $*" >&2; exit 2;;
esac
"""
# docker: login succeeds; inspect succeeds for refs listed in FAKE_TAGGED, else not found (or FAKE_INSPECT_ERR).
FAKE_DOCKER = """#!/bin/bash
[ "$1" = login ] && { cat >/dev/null; exit 0; }
[ "$1 $2 $3" = "buildx imagetools inspect" ] || { echo "unexpected docker $*" >&2; exit 2; }
case " $FAKE_TAGGED " in *" $4 "*) echo ok; exit 0;; esac
[ -z "${FAKE_INSPECT_ERR:-}" ] || { echo 'ERROR: unauthorized' >&2; exit 1; }
echo "ERROR: $4: not found" >&2; exit 1
"""
REF = "ghcr.io/o/r"
GREEN = [{"name": "Backend Checks", "id": 1, "conclusion": "success"}]


def script():
    m = re.search(r"- name: Heal an unpromoted release\n(?:.*\n)*?        run: \|\n((?:          .*\n|\n)+)", WF.read_text())
    return "\n".join(l[10:] for l in m.group(1).splitlines())


SHA_TAGS = (f"{REF}/backend:sha-{R[:7]}", f"{REF}/frontend:sha-{R[:7]}")


def run(tagged=SHA_TAGS, tag="1", version="1.2.3", runs=GREEN, inspect_err=False):
    """Returns (exit code, GITHUB_OUTPUT text)."""
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for name, body in (("gh", FAKE_GH), ("docker", FAKE_DOCKER)):
            (d / name).write_text(body)
            (d / name).chmod(0o755)
        (d / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in runs))
        out = d / "out"
        out.touch()
        manifest = base64.b64encode(json.dumps({".": version}).encode()).decode()
        e = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}", FAKE_DIR=str(d), GITHUB_OUTPUT=str(out),
                 GITHUB_REPOSITORY="O/r", GITHUB_SHA="h" * 40, GITHUB_ACTOR="bot", GH_TOKEN="t", IMAGES="backend frontend",
                 FAKE_MANIFEST_B64=manifest, FAKE_TAG=tag, FAKE_R=R, FAKE_TAGGED=" ".join(tagged),
                 FAKE_INSPECT_ERR="1" if inspect_err else "")
        p = subprocess.run(["bash", "-eo", "pipefail", "-c", script()], env=e, capture_output=True, text=True)
        return p.returncode, out.read_text()


BOTH = (f"{REF}/backend:v1.2.3", f"{REF}/frontend:v1.2.3") + SHA_TAGS
HEALED = "release_created=true\nversion=1.2.3\n"


class Heal(unittest.TestCase):
    def test_released_is_left_alone(self):
        self.assertEqual((0, ""), run(tagged=BOTH))

    def test_unpromoted_release_heals(self):
        self.assertEqual((0, HEALED), run())

    def test_one_image_missing_heals(self):
        # promote stopped after the first image: the rest still need it.
        self.assertEqual((0, HEALED), run(tagged=(BOTH[0],) + SHA_TAGS))

    def test_image_added_after_the_release_is_not_healed(self):
        # frontend joined the list after v1.2.3: no sha- build at the tagged commit, nothing to promote.
        self.assertEqual((0, ""), run(tagged=(BOTH[0], SHA_TAGS[0])))

    def test_untagged_version_is_left_alone(self):
        self.assertEqual((0, ""), run(tag="0"))

    def test_tag_lookup_error_fails(self):
        self.assertEqual((1, ""), run(tag="err"))

    def test_registry_error_fails(self):
        self.assertEqual((1, ""), run(inspect_err=True))

    def test_red_release_commit_is_not_released(self):
        self.assertEqual((1, ""), run(runs=[{"name": "Backend Checks", "id": 1, "conclusion": "failure"}]))

    def test_red_frontend_is_not_released(self):
        runs = GREEN + [{"name": "Frontend Checks", "id": 2, "conclusion": "failure"}]
        self.assertEqual((1, ""), run(runs=runs))

    def test_bad_manifest_fails(self):
        self.assertEqual((1, ""), run(version="1.2"))


if __name__ == "__main__":
    unittest.main()
