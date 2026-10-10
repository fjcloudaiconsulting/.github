# fjcloudaiconsulting/.github

The shared foundation every application repo builds on:

- [RELEASE_CONTRACT.md](RELEASE_CONTRACT.md): what every app repo must provide (commits, versioning, images,
  runtime config, health, migrations, smoke test, deploy handoff, CI hygiene).
- `.github/workflows/`: reusable workflows, consumed pinned to a major tag (`@v1`).
- `actions/`: composite actions for steps every app repeats, consumed as `fjcloudaiconsulting/.github/actions/<name>@v1`.
- `default.json`: shared Renovate preset, consumed as `github>fjcloudaiconsulting/.github#v1`.

Changes here are released by tagging. Apps pick them up by bumping their pinned tag.

### Publishing a release

The `publish` workflow runs release-please on every `main` push and keeps a Release PR open while there are
unreleased `feat`/`fix`/`perf` commits (`docs`, `ci`, `chore`, including Renovate's `chore(deps)`, never release). Merging the Release PR is the
owner's approval: the workflow then creates `vX.Y.Z` (and a GitHub Release with the changelog) and moves the major
tag (`v1`) to the same commit, so every app on `@v1` gets it at once. The tag move runs on every `main` push and
on dispatch (`publish` workflow), so a failed move heals on the next run. A breaking change (`feat!`) cuts `v2` and
leaves `v1` alone.

Only the Release App (a bypass actor on the `tag protection` ruleset) and org admins can create or move `v*` tags.
By hand, if the workflow is unavailable (the push prints "Bypassed rule violations", which is expected):

```
git tag -m "vX.Y.Z: <summary>" vX.Y.Z <sha> && git push origin vX.Y.Z
gh api -X PATCH repos/fjcloudaiconsulting/.github/git/refs/tags/v1 -f sha=<sha> -F force=true
```

## Reusable workflows

Call as `fjcloudaiconsulting/.github/.github/workflows/<name>.yml@v1`. None sets `concurrency`: the caller owns it
(cancel superseded PR runs, never cancel `main`).

**pr-title** (no inputs):

```yaml
on:
  pull_request:
    types: [opened, edited, synchronize, reopened]
jobs:
  pr-title:
    uses: fjcloudaiconsulting/.github/.github/workflows/pr-title.yml@v1
```

**build-image** (one image per call, runs on PRs without pushing, pushes `sha-<7>` on `main`). Example in the ziftbook
shape: `migrations` is built from `backend/` with a different target, `frontend` takes the backend as a named context:

```yaml
jobs:
  image:
    strategy:
      matrix:
        include:
          - {image: backend, context: backend, target: '', build-contexts: ''}
          - {image: migrations, context: backend, target: migrations, build-contexts: ''}
          - {image: frontend, context: frontend, target: '', build-contexts: 'backend=backend'}
    permissions: {contents: read, packages: write}
    uses: fjcloudaiconsulting/.github/.github/workflows/build-image.yml@v1
    with:
      image: ${{ matrix.image }}
      context: ${{ matrix.context }}
      target: ${{ matrix.target }}
      build-contexts: ${{ matrix.build-contexts }}   # optional input: file
```

**release** (replaces calling promote-release and smoke directly): release-please, then promote and smoke on
`release_created`. Before releasing it checks that every merged `autorelease: pending` Release PR's own commit has a
green latest `Backend Checks` run (and `Frontend Checks`, if that run exists), and fails otherwise; re-run the job once
that commit's CI is done. It skips when `main` has moved past the triggering commit. Its `release` job
declares `environment: release`, so the caller needs `RELEASE_APP_ID` /
`RELEASE_APP_PRIVATE_KEY` as secrets of its own `release` environment. Pass `secrets: inherit`: GitHub's docs do not
settle whether environment secrets reach a called workflow without it, and a missing secret is an empty string, not an
error.
Reusable-workflow permissions are capped by the caller's, so the caller grants all of them. Inputs: `images` (space
list), `health-url`, `compose-file` (default `compose.smoke.yaml`); outputs `release_created`, `version`. It calls
promote-release and smoke at `@v1` (full ref), so changes to those reach apps once `v1` moves.

```yaml
jobs:
  release:
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    needs: [backend-checks, frontend-checks, image]
    permissions:
      contents: read
      checks: read
      pull-requests: read
      packages: write
    uses: fjcloudaiconsulting/.github/.github/workflows/release.yml@v1
    with:
      images: backend frontend migrations
      health-url: http://localhost:8000/api/healthz
    secrets: inherit
```

