"""Atomic file writes shared by the state store and the site."""

import os
import pathlib
import tempfile


def atomic_write(path: pathlib.Path, text: str, mode: int) -> None:
    """Replaces `path` with `text` (UTF-8) so readers see the old file or the new one, never
    a partial one.

    The text goes to a temp file in the same directory, is synced to disk, gets `mode` and is
    renamed over `path`. Missing parent directories are created.

    Args:
        path: the file to write.
        text: its new content.
        mode: its permission bits, such as 0o600 or 0o644.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        pathlib.Path(temp).unlink(missing_ok=True)
        raise
