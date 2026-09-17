"""Native junction coverage complements the real symlink fixtures."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pvs.errors import ArtifactError
from pvs.paths import confined_path, path_is_link_or_reparse, walk_directory_entries


@pytest.mark.windows_filesystem
@pytest.mark.skipif(sys.platform != "win32", reason="native Windows junction capability")
@pytest.mark.parametrize("contained", [False, True])
def test_actual_junction_is_rejected_and_not_traversed(tmp_path: Path, contained: bool) -> None:
    root = tmp_path / "package"
    root.mkdir()
    target = (root if contained else tmp_path) / "target"
    target.mkdir()
    (target / "secret.txt").write_bytes(b"target bytes must not be traversed via the junction")
    link = root / "junction"
    result = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    try:
        assert path_is_link_or_reparse(link)
        with pytest.raises(ArtifactError, match="reparse points"):
            confined_path(root, "junction/secret.txt", reject_symlinks=True)
        names = {path.relative_to(root).as_posix() for path, _ in walk_directory_entries(root)}
        assert "junction" in names
        assert "junction/secret.txt" not in names
        if not contained:
            with pytest.raises(ArtifactError, match="escapes"):
                confined_path(root, "junction/secret.txt")
        assert (target / "secret.txt").is_file()
    finally:
        # Remove only the junction; its target must survive cleanup.
        link.rmdir()
    assert (target / "secret.txt").is_file()
