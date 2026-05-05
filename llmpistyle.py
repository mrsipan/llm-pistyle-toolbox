import difflib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

import llm


class PiMonoStyleToolbox(llm.Toolbox):
    """
    A toolbox that mimics Pi‑mono’s core tools (read, write, edit, bash),
    but with two script runners: one for xonsh and one for sh/bash.
    Also provides advanced diff‑based editing with SEARCH/REPLACE blocks.
    """
    def __init__(self, workspace_dir: Optional[str] = None):
        super().__init__()
        self.workspace = Path(workspace_dir
                             ) if workspace_dir else Path.cwd()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._last_command_output = ""

    # -------------------------------------------------------------------------
    # 1) Read a file (optionally with line range)
    # -------------------------------------------------------------------------
    def read(
        self,
        file_path: str,
        start_line: int = 1,
        end_line: Optional[int] = None
        ) -> str:
        """
        Read a file (or a portion thereof) from the agent's workspace.
        Returns the file content as a string.
        """
        full_path = self._safe_path(file_path)
        if not full_path.is_file():
            return f"Error: {full_path} does not exist or is not a file."

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            return f"Error reading file: {e}"

        start = max(0, start_line - 1)
        end = end_line if end_line is not None else len(lines)
        return "".join(lines[start:end]).rstrip("\n")

    # -------------------------------------------------------------------------
    # 2) Write (create or overwrite a file)
    # -------------------------------------------------------------------------
    def write(self, file_path: str, content: str) -> str:
        """
        Create or completely overwrite a file in the workspace.
        Any missing parent directories are created automatically.
        """
        full_path = self._safe_path(file_path)
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote {full_path}"
        except Exception as e:
            return f"Error writing file: {e}"

    # -------------------------------------------------------------------------
    # 3) Simple search/replace edit (first occurrence only)
    # -------------------------------------------------------------------------
    def edit(self, file_path: str, search: str, replace: str) -> str:
        """
        Perform a single search‑and‑replace edit on a file.
        Only the first occurrence of `search` is replaced.
        Returns a status message.
        """
        full_path = self._safe_path(file_path)
        if not full_path.is_file():
            return f"Error: {full_path} does not exist."

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                original = f.read()
        except Exception as e:
            return f"Error reading file for edit: {e}"

        if search not in original:
            return f"Edit failed: search pattern not found in {file_path}."

        new_content = original.replace(search, replace, 1)

        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            return f"Successfully edited {full_path} (replaced first occurrence)."
        except Exception as e:
            return f"Error writing edited file: {e}"

    # -------------------------------------------------------------------------
    # 4) Advanced diff‑based editing (unified diff or SEARCH/REPLACE blocks)
    # -------------------------------------------------------------------------
    def edit_diff(self, file_path: str, diff: str) -> str:
        """
        Apply changes to a file using either:
          - A unified diff (output of `diff -u original modified`), or
          - A SEARCH/REPLACE block (multiple blocks separated by lines:
            <<<<<<< SEARCH
            ... lines to replace ...
            =======
            ... new lines ...
            >>>>>>> REPLACE )

        Returns a detailed success or error message.
        """
        full_path = self._safe_path(file_path)
        if not full_path.is_file():
            return f"Error: {full_path} does not exist."

        if self._looks_like_search_replace(diff):
            unified = self._convert_search_replace_to_unified(
                full_path, diff
                )
            if unified is None:
                return "Error: Could not parse SEARCH/REPLACE blocks. Ensure each block has <<<<<<< SEARCH, =======, and >>>>>>> REPLACE markers."
            diff = unified

        try:
            original_lines = full_path.read_text(
                encoding="utf-8"
                ).splitlines(keepends=True)
            patched_lines = self._apply_unified_diff(
                original_lines, diff
                )
            if patched_lines is None:
                return "Error: Failed to apply unified diff. Hunk(s) did not match the current file content."

            full_path.write_text(
                "".join(patched_lines), encoding="utf-8"
                )
            return f"Successfully applied diff to {full_path}"
        except Exception as e:
            return f"Error applying diff: {e}"

    # -------------------------------------------------------------------------
    # 5) Run a xonsh script
    # -------------------------------------------------------------------------
    def xonsh_run(
        self, script: str, filename: Optional[str] = None
        ) -> str:
        """
        Write the given xonsh code to a temporary .xsh file, execute it,
        and return its stdout + stderr.
        If `filename` is provided, the script is saved permanently in the workspace.
        """
        return self._run_script(
            script, filename, interpreter="xonsh", suffix=".xsh"
            )

    # -------------------------------------------------------------------------
    # 6) Run a sh/bash script
    # -------------------------------------------------------------------------
    def sh_run(
        self, script: str, filename: Optional[str] = None
        ) -> str:
        """
        Write the given shell script (sh/bash) to a temporary .sh file, execute it,
        and return its stdout + stderr.
        If `filename` is provided, the script is saved permanently in the workspace.
        """
        return self._run_script(
            script, filename, interpreter="sh", suffix=".sh"
            )

    # -------------------------------------------------------------------------
    # Internal helpers for diff handling
    # -------------------------------------------------------------------------
    def _looks_like_search_replace(self, text: str) -> bool:
        """Heuristic: contains <<<<<<< SEARCH and ======= and >>>>>>> REPLACE"""
        return (
            "<<<<<<< SEARCH" in text and "=======" in text and
            ">>>>>>> REPLACE" in text
            )

    def _convert_search_replace_to_unified(
        self, file_path: Path, block_text: str
        ) -> Optional[str]:
        """
        Parse SEARCH/REPLACE blocks and produce a unified diff.
        Returns None on parse error.
        """
        blocks = re.split(r'\n?<<<<<<< SEARCH\n', block_text)
        if len(blocks) < 2:
            return None

        unified_diff_lines = []
        for block in blocks[1:]:
            if '=======' not in block or '>>>>>>> REPLACE' not in block:
                return None
            search_part, replace_part = block.split('=======', 1)
            replace_part = replace_part.split('>>>>>>> REPLACE', 1)[0]

            search_lines = search_part.strip('\n').splitlines()
            replace_lines = replace_part.strip('\n').splitlines()

            current = file_path.read_text(encoding="utf-8").splitlines()
            # Locate the search block
            match = None
            for i in range(len(current) - len(search_lines) + 1):
                if current[i:i + len(search_lines)] == search_lines:
                    match = (i, len(search_lines))
                    break
            if match is None:
                return None

            start_line, length = match
            unified_diff_lines.append(
                f"@@ -{start_line+1},{length} +{start_line+1},{len(replace_lines)} @@"
                )
            for line in search_lines:
                unified_diff_lines.append(f"-{line}")
            for line in replace_lines:
                unified_diff_lines.append(f"+{line}")

        return "\n".join(unified_diff_lines)

    def _apply_unified_diff(
        self, original_lines: List[str], diff_text: str
        ) -> Optional[List[str]]:
        """Apply unified diff to original_lines. Returns patched lines or None."""
        diff_lines = diff_text.splitlines()
        hunks = []
        current_hunk = None
        for line in diff_lines:
            if line.startswith('@@'):
                match = re.match(
                    r'^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@', line
                    )
                if match:
                    old_start = int(match.group(1))
                    old_count = int(match.group(2)
                                   ) if match.group(2) else 1
                    new_start = int(match.group(3))
                    new_count = int(match.group(4)
                                   ) if match.group(4) else 1
                    current_hunk = {
                        'old_start': old_start,
                        'old_count': old_count,
                        'new_start': new_start,
                        'new_count': new_count,
                        'lines': []
                        }
                    hunks.append(current_hunk)
            elif current_hunk is not None:
                if line.startswith('-'):
                    current_hunk['lines'].append(('-', line[1:]))
                elif line.startswith('+'):
                    current_hunk['lines'].append(('+', line[1:]))
                elif line.startswith(' '):
                    current_hunk['lines'].append((' ', line[1:]))

        if not hunks:
            return None

        result = original_lines[:]
        for hunk in reversed(hunks):
            old_start = hunk['old_start'] - 1
            ok = True
            idx = old_start
            for op, text in hunk['lines']:
                if op == ' ' or op == '-':
                    if idx >= len(result
                                 ) or result[idx].rstrip('\n') != text:
                        ok = False
                        break
                    idx += 1
            if not ok:
                return None

            new_segment = []
            idx = old_start
            for op, text in hunk['lines']:
                if op == ' ' or op == '-':
                    idx += 1
                if op == '+':
                    new_segment.append(text + '\n')
            result = result[:old_start] + new_segment + result[
                old_start + hunk['old_count']:]

        return result

    # -------------------------------------------------------------------------
    # Common script runner (internal)
    # -------------------------------------------------------------------------
    def _run_script(
        self, script: str, filename: Optional[str], interpreter: str,
        suffix: str
        ) -> str:
        """Generic script runner for any interpreter."""
        if filename:
            script_path = self._safe_path(filename)
            script_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            fd, tmp_name = tempfile.mkstemp(suffix=suffix, text=True)
            os.close(fd)
            script_path = Path(tmp_name)

        try:
            script_path.write_text(script, encoding="utf-8")
            script_path.chmod(0o755)

            result = subprocess.run(
                [interpreter, str(script_path)],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                check=False,
                )

            output = result.stdout
            if result.stderr:
                output += f"\n[STDERR]\n{result.stderr}"
            if result.returncode != 0:
                output += f"\n[EXIT CODE] {result.returncode}"

            self._last_command_output = output
            return output.strip()

        except FileNotFoundError:
            return f"Error: '{interpreter}' executable not found. Please install it and ensure it is on your PATH."
        except Exception as e:
            return f"Error during script execution: {e}"
        finally:
            if not filename and script_path.exists():
                script_path.unlink()

    # -------------------------------------------------------------------------
    # Helper: resolve path safely
    # -------------------------------------------------------------------------
    def _safe_path(self, user_path: str) -> Path:
        expanded = os.path.expanduser(user_path)
        candidate = (self.workspace / expanded).resolve()
        if not str(candidate).startswith(str(self.workspace.resolve())):
            raise PermissionError(
                f"Path {user_path} would escape workspace {self.workspace}"
                )
        return candidate
