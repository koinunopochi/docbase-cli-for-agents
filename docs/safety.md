# Safety model

This CLI includes safeguards because an AI agent may be the caller. The
safeguards reduce accidental publication or mutation; they do not replace
human authorization or DocBase permissions.

## New posts

`create-post` is intentionally restrictive by default. This is accepted:

```sh
./bin/docbase create-post \
  --title "A draft" \
  --body "内容" \
  --draft \
  --scope private
```

Any other draft/publication combination is rejected unless the caller adds
`--allow-public`. That flag is an explicit acknowledgement that the normal
private-draft policy is being bypassed.

## Body content

DocBase already renders a post's title as an H1. `create-post --body`,
`update-post --body`, and `patch-post-body --content` reject any ATX H1
heading (a line starting with `# `, or a line that is just `#`) found outside
a fenced code block, so the title is not duplicated inside the body. There is
no override flag for this check — restructure the body to use `##` or smaller
instead.

## Existing posts

Before `update-post`, `delete-post`, `archive-post`, `unarchive-post`, or
`patch-post-body`, the CLI compares the authenticated profile ID with the post
creator ID. A mismatch or an unavailable ID blocks the operation. `--force`
skips this check and must be reserved for a caller that has already confirmed
the target and authorization.

## Other mutations

Comments and group membership are API mutations but do not use the post-owner
check. Confirm the post, comment, group, and user IDs before invoking them.

Attachment uploads are also API mutations. Confirm the target team and each
local file path before invoking `upload-attachment`. The command sends file
contents to DocBase and does not publish a post by itself.

## Agent contract

An agent should:

1. resolve the intended team and target IDs;
2. explain the mutation in plain language before executing it;
3. use the safest command and omit `--allow-public`/`--force` unless explicitly
   authorized;
4. inspect the JSON response and report the result without exposing the token.
