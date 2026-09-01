# Output and errors

Successful API responses are printed as UTF-8 JSON with two-space indentation.
The CLI does not add a second envelope, so an agent can parse standard output
directly.

Pass `--include-meta` when the response status or rate-limit information is
needed. The result then has this shape:

```json
{
  "data": {},
  "meta": {
    "http_status": 200,
    "rate_limit": {
      "limit": 100,
      "remaining": 99,
      "reset": 1700000000
    }
  }
}
```

Exit codes:

| Code | Meaning |
| ---: | --- |
| 0 | Command completed successfully |
| 1 | DocBase API or network error |
| 2 | Missing configuration, invalid arguments, or a blocked safety policy |

Errors are written to standard error. Do not assume that an error response is
safe to retry: inspect the command and whether the API may have applied a
mutation before retrying. Errors are JSON with a stable `error` object:

```json
{
  "error": {
    "code": "confirmation_required",
    "message": "create-comment は確認なしでは実行しません。",
    "retryable": false,
    "next_action": "対象と変更内容を確認し、同じコマンドに --confirm を付けて再実行してください。"
  }
}
```

API errors also include `status` and safe rate-limit metadata when the API
provides them. Error messages never include the API token. The CLI does not
automatically retry a request, including a mutation.

`download-attachment` is the exception to the JSON-only data path. It writes
the binary response to `--output`, then prints a JSON summary containing the
attachment ID, output path, and byte count. A new path can be written directly;
an existing regular file requires both `--overwrite` and `--confirm`. Symlinks,
special files, and missing parent directories are rejected before the API
request.
