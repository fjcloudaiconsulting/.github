# fjcloudaiconsulting/.github

The shared foundation every application repo builds on:

- [RELEASE_CONTRACT.md](RELEASE_CONTRACT.md): what every app repo must provide (commits, versioning, images,
  runtime config, health, migrations, smoke test, deploy handoff, CI hygiene).
- `.github/workflows/`: reusable workflows, consumed pinned to a major tag (`@v1`).
- `renovate.json`: shared Renovate preset.

Changes here are released by tagging. Apps pick them up by bumping their pinned tag.
