# Repository guidance

This repository contains a small, standalone DocBase API v3 CLI intended to be
called by AI agents.

## Source of truth

- `docbase.py` is the executable source.
- `tests/test_docbase.py` contains the offline tests.
- `docs/` contains user-facing behavior and safety documentation.
- `docbase --help` and `docbase <command> --help` are the first entry points.
- The CLI owns its command contract: usage, API details, input validation,
  safety policy, output, errors, and the bundled docs are released together.

Keep command-specific detail in `docs/commands.md` instead of duplicating the
entire CLI reference in this file or the README.

## Development

The CLI intentionally uses only the Python standard library. Use Python 3.12+
and `uv` for the documented commands:

```sh
make test
make lint
```

Tests must not call the DocBase API. Mock request construction or API responses
when adding unit coverage, and use process-level tests for the executable's
help, stdout, stderr, and exit-code contract.

## Safety boundary

- Keep `DOCBASE_API_TOKEN` in the environment; never put it in arguments,
  source, fixtures, logs, or documentation.
- Preserve the rule that new posts are always private drafts. Do not add a
  publication-state override to `create-post`.
- Preserve the owner check on post mutations. The CLI must not provide an
  override that permits operating on a post created by another user. Every API
  mutation requires `--confirm`.
- Do not overwrite an existing attachment output path unless the caller uses
  `--overwrite --confirm`.
- Do not add organization-specific URLs, paths, names, or operating
  instructions to this standalone repository.

## Documentation changes

When a command or safety rule changes, update the relevant document under
`docs/`, the command help if needed, and the offline tests in the same change.
