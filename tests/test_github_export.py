import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from src.integrations.github_export import export, write_batch

SHA = "a" * 40


class FakeAPI:
    def __init__(
        self,
        *,
        cross_repo: bool = False,
        has_next: bool = False,
        merged: bool = True,
        linked: bool = True,
    ) -> None:
        self.cross_repo, self.has_next = cross_repo, has_next
        self.merged, self.linked = merged, linked

    def closing_links(self, repo: str, pull_number: int) -> object:
        issue = {
            "number": 25,
            "title": "Correct metric",
            "body": None,
            "state": "CLOSED",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-03T00:00:00Z",
            "url": "https://github.com/owner/repo/issues/25",
            "repository": {"nameWithOwner": "other/repo" if self.cross_repo else repo},
        }
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "closingIssuesReferences": {
                            "pageInfo": {"hasNextPage": self.has_next},
                            "nodes": [issue] if self.linked else [],
                        }
                    }
                }
            }
        }

    def rest(self, path: str, *, paginated: bool = False) -> object:
        if path.endswith("/pulls/26"):
            return {
                "number": 26,
                "title": "Fix metric",
                "head": {"ref": "fix"},
                "merged_at": "2026-01-03T00:00:00Z" if self.merged else None,
                "html_url": "https://github.com/owner/repo/pull/26",
            }
        if path.endswith("/pulls/26/commits"):
            return [[{"sha": SHA}]]
        if path.endswith(f"/commits/{SHA}"):
            common = {
                "sha": SHA,
                "commit": {
                    "author": {"date": "2026-01-01T00:00:00Z"},
                    "committer": {"date": "2026-01-02T00:00:00Z"},
                },
            }
            return [
                {
                    **common,
                    "files": [
                        {"filename": "new.py", "previous_filename": "old.py", "status": "renamed"}
                    ],
                },
                {**common, "files": [{"filename": "removed.py", "status": "removed"}]},
            ]
        raise AssertionError(f"Unexpected API path: {path}")


class ExportTests(unittest.TestCase):
    def test_preserves_schema_and_paginated_commit_files(self) -> None:
        result = export("owner/repo", [26, 26], FakeAPI())
        issue = result["issues"][0]
        self.assertEqual(issue["issue_data"]["issue_metadata"]["iid"], 25)
        self.assertEqual(issue["issue_data"]["issue_metadata"]["description"], "")
        self.assertEqual(len(issue["related_mrs"]), 1)
        mr = issue["related_mrs"][0]
        self.assertEqual(mr["mr_metadata"]["merge_status"], "merged")
        commit = mr["commits"][0]
        self.assertEqual(commit["commit_metadata"]["authored_date"], "2026-01-01T00:00:00Z")
        files = commit["commit_diff_summary"]["files_summary"]
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0]["old_path"], "old.py")
        self.assertEqual(files[1]["new_path"], "removed.py")
        self.assertNotIn("label", issue)

    def test_cross_repository_iids_are_not_conflated(self) -> None:
        result = export("owner/repo", [26], FakeAPI(cross_repo=True))
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["metadata"]["skipped_cross_repository_link_count"], 1)
        self.assertNotIn("other/repo", json.dumps(result))

    def test_does_not_infer_links_from_titles_or_bodies(self) -> None:
        result = export("owner/repo", [26], FakeAPI(linked=False))
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["metadata"]["unlinked_pulls"], [26])

    def test_rejects_truncated_closing_links(self) -> None:
        with self.assertRaisesRegex(ValueError, "truncated"):
            export("owner/repo", [26], FakeAPI(has_next=True))

    def test_rejects_unmerged_pr(self) -> None:
        with self.assertRaisesRegex(ValueError, "merged"):
            export("owner/repo", [26], FakeAPI(merged=False))

    def test_validates_repo_before_api_use(self) -> None:
        with self.assertRaisesRegex(ValueError, "owner/name"):
            export("../invalid", [26], FakeAPI())


class BatchWriteTests(unittest.TestCase):
    def test_writes_a_complete_batch_with_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "input" / "issues.json"
            write_batch(output, {"issues": ["Example"]})
            batch = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(batch, {"issues": ["Example"]})
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(output.stat().st_mode) & 0o077, 0)

    def test_preserves_an_existing_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "issues.json"
            output.write_text("original", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_batch(output, {"issues": []})
            self.assertEqual(output.read_text(encoding="utf-8"), "original")
            self.assertEqual(list(output.parent.iterdir()), [output])

    def test_failed_serialization_leaves_no_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "issues.json"
            with self.assertRaises(TypeError):
                write_batch(output, {"issues": [object()]})
            self.assertEqual(list(output.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
