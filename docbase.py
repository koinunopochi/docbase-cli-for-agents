#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""DocBase API v3 CLI for AI agents.

This CLI keeps authentication in environment variables, emits JSON, and applies
safe defaults to operations that can change DocBase data. Start with the
executable help and follow its bundled documentation:

    docbase --help
    docbase <command> --help

Success is written to stdout; structured errors are written to stderr. Exit
codes are 0 for success, 1 for an API or network error, and 2 for invalid
configuration, arguments, or a blocked safety policy.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

VERSION = "0.1.2"
MAX_PAGE_SIZE = 100
MAX_GROUP_PAGE_SIZE = 200
MAX_ATTACHMENT_BYTES = 100 * 1000 * 1000
MAX_ATTACHMENT_REQUEST_BYTES = 100 * 1000 * 1000
API_TIMEOUT_SECONDS = 30
ATTACHMENT_TIMEOUT_SECONDS = 60
COMMANDS_DOC_URL = f"https://github.com/koinunopochi/docbase-cli-for-agents/blob/v{VERSION}/docs/commands.md"


def _redact_text(value: object, secrets: list[str] | tuple[str, ...] = ()) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(x-docbasetoken\s*[:=]\s*)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(docbase_api_token\s*[=:]\s*)\S+", r"\1[REDACTED]", text)
    return text


def _redact_value(value: object, secrets: list[str] | tuple[str, ...] = ()) -> object:
    if isinstance(value, dict):
        return {key: _redact_value(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, secrets) for item in value]
    if isinstance(value, str):
        return _redact_text(value, secrets)
    return value


def _header_value(headers: object, name: str) -> str | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    if value is None:
        value = getter(name.lower())
    return str(value) if value is not None else None


def _response_status(response: object) -> int | None:
    status = getattr(response, "status", None)
    if status is None:
        getcode = getattr(response, "getcode", None)
        if callable(getcode):
            status = getcode()
    return status if isinstance(status, int) else None


def _response_metadata(headers: object, status: int | None = None) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if status is not None:
        metadata["http_status"] = status

    rate_limit: dict[str, object] = {}
    for header, key in (
        ("X-RateLimit-Limit", "limit"),
        ("X-RateLimit-Remaining", "remaining"),
        ("X-RateLimit-Reset", "reset"),
    ):
        value = _header_value(headers, header)
        if value is not None:
            try:
                rate_limit[key] = int(value)
            except ValueError:
                rate_limit[key] = value
    if rate_limit:
        metadata["rate_limit"] = rate_limit

    retry_after = _header_value(headers, "Retry-After")
    if retry_after is not None:
        metadata["retry_after"] = retry_after
    request_id = _header_value(headers, "X-Request-Id") or _header_value(headers, "X-Request-ID")
    if request_id is not None:
        metadata["request_id"] = request_id
    return metadata


def _api_error_detail(body: str, token: str | None = None) -> str | None:
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        detail = body
    else:
        if isinstance(parsed, dict):
            detail = None
            for key in ("message", "error", "detail", "errors"):
                if key in parsed:
                    detail = parsed[key]
                    break
            if detail is None:
                return None
        else:
            detail = parsed
        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
    detail = _redact_text(detail, [token] if token else [])
    return detail[:1000]


def _api_next_action(status: int) -> str:
    if status in (401, 403):
        return "認証情報と DocBase の権限を確認してください。"
    if status == 404:
        return "対象 ID とチームを確認し、既知の投稿は get-post などで再確認してください。"
    if status == 429:
        return "rate_limit または retry_after を確認し、待ってから再実行してください。自動再試行はしません。"
    if status >= 500:
        return "対象の状態を確認してから、時間を置いて再実行してください。自動再試行はしません。"
    return "API の応答と入力・権限を確認してください。"


class DocBaseError(Exception):
    code = "cli_error"
    exit_code = 2
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        exit_code: int | None = None,
        retryable: bool | None = None,
        next_action: str | None = None,
        details: dict[str, object] | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if exit_code is not None:
            self.exit_code = exit_code
        if retryable is not None:
            self.retryable = retryable
        self.next_action = next_action
        self.details = details
        self.status = status

    def to_payload(self, secrets: list[str] | tuple[str, ...] = ()) -> dict[str, object]:
        error: dict[str, object] = {
            "code": self.code,
            "message": _redact_text(self, secrets),
            "retryable": self.retryable,
        }
        if self.status is not None:
            error["status"] = self.status
        if self.next_action:
            error["next_action"] = self.next_action
        if self.details:
            error["details"] = _redact_value(self.details, secrets)
        return {"error": error}


class DocBaseConfigError(DocBaseError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="configuration_error", next_action="必要な環境変数を注入してから再実行してください。")


class DocBaseInputError(DocBaseError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="invalid_argument", next_action="対象コマンドの --help で引数の条件を確認して再実行してください。")


class DocBaseSafetyError(DocBaseError):
    def __init__(
        self,
        message: str,
        *,
        next_action: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            message,
            code="safety_blocked",
            next_action=next_action or "対象と安全条件を確認してから、必要な明示オプションを付けて再実行してください。",
            details=details,
        )


class DocBaseConfirmationError(DocBaseSafetyError):
    def __init__(self, operation: str, target: dict[str, object], change: dict[str, object]) -> None:
        super().__init__(
            f"{operation} は確認なしでは実行しません。",
            next_action="対象と変更内容を確認し、同じコマンドに --confirm を付けて再実行してください。",
            details={"operation": operation, "target": target, "change": change},
        )
        self.code = "confirmation_required"


