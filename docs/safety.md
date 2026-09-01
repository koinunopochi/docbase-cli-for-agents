# Safety model

This CLI includes safeguards because an AI agent may be the caller. The
safeguards reduce accidental publication or mutation. They do not replace
human authorization or DocBase permissions, but the CLI itself refuses every
API mutation until the caller supplies `--confirm`.

## New posts

`create-post` always creates a private draft. The command accepts the title,
body, and optional tags, but it has no option for changing the publication
state.

```sh
./bin/docbase create-post \
  --title "A draft" \
  --body "内容" \
  --tag example \
  --confirm
```

To publish a post, use an owner-checked update operation after the draft has
been reviewed.

## Body content

DocBase already renders a post's title as an H1. `create-post --body`,
`update-post --body`, and `patch-post-body --content` reject any ATX H1
heading (a line starting with `# `, or a line that is just `#`) found outside
a fenced (``` / ~~~) code block, so the title is not duplicated inside the
body. There is no override flag for this check — restructure the body to use
`##` or smaller instead.

The check only recognizes ``` / ~~~ fences, not 4-space-indented code blocks.
A `#` inside an indented code block is treated as a heading and rejected, even
though it renders as code. Use a fenced block instead of indentation if the
code you're including happens to contain a line starting with `#`.

`patch-post-body --content` only inspects the replacement fragment you pass,
not the resulting post body. It cannot tell whether the replacement introduces
or removes an H1 in the context of the surrounding, unpatched text, and its
error message reports a line number relative to `--content`, not to
`--start`/`--end`. Confirm the resulting body separately (e.g. with `get-post`)
when a patch operation spans a heading boundary.

## Existing posts

Before `update-post`, `delete-post`, `archive-post`, `unarchive-post`, or
`patch-post-body`, the CLI compares the authenticated profile ID with the post
creator ID. A mismatch or an unavailable ID blocks the operation. `--force`
is not available in this CLI, so the check cannot be bypassed. `--confirm` is
still required.

## Other mutations

Comments and group membership are API mutations but do not use the post-owner
check. Confirm the post, comment, group, and user IDs, then pass `--confirm`.

Attachment uploads are also API mutations. Confirm the target team and each
local file path, then pass `--confirm`. The command sends file contents to
DocBase and does not publish a post by itself.

Attachment downloads do not overwrite an existing local path by default. Use
`--overwrite --confirm` only after checking the destination.

## Agent contract

An agent should:

1. resolve the intended team and target IDs;
2. explain the mutation in plain language before executing it;
3. use the safest command and confirm the target before executing a mutation;
4. inspect the JSON response and report the result without exposing the token.
