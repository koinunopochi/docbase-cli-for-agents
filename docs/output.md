# Output and errors

Successful API responses are printed as UTF-8 JSON with two-space indentation.
The CLI does not add a second envelope, so an agent can parse standard output
directly.

Exit codes:

| Code | Meaning |
| ---: | --- |
| 0 | Command completed successfully |
| 1 | DocBase API or network error |
| 2 | Missing configuration, invalid arguments, or a blocked safety policy |

Errors are written to standard error. Do not assume that an error response is
safe to retry: inspect the command and whether the API may have applied a
mutation before retrying.

`download-attachment` is the exception to the JSON-only data path. It writes
the binary response to `--output`, then prints a JSON summary containing the
attachment ID, output path, and byte count. Choose a destination deliberately
and do not overwrite an important file without confirmation.