class DocBaseApiError(DocBaseError):
    def __init__(
        self,
        status: int,
        body: str,
        *,
        headers: object = None,
        token: str | None = None,
    ) -> None:
        retryable = status in (408, 425, 429) or status >= 500
        detail = _api_error_detail(body, token)
        message = f"DocBase API returned HTTP {status}"
        if detail:
            message += f": {detail}"
        details: dict[str, object] = {"response": _response_metadata(headers, status)}
        super().__init__(
            message,
            code="api_error",
            exit_code=1,
            retryable=retryable,
            next_action=_api_next_action(status),
            details=details,
            status=status,
        )
        self.status = status
        self.body = body
        self.headers = headers


class DocBaseNetworkError(DocBaseError):
    def __init__(self, reason: object, token: str | None = None) -> None:
        super().__init__(
            f"DocBase API request failed: {_redact_text(reason, [token] if token else [])}",
            code="network_error",
            exit_code=1,
            retryable=True,
            next_action="接続先とネットワークを確認し、状態を確認してから再実行してください。",
        )


class DocBaseFilesystemError(DocBaseError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code="filesystem_error",
            next_action="ファイルの存在、保存先、権限を確認してから再実行してください。",
        )


def resolve_config(env: dict[str, str]) -> tuple[str, str]:
    domain = env.get("DOCBASE_DOMAIN") or env.get("DOCBASE_TEAM")
    token = env.get("DOCBASE_API_TOKEN")
    if not domain or not domain.strip() or not token or not token.strip():
        raise DocBaseConfigError(
            "DOCBASE_DOMAIN（または DOCBASE_TEAM）と DOCBASE_API_TOKEN の両方が必要"
        )
    if any(char in domain for char in "\r\n") or "/" in domain:
        raise DocBaseConfigError("DOCBASE_DOMAIN は URL のパス区切りや改行を含められません")
    if any(char in token for char in "\r\n"):
        raise DocBaseConfigError("DOCBASE_API_TOKEN は改行を含められません")
    return domain, token