**promote-release** (retags the release commit's `sha-<7>` images as `vX.Y.Z`, never builds). The caller's `release`
job (release-please) must expose the outputs `version` (X.Y.Z) and `release_created`, and the tag `vX.Y.Z` must exist:

```yaml
jobs:
  promote:
    needs: [release, image]
    if: needs.release.outputs.release_created == 'true'
    permissions: {contents: read, packages: write}
    uses: fjcloudaiconsulting/.github/.github/workflows/promote-release.yml@v1
    with:
      version: ${{ needs.release.outputs.version }}
      images: backend frontend migrations
```

**smoke** (runs the published `vX.Y.Z` images from `compose-file`):

```yaml
jobs:
  smoke:
    needs: [release, promote]
    if: needs.release.outputs.release_created == 'true'
    permissions: {contents: read, packages: read}
    uses: fjcloudaiconsulting/.github/.github/workflows/smoke.yml@v1
    with:
      version: ${{ needs.release.outputs.version }}
      health-url: http://localhost:8000/api/healthz
```

The compose file (default `compose.smoke.yaml`) must use `${IMAGE_PREFIX}/<image>:${TAG}` for every image under
`IMAGE_PREFIX` and no `build:`; third-party images (postgres, ...) are allowed. It must define a one-shot `migrations`
service in profile `migrate` (run twice, then `up -d --wait`); `backend` must not `depends_on` it. `health-url` must be
reachable from the runner, so publish the backend port. The first commit of a repo has no `HEAD^`, so `build-image`
fails there by design.

### What the caller must provide

- Gate jobs named `Backend Checks` / `Frontend Checks` (contract section 1), in the app's own workflow.
- release-please with a GitHub App token, and a release job that `needs` every CI job (section 2).
- Non-root images, and a Dockerfile that maps `ARG APP_VERSION` / `ARG APP_REVISION` to the app's environment
  (sections 3, 5); `revision` in the liveness JSON (section 5).
- `concurrency` and the Renovate preset (section 9).

## Composite actions

Call as `fjcloudaiconsulting/.github/actions/<name>@v1`, after your own `actions/checkout` (they never check out,
so `fetch-depth` and `persist-credentials: false` stay visible in the app's workflow).

- **uv-sync** (`working-directory`, default `backend`): setup-uv with its cache keyed on `uv.lock`, `uv sync --locked`,
  and the venv's `bin` on `PATH`. The uv version comes from `<working-directory>/.tool-versions`
  (`uv X.Y.Z`, kept by Renovate's asdf manager) when present, else `[tool.uv] required-version` (which uv also
  enforces on local runs); the Python version from `.python-version`. There is no version input. Keep `.tool-versions`
  to a single `uv` line: without one setup-uv fails, and a `python` line there overrides `.python-version`.
- **pnpm-install** (`working-directory`, default `frontend`; `node-version-file`, default `.nvmrc`): setup-node from
  the version file, corepack (pnpm version from `packageManager`), the pnpm store cached on `pnpm-lock.yaml`,
  `pnpm install --frozen-lockfile`.
- **no-docker-hub**: fails on any `FROM`, `image:`, `# syntax=` or `uses: docker://` reference that resolves to Docker
  Hub, which rate-limits anonymous pulls from shared runners. Use `mirror.gcr.io/library/<name>` for official
  images and `mirror.gcr.io/<org>/<name>` otherwise; digests are the same.

## CI naming

One name per stage in every app, so a red check reads the same in any repo. Title Case, `<Area> <What>`.
Stages an app does not have are simply absent; app-specific jobs keep their own names in the same style.

**Workflows:** `CI` in `.github/workflows/ci.yml` (every check that gates a PR or a release), `PR title` in
`pr-title.yml`.

**Jobs** (the `name:` shown in checks):

| Stage | Job name |
|---|---|
| Change detection (only where CI is scoped by path) | `Detect Changes` |
| Repo hygiene (commit hooks, env/catalog scripts, Docker Hub guard) | `Repo Checks` |
| Backend lint, format, types and tests | `Backend Tests`; sharded: `Backend Tests (1/6)` |
| Migrations against the real database engine | `Backend Migrations` |
| Frontend tests | `Frontend Tests` |
| Frontend lint, types and production build, when a separate job | `Frontend Build` |
| Local image smoke (build and import, no push) | `Image Smoke (<image>)` |
| Image build through `build-image` (one per image) | `Image (<image>)`, shown as `Image (<image>) / build` |
| Gates (required by branch protection; never rename) | `Backend Checks`, `Frontend Checks` |
| Release through `release` | `Release`, shown as `Release / release`, `Release / promote`, `Release / smoke` |
| PR title (required; never rename) | `pr-title / check` |

**Steps** that every app has:

| Step | Name |
|---|---|
| `actions/checkout` | `Checkout` |
| `actions/uv-sync` | `Install backend dependencies` |
| `actions/pnpm-install` | `Install frontend dependencies` |
| plain `actions/setup-node` | `Set up Node` |
| `actions/no-docker-hub` | `No Docker Hub images` |
| ruff / ruff format / mypy / pytest | `Lint backend`, `Check backend format`, `Type-check backend`, `Run backend tests` |
| lint / typecheck / build / tests | `Lint frontend`, `Type-check frontend`, `Build frontend`, `Run frontend tests` |
| a gate's verdict | `Verify <what>` (e.g. `Verify every gated job passed`) |

## Conformance probe

`conformance/probe.py` checks each app repo (public API only) against the release contract; the `conformance`
workflow runs it weekly and on dispatch (`targets`: space list of `owner/repo[@ref]`) and keeps one issue
`Conformance drift: <repo>` per drifting repo (closed when clean). Probed: `main` is protected (not each rule) and the required
`Backend Checks` (plus `Frontend Checks` when the repo has a top-level `frontend/`), actions pinned by SHA (or `./`, `docker://@sha256`, shared `@v1`), calls to the four shared
workflows at `@v1`, release-please config (`simple`, `bump-minor-pre-major`), `version.txt`, manifest, `CHANGELOG.md`,
`.env.example`, and the Renovate preset `#v1`. A ref dispatch rewrites or closes that repo's issue until the
next run on `main`. Scheduled workflows in public repos are
disabled after 60 days without commits; Renovate PRs keep this repo active and GitHub emails before disabling.
