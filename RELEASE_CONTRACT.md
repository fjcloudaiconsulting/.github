# Release contract

Every application repo in `fjcloudaiconsulting` meets this contract. It fixes **outputs and interfaces**,
not implementation: language versions, test strategy and extra checks stay per repo.

Shared building blocks live in this repo and are consumed pinned to a major tag
(`uses: fjcloudaiconsulting/.github/.github/workflows/<name>.yml@v1`, Renovate preset `#v1`). A change to
them never reaches an app until the app bumps the tag.

`MUST` items are checked by the conformance probe, or by review where marked *(review)*. `SHOULD` items are
the default; deviate only with a reason written in the repo's CONTRIBUTING.md.

## 1. Commits and pull requests

- MUST squash-merge; the PR title becomes the commit and MUST be a Conventional Commit
  (`type(scope): summary`). The `pr-title` reusable workflow enforces it.
- MUST protect `main` (classic branch protection or a ruleset): PR required, required checks green, no
  force push, no deletion. Approvals: 0, or 1 with the owner on the bypass list. Bot-authored PRs (release,
  Renovate) are approved by the owner.
- MUST name the aggregate required checks `Backend Checks` and `Frontend Checks` (one per area the repo
  has, plus any app extras). These gate jobs live in the app's own workflow, not inside a reusable
  workflow (whose checks report as `caller / callee`). They MUST always run (`if: always()`, no
  workflow-level `paths:` filter) and fail unless every job they gate succeeded or was intentionally
  skipped.

## 2. Versioning and release

- MUST use release-please, release type `simple`, one version for the whole repo (`version.txt`),
  `CHANGELOG.md` at the root, tags `vX.Y.Z`, `bump-minor-pre-major: true` while below 1.0.
- Only release-please changes `version.txt` and `.release-please-manifest.json`.
- MUST run release-please with a GitHub App installation token (not `GITHUB_TOKEN`), so the release PR
  triggers CI and can satisfy required checks.
- MUST release only from a `main` commit whose CI is fully green (the release job `needs` every CI job of
  the same run).
- Release happens when the owner merges the release-please PR. No release on every merge.

## 3. Images

- MUST publish to GHCR as `ghcr.io/fjcloudaiconsulting/<repo>/<image>`.
- Image names: `backend`, `frontend`, `migrations`. Add another name only when it contains different
  code. Workers and schedulers run the `backend` image with a different command.
- MUST build each image **once**, on the `main` push, tagged `sha-<7 char sha>` (as produced by
  `docker/metadata-action` `type=sha`). The build reads the version from `version.txt`: on the release
  commit (the one that changes `.release-please-manifest.json`) the baked version is exactly `X.Y.Z`; on
  every other commit it is `X.Y.Z-dev+<7 char sha>`, where X.Y.Z is the previous release.
- Release MUST promote the digest built from the tagged release commit by adding the tag `vX.Y.Z`. Never
  rebuild. The release job therefore `needs` the image build job of the same run.
- MUST NOT publish `latest` or floating tags.
- MUST set OCI labels `org.opencontainers.image.source`, `.revision`, `.version`, `.title`.
- MUST run as a non-root user. SHOULD pin base images by digest (Renovate keeps them current).
- Platform: `linux/amd64`.
- Packages stay private unless the owner decides otherwise (making a GHCR package public cannot be
  undone). The cluster pulls with a read-only token.

## 4. Runtime configuration

- MUST read all configuration at runtime from environment variables. Nothing environment-specific is baked
  into an image (no `NEXT_PUBLIC_*` or build args carrying URLs, keys or flags). The version and revision
  (§3) are the only build-time values. One image runs in every environment.
- MUST document every variable in `.env.example`, using one app prefix (e.g. `ZIF_`, `TBD_`) for new
  variables.
- Secrets are never in images, compose files or the repo.

## 5. Health and version

- The backend MUST expose a liveness endpoint (no dependency checks) returning HTTP 200 and JSON with
  `"version"` (baked at build time as in §3) and `"revision"` (the git sha). Its path is passed to the
  shared smoke workflow (e.g. `/api/healthz`, `/health`).
- The backend SHOULD expose a readiness endpoint that fails while dependencies are unreachable.

## 6. Database migrations

- MUST ship migrations as the `migrations` image whose default command upgrades to head, built from the
  same commit as `backend`.
- MUST be idempotent: a second run against the same database is a no-op, and CI proves it.
- MUST NOT migrate on app startup. The deploy (aws-infra) runs the image as a Job before the new version
  rolls out.
- SHOULD be safe to run concurrently (advisory lock or `GET_LOCK`).
- SHOULD be forward-only with expand/contract, so the previous app version keeps working against the new
  schema *(review)*.

## 7. Post-release smoke test

- MUST start the published images by their `vX.Y.Z` tag (not a local build): run `migrations` twice
  against an empty database, start backend and frontend, and assert the liveness endpoint reports
  `"version": "X.Y.Z"` and the release commit's `"revision"`.

## 8. Deploy handoff

- An app repo never deploys and never writes to `aws-infra`. Renovate in `fjcloudaiconsulting/aws-infra`
  watches the GHCR images and opens a PR bumping the production tag to the new `vX.Y.Z`. Merging that PR
  is the deploy (Flux applies it). Production manifests reference `vX.Y.Z` only.
- Non-production environments MAY follow `sha-` tags automatically.

## 9. CI hygiene

- MUST pin third-party actions by commit SHA with the version in a trailing comment.
- MUST set `permissions:` explicitly (least privilege) and `persist-credentials: false` on checkout unless
  the job runs `git push`.
- MUST never cancel `main` runs; superseded PR runs SHOULD be cancelled.
- MUST extend the shared Renovate preset pinned to the major tag:
  `extends: ["github>fjcloudaiconsulting/.github#v1"]` (preset file `default.json` in this repo).
- SHOULD skip jobs whose area did not change, failing open when detection is unsure.

## 10. Local development

- MUST start the full stack with one documented command (`docker compose up` or `make dev`) and no real
  secrets.
- MUST commit a lockfile and install from it frozen in CI and image builds.
- SHOULD use `uv` (`uv.lock`) for Python and `pnpm` (version in `packageManager`) for Node. New repos start
  that way.
