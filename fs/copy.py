"""Functions for copying resources *between* filesystem.
"""

from __future__ import print_function, unicode_literals

import typing

import warnings
import uuid

from .errors import (
    DestinationCopyError,
    DirectoryExists,
    IllegalDestination,
    ResourceNotFound,
    SourceCopyError,
)
from .opener import manage_fs
from .path import abspath, combine, dirname, frombase, isbase, normpath
from .tools import is_thread_safe
from .walk import Walker

if typing.TYPE_CHECKING:
    from typing import Callable, List, Optional, Text, Tuple, Union

    from .base import FS

    _OnCopy = Callable[[FS, Text, FS, Text], object]


_TRANSACTION_LOCK_PATH = "/.fstransaction.lock"
_TRANSACTION_STAGING_PREFIX = ".fstransaction-"
_CHUNK_SIZE = 64 * 1024


def copy_fs(
    src_fs,  # type: Union[FS, Text]
    dst_fs,  # type: Union[FS, Text]
    walker=None,  # type: Optional[Walker]
    on_copy=None,  # type: Optional[_OnCopy]
    workers=0,  # type: int
    preserve_time=False,  # type: bool
    transactional=False,  # type: bool
):
    # type: (...) -> None
    """Copy the contents of one filesystem to another.

    Arguments:
        src_fs (FS or str): Source filesystem (URL or instance).
        dst_fs (FS or str): Destination filesystem (URL or instance).
        walker (~fs.walk.Walker, optional): A walker object that will be
            used to scan for files in ``src_fs``. Set this if you only want
            to consider a sub-set of the resources in ``src_fs``.
        on_copy (callable): A function callback called after a single file copy
            is executed. Expected signature is ``(src_fs, src_path, dst_fs,
            dst_path)``.
        workers (int): Use `worker` threads to copy data, or ``0`` (default) for
            a single-threaded copy.
        preserve_time (bool): If `True`, try to preserve mtime of the
            resources (defaults to `False`).
        transactional (bool): If `True`, copy atomically: all files are
            written to temporary names first, then renamed in to place,
            and any failure restores the destination to its previous
            state (defaults to `False`).

    """
    if transactional:
        copy_dir_transactional(
            src_fs,
            "/",
            dst_fs,
            "/",
            walker=walker,
            on_copy=on_copy,
            preserve_time=preserve_time,
        )
        return
    return copy_fs_if(
        src_fs, dst_fs, "always", walker, on_copy, workers, preserve_time=preserve_time
    )


def copy_fs_if_newer(
    src_fs,  # type: Union[FS, Text]
    dst_fs,  # type: Union[FS, Text]
    walker=None,  # type: Optional[Walker]
    on_copy=None,  # type: Optional[_OnCopy]
    workers=0,  # type: int
    preserve_time=False,  # type: bool
):
    # type: (...) -> None
    """Copy the contents of one filesystem to another, checking times.

    .. deprecated:: 2.5.0
       Use `~fs.copy.copy_fs_if` with ``condition="newer"`` instead.

    """
    warnings.warn(
        "copy_fs_if_newer is deprecated. Use copy_fs_if instead.", DeprecationWarning
    )
    return copy_fs_if(
        src_fs, dst_fs, "newer", walker, on_copy, workers, preserve_time=preserve_time
    )


