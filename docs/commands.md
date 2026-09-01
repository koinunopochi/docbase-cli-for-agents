# Command reference

All API commands require `DOCBASE_DOMAIN` (or the compatibility variable
`DOCBASE_TEAM`) and `DOCBASE_API_TOKEN`. Help is available without credentials.
Use `./bin/docbase <command> --help` for the current options, prerequisites,
side effects, and recovery path. Examples use placeholder IDs and do not call
a real team.

Every mutating command requires `--confirm` after the caller has checked the
target and intended change. Without it, the CLI refuses before making an API
request. `--include-meta` can be added to any command when HTTP status and
rate-limit metadata are needed.

## Posts

### `search-posts`

Search posts with a DocBase query. Pagination defaults to page 1 and 20 items.

```sh
./bin/docbase search-posts --query "architecture" --page 1 --per-page 20
```

### `get-post`

Fetch one post by ID.

```sh
./bin/docbase get-post --post-id <post-id>
```

### `create-post`

Create a private draft post. The publication state is fixed by the CLI;
there are no options for creating a post with another state. Repeat `--tag` to
add multiple tags.

`--body` is rejected if it contains an ATX H1 heading (a line starting with
`# `, or a line that is just `#`) outside a fenced (``` / ~~~) code block.
DocBase renders the post title as an H1 already, so a body H1 duplicates it.
Use `##` or smaller for in-body headings; put the title only in `--title`.
Note that 4-space-indented code blocks aren't recognized as code by this
check, only fenced ones — a `#` inside an indented block is still rejected.
See [safety.md](./safety.md#body-content) for details.

```sh
./bin/docbase create-post \
  --title "Draft title" \
  --body "Markdown body" \
  --tag example \
  --confirm
```

### `update-post`

Update one or more of title, body, tags, draft state, or scope. The CLI checks
post ownership before every update and cannot update a post created by another
user. Pass `--confirm` after checking the target and fields.

When `--body` is supplied, it is subject to the same H1 rejection as
`create-post`. The ownership check cannot be bypassed.

```sh
./bin/docbase update-post --post-id <post-id> --title "Revised title" --confirm
./bin/docbase update-post --post-id <post-id> --publish --scope everyone --confirm
```

### `delete-post`

Delete a post after the owner check. This is irreversible from the CLI's point
of view; pass `--confirm` after checking the ID.

```sh
./bin/docbase delete-post --post-id <post-id> --confirm
```

### `archive-post`

Archive a post after the owner check. Pass `--confirm` after checking the ID.

```sh
./bin/docbase archive-post --post-id <post-id> --confirm
```

### `unarchive-post`

Restore an archived post after the owner check. Pass `--confirm` after checking
the ID.

```sh
./bin/docbase unarchive-post --post-id <post-id> --confirm
```

### `patch-post-body`

Replace a line range in a post body. `--start` and `--end` are 1-based. The
provided `--old-content` is sent with the operation so the caller can describe
the expected current text. Post ownership is checked before the operation.
The range is 1-based, `end` must be at least `start`, and `--confirm` is
required.

```sh
./bin/docbase patch-post-body \
  --post-id <post-id> \
  --start 10 \
  --end 12 \
  --old-content "old text" \
  --content "new text" \
  --confirm
```

Use `--no-notice` to suppress the update notice and `--include-body` to ask for
the updated body in the response.

`--content` is subject to the same H1 rejection as `create-post`'s `--body`,
but only the fragment you pass is inspected — not the post body that results
after the patch is applied — and the reported line number is relative to
`--content`, not to `--start`/`--end`.

## Comments

### `get-comments`

List comments for a post. Optional filters are `--order`, `--created-after`,
and `--created-before`.

```sh
./bin/docbase get-comments --post-id <post-id> --order desc
```

### `create-comment`

Add a comment. Notifications are sent by default; use `--no-notice` when that
has been explicitly requested. Pass `--confirm` after checking the post and
body.

```sh
./bin/docbase create-comment --post-id <post-id> --body "確認しました" --confirm
```

### `delete-comment`

Delete a comment by ID. Pass `--confirm` after checking the comment ID.

```sh
./bin/docbase delete-comment --comment-id <comment-id> --confirm
```

## Team, users, tags, and groups

### `get-tags`

Fetch team tags.

```sh
./bin/docbase get-tags
```

### `get-team`

Fetch team information.

```sh
./bin/docbase get-team
```

### `profile`

Fetch the authenticated profile.

```sh
./bin/docbase profile
```

### `search-users`

Search users by an optional query. The default page size is 100.

```sh
./bin/docbase search-users --query "name"
```

### `search-groups`

Search groups by exact name.

```sh
./bin/docbase search-groups --name "Readers"
```

### `get-group`

Fetch one group by ID.

```sh
./bin/docbase get-group --group-id <group-id>
```

### `create-group`

Create a group with an optional description. Pass `--confirm` after checking
the name and description.

```sh
./bin/docbase create-group --name "Readers" --description "Read-only group" --confirm
```

### `add-users-to-group`

Add users to a group. Repeat `--user-id` for multiple users and pass
`--confirm` after checking all IDs.

```sh
./bin/docbase add-users-to-group --group-id <group-id> --user-id <user-id> --confirm
```

### `remove-users-from-group`

Remove users from a group. Repeat `--user-id` for multiple users and pass
`--confirm` after checking all IDs.

```sh
./bin/docbase remove-users-from-group --group-id <group-id> --user-id <user-id> --confirm
```

## Attachments

### `upload-attachment`

Upload one or more local files to the team's DocBase attachment endpoint. The
CLI reads each file as binary data, preserves only its basename, and sends the
content as Base64. Repeat `--file` to include multiple files in one request.
The API decides whether the file format is accepted and how the returned URL is
rendered; the CLI does not inspect video codecs or provide a playback
guarantee.

```sh
./bin/docbase upload-attachment \
  --file ./media/clip.mp4 \
  --file ./media/clip.mov \
  --confirm
```

This is a mutating operation. Confirm the target team and local file paths
before running it, then pass `--confirm`. Each file must be a readable regular
file no larger than 100 MB. The official API documents a 100MB request-size
limit; split larger uploads into separate requests.

### `download-attachment`

Download an attachment to the exact path given by `--output`. A new path is
written directly. An existing regular file is refused; use
`--overwrite --confirm` only after checking the destination. Symlinks and
special files are always refused, and the parent directory must already
exist.

```sh
./bin/docbase download-attachment \
  --attachment-id <attachment-id> \
  --output ./downloads/file.bin
```

For endpoint details beyond this CLI's argument contract, consult the
[official DocBase API overview](https://help.docbase.io/posts/45703),
[upload attachment API](https://help.docbase.io/posts/225804), and
[download attachment API](https://help.docbase.io/posts/1084833).

## Input and result contract

IDs, page numbers, and line-range boundaries must be positive integers. Post,
comment, and user searches accept up to 100 results per page; group searches
accept up to 200. Invalid values are rejected before an API request. Comment
date filters accept `YYYY-MM-DD` or an ISO 8601 datetime.

An empty search result means that no item matched the requested query and page
range. It does not prove that a post, user, or group does not exist elsewhere.
