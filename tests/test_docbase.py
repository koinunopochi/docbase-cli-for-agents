#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "docbase.py"
SPEC = importlib.util.spec_from_file_location("docbase_cli", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ResolveConfigTest(unittest.TestCase):
    def test_prefers_docbase_domain_over_docbase_team(self) -> None:
        domain, token = MODULE.resolve_config(
            {"DOCBASE_DOMAIN": "d1", "DOCBASE_TEAM": "d2", "DOCBASE_API_TOKEN": "t"}
        )
        self.assertEqual((domain, token), ("d1", "t"))

    def test_falls_back_to_docbase_team(self) -> None:
        domain, token = MODULE.resolve_config({"DOCBASE_TEAM": "d2", "DOCBASE_API_TOKEN": "t"})
        self.assertEqual((domain, token), ("d2", "t"))

    def test_raises_when_token_missing(self) -> None:
        with self.assertRaises(MODULE.DocBaseConfigError):
            MODULE.resolve_config({"DOCBASE_DOMAIN": "d1"})

    def test_raises_when_domain_missing(self) -> None:
        with self.assertRaises(MODULE.DocBaseConfigError):
            MODULE.resolve_config({"DOCBASE_API_TOKEN": "t"})


class BuildRequestTest(unittest.TestCase):
    def test_search_posts_request_shape(self) -> None:
        req = MODULE.build_request(
            "myteam", "tok", "GET", "/posts", params={"q": "hello world", "page": 1, "per_page": 20}
        )
        self.assertEqual(req.get_method(), "GET")
        self.assertTrue(req.full_url.startswith("https://api.docbase.io/teams/myteam/posts?"))
        self.assertIn("q=hello", req.full_url)
        self.assertEqual(req.get_header("X-docbasetoken"), "tok")
        self.assertEqual(req.get_header("User-agent"), f"docbase-cli-for-agents/{MODULE.VERSION}")
        self.assertIsNone(req.data)

    def test_create_post_request_has_json_body(self) -> None:
        req = MODULE.build_request(
            "myteam", "tok", "POST", "/posts", body={"title": "t", "body": "b", "tags": [], "draft": False}
        )
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data.decode("utf-8")), {"title": "t", "body": "b", "tags": [], "draft": False})
        self.assertEqual(req.get_header("Content-type"), "application/json")

    def test_upload_attachment_request_accepts_array_body(self) -> None:
        req = MODULE.build_request(
            "myteam", "tok", "POST", "/attachments", body=[{"name": "clip.mp4", "content": "AA=="}]
        )
        self.assertEqual(json.loads(req.data.decode("utf-8")), [{"name": "clip.mp4", "content": "AA=="}])

    def test_domain_is_url_quoted(self) -> None:
        req = MODULE.build_request("my team", "tok", "GET", "/profile")
        self.assertIn("my%20team", req.full_url)


