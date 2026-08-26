#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""DocBase API v3 CLI for AI agents.

This CLI keeps authentication in environment variables, emits JSON, and applies
safe defaults to operations that can change DocBase data. Start with the
executable help and follow its detailed documentation link:

    docbase --help
    docbase <command> --help

Exit codes are 0 for success, 1 for an API or network error, and 2 for invalid
configuration, arguments, or a blocked safety-policy override.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "0.1.0"


class DocBaseError(Exception):
    pass


class DocBaseConfigError(DocBaseError):
    pass


class DocBaseApiError(DocBaseError):
    def __init__(self, status: int, body: str):
        super().__init__(f"DocBase API returned HTTP {status}: {body}")
        self.status = status
        self.body = body


def resolve_config(env: dict[str, str]) -> tuple[str, str]:
    domain = env.get("DOCBASE_DOMAIN") or env.get("DOCBASE_TEAM")
    token = env.get("DOCBASE_API_TOKEN")
    if not domain or not token:
        raise DocBaseConfigError(
            "DOCBASE_DOMAIN（または DOCBASE_TEAM）と DOCBASE_API_TOKEN の両方が必要"
        )
    return domain, token


def build_request(
    domain: str,
    token: str,
    method: str,
    path: str,
    params: dict[str, object] | None = None,
    body: object | None = None,
) -> urllib.request.Request:
    base = f"https://api.docbase.io/teams/{urllib.parse.quote(domain)}"
    url = base + path
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if query:
            url += "?" + query

    data = None
    headers = {
        "X-DocBaseToken": token,
        "Content-Type": "application/json",
        "User-Agent": f"docbase-cli-for-agents/{VERSION}",
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    return urllib.request.Request(url, data=data, headers=headers, method=method)


def docbase_request(
    domain: str,
    token: str,
    method: str,
    path: str,
    params: dict[str, object] | None = None,
    body: object | None = None,
    timeout: int = 30,
) -> object:
    req = build_request(domain, token, method, path, params=params, body=body)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise DocBaseApiError(exc.code, raw) from exc
    except urllib.error.URLError as exc:
        raise DocBaseError(f"DocBase API request failed: {exc.reason}") from exc

    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def search_posts(domain: str, token: str, query: str, page: int = 1, per_page: int = 20) -> object:
    return docbase_request(
        domain,
        token,
        "GET",
        "/posts",
        params={"q": query, "page": page, "per_page": per_page},
    )


def get_post(domain: str, token: str, post_id: int) -> object:
    return docbase_request(domain, token, "GET", f"/posts/{int(post_id)}")


_H1_HEADING_LINE = re.compile(r"^#(?!#)(?:\s|$)")
_CODE_FENCE_LINE = re.compile(r"^(`{3,}|~{3,})")


def _find_h1_heading_line(body: str) -> int | None:
    """本文中で最初に見つかった ATX H1 見出し（前後の空白を除いた行が `# ` で始まる、または `#` 単独）の行番号（1始まり）を返す。

    ``` / ~~~ のコードフェンスで囲まれた範囲内の `#` はコード（コメント等）であり
    見出しではないため対象外にする。CommonMark と同様、閉じフェンスは開始フェンスと
    同じ文字（` か ~）かつ同じ長さ以上でなければ閉じたとみなさない（異なる文字種・
    短い閉じ風の行を挟むと検知漏れになるのを防ぐ）。Setext 形式（下線付き）の見出しは対象外。
    """
    fence_char: str | None = None
    fence_len = 0
    for lineno, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        fence_match = _CODE_FENCE_LINE.match(stripped)
        if fence_match:
            marker = fence_match.group(1)
            if fence_char is None:
                fence_char, fence_len = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                fence_char, fence_len = None, 0
            continue
        if fence_char is not None:
            continue
        if _H1_HEADING_LINE.match(stripped):
            return lineno
    return None


def _check_no_h1_heading(text: str, *, field_label: str = "本文") -> None:
    """title は --title で完結させ、本文の見出しは H2 以降のみを許可する。

    DocBase の title 要素と本文の H1 が同じ内容で重複して表示される事故を防ぐため。
    `field_label` はエラーメッセージの対象呼称で、`patch-post-body` のように渡す
    テキストが投稿本文の一部（置換対象の断片）でしかない呼び出し元は、行番号が
    投稿全体ではなく渡したテキスト内での相対位置であることが伝わる文言を渡す。
    """
    lineno = _find_h1_heading_line(text)
    if lineno is not None:
        raise DocBaseError(
            f"{field_label}の {lineno} 行目に H1 見出し（`# `）があります。"
            "title と重複するため、見出しは `##` 以降にしてください。"
            "title は --title で別途指定してください。"
        )


def create_post(
    domain: str,
    token: str,
    title: str,
    body: str,
    tags: list[str] | None = None,
    draft: bool = False,
    scope: str | None = None,
    allow_public: bool = False,
) -> object:
    if not allow_public and not (draft is True and scope == "private"):
        raise DocBaseError(
            "新規投稿は --draft --scope private が既定です（誤って公開範囲を広げる事故を防ぐため）。"
            "公開したい場合は --allow-public を明示してください。"
        )
    _check_no_h1_heading(body)
    payload: dict[str, object] = {"title": title, "body": body, "tags": tags or [], "draft": draft}
    if scope is not None:
        payload["scope"] = scope
    return docbase_request(domain, token, "POST", "/posts", body=payload)


def update_post(
    domain: str,
    token: str,
    post_id: int,
    title: str | None = None,
    body: str | None = None,
    tags: list[str] | None = None,
    draft: bool | None = None,
    scope: str | None = None,
    force: bool = False,
) -> object:
    if not force:
        _check_post_owner(domain, token, post_id)
    if body is not None:
        _check_no_h1_heading(body)

    payload: dict[str, object] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if tags is not None:
        payload["tags"] = tags
    if draft is not None:
        payload["draft"] = draft
    if scope is not None:
        payload["scope"] = scope
    if not payload:
        raise DocBaseError("At least one update field is required")
    return docbase_request(domain, token, "PATCH", f"/posts/{int(post_id)}", body=payload)


def _check_post_owner(domain: str, token: str, post_id: int) -> None:
    """投稿を書き換える操作の前に、トークンの持ち主が投稿の作成者本人かを確認する。

    自分以外が作成した投稿の更新・削除・アーカイブ操作は事故（他人の記事の
    意図しない書き換え・削除・公開範囲変更）につながるため、既定で拒否する。
    確認済みで進めたい場合は呼び出し側で force=True にしてこの関数をスキップする。
    """
    profile = get_profile(domain, token)
    my_id = profile.get("id") if isinstance(profile, dict) else None

    post = get_post(domain, token, post_id)
    creator = post.get("user") if isinstance(post, dict) else None
    creator_id = creator.get("id") if isinstance(creator, dict) else None

    if my_id is None or creator_id is None:
        raise DocBaseError(
            f"投稿 #{post_id} の所有者を確認できませんでした（profile/post から id を取得できません）。"
            "確認済みなら --force を付けてください。"
        )
    if my_id != creator_id:
        raise DocBaseError(
            f"投稿 #{post_id} は自分以外のユーザー（id={creator_id}）が作成しています。"
            f"自分（id={my_id}）以外が作成した投稿は操作できません。確認済みなら --force を付けてください。"
        )


def delete_post(domain: str, token: str, post_id: int, force: bool = False) -> object:
    if not force:
        _check_post_owner(domain, token, post_id)
    return docbase_request(domain, token, "DELETE", f"/posts/{int(post_id)}")


def archive_post(domain: str, token: str, post_id: int, force: bool = False) -> object:
    if not force:
        _check_post_owner(domain, token, post_id)
    return docbase_request(domain, token, "PUT", f"/posts/{int(post_id)}/archive")


def unarchive_post(domain: str, token: str, post_id: int, force: bool = False) -> object:
    if not force:
        _check_post_owner(domain, token, post_id)
    return docbase_request(domain, token, "PUT", f"/posts/{int(post_id)}/unarchive")


def patch_post_body(
    domain: str,
    token: str,
    post_id: int,
    start: int,
    end: int,
    old_content: str,
    content: str,
    notice: bool = True,
    include_body: bool = False,
    force: bool = False,
) -> object:
    if not force:
        _check_post_owner(domain, token, post_id)
    _check_no_h1_heading(content, field_label="置換後の content（--start/--end の行番号ではなく content 内の相対行）")
    payload = {
        "operations": [
            {"start": start, "end": end, "old_content": old_content, "content": content}
        ],
        "notice": notice,
        "include_body": include_body,
    }
    return docbase_request(domain, token, "PATCH", f"/posts/{int(post_id)}/body", body=payload)


def get_comments(
    domain: str,
    token: str,
    post_id: int,
    page: int = 1,
    per_page: int = 20,
    order: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
) -> object:
    return docbase_request(
        domain,
        token,
        "GET",
        f"/posts/{int(post_id)}/comments",
        params={
            "page": page,
            "per_page": per_page,
            "order": order,
            "created_after": created_after,
            "created_before": created_before,
        },
    )


def create_comment(domain: str, token: str, post_id: int, body: str, notice: bool = True) -> object:
    return docbase_request(
        domain, token, "POST", f"/posts/{int(post_id)}/comments", body={"body": body, "notice": notice}
    )


def delete_comment(domain: str, token: str, comment_id: int) -> object:
    return docbase_request(domain, token, "DELETE", f"/comments/{int(comment_id)}")


def get_tags(domain: str, token: str) -> object:
    return docbase_request(domain, token, "GET", "/tags")


def get_team(domain: str, token: str) -> object:
    return docbase_request(domain, token, "GET", "/team")


def search_users(domain: str, token: str, query: str | None = None, page: int = 1, per_page: int = 100) -> object:
    return docbase_request(domain, token, "GET", "/users", params={"q": query, "page": page, "per_page": per_page})


def search_groups(domain: str, token: str, name: str | None = None, page: int = 1, per_page: int = 100) -> object:
    return docbase_request(domain, token, "GET", "/groups", params={"name": name, "page": page, "per_page": per_page})


def get_group(domain: str, token: str, group_id: int) -> object:
    return docbase_request(domain, token, "GET", f"/groups/{int(group_id)}")


def create_group(domain: str, token: str, name: str, description: str | None = None) -> object:
    payload: dict[str, object] = {"name": name}
    if description is not None:
        payload["description"] = description
    return docbase_request(domain, token, "POST", "/groups", body=payload)


def add_users_to_group(domain: str, token: str, group_id: int, user_ids: list[int]) -> object:
    return docbase_request(
        domain, token, "POST", f"/groups/{int(group_id)}/users", body={"user_ids": user_ids}
    )


def remove_users_from_group(domain: str, token: str, group_id: int, user_ids: list[int]) -> object:
    return docbase_request(
        domain, token, "DELETE", f"/groups/{int(group_id)}/users", body={"user_ids": user_ids}
    )


def _read_attachment(file_path: str) -> dict[str, str]:
    path = Path(file_path)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise DocBaseError(f"添付ファイルを読み込めません: {file_path}") from exc

    return {
        "name": path.name,
        "content": base64.b64encode(content).decode("ascii"),
    }


def upload_attachments(domain: str, token: str, file_paths: list[str]) -> object:
    if not file_paths:
        raise DocBaseError("At least one attachment file is required")

    attachments = [_read_attachment(file_path) for file_path in file_paths]
    return docbase_request(domain, token, "POST", "/attachments", body=attachments)


def download_attachment(domain: str, token: str, attachment_id: str, output_path: str) -> object:
    req = build_request(domain, token, "GET", f"/attachments/{attachment_id}")
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            data = res.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise DocBaseApiError(exc.code, raw) from exc
    except urllib.error.URLError as exc:
        raise DocBaseError(f"DocBase API request failed: {exc.reason}") from exc

    with open(output_path, "wb") as f:
        f.write(data)
    return {"attachment_id": attachment_id, "output_path": output_path, "size": len(data)}


def get_profile(domain: str, token: str) -> object:
    return docbase_request(domain, token, "GET", "/profile")


COMMANDS_DOC_URL = "https://github.com/koinunopochi/docbase-cli-for-agents/blob/main/docs/commands.md"


def command_parser(sub: argparse._SubParsersAction, name: str, help_text: str) -> argparse.ArgumentParser:
    return sub.add_parser(
        name,
        help=help_text,
        description=help_text,
        epilog=f"Detailed documentation: {COMMANDS_DOC_URL}#{name}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docbase",
        description="DocBase API v3 CLI for AI agents",
        epilog=f"Detailed documentation: {COMMANDS_DOC_URL}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = command_parser(sub, "search-posts", help_text="DocBase の投稿を検索する")
    p_search.add_argument("--query", "-q", required=True, help="DocBase の検索クエリ")
    p_search.add_argument("--page", type=int, default=1, help="ページ番号（既定: 1）")
    p_search.add_argument("--per-page", type=int, default=20, help="1ページの件数（既定: 20、最大100）")

    p_get = command_parser(sub, "get-post", help_text="投稿 ID で1件取得する")
    p_get.add_argument("--post-id", type=int, required=True, help="取得する投稿の ID")

    p_create = command_parser(sub, "create-post", help_text="投稿を作成する")
    p_create.add_argument("--title", required=True, help="投稿タイトル")
    p_create.add_argument(
        "--body", required=True, help="投稿本文（Markdown）。H1 見出し（`# `）は title と重複するため拒否される"
    )
    p_create.add_argument("--tag", dest="tags", action="append", default=[], help="付与するタグ名。複数指定可")
    p_create.add_argument("--draft", action="store_true", default=False, help="下書きとして作成する（--allow-public 無しでは実質必須）")
    p_create.add_argument("--scope", default=None, help="公開範囲（例: everyone / group / private）")
    p_create.add_argument(
        "--allow-public",
        action="store_true",
        default=False,
        help="draft: true + scope: private 以外での作成を明示的に許可する（安全ポリシーの解除）",
    )

    p_update = command_parser(sub, "update-post", help_text="投稿を更新する")
    p_update.add_argument("--post-id", type=int, required=True, help="更新する投稿の ID")
    p_update.add_argument("--title", default=None, help="更新後のタイトル。省略時は変更しない")
    p_update.add_argument(
        "--body",
        default=None,
        help="更新後の本文。省略時は変更しない。H1 見出し（`# `）は title と重複するため拒否される",
    )
    p_update.add_argument(
        "--tag", dest="tags", action="append", default=None, help="更新後のタグ名。複数指定可。1回も指定しない場合は既存のタグを変更しない"
    )
    draft_group = p_update.add_mutually_exclusive_group()
    draft_group.add_argument(
        "--draft", dest="draft", action="store_true", default=None, help="下書きに変更する（--publish と同時指定不可）"
    )
    draft_group.add_argument("--publish", dest="draft", action="store_false", help="公開に変更する（--draft と同時指定不可）")
    p_update.add_argument("--scope", default=None, help="更新後の公開範囲。省略時は変更しない")
    p_update.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="所有者確認（自分が作成した投稿かどうか）をスキップする（安全ポリシーの解除）",
    )

    p_delete = command_parser(sub, "delete-post", help_text="投稿を削除する")
    p_delete.add_argument("--post-id", type=int, required=True, help="削除する投稿の ID")
    p_delete.add_argument(
        "--force", action="store_true", default=False, help="所有者確認をスキップする（安全ポリシーの解除）"
    )

    p_archive = command_parser(sub, "archive-post", help_text="投稿をアーカイブする")
    p_archive.add_argument("--post-id", type=int, required=True, help="アーカイブする投稿の ID")
    p_archive.add_argument(
        "--force", action="store_true", default=False, help="所有者確認をスキップする（安全ポリシーの解除）"
    )

    p_unarchive = command_parser(sub, "unarchive-post", help_text="投稿のアーカイブを解除する")
    p_unarchive.add_argument("--post-id", type=int, required=True, help="アーカイブを解除する投稿の ID")
    p_unarchive.add_argument(
        "--force", action="store_true", default=False, help="所有者確認をスキップする（安全ポリシーの解除）"
    )

    p_patch = command_parser(sub, "patch-post-body", help_text="投稿本文の一部範囲だけを置き換える")
    p_patch.add_argument("--post-id", type=int, required=True, help="対象の投稿 ID")
    p_patch.add_argument("--start", type=int, required=True, help="置き換える開始行（1始まり）")
    p_patch.add_argument("--end", type=int, required=True, help="置き換える終了行（1始まり）")
    p_patch.add_argument("--old-content", required=True, help="指定範囲の現在の文字列")
    p_patch.add_argument(
        "--content",
        required=True,
        help="指定範囲を置き換える新しい文字列。H1 見出し（`# `）は title と重複するため拒否される",
    )
    p_patch.add_argument(
        "--no-notice", dest="notice", action="store_false", default=True, help="更新通知を送らない（既定は通知する）"
    )
    p_patch.add_argument(
        "--include-body", action="store_true", default=False, help="レスポンスに更新後の本文を含める"
    )
    p_patch.add_argument(
        "--force", action="store_true", default=False, help="所有者確認をスキップする（安全ポリシーの解除）"
    )

    p_get_comments = command_parser(sub, "get-comments", help_text="投稿のコメント一覧を取得する")
    p_get_comments.add_argument("--post-id", type=int, required=True, help="対象の投稿 ID")
    p_get_comments.add_argument("--page", type=int, default=1, help="ページ番号（既定: 1）")
    p_get_comments.add_argument("--per-page", type=int, default=20, help="1ページの件数（既定: 20、最大100）")
    p_get_comments.add_argument("--order", default=None, choices=["asc", "desc"], help="並び順（既定: asc）")
    p_get_comments.add_argument("--created-after", default=None, help="この日付以降のコメントに絞る（例: 2026-01-01）")
    p_get_comments.add_argument("--created-before", default=None, help="この日付以前のコメントに絞る（例: 2026-01-01）")

    p_create_comment = command_parser(sub, "create-comment", help_text="投稿にコメントする")
    p_create_comment.add_argument("--post-id", type=int, required=True, help="対象の投稿 ID")
    p_create_comment.add_argument("--body", required=True, help="コメント本文")
    p_create_comment.add_argument(
        "--no-notice", dest="notice", action="store_false", default=True, help="更新通知を送らない（既定は通知する）"
    )

    p_delete_comment = command_parser(sub, "delete-comment", help_text="コメントを削除する")
    p_delete_comment.add_argument("--comment-id", type=int, required=True, help="削除するコメントの ID")

    command_parser(sub, "get-tags", help_text="チームのタグ一覧を取得する")
    command_parser(sub, "get-team", help_text="チーム情報を取得する")

    p_search_users = command_parser(sub, "search-users", help_text="ユーザーを検索する")
    p_search_users.add_argument("--query", "-q", default=None, help="ユーザー名または ID の一部")
    p_search_users.add_argument("--page", type=int, default=1, help="ページ番号（既定: 1）")
    p_search_users.add_argument("--per-page", type=int, default=100, help="1ページの件数（既定: 100、最大100）")

    p_search_groups = command_parser(sub, "search-groups", help_text="グループを検索する")
    p_search_groups.add_argument("--name", default=None, help="完全一致するグループ名")
    p_search_groups.add_argument("--page", type=int, default=1, help="ページ番号（既定: 1）")
    p_search_groups.add_argument("--per-page", type=int, default=100, help="1ページの件数（既定: 100、最大200）")

    p_get_group = command_parser(sub, "get-group", help_text="グループを1件取得する")
    p_get_group.add_argument("--group-id", type=int, required=True, help="取得するグループの ID")

    p_create_group = command_parser(sub, "create-group", help_text="グループを作成する")
    p_create_group.add_argument("--name", required=True, help="グループ名")
    p_create_group.add_argument("--description", default=None, help="グループの説明")

    p_add_users = command_parser(sub, "add-users-to-group", help_text="グループにユーザーを追加する")
    p_add_users.add_argument("--group-id", type=int, required=True, help="対象のグループ ID")
    p_add_users.add_argument(
        "--user-id", dest="user_ids", type=int, action="append", required=True, help="追加するユーザー ID。複数指定可"
    )

    p_remove_users = command_parser(sub, "remove-users-from-group", help_text="グループからユーザーを削除する")
    p_remove_users.add_argument("--group-id", type=int, required=True, help="対象のグループ ID")
    p_remove_users.add_argument(
        "--user-id", dest="user_ids", type=int, action="append", required=True, help="削除するユーザー ID。複数指定可"
    )

    p_download = command_parser(sub, "download-attachment", help_text="添付ファイルをダウンロードする")
    p_download.add_argument("--attachment-id", required=True, help="添付ファイルの ID")
    p_download.add_argument("--output", required=True, help="保存先のファイルパス")

    p_upload = command_parser(sub, "upload-attachment", help_text="添付ファイルをアップロードする")
    p_upload.add_argument(
        "--file",
        dest="files",
        action="append",
        required=True,
        help="アップロードするローカルファイル。複数指定可",
    )

    command_parser(sub, "profile", help_text="設定済みトークンのプロフィールを取得する")

    return parser


def dispatch(args: argparse.Namespace, domain: str, token: str) -> object:
    if args.command == "search-posts":
        return search_posts(domain, token, args.query, page=args.page, per_page=args.per_page)
    if args.command == "get-post":
        return get_post(domain, token, args.post_id)
    if args.command == "create-post":
        return create_post(
            domain,
            token,
            args.title,
            args.body,
            tags=args.tags,
            draft=args.draft,
            scope=args.scope,
            allow_public=args.allow_public,
        )
    if args.command == "update-post":
        return update_post(
            domain,
            token,
            args.post_id,
            title=args.title,
            body=args.body,
            tags=args.tags,
            draft=args.draft,
            scope=args.scope,
            force=args.force,
        )
    if args.command == "delete-post":
        return delete_post(domain, token, args.post_id, force=args.force)
    if args.command == "archive-post":
        return archive_post(domain, token, args.post_id, force=args.force)
    if args.command == "unarchive-post":
        return unarchive_post(domain, token, args.post_id, force=args.force)
    if args.command == "patch-post-body":
        return patch_post_body(
            domain,
            token,
            args.post_id,
            args.start,
            args.end,
            args.old_content,
            args.content,
            notice=args.notice,
            include_body=args.include_body,
            force=args.force,
        )
    if args.command == "get-comments":
        return get_comments(
            domain,
            token,
            args.post_id,
            page=args.page,
            per_page=args.per_page,
            order=args.order,
            created_after=args.created_after,
            created_before=args.created_before,
        )
    if args.command == "create-comment":
        return create_comment(domain, token, args.post_id, args.body, notice=args.notice)
    if args.command == "delete-comment":
        return delete_comment(domain, token, args.comment_id)
    if args.command == "get-tags":
        return get_tags(domain, token)
    if args.command == "get-team":
        return get_team(domain, token)
    if args.command == "search-users":
        return search_users(domain, token, query=args.query, page=args.page, per_page=args.per_page)
    if args.command == "search-groups":
        return search_groups(domain, token, name=args.name, page=args.page, per_page=args.per_page)
    if args.command == "get-group":
        return get_group(domain, token, args.group_id)
    if args.command == "create-group":
        return create_group(domain, token, args.name, description=args.description)
    if args.command == "add-users-to-group":
        return add_users_to_group(domain, token, args.group_id, args.user_ids)
    if args.command == "remove-users-from-group":
        return remove_users_from_group(domain, token, args.group_id, args.user_ids)
    if args.command == "upload-attachment":
        return upload_attachments(domain, token, args.files)
    if args.command == "download-attachment":
        return download_attachment(domain, token, args.attachment_id, args.output)
    if args.command == "profile":
        return get_profile(domain, token)
    raise DocBaseError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        domain, token = resolve_config(dict(os.environ))
    except DocBaseConfigError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2

    try:
        result = dispatch(args, domain, token)
    except DocBaseApiError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    except DocBaseError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
