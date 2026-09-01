# DocBase CLI for AI agents

A small, standard-library-only command-line client for DocBase API v3. It is
designed to be invoked by an AI agent: credentials stay in environment
variables, output is JSON, and write operations have conservative defaults.

## Quick start

Install [uv](https://docs.astral.sh/uv/) and provide a DocBase API token through
the environment:

```sh
export DOCBASE_DOMAIN="your-team"
export DOCBASE_API_TOKEN="..."

./bin/docbase --help
./bin/docbase search-posts --query "release notes"
```

`DOCBASE_TEAM` is accepted as a compatibility fallback for the team name, but
`DOCBASE_DOMAIN` is preferred. The token is never accepted as a command-line
argument.

Start with help. Every command points to its detailed reference:

```sh
./bin/docbase --help
./bin/docbase search-posts --help
```

See [the command reference](docs/commands.md), [authentication](docs/authentication.md),
and [the safety model](docs/safety.md) for details. The CLI, its help, and the
docs in this repository are one versioned contract; a consumer only needs the
CLI entry point and does not copy or maintain these details.

## Downloads

Published releases contain ready-to-run archives with the launcher, source,
documentation, `LICENSE`, and `NOTICE`. Install `uv`, download the archive from
the GitHub Releases page, verify `SHA256SUMS`, and extract it. You do not need a
local build.

See [the release guide](docs/releases.md) for the asset names and verification
steps.

## Safety defaults

- `create-post` always creates a private draft. The command has no option to
  create a post with another publication state.
- `update-post`, `delete-post`, `archive-post`, `unarchive-post`, and
  `patch-post-body` verify that the authenticated user created the post.
- A post created by another user cannot be operated on through this CLI.
- `create-post`, `update-post`, and `patch-post-body` reject an H1 heading
  (`# `) in the body, since DocBase already renders the post title as an H1.
- Attachment downloads refuse to overwrite an existing path unless
  `--overwrite --confirm` is supplied, and return a JSON summary on success.
- Successful commands print API JSON to stdout. Use `--include-meta` when the
  HTTP status and rate-limit metadata are needed; failures are structured JSON
  on stderr with an exit code and next action.

The CLI can change DocBase data. An AI agent should treat mutating commands as
an explicit-action boundary and confirm the target and intended change before
using them.

## Development

```sh
make test
make lint
```

The tests are offline and do not require a DocBase account. Release archives
are built by GitHub Actions when a `v*` tag is pushed. The repository's
[release guide](docs/releases.md) documents the maintainer flow.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