def copy_fs_if(
    src_fs,  # type: Union[FS, Text]
    dst_fs,  # type: Union[FS, Text]
    condition="always",  # type: Text
    walker=None,  # type: Optional[Walker]
    on_copy=None,  # type: Optional[_OnCopy]
    workers=0,  # type: int
    preserve_time=False,  # type: bool
):
    # type: (...) -> None
    """Copy the contents of one filesystem to another, depending on a condition.

    Arguments:
        src_fs (FS or str): Source filesystem (URL or instance).
        dst_fs (FS or str): Destination filesystem (URL or instance).
        condition (str): Name of the condition to check for each file.
        walker (~fs.walk.Walker, optional): A walker object that will be
            used to scan for files in ``src_fs``. Set this if you only want
            to consider a sub-set of the resources in ``src_fs``.
        on_copy (callable):A function callback called after a single file copy
            is executed. Expected signature is ``(src_fs, src_path, dst_fs,
            dst_path)``.
        workers (int): Use ``worker`` threads to copy data, or ``0`` (default)
            for a single-threaded copy.
        preserve_time (bool): If `True`, try to preserve mtime of the
            resources (defaults to `False`).

    See Also:
        `~fs.copy.copy_file_if` for the full list of supported values for the
        ``condition`` argument.

    """
    return copy_dir_if(
        src_fs,
        "/",
        dst_fs,
        "/",
        condition,
        walker=walker,
        on_copy=on_copy,
        workers=workers,
        preserve_time=preserve_time,
    )


def copy_file(
    src_fs,  # type: Union[FS, Text]
    src_path,  # type: Text
    dst_fs,  # type: Union[FS, Text]
    dst_path,  # type: Text
    preserve_time=False,  # type: bool
):
    # type: (...) -> None
    """Copy a file from one filesystem to another.

    If the destination exists, and is a file, it will be first truncated.

    Arguments:
        src_fs (FS or str): Source filesystem (instance or URL).
        src_path (str): Path to a file on the source filesystem.
        dst_fs (FS or str): Destination filesystem (instance or URL).
        dst_path (str): Path to a file on the destination filesystem.
        preserve_time (bool): If `True`, try to preserve mtime of the
            resource (defaults to `False`).

    """
    copy_file_if(
        src_fs, src_path, dst_fs, dst_path, "always", preserve_time=preserve_time
    )


def copy_file_if_newer(
    src_fs,  # type: Union[FS, Text]
    src_path,  # type: Text
    dst_fs,  # type: Union[FS, Text]
    dst_path,  # type: Text
    preserve_time=False,  # type: bool
):
    # type: (...) -> bool
    """Copy a file from one filesystem to another, checking times.

    .. deprecated:: 2.5.0
       Use `~fs.copy.copy_file_if` with ``condition="newer"`` instead.

    """
    warnings.warn(
        "copy_file_if_newer is deprecated. Use copy_file_if instead.",
        DeprecationWarning,
    )
    return copy_file_if(
        src_fs, src_path, dst_fs, dst_path, "newer", preserve_time=preserve_time
    )


def copy_file_if(
    src_fs,  # type: Union[FS, Text]
    src_path,  # type: Text
    dst_fs,  # type: Union[FS, Text]
    dst_path,  # type: Text
    condition,  # type: Text
    preserve_time=False,  # type: bool
):
    # type: (...) -> bool
    """Copy a file from one filesystem to another, depending on a condition.

    Depending on the value of ``condition``, certain requirements must
    be fulfilled for a file to be copied to ``dst_fs``. The following
    values are supported:

    ``"always"``
        The source file is always copied.
    ``"newer"``
        The last modification time of the source file must be newer than that
        of the destination file. If either file has no modification time, the
        copy is performed always.
    ``"older"``
        The last modification time of the source file must be older than that
        of the destination file. If either file has no modification time, the
        copy is performed always.
    ``"exists"``
        The source file is only copied if a file of the same path already
        exists in ``dst_fs``.
    ``"not_exists"``
        The source file is only copied if no file of the same path already
        exists in ``dst_fs``.

    Arguments:
        src_fs (FS or str): Source filesystem (instance or URL).
        src_path (str): Path to a file on the source filesystem.
        dst_fs (FS or str): Destination filesystem (instance or URL).
        dst_path (str): Path to a file on the destination filesystem.
        condition (str): Name of the condition to check for each file.
        preserve_time (bool): If `True`, try to preserve mtime of the
            resource (defaults to `False`).

    Returns:
        bool: `True` if the file copy was executed, `False` otherwise.

    """
    with manage_fs(src_fs, writeable=False) as _src_fs:
        with manage_fs(dst_fs, create=True) as _dst_fs:
            do_copy = _copy_is_necessary(
                _src_fs, src_path, _dst_fs, dst_path, condition
            )
            if do_copy:
                copy_file_internal(
                    _src_fs,
                    src_path,
                    _dst_fs,
                    dst_path,
                    preserve_time=preserve_time,
                    lock=True,
                )
            return do_copy