def _positive_int(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DocBaseInputError(f"{label} は 1 以上の整数が必要です")
    return value


def _validate_pagination(page: int, per_page: int, *, max_per_page: int = MAX_PAGE_SIZE) -> None:
    _positive_int(page, "page")
    _positive_int(per_page, "per_page")
    if per_page > max_per_page:
        raise DocBaseInputError(f"per_page は {max_per_page} 以下で指定してください")


def _validate_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DocBaseInputError(f"{label} は空にできません")
    if any(char in value for char in "\r\n/"):
        raise DocBaseInputError(f"{label} に改行やパス区切りを含められません")
    return value


def _validate_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DocBaseInputError(f"{label} は空にできません")
    return value


def _validate_string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise DocBaseInputError(f"{label} は文字列が必要です")
    return value


def _validate_tags(tags: list[str] | None) -> list[str] | None:
    if tags is None:
        return None
    if not isinstance(tags, list):
        raise DocBaseInputError("tags は文字列の配列が必要です")
    for tag in tags:
        _validate_text(tag, "tag")
    return tags


def _parse_positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("1 以上の整数が必要です") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("1 以上の整数が必要です")
    return parsed


def _bounded_int_type(maximum: int):
    def parse(value: str) -> int:
        parsed = _parse_positive_int(value)
        if parsed > maximum:
            raise argparse.ArgumentTypeError(f"{maximum} 以下で指定してください")
        return parsed

    return parse


def _parse_iso_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as datetime_exc:
            raise argparse.ArgumentTypeError("YYYY-MM-DD または ISO 8601 日時が必要です") from datetime_exc
    return value


def _validate_iso_date(value: str, label: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DocBaseInputError(f"{label} は YYYY-MM-DD または ISO 8601 日時が必要です") from exc
    return value


def _validate_user_ids(user_ids: list[int]) -> None:
    if not user_ids:
        raise DocBaseInputError("user_ids は 1 件以上必要です")
    for user_id in user_ids:
        _positive_int(user_id, "user_id")


def _request(
    domain: str,
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, object] | None = None,
    body: object | None = None,
    include_meta: bool = False,
) -> object:
    kwargs: dict[str, object] = {}
    if params is not None:
        kwargs["params"] = params
    if body is not None:
        kwargs["body"] = body
    if include_meta:
        kwargs["include_meta"] = True
    return docbase_request(domain, token, method, path, **kwargs)


def _require_confirmation(
    confirmed: bool,
    operation: str,
    target: dict[str, object],
    change: dict[str, object],
) -> None:
    if not confirmed:
        raise DocBaseConfirmationError(operation, target, change)


def build_request(
    domain: str,
    token: str,
    method: str,
    path: str,
    params: dict[str, object] | None = None,
    body: object | None = None,
) -> urllib.request.Request:
    if any(char in token for char in "\r\n"):
        raise DocBaseConfigError("DOCBASE_API_TOKEN は改行を含められません")
    base = f"https://api.docbase.io/teams/{urllib.parse.quote(domain, safe='')}"
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
    timeout: int = API_TIMEOUT_SECONDS,
    include_meta: bool = False,
) -> object:
    req = build_request(domain, token, method, path, params=params, body=body)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8", errors="replace")
            metadata = _response_metadata(
                getattr(res, "headers", None),
                _response_status(res),
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise DocBaseApiError(exc.code, raw, headers=exc.headers, token=token) from exc
    except urllib.error.URLError as exc:
        raise DocBaseNetworkError(exc.reason, token=token) from exc
    except (TimeoutError, OSError) as exc:
        raise DocBaseNetworkError(exc, token=token) from exc

    if not raw:
        result: object = {}
    else:
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = raw
    if include_meta:
        return {"data": result, "meta": metadata}
    return result


def search_posts(
    domain: str,
    token: str,
    query: str,
    page: int = 1,
    per_page: int = 20,
    include_meta: bool = False,
) -> object:
    _validate_text(query, "query")
    _validate_pagination(page, per_page)
    return _request(
        domain,
        token,
        "GET",
        "/posts",
        params={"q": query, "page": page, "per_page": per_page},
        include_meta=include_meta,
    )


def get_post(domain: str, token: str, post_id: int, include_meta: bool = False) -> object:
    _positive_int(post_id, "post_id")
    return _request(domain, token, "GET", f"/posts/{post_id}", include_meta=include_meta)


_H1_HEADING_LINE = re.compile(r"^#(?!#)(?:\s|$)")
_CODE_FENCE_LINE = re.compile(r"^(`{3,}|~{3,})")


def _find_h1_heading_line(body: str) -> int | None:
    """本文中で最初に見つかった ATX H1 見出しの行番号（1始まり）を返す。"""
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
    """title と重複する本文の H1 を拒否する。"""
    lineno = _find_h1_heading_line(text)
    if lineno is not None:
        raise DocBaseInputError(
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
    confirm: bool = False,
    include_meta: bool = False,
) -> object:
    _validate_text(title, "title")
    _validate_text(body, "body")
    _validate_tags(tags)
    _check_no_h1_heading(body)
    _require_confirmation(
        confirm,
        "create-post",
        {"team": domain},
        {"title": title, "body_provided": bool(body), "tags": tags or [], "draft": True, "scope": "private"},
    )
    payload: dict[str, object] = {
        "title": title,
        "body": body,
        "tags": tags or [],
        "draft": True,
        "scope": "private",
    }
    return _request(domain, token, "POST", "/posts", body=payload, include_meta=include_meta)


def update_post(
    domain: str,
    token: str,
    post_id: int,
    title: str | None = None,
    body: str | None = None,
    tags: list[str] | None = None,
    draft: bool | None = None,
    scope: str | None = None,
    confirm: bool = False,
    include_meta: bool = False,
) -> object:
    _positive_int(post_id, "post_id")
    if title is not None:
        _validate_text(title, "title")
    if body is not None:
        _validate_string(body, "body")
    _validate_tags(tags)
    if scope is not None and scope not in {"everyone", "group", "private"}:
        raise DocBaseInputError("scope は everyone、group、private のいずれかが必要です")
    changed_fields = [
        name
        for name, value in (
            ("title", title),
            ("body", body),
            ("tags", tags),
            ("draft", draft),
            ("scope", scope),
        )
        if value is not None
    ]
    if not changed_fields:
        raise DocBaseInputError("少なくとも 1 つの更新項目が必要です")
    if body is not None:
        _check_no_h1_heading(body)
    _require_confirmation(
        confirm,
        "update-post",
        {"post_id": post_id},
        {"fields": changed_fields},
    )
    _check_post_owner(domain, token, post_id)

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
    return _request(domain, token, "PATCH", f"/posts/{post_id}", body=payload, include_meta=include_meta)


def _check_post_owner(domain: str, token: str, post_id: int) -> None:
    """投稿を書き換える操作の前に、トークンの持ち主が投稿の作成者本人かを確認する。

    自分以外が作成した投稿の更新・削除・アーカイブ操作は事故（他人の記事の
    意図しない書き換え・削除・公開範囲変更）につながるため、既定で拒否する。
    このCLIには所有者確認を解除する経路を設けない。
    """
    profile = get_profile(domain, token)
    my_id = profile.get("id") if isinstance(profile, dict) else None

    post = get_post(domain, token, post_id)
    creator = post.get("user") if isinstance(post, dict) else None
    creator_id = creator.get("id") if isinstance(creator, dict) else None

    if my_id is None or creator_id is None:
        raise DocBaseSafetyError(
            f"投稿 #{post_id} の所有者を確認できませんでした（profile/post から id を取得できません）。"
        )
    if my_id != creator_id:
        raise DocBaseSafetyError(
            f"投稿 #{post_id} は自分以外のユーザー（id={creator_id}）が作成しています。"
            f"自分（id={my_id}）以外が作成した投稿はこのCLIで操作できません。"
        )


def delete_post(
    domain: str,
    token: str,
    post_id: int,
    confirm: bool = False,
    include_meta: bool = False,
) -> object:
    _positive_int(post_id, "post_id")
    _require_confirmation(confirm, "delete-post", {"post_id": post_id}, {"irreversible": True})
    _check_post_owner(domain, token, post_id)
    return _request(domain, token, "DELETE", f"/posts/{post_id}", include_meta=include_meta)


def archive_post(
    domain: str,
    token: str,
    post_id: int,
    confirm: bool = False,
    include_meta: bool = False,
) -> object:
    _positive_int(post_id, "post_id")
    _require_confirmation(confirm, "archive-post", {"post_id": post_id}, {})
    _check_post_owner(domain, token, post_id)
    return _request(domain, token, "PUT", f"/posts/{post_id}/archive", include_meta=include_meta)


def unarchive_post(
    domain: str,
    token: str,
    post_id: int,
    confirm: bool = False,
    include_meta: bool = False,
) -> object:
    _positive_int(post_id, "post_id")
    _require_confirmation(confirm, "unarchive-post", {"post_id": post_id}, {})
    _check_post_owner(domain, token, post_id)
    return _request(domain, token, "PUT", f"/posts/{post_id}/unarchive", include_meta=include_meta)


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
    confirm: bool = False,
    include_meta: bool = False,
) -> object:
    _positive_int(post_id, "post_id")
    _positive_int(start, "start")
    _positive_int(end, "end")
    if end < start:
        raise DocBaseInputError("end は start 以上で指定してください")
    _validate_string(old_content, "old_content")
    _validate_string(content, "content")
    _check_no_h1_heading(content, field_label="置換後の content（--start/--end の行番号ではなく content 内の相対行）")
    _require_confirmation(
        confirm,
        "patch-post-body",
        {"post_id": post_id},
        {"start": start, "end": end, "old_content_provided": bool(old_content)},
    )
    _check_post_owner(domain, token, post_id)
    payload = {
        "operations": [
            {"start": start, "end": end, "old_content": old_content, "content": content}
        ],
        "notice": notice,
        "include_body": include_body,
    }
    return _request(domain, token, "PATCH", f"/posts/{post_id}/body", body=payload, include_meta=include_meta)


