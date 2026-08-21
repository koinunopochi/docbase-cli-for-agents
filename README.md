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
and [the safety model](docs/safety.md) for details.

## Safety defaults

- `create-post` requires `--draft --scope private` unless the caller explicitly
  supplies `--allow-public`.
- `update-post`, `delete-post`, `archive-post`, `unarchive-post`, and
  `patch-post-body` verify that the authenticated user created the post.
- `--force` bypasses that owner check and should only be used after the caller
  has explicitly confirmed the target.
- Attachment downloads write only to the path supplied by the caller and return
  a JSON summary on success.

The CLI can change DocBase data. An AI agent should treat mutating commands as
an explicit-action boundary and confirm the target and intended change before
using them.

## Development

```sh
make test
make lint
```

The tests are offline and do not require a DocBase account. There is deliberately
no release workflow while this repository is private; release automation and
downloadable release artifacts can be added after the repository is made public.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