def copy_file_internal(
    src_fs,  # type: FS
    src_path,  # type: Text
    dst_fs,  # type: FS
    dst_path,  # type: Text
    preserve_time=False,  # type: bool
    lock=False,  # type: bool
):
    # type: (...) -> None
    """Copy a file at low level, without calling `manage_fs` or locking.

    If the destination exists, and is a file, it will be first truncated.

    This method exists to optimize copying in loops. In general you
    should prefer `copy_file`.

    Arguments:
        src_fs (FS): Source filesystem.
        src_path (str): Path to a file on the source filesystem.
        dst_fs (FS): Destination filesystem.
        dst_path (str): Path to a file on the destination filesystem.
        preserve_time (bool): If `True`, try to preserve mtime of the
            resource (defaults to `False`).
        lock (bool): Lock both filesystems before copying.

    """
    _src_path = src_fs.validatepath(src_path)
    _dst_path = dst_fs.validatepath(dst_path)
    if src_fs is dst_fs:
        # It's not allowed to copy a file onto itself
        if _src_path == _dst_path:
            raise IllegalDestination(dst_path)
        # Same filesystem, so we can do a potentially optimized copy
        src_fs.copy(src_path, dst_path, overwrite=True, preserve_time=preserve_time)
        return

    def _copy_locked():
        if dst_fs.hassyspath(dst_path):
            with dst_fs.openbin(dst_path, "w") as write_file:
                src_fs.download(src_path, write_file)
        else:
            with src_fs.openbin(src_path) as read_file:
                dst_fs.upload(dst_path, read_file)

        if preserve_time:
            copy_modified_time(src_fs, src_path, dst_fs, dst_path)

    if lock:
        with src_fs.lock(), dst_fs.lock():
            _copy_locked()
    else:
        _copy_locked()


def copy_structure(
    src_fs,  # type: Union[FS, Text]
    dst_fs,  # type: Union[FS, Text]
    walker=None,  # type: Optional[Walker]
    src_root="/",  # type: Text
    dst_root="/",  # type: Text
):
    # type: (...) -> None
    """Copy directories (but not files) from ``src_fs`` to ``dst_fs``.

    Arguments:
        src_fs (FS or str): Source filesystem (instance or URL).
        dst_fs (FS or str): Destination filesystem (instance or URL).
        walker (~fs.walk.Walker, optional): A walker object that will be
            used to scan for files in ``src_fs``. Set this if you only
            want to consider a sub-set of the resources in ``src_fs``.
        src_root (str): Path of the base directory to consider as the root
            of the tree structure to copy.
        dst_root (str): Path to the target root of the tree structure.

    """
    walker = walker or Walker()
    with manage_fs(src_fs) as _src_fs:
        with manage_fs(dst_fs, create=True) as _dst_fs:
            _src_root = _src_fs.validatepath(src_root)
            _dst_root = _dst_fs.validatepath(dst_root)

            # It's not allowed to copy a structure into itself
            if _src_fs == _dst_fs and isbase(_src_root, _dst_root):
                raise IllegalDestination(dst_root)

            with _src_fs.lock(), _dst_fs.lock():
                _dst_fs.makedirs(_dst_root, recreate=True)
                for dir_path in walker.dirs(_src_fs, _src_root):
                    _dst_fs.makedir(
                        combine(_dst_root, frombase(_src_root, dir_path)), recreate=True
                    )