class CreatePostPolicyTest(unittest.TestCase):
    def test_rejects_default_draft_false(self) -> None:
        with self.assertRaises(MODULE.DocBaseError):
            MODULE.create_post("d", "t", "title", "body")

    def test_rejects_draft_true_without_private_scope(self) -> None:
        with self.assertRaises(MODULE.DocBaseError):
            MODULE.create_post("d", "t", "title", "body", draft=True, scope="everyone")

    def test_rejects_private_scope_without_draft(self) -> None:
        with self.assertRaises(MODULE.DocBaseError):
            MODULE.create_post("d", "t", "title", "body", draft=False, scope="private")

    @mock.patch.object(MODULE, "docbase_request")
    def test_allows_draft_true_and_scope_private(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {"id": 1}
        MODULE.create_post("d", "t", "title", "body", draft=True, scope="private")
        mock_request.assert_called_once()

    @mock.patch.object(MODULE, "docbase_request")
    def test_allow_public_bypasses_policy(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {"id": 1}
        MODULE.create_post("d", "t", "title", "body", draft=False, scope="everyone", allow_public=True)
        mock_request.assert_called_once()


class UpdatePostOwnerCheckTest(unittest.TestCase):
    def test_rejects_empty_update_even_with_force(self) -> None:
        with self.assertRaises(MODULE.DocBaseError):
            MODULE.update_post("d", "t", 1, force=True)

    @mock.patch.object(MODULE, "docbase_request")
    def test_checks_owner_before_patch_and_blocks_mismatch(self, mock_request: mock.Mock) -> None:
        def side_effect(domain, token, method, path, params=None, body=None, timeout=30):
            if method == "GET" and path == "/profile":
                return {"id": 1}
            if method == "GET" and path == "/posts/1":
                return {"id": 1, "user": {"id": 2}}
            raise AssertionError(f"unexpected call: {method} {path}")

        mock_request.side_effect = side_effect
        with self.assertRaises(MODULE.DocBaseError):
            MODULE.update_post("d", "t", 1, title="new title")
        # PATCH was never reached
        patch_calls = [c for c in mock_request.call_args_list if c.args[2] == "PATCH"]
        self.assertEqual(patch_calls, [])

    @mock.patch.object(MODULE, "docbase_request")
    def test_allows_update_when_owner_matches(self, mock_request: mock.Mock) -> None:
        def side_effect(domain, token, method, path, params=None, body=None, timeout=30):
            if method == "GET" and path == "/profile":
                return {"id": 1}
            if method == "GET" and path == "/posts/1":
                return {"id": 1, "user": {"id": 1}}
            if method == "PATCH" and path == "/posts/1":
                return {"id": 1, "title": "new title"}
            raise AssertionError(f"unexpected call: {method} {path}")

        mock_request.side_effect = side_effect
        result = MODULE.update_post("d", "t", 1, title="new title")
        self.assertEqual(result, {"id": 1, "title": "new title"})

    @mock.patch.object(MODULE, "docbase_request")
    def test_force_skips_owner_check(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {"id": 1, "title": "new title"}
        MODULE.update_post("d", "t", 1, title="new title", force=True)
        # Only the PATCH call happened — no /profile or /posts GET
        mock_request.assert_called_once()
        self.assertEqual(mock_request.call_args.args[2], "PATCH")


class UpdatePostTest(unittest.TestCase):
    @mock.patch.object(MODULE, "docbase_request")
    def test_only_includes_provided_fields(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {"id": 1}
        MODULE.update_post("d", "t", 1, title="new title", force=True)
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["body"], {"title": "new title"})

    @mock.patch.object(MODULE, "docbase_request")
    def test_draft_false_is_sent_explicitly(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {"id": 1}
        MODULE.update_post("d", "t", 1, draft=False, force=True)
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["body"], {"draft": False})


class DeletePostOwnerCheckTest(unittest.TestCase):
    @mock.patch.object(MODULE, "docbase_request")
    def test_blocks_mismatch(self, mock_request: mock.Mock) -> None:
        def side_effect(domain, token, method, path, params=None, body=None, timeout=30):
            if method == "GET" and path == "/profile":
                return {"id": 1}
            if method == "GET" and path == "/posts/1":
                return {"id": 1, "user": {"id": 2}}
            raise AssertionError(f"unexpected call: {method} {path}")

        mock_request.side_effect = side_effect
        with self.assertRaises(MODULE.DocBaseError):
            MODULE.delete_post("d", "t", 1)
        delete_calls = [c for c in mock_request.call_args_list if c.args[2] == "DELETE"]
        self.assertEqual(delete_calls, [])

    @mock.patch.object(MODULE, "docbase_request")
    def test_force_skips_owner_check(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {}
        MODULE.delete_post("d", "t", 1, force=True)
        mock_request.assert_called_once_with("d", "t", "DELETE", "/posts/1")


class ArchivePostOwnerCheckTest(unittest.TestCase):
    @mock.patch.object(MODULE, "docbase_request")
    def test_blocks_mismatch(self, mock_request: mock.Mock) -> None:
        def side_effect(domain, token, method, path, params=None, body=None, timeout=30):
            if method == "GET" and path == "/profile":
                return {"id": 1}
            if method == "GET" and path == "/posts/1":
                return {"id": 1, "user": {"id": 2}}
            raise AssertionError(f"unexpected call: {method} {path}")

        mock_request.side_effect = side_effect
        with self.assertRaises(MODULE.DocBaseError):
            MODULE.archive_post("d", "t", 1)
        with self.assertRaises(MODULE.DocBaseError):
            MODULE.unarchive_post("d", "t", 1)

    @mock.patch.object(MODULE, "docbase_request")
    def test_force_skips_owner_check(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {}
        MODULE.archive_post("d", "t", 1, force=True)
        mock_request.assert_called_once_with("d", "t", "PUT", "/posts/1/archive")


class PatchPostBodyTest(unittest.TestCase):
    @mock.patch.object(MODULE, "docbase_request")
    def test_blocks_mismatch(self, mock_request: mock.Mock) -> None:
        def side_effect(domain, token, method, path, params=None, body=None, timeout=30):
            if method == "GET" and path == "/profile":
                return {"id": 1}
            if method == "GET" and path == "/posts/1":
                return {"id": 1, "user": {"id": 2}}
            raise AssertionError(f"unexpected call: {method} {path}")

        mock_request.side_effect = side_effect
        with self.assertRaises(MODULE.DocBaseError):
            MODULE.patch_post_body("d", "t", 1, 1, 2, "old", "new")

    @mock.patch.object(MODULE, "docbase_request")
    def test_builds_single_operation_payload(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {"id": 1}
        MODULE.patch_post_body("d", "t", 1, 3, 5, "old text", "new text", force=True)
        mock_request.assert_called_once_with(
            "d",
            "t",
            "PATCH",
            "/posts/1/body",
            body={
                "operations": [{"start": 3, "end": 5, "old_content": "old text", "content": "new text"}],
                "notice": True,
                "include_body": False,
            },
        )


class CommentsTest(unittest.TestCase):
    @mock.patch.object(MODULE, "docbase_request")
    def test_get_comments_request_shape(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {"comments": []}
        MODULE.get_comments("d", "t", 1, page=2, per_page=10, order="desc")
        mock_request.assert_called_once_with(
            "d",
            "t",
            "GET",
            "/posts/1/comments",
            params={"page": 2, "per_page": 10, "order": "desc", "created_after": None, "created_before": None},
        )

    @mock.patch.object(MODULE, "docbase_request")
    def test_create_comment_request_shape(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {"id": 9}
        MODULE.create_comment("d", "t", 1, "hello", notice=False)
        mock_request.assert_called_once_with(
            "d", "t", "POST", "/posts/1/comments", body={"body": "hello", "notice": False}
        )

    @mock.patch.object(MODULE, "docbase_request")
    def test_delete_comment_has_no_owner_check(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {}
        MODULE.delete_comment("d", "t", 9)
        mock_request.assert_called_once_with("d", "t", "DELETE", "/comments/9")


class GroupsAndLookupsTest(unittest.TestCase):
    @mock.patch.object(MODULE, "docbase_request")
    def test_search_groups_request_shape(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = []
        MODULE.search_groups("d", "t", name="開発部")
        mock_request.assert_called_once_with(
            "d", "t", "GET", "/groups", params={"name": "開発部", "page": 1, "per_page": 100}
        )

    @mock.patch.object(MODULE, "docbase_request")
    def test_create_group_omits_description_when_absent(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {"id": 1}
        MODULE.create_group("d", "t", "新グループ")
        mock_request.assert_called_once_with("d", "t", "POST", "/groups", body={"name": "新グループ"})

    @mock.patch.object(MODULE, "docbase_request")
    def test_add_users_to_group_request_shape(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {}
        MODULE.add_users_to_group("d", "t", 5, [1, 2, 3])
        mock_request.assert_called_once_with("d", "t", "POST", "/groups/5/users", body={"user_ids": [1, 2, 3]})

    @mock.patch.object(MODULE, "docbase_request")
    def test_remove_users_from_group_request_shape(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = {}
        MODULE.remove_users_from_group("d", "t", 5, [1])
        mock_request.assert_called_once_with("d", "t", "DELETE", "/groups/5/users", body={"user_ids": [1]})

    @mock.patch.object(MODULE, "docbase_request")
    def test_get_tags_and_get_team(self, mock_request: mock.Mock) -> None:
        mock_request.return_value = []
        MODULE.get_tags("d", "t")
        mock_request.assert_called_with("d", "t", "GET", "/tags")
        MODULE.get_team("d", "t")
        mock_request.assert_called_with("d", "t", "GET", "/team")


class DownloadAttachmentTest(unittest.TestCase):
    def test_writes_response_body_to_output_path(self) -> None:
        import io
        import tempfile

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with (
            mock.patch.object(MODULE.urllib.request, "urlopen", return_value=FakeResponse(b"binary-data")),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            out_path = f"{tmpdir}/out.bin"
            result = MODULE.download_attachment("d", "t", "abc123", out_path)
            with open(out_path, "rb") as f:
                self.assertEqual(f.read(), b"binary-data")
            self.assertEqual(result, {"attachment_id": "abc123", "output_path": out_path, "size": len(b"binary-data")})


class UploadAttachmentTest(unittest.TestCase):
    @mock.patch.object(MODULE, "docbase_request")
    def test_encodes_mp4_bytes_and_uses_basename(self, mock_request: mock.Mock) -> None:
        import tempfile

        mock_request.return_value = [{"name": "clip.mp4"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "clip.mp4"
            file_path.write_bytes(b"mock-mp4-bytes")

            result = MODULE.upload_attachments("d", "t", [str(file_path)])

        mock_request.assert_called_once_with(
            "d",
            "t",
            "POST",
            "/attachments",
            body=[
                {
                    "name": "clip.mp4",
                    "content": MODULE.base64.b64encode(b"mock-mp4-bytes").decode("ascii"),
                }
            ],
        )
        self.assertEqual(result, [{"name": "clip.mp4"}])

    @mock.patch.object(MODULE, "docbase_request")
    def test_rejects_missing_file_without_api_call(self, mock_request: mock.Mock) -> None:
        with self.assertRaises(MODULE.DocBaseError):
            MODULE.upload_attachments("d", "t", ["missing.mp4"])
        mock_request.assert_not_called()


class DispatchTest(unittest.TestCase):
    @mock.patch.object(MODULE, "search_posts")
    def test_search_posts_dispatch(self, mock_search: mock.Mock) -> None:
        mock_search.return_value = {"posts": []}
        args = MODULE.build_parser().parse_args(["search-posts", "--query", "q", "--page", "2"])
        result = MODULE.dispatch(args, "d", "t")
        mock_search.assert_called_once_with("d", "t", "q", page=2, per_page=20)
        self.assertEqual(result, {"posts": []})

    @mock.patch.object(MODULE, "create_post")
    def test_create_post_dispatch_passes_tags_and_draft(self, mock_create: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(
            ["create-post", "--title", "t", "--body", "b", "--tag", "a", "--tag", "b", "--draft"]
        )
        MODULE.dispatch(args, "d", "tok")
        mock_create.assert_called_once_with(
            "d", "tok", "t", "b", tags=["a", "b"], draft=True, scope=None, allow_public=False
        )

    @mock.patch.object(MODULE, "create_post")
    def test_create_post_dispatch_passes_allow_public(self, mock_create: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(
            ["create-post", "--title", "t", "--body", "b", "--allow-public"]
        )
        MODULE.dispatch(args, "d", "tok")
        _, kwargs = mock_create.call_args
        self.assertEqual(kwargs["allow_public"], True)

    @mock.patch.object(MODULE, "update_post")
    def test_update_post_dispatch_publish_sets_draft_false(self, mock_update: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(["update-post", "--post-id", "5", "--publish"])
        MODULE.dispatch(args, "d", "tok")
        _, kwargs = mock_update.call_args
        self.assertEqual(kwargs["draft"], False)
        self.assertEqual(kwargs["force"], False)

    @mock.patch.object(MODULE, "update_post")
    def test_update_post_dispatch_passes_force(self, mock_update: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(["update-post", "--post-id", "5", "--force"])
        MODULE.dispatch(args, "d", "tok")
        _, kwargs = mock_update.call_args
        self.assertEqual(kwargs["force"], True)

    @mock.patch.object(MODULE, "get_profile")
    def test_profile_dispatch(self, mock_profile: mock.Mock) -> None:
        mock_profile.return_value = {"name": "x"}
        args = MODULE.build_parser().parse_args(["profile"])
        result = MODULE.dispatch(args, "d", "t")
        mock_profile.assert_called_once_with("d", "t")
        self.assertEqual(result, {"name": "x"})

    @mock.patch.object(MODULE, "delete_post")
    def test_delete_post_dispatch_passes_force(self, mock_delete: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(["delete-post", "--post-id", "5", "--force"])
        MODULE.dispatch(args, "d", "tok")
        mock_delete.assert_called_once_with("d", "tok", 5, force=True)

    @mock.patch.object(MODULE, "archive_post")
    def test_archive_post_dispatch(self, mock_archive: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(["archive-post", "--post-id", "5"])
        MODULE.dispatch(args, "d", "tok")
        mock_archive.assert_called_once_with("d", "tok", 5, force=False)

    @mock.patch.object(MODULE, "unarchive_post")
    def test_unarchive_post_dispatch(self, mock_unarchive: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(["unarchive-post", "--post-id", "5"])
        MODULE.dispatch(args, "d", "tok")
        mock_unarchive.assert_called_once_with("d", "tok", 5, force=False)

    @mock.patch.object(MODULE, "patch_post_body")
    def test_patch_post_body_dispatch(self, mock_patch: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(
            [
                "patch-post-body",
                "--post-id", "5",
                "--start", "1",
                "--end", "2",
                "--old-content", "old",
                "--content", "new",
                "--no-notice",
                "--include-body",
            ]
        )
        MODULE.dispatch(args, "d", "tok")
        mock_patch.assert_called_once_with(
            "d", "tok", 5, 1, 2, "old", "new", notice=False, include_body=True, force=False
        )

    @mock.patch.object(MODULE, "get_comments")
    def test_get_comments_dispatch(self, mock_get: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(["get-comments", "--post-id", "5"])
        MODULE.dispatch(args, "d", "tok")
        mock_get.assert_called_once_with(
            "d", "tok", 5, page=1, per_page=20, order=None, created_after=None, created_before=None
        )

    @mock.patch.object(MODULE, "create_comment")
    def test_create_comment_dispatch(self, mock_create: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(["create-comment", "--post-id", "5", "--body", "hi"])
        MODULE.dispatch(args, "d", "tok")
        mock_create.assert_called_once_with("d", "tok", 5, "hi", notice=True)

    @mock.patch.object(MODULE, "delete_comment")
    def test_delete_comment_dispatch(self, mock_delete: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(["delete-comment", "--comment-id", "9"])
        MODULE.dispatch(args, "d", "tok")
        mock_delete.assert_called_once_with("d", "tok", 9)

    @mock.patch.object(MODULE, "search_groups")
    def test_search_groups_dispatch(self, mock_search: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(["search-groups", "--name", "開発部"])
        MODULE.dispatch(args, "d", "tok")
        mock_search.assert_called_once_with("d", "tok", name="開発部", page=1, per_page=100)

    @mock.patch.object(MODULE, "add_users_to_group")
    def test_add_users_to_group_dispatch_collects_repeated_flags(self, mock_add: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(
            ["add-users-to-group", "--group-id", "5", "--user-id", "1", "--user-id", "2"]
        )
        MODULE.dispatch(args, "d", "tok")
        mock_add.assert_called_once_with("d", "tok", 5, [1, 2])

    @mock.patch.object(MODULE, "download_attachment")
    def test_download_attachment_dispatch(self, mock_download: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(
            ["download-attachment", "--attachment-id", "abc", "--output", "/tmp/out.bin"]
        )
        MODULE.dispatch(args, "d", "tok")
        mock_download.assert_called_once_with("d", "tok", "abc", "/tmp/out.bin")

    @mock.patch.object(MODULE, "upload_attachments")
    def test_upload_attachment_dispatch_collects_repeated_files(self, mock_upload: mock.Mock) -> None:
        args = MODULE.build_parser().parse_args(
            ["upload-attachment", "--file", "clip.mp4", "--file", "clip.mov"]
        )
        MODULE.dispatch(args, "d", "tok")
        mock_upload.assert_called_once_with("d", "tok", ["clip.mp4", "clip.mov"])


if __name__ == "__main__":
    unittest.main()
