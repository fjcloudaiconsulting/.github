# Release contract

Every application repo in `fjcloudaiconsulting` meets this contract. It fixes **outputs and interfaces**,
not implementation: language versions, test strategy and extra checks stay per repo.

Shared building blocks live in this repo and are consumed pinned to a major tag
(`uses: fjcloudaiconsulting/.github/.github/workflows/<name>.yml@v1`). A change to them never reaches an
app until the app bumps the tag.

`MUST` items are checked by the conformance probe. `SHOULD` items are the default; deviate only with a
reason written in the repo's CONTRIBUTING.md.

## 1. Commits and pull requests

- MUST squash-merge; the PR title becomes the commit and MUST be a Conventional Commit
  (`type(scope): summary`). The `pr-title` reusable workflow enforces it.
- MUST protect `main`: PR required, 1 approval, required checks green, no force push.
- MUST name the aggregate required checks `Backend Checks` and `Frontend Checks` (plus any app extras),
  so branch protection reads the same across repos.

## 2. Versioning and release

- MUST use release-please, release type `simple`, one version for the whole repo (`version.txt`),
  `CHANGELOG.md` at the root, tags `vX.Y.Z`.
- MUST release only from a `main` commit whose CI is fully green (the release job `needs` every CI job of
  the same run).
- Release happens when the owner merges the release-please PR. No release on every merge.

## 3. Images

- MUST publish to GHCR as `ghcr.io/fjcloudaiconsulting/<repo>/<image>`.
- Image names: `backend`, `frontend`, `migrations`. Add another name only when it contains different
  code. Workers and schedulers run the `backend` image with a different command.
- MUST build each image **once**, on the `main` push, tagged `sha-<7 char sha>`. Release MUST promote
  that exact digest by adding tags `vX.Y.Z` and `X.Y` (retag, never rebuild).
- MUST set OCI labels `org.opencontainers.image.source`, `.revision`, `.version`, `.title`.
- MUST run as a non-root user. SHOULD pin base images by digest (Renovate keeps them current).
- Platform: `linux/amd64`.

## 4. Runtime configuration

- MUST read all configuration at runtime from environment variables. Nothing environment-specific is
  baked into an image (no `NEXT_PUBLIC_*` or build args carrying URLs, keys or flags). One image runs in
  every environment.
- MUST document every variable in `.env.example`, using one app prefix (e.g. `ZIF_`, `TBD_`) for new
  variables.
- Secrets are never in images, compose files or the repo.

## 5. Health and version

- The backend MUST expose a liveness endpoint returning HTTP 200 and JSON containing
  `"version": "X.Y.Z"` (the released version, injected at build time). Its path is passed to the shared
  smoke workflow (e.g. `/api/healthz`).
- The backend SHOULD expose a readiness endpoint that fails while dependencies are unreachable.

## 6. Database migrations

- MUST ship migrations as the `migrations` image whose default command upgrades to head.
- MUST be idempotent (running twice is a no-op) and safe to run concurrently (advisory lock or equivalent).
- MUST run as a Job before the new version rolls out, never on app startup.
- Forward-only: no downgrade in production. Schema changes follow expand/contract so the previous app
  version keeps working against the new schema.

## 7. Post-release smoke test

- MUST start the published images (not a local build): run `migrations` twice against an empty database,
  start backend and frontend, and assert the liveness endpoint reports the released version.

## 8. Deploy handoff

- An app repo never deploys. After a release it notifies `fjcloudaiconsulting/aws-infra`, where a bot opens
  a PR bumping the image tag for production. Merging that PR is the deploy (Flux applies it).
- Non-production environments MAY follow `sha-` tags automatically.

## 9. CI hygiene

- MUST pin third-party actions by commit SHA with the version in a trailing comment.
- MUST set `permissions:` explicitly (least privilege) and `persist-credentials: false` on checkout
  unless the job pushes.
- MUST never cancel `main` runs; superseded PR runs SHOULD be cancelled.
- MUST use the shared Renovate preset (`extends: ["github>fjcloudaiconsulting/.github:renovate"]`).
- SHOULD skip jobs whose area did not change, failing open when detection is unsure.

## 10. Local development

- MUST start the full stack with one documented command (`docker compose up` or `make dev`) and no
  real secrets.
- Toolchain: Python via `uv` (`uv.lock` committed), Node via `pnpm` (version in `packageManager`).
