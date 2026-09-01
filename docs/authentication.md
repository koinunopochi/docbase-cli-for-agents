# Authentication

The CLI reads credentials from environment variables. It does not accept a
token argument, configuration file, or interactive prompt.

```sh
export DOCBASE_DOMAIN="your-team"
export DOCBASE_API_TOKEN="..."
```

`DOCBASE_TEAM` may be used instead of `DOCBASE_DOMAIN` for compatibility. When
both are present, `DOCBASE_DOMAIN` wins.

The token is sent to DocBase as the `X-DocBaseToken` request header. Keep it in
the secret-management mechanism of the environment running the agent. Do not
print it, include it in a command transcript, commit it, or put it in an issue
or document.

Check only that configuration is available with:

```sh
./bin/docbase profile
```

The command calls DocBase and returns the API response as JSON; it is not a
local token validator. A missing variable exits with code 2 and produces a
structured error on stderr. An API or network failure exits with code 1. The
CLI never prompts for a token.
