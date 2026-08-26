# Command reference

All commands require `DOCBASE_DOMAIN` (or the compatibility variable
`DOCBASE_TEAM`) and `DOCBASE_API_TOKEN`. Use `./bin/docbase <command> --help`
for the exact parser-level options. Examples use placeholder IDs and do not
call a real team.

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

Create a post. The safe default requires `--draft --scope private`; use
`--allow-public` only when the caller has explicitly authorized a different
publication state. Repeat `--tag` to add multiple tags.

`--body` is rejected if it contains an ATX H1 heading (a line starting with
`# `, or a line that is just `#`) outside a fenced code block. DocBase renders
the post title as an H1 already, so a body H1 duplicates it. Use `##` or
smaller for in-body headings; put the title only in `--title`.

```sh
./bin/docbase create-post \
  --title "Draft title" \
  --body "Markdown body" \
  --tag example \
  --draft \
  --scope private
```

### `update-post`

Update one or more of title, body, tags, draft state, or scope. The CLI checks
post ownership unless `--force` is supplied.

When `--body` is supplied, it is subject to the same H1 rejection as
`create-post`. This check runs even with `--force`, since it is unrelated to
the ownership check.

```sh
./bin/docbase update-post --post-id <post-id> --title "Revised title"
./bin/docbase update-post --post-id <post-id> --publish --scope everyone
```

### `delete-post`

Delete a post after the owner check. This is irreversible from the CLI's point
of view; confirm the ID before execution.

```sh
./bin/docbase delete-post --post-id <post-id>
```

### `archive-post`

Archive a post after the owner check.

```sh
./bin/docbase archive-post --post-id <post-id>
```

### `unarchive-post`

Restore an archived post after the owner check.

```sh
./bin/docbase unarchive-post --post-id <post-id>
```

### `patch-post-body`

Replace a line range in a post body. `--start` and `--end` are 1-based. The
provided `--old-content` is sent with the operation so the caller can describe
the expected current text. Ownership is checked unless `--force` is supplied.

```sh
./bin/docbase patch-post-body \
  --post-id <post-id> \
  --start 10 \
  --end 12 \
  --old-content "old text" \
  --content "new text"
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
has been explicitly requested.

```sh
./bin/docbase create-comment --post-id <post-id> --body "確認しました"
```

### `delete-comment`

Delete a comment by ID. Confirm the comment ID before execution.

```sh
./bin/docbase delete-comment --comment-id <comment-id>
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

Create a group with an optional description.

```sh
./bin/docbase create-group --name "Readers" --description "Read-only group"
```

### `add-users-to-group`

Add users to a group. Repeat `--user-id` for multiple users.

```sh
./bin/docbase add-users-to-group --group-id <group-id> --user-id <user-id>
```

### `remove-users-from-group`

Remove users from a group. Repeat `--user-id` for multiple users.

```sh
./bin/docbase remove-users-from-group --group-id <group-id> --user-id <user-id>
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
  --file ./media/clip.mov
```

This is a mutating operation. Confirm the target team and local file paths
before running it. The official API documents a 100MB request-size limit.

### `download-attachment`

Download an attachment to the exact path given by `--output`. The command
overwrites an existing path if the process has permission, so confirm the path
before running it.

```sh
./bin/docbase download-attachment \
  --attachment-id <attachment-id> \
  --output ./downloads/file.bin
```

For endpoint details beyond this CLI's argument contract, consult the
[official DocBase API documentation](https://help.docbase.io/posts/45703).
