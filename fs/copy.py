"""Functions for copying resources *between* filesystem.
"""

from __future__ import print_function, unicode_literals

import typing

import uuid
import warnings

from .errors import (
    DestinationExists,
    FSError,
    IllegalDestination,
    ResourceNotFound,
    TransactionalDestinationError,
    TransactionalSourceError,
)
from .opener import manage_fs
from .path import abspath, combine, dirname, frombase, isbase, normpath, recursepath
from .tools import is_thread_safe
from .walk import Walker

if typing.TYPE_CHECKING:
    from typing import Callable, Optional, Text, Union

    from .base import FS

    _OnCopy = Callable[[FS, Text, FS, Text], object]


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
        transactional (bool): If `True`, perform the copy as a single
            all-or-nothing transaction (defaults to `False`). All files
            are first written to temporary names and only renamed into
            place once every file has been copied successfully. If any
            step fails, everything written by the copy is rolled back
            and the destination is restored to its previous state.
            Concurrent transactional copies to the same destination are
            serialized: while one transaction is in progress any other
            raises `~fs.errors.TransactionalDestinationError`.
            The ``workers`` argument is ignored in transactional mode,
            files are copied sequentially.

    """
    if transactional:
        return _copy_dir_transactional(
            src_fs,
            "/",
            dst_fs,
            "/",
            walker=walker,
            on_copy=on_copy,
            preserve_time=preserve_time,
        )
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
        transactional (bool): If `True`, perform the copy as a single
            all-or-nothing transaction, see `copy_fs` for details.

    """
    if transactional:
        return _copy_dir_transactional(
            src_fs,
            src_path,
            dst_fs,
            dst_path,
            walker=walker,
            on_copy=on_copy,
            preserve_time=preserve_time,
        )
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


_TX_LOCK_DIR = "/__fs_transaction_lock__"
_TX_STAGING_DIR = "/__fs_transaction_{}__"


