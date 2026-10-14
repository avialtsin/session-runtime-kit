import subprocess
import tempfile
import unittest
from pathlib import Path

from context_pipeline import (
    architecture_is_fresh,
    checkpoint_freshness,
    discover_repo_files,
    filtered_status,
    normalize_repo_file,
    read_git_state,
    resolve_repo_path,
    sha256,
)
from run_pipeline import (
    exclusive_repowise_refresh,
    hide_repowise_excluded_roots,
    protect_editor_configuration,
    repowise_exclude_patterns,
    repowise_excluded_roots,
)


def command(repo: Path, *argv: str) -> str:
    return subprocess.run(
        list(argv),
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        command(self.repo, "git", "init", "-q")
        command(self.repo, "git", "config", "user.email", "pipeline@example.test")
        command(self.repo, "git", "config", "user.name", "Pipeline Test")
        (self.repo / "src.ts").write_text("export const value = 1;\n")
        command(self.repo, "git", "add", "src.ts")
        command(self.repo, "git", "commit", "-qm", "initial code")
        self.code_head = command(self.repo, "git", "rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_architecture(self) -> None:
        (self.repo / "ARCHITECTURE.md").write_text(
            f"# Architecture\n\nCurrent code `{self.code_head[:8]}`.\n"
        )
        command(self.repo, "git", "add", "ARCHITECTURE.md")
        command(self.repo, "git", "commit", "-qm", "document architecture")

    def test_docs_only_commit_does_not_stale_code_snapshot(self) -> None:
        self.write_architecture()
        state = read_git_state(self.repo)
        self.assertTrue(state.architecture_ref_valid)
        self.assertEqual(state.architecture_relevant_changed, ())
        self.assertTrue(architecture_is_fresh(state))

    def test_source_change_stales_architecture_and_checkpoint(self) -> None:
        self.write_architecture()
        checkpoint = self.repo / "checkpoint.md"
        checkpoint.write_text(f"- `src.ts` — `{sha256(self.repo, 'src.ts')}`\n")
        (self.repo / "src.ts").write_text("export const value = 2;\n")
        command(self.repo, "git", "add", "src.ts")
        command(self.repo, "git", "commit", "-qm", "change code")
        state = read_git_state(self.repo)
        self.assertEqual(state.architecture_relevant_changed, ("src.ts",))
        self.assertFalse(architecture_is_fresh(state))
        freshness = checkpoint_freshness(self.repo, [checkpoint])
        self.assertIn("STALE", freshness)
        self.assertIn("src.ts", freshness)

    def test_status_filters_generated_tool_state(self) -> None:
        status = " M src.ts\n?? .repowise/\n?? .mcp.json\n?? notes.txt"
        self.assertEqual(filtered_status(status), " M src.ts\n?? notes.txt")

    def test_discovers_generic_repo_paths_from_structured_search(self) -> None:
        nested = self.repo / "lib" / "domain" / "record.py"
        nested.parent.mkdir(parents=True)
        nested.write_text("record = True\n")
        search = (
            '{"results": [{"file_path": "lib/domain/record.py"}, '
            '{"target_path": "src.ts"}]}'
        )
        self.assertEqual(
            discover_repo_files(self.repo, search),
            ["lib/domain/record.py", "src.ts"],
        )

    def test_focus_paths_are_relative_and_cannot_escape_repo(self) -> None:
        absolute = self.repo / "src.ts"
        self.assertEqual(normalize_repo_file(self.repo, absolute), "src.ts")
        self.assertIsNone(normalize_repo_file(self.repo, "/etc/hosts"))

    def test_relative_artifact_paths_are_resolved_from_repo(self) -> None:
        self.assertEqual(
            resolve_repo_path(self.repo, Path(".context/brief.md")),
            (self.repo / ".context" / "brief.md").resolve(),
        )

    def test_editor_configuration_is_restored_after_refresh(self) -> None:
        mcp = self.repo / ".mcp.json"
        mcp.write_bytes(b'{"owned_by":"project"}\n')
        with protect_editor_configuration(self.repo):
            mcp.write_bytes(b'{"owned_by":"repowise"}\n')
            generated = self.repo / ".vscode" / "mcp.json"
            generated.parent.mkdir()
            generated.write_text("{}\n")
        self.assertEqual(mcp.read_bytes(), b'{"owned_by":"project"}\n')
        self.assertFalse((self.repo / ".vscode").exists())

    def test_repowise_ignore_rules_are_available_to_init(self) -> None:
        (self.repo / ".repowiseIgnore").write_text(
            "# Keep tracked references out of default context.\n\nvendors/\n"
        )
        self.assertEqual(repowise_exclude_patterns(self.repo), ["vendors/"])

    def test_top_level_ignored_directory_is_hidden_and_restored(self) -> None:
        vendors = self.repo / "vendors"
        vendors.mkdir()
        (vendors / "package.json").write_text("{}\n")
        (self.repo / ".repowiseIgnore").write_text("vendors/\n")
        self.assertEqual(repowise_excluded_roots(self.repo), [vendors])

        hidden_root = self.repo.parent / f".{self.repo.name}.repowise-hidden"
        with self.assertRaisesRegex(RuntimeError, "simulated refresh failure"):
            with hide_repowise_excluded_roots(self.repo):
                self.assertFalse(vendors.exists())
                self.assertTrue((hidden_root / "vendors" / "package.json").is_file())
                raise RuntimeError("simulated refresh failure")

        self.assertTrue((vendors / "package.json").is_file())
        self.assertFalse(hidden_root.exists())

    def test_repowise_refresh_rejects_a_concurrent_writer(self) -> None:
        with exclusive_repowise_refresh(self.repo):
            with self.assertRaisesRegex(SystemExit, "already running"):
                with exclusive_repowise_refresh(self.repo):
                    self.fail("the second writer must not acquire the lock")


if __name__ == "__main__":
    unittest.main()