def get_comments(
    domain: str,
    token: str,
    post_id: int,
    page: int = 1,
    per_page: int = 20,
    order: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    include_meta: bool = False,
) -> object:
    _positive_int(post_id, "post_id")
    _validate_pagination(page, per_page)
    if order is not None and order not in {"asc", "desc"}:
        raise DocBaseInputError("order は asc または desc が必要です")
    if created_after is not None:
        _validate_iso_date(created_after, "created_after")
    if created_before is not None:
        _validate_iso_date(created_before, "created_before")
    return _request(
        domain,
        token,
        "GET",
        f"/posts/{post_id}/comments",
        params={
            "page": page,
            "per_page": per_page,
            "order": order,
            "created_after": created_after,
            "created_before": created_before,
        },
        include_meta=include_meta,
    )


def create_comment(
    domain: str,
    token: str,
    post_id: int,
    body: str,
    notice: bool = True,
    confirm: bool = False,
    include_meta: bool = False,
) -> object:
    _positive_int(post_id, "post_id")
    _validate_text(body, "body")
    _require_confirmation(
        confirm,
        "create-comment",
        {"post_id": post_id},
        {"body_provided": bool(body), "notice": notice},
    )
    return _request(
        domain,
        token,
        "POST",
        f"/posts/{post_id}/comments",
        body={"body": body, "notice": notice},
        include_meta=include_meta,
    )


def delete_comment(
    domain: str,
    token: str,
    comment_id: int,
    confirm: bool = False,
    include_meta: bool = False,
) -> object:
    _positive_int(comment_id, "comment_id")
    _require_confirmation(confirm, "delete-comment", {"comment_id": comment_id}, {"irreversible": True})
    return _request(domain, token, "DELETE", f"/comments/{comment_id}", include_meta=include_meta)


def get_tags(domain: str, token: str, include_meta: bool = False) -> object:
    return _request(domain, token, "GET", "/tags", include_meta=include_meta)


def get_team(domain: str, token: str, include_meta: bool = False) -> object:
    return _request(domain, token, "GET", "/team", include_meta=include_meta)


def search_users(
    domain: str,
    token: str,
    query: str | None = None,
    page: int = 1,
    per_page: int = 100,
    include_meta: bool = False,
) -> object:
    if query is not None:
        _validate_text(query, "query")
    _validate_pagination(page, per_page)
    return _request(
        domain,
        token,
        "GET",
        "/users",
        params={"q": query, "page": page, "per_page": per_page},
        include_meta=include_meta,
    )


def search_groups(
    domain: str,
    token: str,
    name: str | None = None,
    page: int = 1,
    per_page: int = 100,
    include_meta: bool = False,
) -> object:
    if name is not None:
        _validate_text(name, "name")
    _validate_pagination(page, per_page, max_per_page=MAX_GROUP_PAGE_SIZE)
    return _request(
        domain,
        token,
        "GET",
        "/groups",
        params={"name": name, "page": page, "per_page": per_page},
        include_meta=include_meta,
    )


def get_group(domain: str, token: str, group_id: int, include_meta: bool = False) -> object:
    _positive_int(group_id, "group_id")
    return _request(domain, token, "GET", f"/groups/{group_id}", include_meta=include_meta)


def create_group(
    domain: str,
    token: str,
    name: str,
    description: str | None = None,
    confirm: bool = False,
    include_meta: bool = False,
) -> object:
    _validate_text(name, "name")
    _require_confirmation(
        confirm,
        "create-group",
        {"team": domain},
        {"name": name, "description_provided": description is not None},
    )
    payload: dict[str, object] = {"name": name}
    if description is not None:
        payload["description"] = description
    return _request(domain, token, "POST", "/groups", body=payload, include_meta=include_meta)


def add_users_to_group(
    domain: str,
    token: str,
    group_id: int,
    user_ids: list[int],
    confirm: bool = False,
    include_meta: bool = False,
) -> object:
    _positive_int(group_id, "group_id")
    _validate_user_ids(user_ids)
    _require_confirmation(
        confirm,
        "add-users-to-group",
        {"group_id": group_id},
        {"user_ids": user_ids},
    )
    return _request(
        domain,
        token,
        "POST",
        f"/groups/{group_id}/users",
        body={"user_ids": user_ids},
        include_meta=include_meta,
    )