def copy_dir_transactional(
    src_fs,  # type: Union[FS, Text]
    src_path,  # type: Text
    dst_fs,  # type: Union[FS, Text]
    dst_path,  # type: Text
    walker=None,  # type: Optional[Walker]
    on_copy=None,  # type: Optional[_OnCopy]
    workers=0,  # type: int
    preserve_time=False,  # type: bool
):
    # type: (...) -> None
    """Copy a directory from one filesystem to another, atomically.

    Unlike `~fs.copy.copy_dir`, this guarantees that the destination is
    either fully updated or left completely untouched:

    * Every file is written to a temporary name inside a hidden staging
      directory on the destination, and renamed to its final location
      only after *all* files have been copied successfully.
    * Existing files are never left truncated or deleted: they are
      backed up inside the staging directory before a fully copied file
      takes their place.
    * If any step fails, everything written by this copy is removed and
      backed-up files are moved back, restoring the destination to its
      pre-call state, and a `~fs.errors.SourceCopyError` or
      `~fs.errors.DestinationCopyError` is raised.
    * Concurrent transactional copies targeting the same filesystem are
      serialized with a lock directory; the loser fails immediately
      rather than corrupting the other transaction.

    Arguments:
        src_fs (FS or str): Source filesystem (instance or URL).
        src_path (str): Path to a directory on the source filesystem.
        dst_fs (FS or str): Destination filesystem (instance or URL).
        dst_path (str): Path to a directory on the destination filesystem.
        walker (~fs.walk.Walker, optional): A walker object that will be
            used to scan for files in ``src_fs``.
        on_copy (callable, optional): A function callback called after
            a single file is committed to its final location.
        workers (int): Accepted for parity with `~fs.copy.copy_dir`;
            transactional copies are always single-threaded.
        preserve_time (bool): If `True`, try to preserve mtime of the
            resources (defaults to `False`).

    """
    del workers
    on_copy = on_copy or (lambda *args: None)
    walker = walker or Walker()
    _src_path = abspath(normpath(src_path))
    _dst_path = abspath(normpath(dst_path))

    with manage_fs(src_fs, writeable=False) as _src_fs, manage_fs(
        dst_fs, create=True
    ) as _dst_fs:
        if _src_fs is _dst_fs and isbase(_src_path, _dst_path):
            raise IllegalDestination(dst_path)
        _TransactionalCopier(
            _src_fs,
            _src_path,
            _dst_fs,
            _dst_path,
            walker,
            on_copy,
            preserve_time,
        ).run()


