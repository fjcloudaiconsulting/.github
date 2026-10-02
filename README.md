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

**build-image** (one image per call, runs on PRs without pushing, pushes `sha-<7>` on `main`):

```yaml
jobs:
  image:
    strategy:
      matrix:
        image: [backend, frontend, migrations]
    permissions: {contents: read, packages: write}
    uses: fjcloudaiconsulting/.github/.github/workflows/build-image.yml@v1
    with:
      image: ${{ matrix.image }}
      context: ${{ matrix.image }}   # optional: context, file, target, build-contexts
```

**promote-release** (retags the release commit's `sha-<7>` images as `vX.Y.Z`, never builds):

```yaml
jobs:
  promote:
    needs: [release, image]   # release outputs the version; the tag vX.Y.Z must already exist
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
    needs: promote
    permissions: {contents: read, packages: read}
    uses: fjcloudaiconsulting/.github/.github/workflows/smoke.yml@v1
    with:
      version: ${{ needs.release.outputs.version }}
      health-url: http://localhost:8000/api/healthz
```

The compose file (default `compose.smoke.yaml`) may use only `${IMAGE_PREFIX}/<image>:${TAG}` images and no `build:`.
It must define a one-shot `migrations` service in profile `migrate` (run twice, then `up -d --wait`); `backend`
must not `depends_on` it.

### What the caller must provide

- Gate jobs named `Backend Checks` / `Frontend Checks` (contract section 1), in the app's own workflow.
- release-please with a GitHub App token, and a release job that `needs` every CI job (section 2).
- Non-root images, and a Dockerfile that maps `ARG APP_VERSION` / `ARG APP_REVISION` to the app's environment
  (sections 3, 5); `revision` in the liveness JSON (section 5).
- `concurrency` and the Renovate preset (section 9).