def _copy_dir_transactional(
    src_fs,  # type: Union[FS, Text]
    src_path,  # type: Text
    dst_fs,  # type: Union[FS, Text]
    dst_path,  # type: Text
    walker=None,  # type: Optional[Walker]
    on_copy=None,  # type: Optional[_OnCopy]
    preserve_time=False,  # type: bool
):
    # type: (...) -> None
    """Copy a directory as a single all-or-nothing transaction.

    Every file is first copied to a unique temporary staging directory
    on the destination filesystem. Only once every file has been staged
    successfully are the staged copies renamed into place. Existing
    destination files are renamed aside before they are replaced, so the
    destination never has a window where a file is missing or truncated.

    A lock directory at the root of the destination filesystem prevents
    two transactional copies from running concurrently against the same
    destination: the second copy raises
    `~fs.errors.TransactionalDestinationError` without writing
    anything.

    Any failure rolls the destination back to the state it had before
    the copy was started, and raises
    `~fs.errors.TransactionalSourceError` or
    `~fs.errors.TransactionalDestinationError` carrying the source and
    destination paths and the list of completed steps.

    """
    on_copy = on_copy or (lambda *args: None)
    walker = walker or Walker()
    _src_path = abspath(normpath(src_path))
    _dst_path = abspath(normpath(dst_path))
    completed_steps = []  # type: typing.List[Text]

    with manage_fs(src_fs, writeable=False) as _src_fs, manage_fs(
        dst_fs, create=True
    ) as _dst_fs:
        if _src_fs == _dst_fs and isbase(_src_path, _dst_path):
            raise IllegalDestination(dst_path)

        # Fail before a single byte is written if the destination is
        # known to be read only.
        if _dst_fs.getmeta().get("read_only", False):
            raise TransactionalDestinationError(
                _src_path,
                _dst_path,
                completed_steps,
                msg="destination filesystem is read only",
            )

        # Snapshot the source tree before the destination is touched, so
        # that the staging files can never be picked up by the walk.
        try:
            src_dirs = list(walker.dirs(_src_fs, _src_path))
            src_files = list(walker.files(_src_fs, _src_path))
        except Exception as error:
            raise TransactionalSourceError(
                _src_path, _dst_path, completed_steps, exc=error
            )

        staging_dir = _TX_STAGING_DIR.format(uuid.uuid4().hex)
        new_dir = combine(staging_dir, "new")
        old_dir = combine(staging_dir, "old")
        committed = []  # type: typing.List[typing.Tuple[Text, Optional[Text]]]
        created_dirs = []  # type: typing.List[Text]
        pending_backup = []  # type: typing.List[typing.Tuple[Text, Text]]
        lock_acquired = False

        def _rollback():
            # type: () -> None
            """Undo a partially committed transaction on the destination."""
            for final_path, backup_path in reversed(committed):
                try:
                    _dst_fs.remove(final_path)
                except Exception:
                    pass
                if backup_path is not None:
                    try:
                        _dst_fs.move(backup_path, final_path)
                    except Exception:
                        pass
            for final_path, backup_path in pending_backup:
                try:
                    _dst_fs.move(backup_path, final_path)
                except Exception:
                    pass
            for dir_path in sorted(
                created_dirs, key=lambda path: path.count("/"), reverse=True
            ):
                try:
                    _dst_fs.removedir(dir_path)
                except Exception:
                    pass

        try:
            # Acquire the transaction lock. This serializes concurrent
            # transactional copies to the same destination, and also
            # acts as a writability probe before any file is written.
            try:
                _dst_fs.makedir(_TX_LOCK_DIR)
                lock_acquired = True
            except DestinationExists:
                raise TransactionalDestinationError(
                    _src_path,
                    _dst_path,
                    completed_steps,
                    msg="another transactional copy is already in progress",
                )
            except Exception as error:
                raise TransactionalDestinationError(
                    _src_path, _dst_path, completed_steps, exc=error
                )

            # Stage every file under a temporary name.
            for src_file in src_files:
                rel_path = frombase(_src_path, src_file)
                tmp_path = combine(new_dir, rel_path)
                try:
                    _dst_fs.makedirs(dirname(tmp_path), recreate=True)
                except Exception as error:
                    raise TransactionalDestinationError(
                        _src_path, _dst_path, completed_steps, exc=error
                    )
                try:
                    read_file = _src_fs.openbin(src_file)
                except Exception as error:
                    raise TransactionalSourceError(
                        _src_path, _dst_path, completed_steps, exc=error
                    )
                try:
                    _dst_fs.upload(tmp_path, read_file)
                except Exception as error:
                    raise TransactionalDestinationError(
                        _src_path, _dst_path, completed_steps, exc=error
                    )
                finally:
                    read_file.close()
                if preserve_time:
                    try:
                        copy_modified_time(_src_fs, src_file, _dst_fs, tmp_path)
                    except Exception as error:
                        raise TransactionalDestinationError(
                            _src_path, _dst_path, completed_steps, exc=error
                        )
                completed_steps.append("staged '{}'".format(rel_path))

            # Create the destination directory structure, remembering
            # which directories are new so they can be rolled back.
            dst_dirs = [
                combine(_dst_path, frombase(_src_path, src_dir))
                for src_dir in src_dirs
            ]
            if _dst_path != "/":
                dst_dirs = [
                    path
                    for path in recursepath(_dst_path, reverse=True)
                    if path != "/"
                ] + dst_dirs
            try:
                for dst_dir in dst_dirs:
                    if not _dst_fs.exists(dst_dir):
                        created_dirs.append(dst_dir)
                    _dst_fs.makedirs(dst_dir, recreate=True)
            except Exception as error:
                _rollback()
                raise TransactionalDestinationError(
                    _src_path, _dst_path, completed_steps, exc=error
                )

            # Commit: move each staged copy into place. Existing files
            # are renamed aside first, so a failure can restore them and
            # no destination path is ever missing its file.
            try:
                for src_file in src_files:
                    rel_path = frombase(_src_path, src_file)
                    final_path = combine(_dst_path, rel_path)
                    tmp_path = combine(new_dir, rel_path)
                    backup_path = None  # type: Optional[Text]
                    if _dst_fs.exists(final_path):
                        backup_path = combine(old_dir, rel_path)
                        _dst_fs.makedirs(dirname(backup_path), recreate=True)
                        _dst_fs.move(final_path, backup_path)
                        pending_backup.append((final_path, backup_path))
                    _dst_fs.move(tmp_path, final_path, overwrite=True)
                    committed.append((final_path, backup_path))
                    del pending_backup[:]
                    completed_steps.append("committed '{}'".format(rel_path))
                    on_copy(_src_fs, src_file, _dst_fs, final_path)
            except Exception as error:
                _rollback()
                raise TransactionalDestinationError(
                    _src_path, _dst_path, completed_steps, exc=error
                )

            # Preserve directory modification times, deepest directories
            # first so that parent directories keep their own mtime.
            if preserve_time:
                dir_pairs = [
                    (
                        src_dir,
                        combine(_dst_path, frombase(_src_path, src_dir)),
                    )
                    for src_dir in src_dirs
                ]
                dir_pairs.append((_src_path, _dst_path))
                for src_dir, dst_dir in sorted(
                    dir_pairs, key=lambda pair: pair[0].count("/"), reverse=True
                ):
                    try:
                        copy_modified_time(_src_fs, src_dir, _dst_fs, dst_dir)
                    except Exception:
                        pass
        finally:
            try:
                _dst_fs.removetree(staging_dir)
            except Exception:
                pass
            if lock_acquired:
                try:
                    _dst_fs.removedir(_TX_LOCK_DIR)
                except Exception:
                    pass


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