class _TransactionalCopier(object):
    """Copy a directory tree atomically.

    Files are first copied into a hidden staging directory on the
    destination, then moved to their final location once the whole copy
    has succeeded. Any failure triggers a rollback.
    """

    def __init__(
        self,
        src_fs,  # type: FS
        src_path,  # type: Text
        dst_fs,  # type: FS
        dst_path,  # type: Text
        walker,  # type: Walker
        on_copy,  # type: _OnCopy
        preserve_time,  # type: bool
    ):
        self.src_fs = src_fs
        self.src_path = src_path
        self.dst_fs = dst_fs
        self.dst_path = dst_path
        self.walker = walker
        self.on_copy = on_copy
        self.preserve_time = preserve_time
        self.completed = []  # type: List[Text]

        self._staging = None  # type: Optional[Text]
        self._staging_files = None  # type: Optional[Text]
        self._staging_backup = None  # type: Optional[Text]
        self._staging_parents = []  # type: List[Text]
        self._src_dirs = []  # type: List[Text]
        self._staged = []  # type: List[Text]
        self._backups = []  # type: List[Tuple[Text, Text]]
        self._new_files = []  # type: List[Text]
        self._new_dirs = []  # type: List[Text]
        self._lock_held = False

    def run(self):
        # type: () -> None
        self._preflight()
        self._acquire_lock()
        try:
            try:
                self._stage()
                self._commit()
            except (SourceCopyError, DestinationCopyError):
                self._rollback()
                raise
            except Exception as error:
                self._rollback()
                raise DestinationCopyError(
                    self.src_path,
                    self.dst_path,
                    self.completed,
                    exc=error,
                )
            self._cleanup_staging()
        finally:
            self._release_lock()

    # -- preflight ---------------------------------------------------------

    def _preflight(self):
        # type: () -> None
        try:
            meta = self.dst_fs.getmeta()
        except Exception as error:
            raise DestinationCopyError(
                self.src_path, self.dst_path, self.completed, exc=error
            )
        if meta.get("read_only"):
            raise DestinationCopyError(
                self.src_path,
                self.dst_path,
                self.completed,
                msg="destination filesystem is read only",
            )
        if not self.src_fs.isdir(self.src_path):
            raise SourceCopyError(
                self.src_path,
                self.dst_path,
                self.completed,
                msg="source directory '{src_path}' does not exist",
            )
        if self.dst_fs.exists(self.dst_path) and not self.dst_fs.isdir(
            self.dst_path
        ):
            raise DestinationCopyError(
                self.src_path,
                self.dst_path,
                self.completed,
                msg="destination path '{dst_path}' exists and is not a directory",
            )

    def _acquire_lock(self):
        # type: () -> None
        try:
            self.dst_fs.makedir(_TRANSACTION_LOCK_PATH)
        except DirectoryExists:
            raise DestinationCopyError(
                self.src_path,
                self.dst_path,
                self.completed,
                msg="another transactional copy is already in progress",
            )
        except Exception as error:
            raise DestinationCopyError(
                self.src_path, self.dst_path, self.completed, exc=error
            )
        self._lock_held = True

    def _release_lock(self):
        # type: () -> None
        if self._lock_held:
            self._lock_held = False
            try:
                self.dst_fs.removedir(_TRANSACTION_LOCK_PATH)
            except Exception:
                pass

    # -- staging -----------------------------------------------------------

    def _stage(self):
        # type: () -> None
        self._create_staging_dir()

        try:
            dir_paths = list(self.walker.dirs(self.src_fs, self.src_path))
            file_paths = list(self.walker.files(self.src_fs, self.src_path))
        except Exception as error:
            raise SourceCopyError(
                self.src_path, self.dst_path, self.completed, exc=error
            )

        for dir_path in dir_paths:
            rel = frombase(self.src_path, dir_path)
            self._src_dirs.append(dir_path)
            try:
                self.dst_fs.makedir(
                    combine(self._staging_files, rel), recreate=True
                )
            except Exception as error:
                raise DestinationCopyError(
                    self.src_path, self.dst_path, self.completed, exc=error
                )
            self.completed.append("scanned directory '{0}'".format(rel))

        for file_path in file_paths:
            rel = frombase(self.src_path, file_path)
            self._stage_file(file_path, rel)

    def _create_staging_dir(self):
        # type: () -> None
        missing = []
        staging = combine(
            dirname(self.dst_path) or "/",
            _TRANSACTION_STAGING_PREFIX + uuid.uuid4().hex,
        )
        path = staging
        while path != "/" and not self.dst_fs.exists(path):
            missing.append(path)
            path = dirname(path)
        try:
            self.dst_fs.makedirs(staging, recreate=True)
        except Exception as error:
            raise DestinationCopyError(
                self.src_path, self.dst_path, self.completed, exc=error
            )
        self._staging = staging
        self._staging_parents = missing
        self._staging_files = combine(staging, "files")
        self._staging_backup = combine(staging, "backup")
        try:
            self.dst_fs.makedir(self._staging_files)
            self.dst_fs.makedir(self._staging_backup)
        except Exception as error:
            raise DestinationCopyError(
                self.src_path, self.dst_path, self.completed, exc=error
            )

    def _stage_file(self, src_file_path, rel):
        # type: (Text, Text) -> None
        staged_path = combine(self._staging_files, rel)
        part_path = staged_path + ".part"
        try:
            src_file = self.src_fs.openbin(src_file_path)
        except Exception as error:
            raise SourceCopyError(
                self.src_path, self.dst_path, self.completed, exc=error
            )
        try:
            try:
                dst_file = self.dst_fs.openbin(part_path, "w")
            except Exception as error:
                raise DestinationCopyError(
                    self.src_path, self.dst_path, self.completed, exc=error
                )
            try:
                while True:
                    try:
                        chunk = src_file.read(_CHUNK_SIZE)
                    except Exception as error:
                        raise SourceCopyError(
                            self.src_path,
                            self.dst_path,
                            self.completed,
                            exc=error,
                        )
                    if not chunk:
                        break
                    try:
                        dst_file.write(chunk)
                    except Exception as error:
                        raise DestinationCopyError(
                            self.src_path,
                            self.dst_path,
                            self.completed,
                            exc=error,
                        )
            finally:
                dst_file.close()
        finally:
            src_file.close()

        try:
            self.dst_fs.move(part_path, staged_path, overwrite=True)
        except Exception as error:
            raise DestinationCopyError(
                self.src_path, self.dst_path, self.completed, exc=error
            )

        if self.preserve_time:
            try:
                copy_modified_time(
                    self.src_fs, src_file_path, self.dst_fs, staged_path
                )
            except Exception as error:
                raise DestinationCopyError(
                    self.src_path, self.dst_path, self.completed, exc=error
                )

        self._staged.append(rel)
        self.completed.append("staged '{0}'".format(rel))

    # -- commit ------------------------------------------------------------

    def _commit(self):
        # type: () -> None
        self._ensure_dir(self.dst_path)
        for dir_path in self._src_dirs:
            self._ensure_dir(
                combine(self.dst_path, frombase(self.src_path, dir_path))
            )
        for rel in self._staged:
            final_path = combine(self.dst_path, rel)
            staged_path = combine(self._staging_files, rel)
            self._ensure_dir(dirname(final_path))

            try:
                if self.dst_fs.exists(final_path):
                    if not self.dst_fs.isfile(final_path):
                        raise DestinationCopyError(
                            self.src_path,
                            self.dst_path,
                            self.completed,
                            msg=(
                                "cannot replace non-file resource at "
                                "'{dst_path}'"
                            ),
                        )
                    backup_path = combine(self._staging_backup, rel)
                    self.dst_fs.makedirs(dirname(backup_path), recreate=True)
                    self.dst_fs.copy(final_path, backup_path, overwrite=True)
                    self._backups.append((final_path, backup_path))
                else:
                    self._new_files.append(final_path)

                self.dst_fs.move(staged_path, final_path, overwrite=True)
            except (SourceCopyError, DestinationCopyError):
                raise
            except Exception as error:
                raise DestinationCopyError(
                    self.src_path, self.dst_path, self.completed, exc=error
                )

            self.completed.append("committed '{0}'".format(rel))
            self.on_copy(
                self.src_fs,
                combine(self.src_path, rel),
                self.dst_fs,
                final_path,
            )

        if self.preserve_time:
            self._preserve_directory_times()

    def _ensure_dir(self, path):
        # type: (Text) -> None
        if path == "/":
            return
        missing = []
        current = path
        while current != "/" and not self.dst_fs.exists(current):
            missing.append(current)
            current = dirname(current)
        if not missing and self.dst_fs.isdir(path):
            return
        try:
            self.dst_fs.makedirs(path, recreate=True)
        except Exception as error:
            raise DestinationCopyError(
                self.src_path, self.dst_path, self.completed, exc=error
            )
        self._new_dirs.extend(reversed(missing))

    def _preserve_directory_times(self):
        # type: () -> None
        paths = [(self.src_path, self.dst_path)]
        paths.extend(
            (dir_path, combine(self.dst_path, frombase(self.src_path, dir_path)))
            for dir_path in self._src_dirs
        )
        for src_dir, dst_dir in reversed(paths):
            if src_dir == "/" or dst_dir == "/":
                continue
            try:
                copy_modified_time(self.src_fs, src_dir, self.dst_fs, dst_dir)
            except Exception as error:
                raise DestinationCopyError(
                    self.src_path, self.dst_path, self.completed, exc=error
                )

    # -- rollback / cleanup ------------------------------------------------

    def _rollback(self):
        # type: () -> None
        for final_path, backup_path in reversed(self._backups):
            try:
                self.dst_fs.move(backup_path, final_path, overwrite=True)
            except Exception:
                pass
        for path in reversed(self._new_files):
            try:
                self.dst_fs.remove(path)
            except Exception:
                pass
        for path in reversed(self._new_dirs):
            try:
                self.dst_fs.removedir(path)
            except Exception:
                pass
        self._cleanup_staging()

    def _cleanup_staging(self):
        # type: () -> None
        if self._staging is not None:
            try:
                self.dst_fs.removetree(self._staging)
            except Exception:
                pass
            for path in self._staging_parents:
                if path == self._staging:
                    continue
                try:
                    self.dst_fs.removedir(path)
                except Exception:
                    pass
            self._staging = None


