# Releases

## For users

Download the latest version from this repository's GitHub Releases page.

Each release contains two platform-neutral archives:

| Asset | Use |
| --- | --- |
| `docbase-cli-for-agents_<tag>.tar.gz` | Linux, macOS, or other Unix-like systems |
| `docbase-cli-for-agents_<tag>.zip` | Windows or any system with ZIP support |
| `SHA256SUMS` | Verify the archive checksum |

The archive contains `docbase.py`, the `bin/docbase` launcher, the README,
documentation, `LICENSE`, and `NOTICE`. Install `uv`, extract the archive, and
run the launcher; a local build is not required.

```sh
sha256sum -c SHA256SUMS
export DOCBASE_DOMAIN="your-team"
export DOCBASE_API_TOKEN="..."
./docbase-cli-for-agents_v0.1.2/bin/docbase --help
```

On Windows, use the ZIP archive and run `bin\\docbase` from a shell that
provides `bash`, or invoke the Python script directly with Python 3.12+.

## For maintainers

Update `VERSION` in `docbase.py`, add a release note under
`docs/releases/<tag>.md`, and run the offline checks before creating an
annotated semantic-version tag:

```sh
make test
make lint
git tag -a v0.1.2 -m "Release v0.1.2"
git push origin v0.1.2
```

Pushing a `v*` tag starts `.github/workflows/release.yml`. The workflow tests
and lints the tagged source, creates the two archives and `SHA256SUMS`, and
creates or updates the GitHub Release. If a matching release-note file exists,
it is used; otherwise GitHub's generated notes are used.

Do not move an existing release tag. Publish a new patch version for a rebuilt
or corrected release.