def remove_users_from_group(
    domain: str,
    token: str,
    group_id: int,
    user_ids: list[int],
    confirm: bool = False,
    include_meta: bool = False,
) -> object:
    _positive_int(group_id, "group_id")
    _validate_user_ids(user_ids)
    _require_confirmation(
        confirm,
        "remove-users-from-group",
        {"group_id": group_id},
        {"user_ids": user_ids},
    )
    return _request(
        domain,
        token,
        "DELETE",
        f"/groups/{group_id}/users",
        body={"user_ids": user_ids},
        include_meta=include_meta,
    )


def _attachment_path(file_path: str) -> tuple[Path, int]:
    if not isinstance(file_path, str) or not file_path.strip() or "\x00" in file_path:
        raise DocBaseInputError("添付ファイルのパスは空にできません")
    path = Path(file_path)
    if not path.is_file():
        raise DocBaseFilesystemError(f"添付ファイルが通常のファイルではありません: {file_path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise DocBaseFilesystemError(f"添付ファイルの情報を取得できません: {file_path}") from exc
    if size > MAX_ATTACHMENT_BYTES:
        raise DocBaseInputError(f"添付ファイルは {MAX_ATTACHMENT_BYTES // 1_000_000} MB 以下にしてください: {file_path}")
    return path, size


def _read_attachment(file_path: str) -> dict[str, str]:
    path, _ = _attachment_path(file_path)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise DocBaseFilesystemError(f"添付ファイルを読み込めません: {file_path}") from exc

    return {
        "name": path.name,
        "content": base64.b64encode(content).decode("ascii"),
    }


def upload_attachments(
    domain: str,
    token: str,
    file_paths: list[str],
    confirm: bool = False,
    include_meta: bool = False,
) -> object:
    if not file_paths:
        raise DocBaseInputError("添付ファイルは 1 件以上必要です")

    _require_confirmation(
        confirm,
        "upload-attachment",
        {"team": domain},
        {"file_paths": file_paths},
    )

    preflight = [_attachment_path(file_path) for file_path in file_paths]
    estimated_size = sum(
        ((size + 2) // 3 * 4) + len(path.name.encode("utf-8")) + 32
        for path, size in preflight
    )
    if estimated_size > MAX_ATTACHMENT_REQUEST_BYTES:
        raise DocBaseInputError("添付ファイルのリクエストサイズが大きすぎます。ファイルを分けて送信してください")

    attachments = [_read_attachment(file_path) for file_path in file_paths]
    total_size = sum(
        len(item["content"].encode("ascii")) + len(item["name"].encode("utf-8")) + 32
        for item in attachments
    )
    if total_size > MAX_ATTACHMENT_REQUEST_BYTES:
        raise DocBaseInputError("添付ファイルのリクエストサイズが大きすぎます。ファイルを分けて送信してください")
    return _request(domain, token, "POST", "/attachments", body=attachments, include_meta=include_meta)


def download_attachment(
    domain: str,
    token: str,
    attachment_id: str,
    output_path: str,
    overwrite: bool = False,
    confirm: bool = False,
    include_meta: bool = False,
) -> object:
    attachment_id = _validate_identifier(attachment_id, "attachment_id")
    output_path = _validate_string(output_path, "output")
    if not output_path.strip() or "\x00" in output_path:
        raise DocBaseInputError("output は空にできません")
    output = Path(output_path)
    output_state = _download_output_state(output)
    if output_state == "existing" and not overwrite:
        raise DocBaseSafetyError(
            f"保存先が既に存在します: {output_path}",
            next_action="別の保存先を指定するか、既存ファイルを確認したうえで --overwrite --confirm を付けて再実行してください。",
            details={"output_path": output_path, "overwrite_requested": False},
        )
    if output_state == "existing" and overwrite:
        _require_confirmation(
            confirm,
            "download-attachment (overwrite local file)",
            {"attachment_id": attachment_id, "output_path": output_path},
            {"overwrite": True},
        )

    req = build_request(domain, token, "GET", f"/attachments/{urllib.parse.quote(attachment_id, safe='')}")
    try:
        with urllib.request.urlopen(req, timeout=ATTACHMENT_TIMEOUT_SECONDS) as res:
            data = res.read()
            metadata = _response_metadata(
                getattr(res, "headers", None),
                _response_status(res),
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise DocBaseApiError(exc.code, raw, headers=exc.headers, token=token) from exc
    except urllib.error.URLError as exc:
        raise DocBaseNetworkError(exc.reason, token=token) from exc
    except (TimeoutError, OSError) as exc:
        raise DocBaseNetworkError(exc, token=token) from exc

    _write_download(output, data, overwrite=output_state == "existing")
    result: dict[str, object] = {"attachment_id": attachment_id, "output_path": output_path, "size": len(data)}
    if include_meta:
        result["meta"] = metadata
    return result


def _download_output_state(output: Path) -> str:
    """Validate a download destination before making the remote request."""
    try:
        output_info = output.lstat()
    except FileNotFoundError:
        parent = output.parent
        try:
            parent_info = parent.stat()
        except FileNotFoundError as exc:
            raise DocBaseFilesystemError(f"ダウンロード先の親ディレクトリがありません: {parent}") from exc
        except OSError as exc:
            raise DocBaseFilesystemError(f"ダウンロード先の親ディレクトリを確認できません: {parent}") from exc
        if not stat.S_ISDIR(parent_info.st_mode):
            raise DocBaseFilesystemError(f"ダウンロード先の親がディレクトリではありません: {parent}")
        return "new"
    except OSError as exc:
        raise DocBaseFilesystemError(f"ダウンロード先を確認できません: {output}") from exc

    if stat.S_ISLNK(output_info.st_mode):
        raise DocBaseSafetyError(
            f"保存先が symlink です: {output}",
            next_action="symlink ではない通常のファイルパスを指定してください。",
            details={"output_path": str(output), "symlink": True},
        )
    if not stat.S_ISREG(output_info.st_mode):
        raise DocBaseSafetyError(
            f"保存先が通常のファイルではありません: {output}",
            next_action="通常のファイルではない保存先を指定しないでください。",
            details={"output_path": str(output), "regular_file": False},
        )
    return "existing"


def _write_download(output: Path, data: bytes, *, overwrite: bool) -> None:
    """Write bytes without following a destination symlink or clobbering a new path."""
    if not overwrite:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(output, flags | nofollow, 0o600)
        except FileExistsError as exc:
            raise DocBaseSafetyError(
                f"保存先が実行中に作成されました: {output}",
                next_action="保存先を確認して別のパスを指定してください。",
                details={"output_path": str(output), "race": True},
            ) from exc
        except OSError as exc:
            raise DocBaseFilesystemError(f"ダウンロード先を作成できません: {output}") from exc
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
        except OSError as exc:
            raise DocBaseFilesystemError(f"ダウンロード先へ書き込めません: {output}") from exc
        return

    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=output.parent, prefix=f".{output.name}.", suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = temporary.name
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, output)
        temporary_path = None
    except OSError as exc:
        raise DocBaseFilesystemError(f"ダウンロード先へ安全に書き込めません: {output}") from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def get_profile(domain: str, token: str, include_meta: bool = False) -> object:
    return _request(domain, token, "GET", "/profile", include_meta=include_meta)


class DocBaseArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise DocBaseInputError(message)


def _parse_nonempty_text(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("空では指定できません")
    return value


def _parse_identifier(value: str) -> str:
    if not value.strip() or any(char in value for char in "\r\n/"):
        raise argparse.ArgumentTypeError("空、改行、パス区切りを含められません")
    return value


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--include-meta",
        action="store_true",
        help="成功出力を data と API の HTTP / rate-limit metadata の envelope にする",
    )


def command_parser(
    sub: argparse._SubParsersAction,
    name: str,
    help_text: str,
    *,
    mutating: bool = False,
    local_write: bool = False,
    search: bool = False,
) -> argparse.ArgumentParser:
    if local_write:
        contract = (
            "Local side effect: an existing output path is never overwritten by default. "
            "Use --overwrite --confirm only after checking the path."
        )
    elif mutating:
        contract = (
            "Safety: this command can change DocBase. Verify the target and change, "
            "then pass --confirm; without it no API request is made."
        )
    else:
        contract = (
            "Prerequisites: credentials come from the process environment; there is no prompt. "
            "This command calls the DocBase API."
        )
        if search:
            contract += " An empty search is no match within the requested scope, not proof of absence."
    parser = sub.add_parser(
        name,
        help=help_text,
        description=help_text,
        epilog=(
            f"{contract}\n"
            "Errors are JSON on stderr with an exit code and next action.\n"
            f"Bundled docs: {Path(__file__).resolve().parent / 'docs' / 'commands.md'}\n"
            f"Online docs: {COMMANDS_DOC_URL}#{name}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_output_options(parser)
    if mutating:
        confirmation_help = (
            "既存ファイルを上書きするときの確認済み指定（新しい保存先には不要）"
            if local_write
            else "対象と変更内容を確認済みであることを明示する（未指定なら実行しない）"
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help=confirmation_help,
        )
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = DocBaseArgumentParser(
        prog="docbase",
        description=(
            "DocBase API v3 CLI for AI agents.\n"
            "Success: JSON on stdout. Errors: JSON on stderr.\n"
            "Mutations require target/change confirmation via --confirm."
        ),
        epilog=(
            "Use docbase <command> --help for prerequisites, side effects, constraints, and recovery.\n"
            f"Bundled docs: {Path(__file__).resolve().parent / 'docs'}\n"
            f"Online docs: {COMMANDS_DOC_URL}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=DocBaseArgumentParser)

    p_search = command_parser(sub, "search-posts", help_text="DocBase の投稿を検索する", search=True)
    p_search.add_argument("--query", "-q", required=True, type=_parse_nonempty_text, help="DocBase の検索クエリ")
    p_search.add_argument("--page", type=_parse_positive_int, default=1, help="ページ番号（1 以上。既定: 1）")
    p_search.add_argument("--per-page", type=_bounded_int_type(MAX_PAGE_SIZE), default=20, help="1ページの件数（1〜100。既定: 20）")

    p_get = command_parser(sub, "get-post", help_text="投稿 ID で1件取得する")
    p_get.add_argument("--post-id", type=_parse_positive_int, required=True, help="取得する投稿の ID（1 以上）")

    p_create = command_parser(sub, "create-post", help_text="個人の下書きとして投稿を作成する", mutating=True)
    p_create.add_argument("--title", required=True, type=_parse_nonempty_text, help="投稿タイトル")
    p_create.add_argument(
        "--body",
        required=True,
        type=_parse_nonempty_text,
        help="投稿本文（Markdown）。H1 見出し（`# `）は title と重複するため拒否される",
    )
    p_create.add_argument("--tag", dest="tags", action="append", default=[], type=_parse_nonempty_text, help="付与するタグ名。複数指定可")

    p_update = command_parser(sub, "update-post", help_text="投稿を更新する", mutating=True)
    p_update.add_argument("--post-id", type=_parse_positive_int, required=True, help="更新する投稿の ID（1 以上）")
    p_update.add_argument("--title", default=None, help="更新後のタイトル。省略時は変更しない")
    p_update.add_argument(
        "--body",
        default=None,
        help="更新後の本文。省略時は変更しない。H1 見出し（`# `）は title と重複するため拒否される",
    )
    p_update.add_argument(
        "--tag", dest="tags", action="append", default=None, type=_parse_nonempty_text, help="更新後のタグ名。複数指定可。1回も指定しない場合は既存のタグを変更しない"
    )
    draft_group = p_update.add_mutually_exclusive_group()
    draft_group.add_argument(
        "--draft", dest="draft", action="store_true", default=None, help="下書きに変更する（--publish と同時指定不可）"
    )
    draft_group.add_argument("--publish", dest="draft", action="store_false", help="公開に変更する（--draft と同時指定不可）")
    p_update.add_argument("--scope", default=None, choices=["everyone", "group", "private"], help="更新後の公開範囲。省略時は変更しない")
    p_delete = command_parser(sub, "delete-post", help_text="投稿を削除する", mutating=True)
    p_delete.add_argument("--post-id", type=_parse_positive_int, required=True, help="削除する投稿の ID（1 以上）")

    p_archive = command_parser(sub, "archive-post", help_text="投稿をアーカイブする", mutating=True)
    p_archive.add_argument("--post-id", type=_parse_positive_int, required=True, help="アーカイブする投稿の ID（1 以上）")

    p_unarchive = command_parser(sub, "unarchive-post", help_text="投稿のアーカイブを解除する", mutating=True)
    p_unarchive.add_argument("--post-id", type=_parse_positive_int, required=True, help="アーカイブを解除する投稿の ID（1 以上）")

    p_patch = command_parser(sub, "patch-post-body", help_text="投稿本文の一部範囲だけを置き換える", mutating=True)
    p_patch.add_argument("--post-id", type=_parse_positive_int, required=True, help="対象の投稿 ID（1 以上）")
    p_patch.add_argument("--start", type=_parse_positive_int, required=True, help="置き換える開始行（1 始まり）")
    p_patch.add_argument("--end", type=_parse_positive_int, required=True, help="置き換える終了行（start 以上）")
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

    p_get_comments = command_parser(sub, "get-comments", help_text="投稿のコメント一覧を取得する", search=True)
    p_get_comments.add_argument("--post-id", type=_parse_positive_int, required=True, help="対象の投稿 ID（1 以上）")
    p_get_comments.add_argument("--page", type=_parse_positive_int, default=1, help="ページ番号（1 以上。既定: 1）")
    p_get_comments.add_argument("--per-page", type=_bounded_int_type(MAX_PAGE_SIZE), default=20, help="1ページの件数（1〜100。既定: 20）")
    p_get_comments.add_argument("--order", default=None, choices=["asc", "desc"], help="並び順（既定: asc）")
    p_get_comments.add_argument("--created-after", type=_parse_iso_date, default=None, help="この日付以降のコメント（YYYY-MM-DD または ISO 8601）")
    p_get_comments.add_argument("--created-before", type=_parse_iso_date, default=None, help="この日付以前のコメント（YYYY-MM-DD または ISO 8601）")

    p_create_comment = command_parser(sub, "create-comment", help_text="投稿にコメントする", mutating=True)
    p_create_comment.add_argument("--post-id", type=_parse_positive_int, required=True, help="対象の投稿 ID（1 以上）")
    p_create_comment.add_argument("--body", required=True, type=_parse_nonempty_text, help="コメント本文")
    p_create_comment.add_argument(
        "--no-notice", dest="notice", action="store_false", default=True, help="更新通知を送らない（既定は通知する）"
    )

    p_delete_comment = command_parser(sub, "delete-comment", help_text="コメントを削除する", mutating=True)
    p_delete_comment.add_argument("--comment-id", type=_parse_positive_int, required=True, help="削除するコメントの ID（1 以上）")

    command_parser(sub, "get-tags", help_text="チームのタグ一覧を取得する")
    command_parser(sub, "get-team", help_text="チーム情報を取得する")

    p_search_users = command_parser(sub, "search-users", help_text="ユーザーを検索する", search=True)
    p_search_users.add_argument("--query", "-q", default=None, type=_parse_nonempty_text, help="ユーザー名または ID の一部")
    p_search_users.add_argument("--page", type=_parse_positive_int, default=1, help="ページ番号（1 以上。既定: 1）")
    p_search_users.add_argument("--per-page", type=_bounded_int_type(MAX_PAGE_SIZE), default=100, help="1ページの件数（1〜100。既定: 100）")

    p_search_groups = command_parser(sub, "search-groups", help_text="グループを検索する", search=True)
    p_search_groups.add_argument("--name", default=None, type=_parse_nonempty_text, help="完全一致するグループ名")
    p_search_groups.add_argument("--page", type=_parse_positive_int, default=1, help="ページ番号（1 以上。既定: 1）")
    p_search_groups.add_argument("--per-page", type=_bounded_int_type(MAX_GROUP_PAGE_SIZE), default=100, help="1ページの件数（1〜200。既定: 100）")

    p_get_group = command_parser(sub, "get-group", help_text="グループを1件取得する")
    p_get_group.add_argument("--group-id", type=_parse_positive_int, required=True, help="取得するグループの ID（1 以上）")

    p_create_group = command_parser(sub, "create-group", help_text="グループを作成する", mutating=True)
    p_create_group.add_argument("--name", required=True, type=_parse_nonempty_text, help="グループ名")
    p_create_group.add_argument("--description", default=None, help="グループの説明")

    p_add_users = command_parser(sub, "add-users-to-group", help_text="グループにユーザーを追加する", mutating=True)
    p_add_users.add_argument("--group-id", type=_parse_positive_int, required=True, help="対象のグループ ID（1 以上）")
    p_add_users.add_argument(
        "--user-id", dest="user_ids", type=_parse_positive_int, action="append", required=True, help="追加するユーザー ID（複数指定可）"
    )

    p_remove_users = command_parser(sub, "remove-users-from-group", help_text="グループからユーザーを削除する", mutating=True)
    p_remove_users.add_argument("--group-id", type=_parse_positive_int, required=True, help="対象のグループ ID（1 以上）")
    p_remove_users.add_argument(
        "--user-id", dest="user_ids", type=_parse_positive_int, action="append", required=True, help="削除するユーザー ID（複数指定可）"
    )

    p_download = command_parser(sub, "download-attachment", help_text="添付ファイルをダウンロードする", mutating=True, local_write=True)
    p_download.add_argument("--attachment-id", required=True, type=_parse_identifier, help="添付ファイルの ID")
    p_download.add_argument("--output", required=True, type=_parse_nonempty_text, help="保存先のファイルパス")
    p_download.add_argument("--overwrite", action="store_true", default=False, help="既存ファイルを上書きする（--confirm と併用）")

    p_upload = command_parser(sub, "upload-attachment", help_text="添付ファイルをアップロードする", mutating=True)
    p_upload.add_argument(
        "--file",
        dest="files",
        action="append",
        required=True,
        type=_parse_nonempty_text,
        help="アップロードするローカルファイル。複数指定可",
    )

    command_parser(sub, "profile", help_text="設定済みトークンのプロフィールを取得する")

    return parser


def dispatch(args: argparse.Namespace, domain: str, token: str) -> object:
    include_meta = getattr(args, "include_meta", False)
    meta_kwargs = {"include_meta": True} if include_meta else {}
    if args.command == "search-posts":
        return search_posts(domain, token, args.query, page=args.page, per_page=args.per_page, **meta_kwargs)
    if args.command == "get-post":
        return get_post(domain, token, args.post_id, **meta_kwargs)
    if args.command == "create-post":
        return create_post(
            domain,
            token,
            args.title,
            args.body,
            tags=args.tags,
            confirm=args.confirm,
            **meta_kwargs,
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
            confirm=args.confirm,
            **meta_kwargs,
        )
    if args.command == "delete-post":
        return delete_post(domain, token, args.post_id, confirm=args.confirm, **meta_kwargs)
    if args.command == "archive-post":
        return archive_post(domain, token, args.post_id, confirm=args.confirm, **meta_kwargs)
    if args.command == "unarchive-post":
        return unarchive_post(domain, token, args.post_id, confirm=args.confirm, **meta_kwargs)
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
            confirm=args.confirm,
            **meta_kwargs,
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
            **meta_kwargs,
        )
    if args.command == "create-comment":
        return create_comment(domain, token, args.post_id, args.body, notice=args.notice, confirm=args.confirm, **meta_kwargs)
    if args.command == "delete-comment":
        return delete_comment(domain, token, args.comment_id, confirm=args.confirm, **meta_kwargs)
    if args.command == "get-tags":
        return get_tags(domain, token, **meta_kwargs)
    if args.command == "get-team":
        return get_team(domain, token, **meta_kwargs)
    if args.command == "search-users":
        return search_users(domain, token, query=args.query, page=args.page, per_page=args.per_page, **meta_kwargs)
    if args.command == "search-groups":
        return search_groups(domain, token, name=args.name, page=args.page, per_page=args.per_page, **meta_kwargs)
    if args.command == "get-group":
        return get_group(domain, token, args.group_id, **meta_kwargs)
    if args.command == "create-group":
        return create_group(domain, token, args.name, description=args.description, confirm=args.confirm, **meta_kwargs)
    if args.command == "add-users-to-group":
        return add_users_to_group(domain, token, args.group_id, args.user_ids, confirm=args.confirm, **meta_kwargs)
    if args.command == "remove-users-from-group":
        return remove_users_from_group(domain, token, args.group_id, args.user_ids, confirm=args.confirm, **meta_kwargs)
    if args.command == "upload-attachment":
        return upload_attachments(domain, token, args.files, confirm=args.confirm, **meta_kwargs)
    if args.command == "download-attachment":
        return download_attachment(
            domain,
            token,
            args.attachment_id,
            args.output,
            overwrite=args.overwrite,
            confirm=args.confirm,
            **meta_kwargs,
        )
    if args.command == "profile":
        return get_profile(domain, token, **meta_kwargs)
    raise DocBaseError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    token: str | None = os.environ.get("DOCBASE_API_TOKEN")
    try:
        args = parser.parse_args(argv)
        domain, token = resolve_config(dict(os.environ))
        result = dispatch(args, domain, token)
    except DocBaseError as exc:
        print(json.dumps(exc.to_payload([token] if token else []), ensure_ascii=False, indent=2), file=sys.stderr)
        return exc.exit_code

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