def copy_dir(
    src_fs,  # type: Union[FS, Text]
    src_path,  # type: Text
    dst_fs,  # type: Union[FS, Text]
    dst_path,  # type: Text
    walker=None,  # type: Optional[Walker]
    on_copy=None,  # type: Optional[_OnCopy]
    workers=0,  # type: int
    preserve_time=False,  # type: bool
    transactional=False,  # type: bool
):
    # type: (...) -> None
    """Copy a directory from one filesystem to another.

    Arguments:
        src_fs (FS or str): Source filesystem (instance or URL).
        src_path (str): Path to a directory on the source filesystem.
        dst_fs (FS or str): Destination filesystem (instance or URL).
        dst_path (str): Path to a directory on the destination filesystem.
        walker (~fs.walk.Walker, optional): A walker object that will be
            used to scan for files in ``src_fs``. Set this if you only
            want to consider a sub-set of the resources in ``src_fs``.
        on_copy (callable, optional):  A function callback called after
            a single file copy is executed. Expected signature is
            ``(src_fs, src_path, dst_fs, dst_path)``.
        workers (int): Use ``worker`` threads to copy data, or ``0`` (default) for
            a single-threaded copy.
        preserve_time (bool): If `True`, try to preserve mtime of the
            resources (defaults to `False`).
        transactional (bool): If `True`, copy atomically: all files are
            written to temporary names first, then renamed in to place,
            and any failure restores the destination to its previous
            state (defaults to `False`).

    """
    if transactional:
        copy_dir_transactional(
            src_fs,
            src_path,
            dst_fs,
            dst_path,
            walker=walker,
            on_copy=on_copy,
            preserve_time=preserve_time,
        )
        return
    copy_dir_if(
        src_fs,
        src_path,
        dst_fs,
        dst_path,
        "always",
        walker,
        on_copy,
        workers,
        preserve_time=preserve_time,
    )


