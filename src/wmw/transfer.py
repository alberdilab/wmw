"""SFTP transfer helpers for wmw (ERDA uploads via paramiko).

Ported from the ehio transfer layer so both tools reach ERDA the same way:
an ``SFTPTransfer`` context manager over paramiko, with remote directories
created on demand and every transfer staged through a ``.part`` name so an
interrupted upload never leaves behind a file that looks complete.
"""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from wmw import output as out

try:
    import paramiko
    _PARAMIKO_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without paramiko
    _PARAMIKO_AVAILABLE = False


# Assemblies and bins run to several GB, so they are compressed into the SFTP
# connection rather than to a temporary .gz on the local disk. The gzip layer is
# built explicitly because Python 3.11 does not accept a compresslevel on a
# stream, which is what the cluster environment runs.
GZIP_COMPRESS_LEVEL = 6


def paramiko_available() -> bool:
    """Return True if paramiko can be imported."""
    return _PARAMIKO_AVAILABLE


def _require_paramiko() -> None:
    if not _PARAMIKO_AVAILABLE:
        raise RuntimeError("paramiko is required for ERDA transfers. Run: pip install paramiko")


def gzip_into(source: Path, handle: Any) -> None:
    """Gzip `source` straight into an open remote file handle."""
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=handle, compresslevel=GZIP_COMPRESS_LEVEL
    ) as gz, Path(source).open("rb") as fin:
        shutil.copyfileobj(fin, gz)


def _copy_into(source: Path, handle: Any) -> None:
    """Copy `source` verbatim into an open remote file handle."""
    with Path(source).open("rb") as fin:
        shutil.copyfileobj(fin, handle)


class SFTPTransfer:
    """Per-file SFTP transfers to a remote host (ERDA)."""

    def __init__(
        self,
        host: str,
        username: str,
        port: int = 22,
        key_path: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        _require_paramiko()
        self._host = host
        self._username = username
        self._port = port
        self._key_path = key_path or None
        self._timeout = timeout
        self._client: Any = None
        self._sftp: Any = None

    def connect(self) -> None:
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict[str, Any] = {
            "hostname": self._host,
            "username": self._username,
            "port": self._port,
            "timeout": self._timeout,
            "banner_timeout": self._timeout,
            "auth_timeout": self._timeout,
        }
        if self._key_path:
            kwargs["key_filename"] = self._key_path
        self._client.connect(**kwargs)
        self._sftp = self._client.open_sftp()

    def disconnect(self) -> None:
        if self._sftp:
            self._sftp.close()
        if self._client:
            self._client.close()

    def ensure_remote_dir(self, remote_dir: str) -> None:
        """Create remote_dir and every missing parent."""
        current = ""
        for part in PurePosixPath(remote_dir).parts:
            current = str(PurePosixPath(current) / part)
            try:
                self._sftp.stat(current)
            except FileNotFoundError:
                self._sftp.mkdir(current)

    def remote_exists(self, remote_path: str) -> bool:
        """Return True if remote_path exists on the remote host."""
        try:
            self._sftp.stat(remote_path)
            return True
        except FileNotFoundError:
            return False

    def upload_stream(
        self,
        remote_path: str,
        writer: Callable[[Any], None],
        verbose: bool = False,
    ) -> None:
        """Create a remote file and let `writer` write its content into it.

        Used for content generated on the fly (a gzip stream of a multi-GB
        assembly, say), so it never needs a temporary copy on the local disk.
        The data goes to a '.part' name and is renamed only once the writer
        returns.
        """
        self.ensure_remote_dir(str(PurePosixPath(remote_path).parent))
        part_path = f"{remote_path}.part"
        if verbose:
            out.info(f"  PUT <stream> → {remote_path}")
        try:
            handle = self._sftp.open(part_path, "wb")
            handle.set_pipelined(True)
            with handle:
                writer(handle)
        except BaseException:
            try:
                self._sftp.remove(part_path)
            except OSError:
                pass
            raise
        try:
            self._sftp.remove(remote_path)
        except OSError:
            pass
        self._sftp.rename(part_path, remote_path)

    def upload_file(
        self,
        source: Path,
        remote_path: str,
        verbose: bool = False,
        skip_existing: bool = True,
    ) -> bool:
        """Copy `source` to remote_path unchanged. Returns True if it was uploaded.

        For content that is already compressed — the .tsv.xz result tables of a
        drakkar amr run — where a second gzip layer would only cost time.
        """
        if skip_existing and self.remote_exists(remote_path):
            if verbose:
                out.info(f"  SKIP {source.name} (already on ERDA)")
            return False
        self.upload_stream(
            remote_path,
            lambda handle: _copy_into(source, handle),
            verbose=verbose,
        )
        return True

    def upload_gzipped(
        self,
        source: Path,
        remote_path: str,
        verbose: bool = False,
        skip_existing: bool = True,
    ) -> bool:
        """Compress `source` into remote_path. Returns True if it was uploaded."""
        if skip_existing and self.remote_exists(remote_path):
            if verbose:
                out.info(f"  SKIP {source.name} (already on ERDA)")
            return False
        self.upload_stream(
            remote_path,
            lambda handle: gzip_into(source, handle),
            verbose=verbose,
        )
        return True

    def remove_remote_dir(self, remote_dir: str) -> None:
        """Recursively remove a remote directory; silent if it does not exist."""
        import stat as _stat

        try:
            entries = self._sftp.listdir_attr(remote_dir)
        except FileNotFoundError:
            return
        for entry in entries:
            child = f"{remote_dir}/{entry.filename}"
            if _stat.S_ISDIR(entry.st_mode):
                self.remove_remote_dir(child)
            else:
                self._sftp.remove(child)
        self._sftp.rmdir(remote_dir)

    def __enter__(self) -> SFTPTransfer:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.disconnect()
