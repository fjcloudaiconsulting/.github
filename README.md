# fjcloudaiconsulting/.github

The shared foundation every application repo builds on:

- [RELEASE_CONTRACT.md](RELEASE_CONTRACT.md): what every app repo must provide (commits, versioning, images,
  runtime config, health, migrations, smoke test, deploy handoff, CI hygiene).
- `.github/workflows/`: reusable workflows, consumed pinned to a major tag (`@v1`).
- `default.json`: shared Renovate preset, consumed as `github>fjcloudaiconsulting/.github#v1`.

Changes here are released by tagging. Apps pick them up by bumping their pinned tag.

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

## Conformance probe

`conformance/probe.py` checks each app repo (public API only) against the release contract; the `conformance`
workflow runs it weekly and on dispatch (`targets`: space list of `owner/repo[@ref]`) and keeps one issue
`Conformance drift: <repo>` per drifting repo (closed when clean). A ref dispatch rewrites that repo's issue until the
next run on `main`. Items marked *(review)* in the contract are not probed. Scheduled workflows in public repos are
disabled after 60 days without commits; Renovate PRs keep this repo active and GitHub emails before disabling.