def copy_dir_if_newer(
    src_fs,  # type: Union[FS, Text]
    src_path,  # type: Text
    dst_fs,  # type: Union[FS, Text]
    dst_path,  # type: Text
    walker=None,  # type: Optional[Walker]
    on_copy=None,  # type: Optional[_OnCopy]
    workers=0,  # type: int
    preserve_time=False,  # type: bool
):
    # type: (...) -> None
    """Copy a directory from one filesystem to another, checking times.

    .. deprecated:: 2.5.0
       Use `~fs.copy.copy_dir_if` with ``condition="newer"`` instead.

    """
    warnings.warn(
        "copy_dir_if_newer is deprecated. Use copy_dir_if instead.", DeprecationWarning
    )
    copy_dir_if(
        src_fs,
        src_path,
        dst_fs,
        dst_path,
        "newer",
        walker,
        on_copy,
        workers,
        preserve_time=preserve_time,
    )


def copy_dir_if(
    src_fs,  # type: Union[FS, Text]
    src_path,  # type: Text
    dst_fs,  # type: Union[FS, Text]
    dst_path,  # type: Text
    condition,  # type: Text
    walker=None,  # type: Optional[Walker]
    on_copy=None,  # type: Optional[_OnCopy]
    workers=0,  # type: int
    preserve_time=False,  # type: bool
):
    # type: (...) -> None
    """Copy a directory from one filesystem to another, depending on a condition.

    Arguments:
        src_fs (FS or str): Source filesystem (instance or URL).
        src_path (str): Path to a directory on the source filesystem.
        dst_fs (FS or str): Destination filesystem (instance or URL).
        dst_path (str): Path to a directory on the destination filesystem.
        condition (str): Name of the condition to check for each file.
        walker (~fs.walk.Walker, optional): A walker object that will be
            used to scan for files in ``src_fs``. Set this if you only want
            to consider a sub-set of the resources in ``src_fs``.
        on_copy (callable):A function callback called after a single file copy
            is executed. Expected signature is ``(src_fs, src_path, dst_fs,
            dst_path)``.
        workers (int): Use ``worker`` threads to copy data, or ``0`` (default) for
            a single-threaded copy.
        preserve_time (bool): If `True`, try to preserve mtime of the
            resources (defaults to `False`).

    See Also:
        `~fs.copy.copy_file_if` for the full list of supported values for the
        ``condition`` argument.

    """
    on_copy = on_copy or (lambda *args: None)
    walker = walker or Walker()
    _src_path = abspath(normpath(src_path))
    _dst_path = abspath(normpath(dst_path))

    from ._bulk import Copier

    copy_structure(src_fs, dst_fs, walker, src_path, dst_path)

    with manage_fs(src_fs, writeable=False) as _src_fs, manage_fs(
        dst_fs, create=True
    ) as _dst_fs:
        with _src_fs.lock(), _dst_fs.lock():
            _thread_safe = is_thread_safe(_src_fs, _dst_fs)
            with Copier(
                num_workers=workers if _thread_safe else 0, preserve_time=preserve_time
            ) as copier:
                for dir_path in walker.files(_src_fs, _src_path):
                    copy_path = combine(_dst_path, frombase(_src_path, dir_path))
                    if _copy_is_necessary(
                        _src_fs, dir_path, _dst_fs, copy_path, condition
                    ):
                        copier.copy(_src_fs, dir_path, _dst_fs, copy_path)
                        on_copy(_src_fs, dir_path, _dst_fs, copy_path)


def _copy_is_necessary(
    src_fs,  # type: FS
    src_path,  # type: Text
    dst_fs,  # type: FS
    dst_path,  # type: Text
    condition,  # type: Text
):
    # type: (...) -> bool

    if condition == "always":
        return True

    elif condition == "newer":
        try:
            src_modified = src_fs.getmodified(src_path)
            dst_modified = dst_fs.getmodified(dst_path)
        except ResourceNotFound:
            return True
        else:
            return (
                src_modified is None
                or dst_modified is None
                or src_modified > dst_modified
            )

    elif condition == "older":
        try:
            src_modified = src_fs.getmodified(src_path)
            dst_modified = dst_fs.getmodified(dst_path)
        except ResourceNotFound:
            return True
        else:
            return (
                src_modified is None
                or dst_modified is None
                or src_modified < dst_modified
            )

    elif condition == "exists":
        return dst_fs.exists(dst_path)

    elif condition == "not_exists":
        return not dst_fs.exists(dst_path)

    else:
        raise ValueError("{} is not a valid copy condition.".format(condition))


def copy_modified_time(
    src_fs,  # type: Union[FS, Text]
    src_path,  # type: Text
    dst_fs,  # type: Union[FS, Text]
    dst_path,  # type: Text
):
    # type: (...) -> None
    """Copy modified time metadata from one file to another.

    Arguments:
        src_fs (FS or str): Source filesystem (instance or URL).
        src_path (str): Path to a directory on the source filesystem.
        dst_fs (FS or str): Destination filesystem (instance or URL).
        dst_path (str): Path to a directory on the destination filesystem.

    """
    namespaces = ("details",)
    with manage_fs(src_fs, writeable=False) as _src_fs:
        with manage_fs(dst_fs, create=True) as _dst_fs:
            src_meta = _src_fs.getinfo(src_path, namespaces)
            src_details = src_meta.raw.get("details", {})
            dst_details = {}
            for value in ("metadata_changed", "modified"):
                if value in src_details:
                    dst_details[value] = src_details[value]
            _dst_fs.setinfo(dst_path, {"details": dst_details})
