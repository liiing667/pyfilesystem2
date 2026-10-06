from __future__ import unicode_literals

import calendar
import datetime
import errno
import os
import shutil
import tempfile
import threading
import unittest
from parameterized import parameterized

import fs.copy
import fs.errors
from fs import open_fs
from fs.memoryfs import MemoryFS
from fs.path import normpath
from fs.wrap import read_only


def _create_sandbox_dir(prefix="pyfilesystem2_sandbox_", home=None):
    if home is None:
        return tempfile.mkdtemp(prefix=prefix)
    else:
        sandbox_path = os.path.join(home, prefix)
        mkdirp(sandbox_path)
        return sandbox_path


def _touch(root, filepath):
    # create abs filename
    abs_filepath = os.path.join(root, filepath)
    # create dir
    dirname = os.path.dirname(abs_filepath)
    mkdirp(dirname)
    # touch file
    with open(abs_filepath, "a"):
        os.utime(
            abs_filepath, None
        )  # update the mtime in case the file exists, same as touch

    return abs_filepath


def _write_file(filepath, write_chars=1024):
    with open(filepath, "w") as f:
        f.write("1" * write_chars)
    return filepath


def _delay_file_utime(filepath, delta_sec):
    utcnow = datetime.datetime.utcnow()
    unix_timestamp = calendar.timegm(utcnow.timetuple())
    times = unix_timestamp + delta_sec, unix_timestamp + delta_sec
    os.utime(filepath, times)


def mkdirp(path):
    # os.makedirs(path, exist_ok=True) only for python3.?
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise


class TestCopySimple(unittest.TestCase):
    @parameterized.expand([(0,), (1,), (2,), (4,)])
    def test_copy_fs(self, workers):
        namespaces = ("details", "modified")

        src_fs = open_fs("mem://")
        src_fs.makedirs("foo/bar")
        src_fs.makedirs("foo/empty")
        src_fs.touch("test.txt")
        src_fs.touch("foo/bar/baz.txt")
        src_file1_info = src_fs.getinfo("test.txt", namespaces)
        src_file2_info = src_fs.getinfo("foo/bar/baz.txt", namespaces)

        dst_fs = open_fs("mem://")
        fs.copy.copy_fs(src_fs, dst_fs, workers=workers, preserve_time=True)

        self.assertTrue(dst_fs.isdir("foo/empty"))
        self.assertTrue(dst_fs.isdir("foo/bar"))
        self.assertTrue(dst_fs.isfile("test.txt"))

        dst_file1_info = dst_fs.getinfo("test.txt", namespaces)
        dst_file2_info = dst_fs.getinfo("foo/bar/baz.txt", namespaces)
        self.assertEqual(dst_file1_info.modified, src_file1_info.modified)
        self.assertEqual(dst_file2_info.modified, src_file2_info.modified)

    def test_copy_value_error(self):
        src_fs = open_fs("mem://")
        dst_fs = open_fs("mem://")
        with self.assertRaises(ValueError):
            fs.copy.copy_fs(src_fs, dst_fs, workers=-1)

    def test_copy_dir0(self):
        namespaces = ("details", "modified")

        src_fs = open_fs("mem://")
        src_fs.makedirs("foo/bar")
        src_fs.makedirs("foo/empty")
        src_fs.touch("test.txt")
        src_fs.touch("foo/bar/baz.txt")
        src_file2_info = src_fs.getinfo("foo/bar/baz.txt", namespaces)

        with open_fs("mem://") as dst_fs:
            fs.copy.copy_dir(src_fs, "/foo", dst_fs, "/", workers=0, preserve_time=True)
            self.assertTrue(dst_fs.isdir("bar"))
            self.assertTrue(dst_fs.isdir("empty"))
            self.assertTrue(dst_fs.isfile("bar/baz.txt"))

            dst_file2_info = dst_fs.getinfo("bar/baz.txt", namespaces)
            self.assertEqual(dst_file2_info.modified, src_file2_info.modified)

    @parameterized.expand([(0,), (1,), (2,), (4,)])
    def test_copy_dir(self, workers):
        namespaces = ("details", "modified")

        src_fs = open_fs("mem://")
        src_fs.makedirs("foo/bar")
        src_fs.makedirs("foo/empty")
        src_fs.touch("test.txt")
        src_fs.touch("foo/bar/baz.txt")
        src_file2_info = src_fs.getinfo("foo/bar/baz.txt", namespaces)

        with open_fs("mem://") as dst_fs:
            fs.copy.copy_dir(
                src_fs, "/foo", dst_fs, "/", workers=workers, preserve_time=True
            )
            self.assertTrue(dst_fs.isdir("bar"))
            self.assertTrue(dst_fs.isdir("empty"))
            self.assertTrue(dst_fs.isfile("bar/baz.txt"))

            dst_file2_info = dst_fs.getinfo("bar/baz.txt", namespaces)
            self.assertEqual(dst_file2_info.modified, src_file2_info.modified)

    def test_copy_large(self):
        data1 = b"foo" * 512 * 1024
        data2 = b"bar" * 2 * 512 * 1024
        data3 = b"baz" * 3 * 512 * 1024
        data4 = b"egg" * 7 * 512 * 1024
        with open_fs("temp://") as src_fs:
            src_fs.writebytes("foo", data1)
            src_fs.writebytes("bar", data2)
            src_fs.makedir("dir1").writebytes("baz", data3)
            src_fs.makedirs("dir2/dir3").writebytes("egg", data4)
            for workers in (0, 1, 2, 4):
                with open_fs("temp://") as dst_fs:
                    fs.copy.copy_fs(src_fs, dst_fs, workers=workers)
                    self.assertEqual(dst_fs.readbytes("foo"), data1)
                    self.assertEqual(dst_fs.readbytes("bar"), data2)
                    self.assertEqual(dst_fs.readbytes("dir1/baz"), data3)
                    self.assertEqual(dst_fs.readbytes("dir2/dir3/egg"), data4)

    def test_copy_dir_on_copy(self):
        src_fs = open_fs("mem://")
        src_fs.touch("baz.txt")

        on_copy_calls = []

        def on_copy(*args):
            on_copy_calls.append(args)

        dst_fs = open_fs("mem://")
        fs.copy.copy_dir(src_fs, "/", dst_fs, "/", on_copy=on_copy)
        self.assertEqual(on_copy_calls, [(src_fs, "/baz.txt", dst_fs, "/baz.txt")])


class TestCopyIfNewer(unittest.TestCase):
    copy_if_condition = "newer"

    def test_copy_file_if_same_fs(self):
        src_fs = open_fs("mem://")
        src_fs.makedir("foo2").touch("exists")
        src_fs.makedir("foo1").touch("test1.txt")
        src_fs.settimes(
            "foo2/exists", datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        )
        self.assertTrue(
            fs.copy.copy_file_if(
                src_fs,
                "foo1/test1.txt",
                src_fs,
                "foo2/test1.txt.copy",
                self.copy_if_condition,
            )
        )
        self.assertFalse(
            fs.copy.copy_file_if(
                src_fs, "foo1/test1.txt", src_fs, "foo2/exists", self.copy_if_condition
            )
        )
        self.assertTrue(src_fs.exists("foo2/test1.txt.copy"))

    def test_copy_file_if_dst_is_older(self):
        try:
            # create first dst ==> dst is older the src ==> file should be copied
            dst_dir = _create_sandbox_dir()
            dst_file1 = _touch(dst_dir, "file1.txt")
            _write_file(dst_file1)

            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            # ensure src file is newer than dst, changing its modification time
            _delay_file_utime(src_file1, delta_sec=60)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            self.assertTrue(dst_fs.exists("/file1.txt"))

            copied = fs.copy.copy_file_if(
                src_fs, "/file1.txt", dst_fs, "/file1.txt", self.copy_if_condition
            )

            self.assertTrue(copied)
            self.assertTrue(dst_fs.exists("/file1.txt"))
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_file_if_dst_doesnt_exists(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            dst_dir = _create_sandbox_dir()

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            copied = fs.copy.copy_file_if(
                src_fs, "/file1.txt", dst_fs, "/file1.txt", self.copy_if_condition
            )

            self.assertTrue(copied)
            self.assertTrue(dst_fs.exists("/file1.txt"))
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_file_if_dst_is_newer(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            dst_dir = _create_sandbox_dir()
            dst_file1 = _touch(dst_dir, "file1.txt")
            _write_file(dst_file1)

            # ensure dst file is newer than src, changing its modification time
            _delay_file_utime(dst_file1, delta_sec=60)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            self.assertTrue(dst_fs.exists("/file1.txt"))

            copied = fs.copy.copy_file_if(
                src_fs, "/file1.txt", dst_fs, "/file1.txt", self.copy_if_condition
            )

            self.assertFalse(copied)
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_fs_if(self):
        try:
            dst_dir = _create_sandbox_dir()
            dst_file1 = _touch(dst_dir, "file1.txt")
            dst_file2 = _touch(dst_dir, "file2.txt")
            _write_file(dst_file1)
            _write_file(dst_file2)

            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            src_file2 = _touch(src_dir, "file2.txt")
            src_file3 = _touch(src_dir, "file3.txt")
            _write_file(src_file1)
            _write_file(src_file2)
            _write_file(src_file3)

            # ensure src_file1 is newer than dst_file1, changing its modification time
            # ensure dst_file2 is newer than src_file2, changing its modification time
            _delay_file_utime(src_file1, delta_sec=60)
            _delay_file_utime(dst_file2, delta_sec=60)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            self.assertTrue(dst_fs.exists("/file1.txt"))
            self.assertTrue(dst_fs.exists("/file2.txt"))

            copied = []

            def on_copy(src_fs, src_path, dst_fs, dst_path):
                copied.append(dst_path)

            fs.copy.copy_fs_if(
                src_fs, dst_fs, on_copy=on_copy, condition=self.copy_if_condition
            )

            self.assertTrue("/file1.txt" in copied)
            self.assertTrue("/file2.txt" not in copied)
            self.assertTrue("/file3.txt" in copied)
            self.assertTrue(dst_fs.exists("/file1.txt"))
            self.assertTrue(dst_fs.exists("/file2.txt"))
            self.assertTrue(dst_fs.exists("/file3.txt"))

            src_fs.close()
            dst_fs.close()

        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_dir_if(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            src_file2 = _touch(src_dir, os.path.join("one_level_down", "file2.txt"))
            _write_file(src_file2)

            dst_dir = _create_sandbox_dir()
            mkdirp(os.path.join(dst_dir, "target_dir"))
            dst_file1 = _touch(dst_dir, os.path.join("target_dir", "file1.txt"))
            _write_file(dst_file1)

            # ensure dst file is newer than src, changing its modification time
            _delay_file_utime(dst_file1, delta_sec=60)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            copied = []

            def on_copy(src_fs, src_path, dst_fs, dst_path):
                copied.append(dst_path)

            fs.copy.copy_dir_if(
                src_fs,
                "/",
                dst_fs,
                "/target_dir/",
                on_copy=on_copy,
                condition=self.copy_if_condition,
            )

            self.assertEqual(copied, ["/target_dir/one_level_down/file2.txt"])
            self.assertTrue(dst_fs.exists("/target_dir/one_level_down/file2.txt"))

            src_fs.close()
            dst_fs.close()
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_dir_if_same_fs(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "src" + os.sep + "file1.txt")
            _write_file(src_file1)

            _create_sandbox_dir(home=src_dir)

            src_fs = open_fs("osfs://" + src_dir)

            copied = []

            def on_copy(src_fs, src_path, dst_fs, dst_path):
                copied.append(dst_path)

            fs.copy.copy_dir_if(
                src_fs, "/src", src_fs, "/dst", on_copy=on_copy, condition="newer"
            )

            self.assertEqual(copied, ["/dst/file1.txt"])
            self.assertTrue(src_fs.exists("/dst/file1.txt"))

            src_fs.close()

        finally:
            shutil.rmtree(src_dir)

    def test_copy_dir_if_multiple_files(self):
        try:
            src_dir = _create_sandbox_dir()
            src_fs = open_fs("osfs://" + src_dir)
            src_fs.makedirs("foo/bar")
            src_fs.makedirs("foo/empty")
            src_fs.touch("test.txt")
            src_fs.touch("foo/bar/baz.txt")

            dst_dir = _create_sandbox_dir()
            dst_fs = open_fs("osfs://" + dst_dir)

            fs.copy.copy_dir_if(src_fs, "/foo", dst_fs, "/", condition="newer")

            self.assertTrue(dst_fs.isdir("bar"))
            self.assertTrue(dst_fs.isdir("empty"))
            self.assertTrue(dst_fs.isfile("bar/baz.txt"))
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)


class TestCopyIfOlder(unittest.TestCase):
    copy_if_condition = "older"

    def test_copy_file_if_same_fs(self):
        src_fs = open_fs("mem://")
        src_fs.makedir("foo2").touch("exists")
        src_fs.makedir("foo1").touch("test1.txt")
        src_fs.settimes(
            "foo2/exists", datetime.datetime.utcnow() - datetime.timedelta(hours=1)
        )
        self.assertTrue(
            fs.copy.copy_file_if(
                src_fs,
                "foo1/test1.txt",
                src_fs,
                "foo2/test1.txt.copy",
                self.copy_if_condition,
            )
        )
        self.assertFalse(
            fs.copy.copy_file_if(
                src_fs, "foo1/test1.txt", src_fs, "foo2/exists", self.copy_if_condition
            )
        )
        self.assertTrue(src_fs.exists("foo2/test1.txt.copy"))

    def test_copy_file_if_dst_is_older(self):
        try:
            # create first dst ==> dst is older the src ==> file should be copied
            dst_dir = _create_sandbox_dir()
            dst_file1 = _touch(dst_dir, "file1.txt")
            _write_file(dst_file1)

            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            # ensure src file is newer than dst, changing its modification time
            _delay_file_utime(src_file1, delta_sec=60)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            self.assertTrue(dst_fs.exists("/file1.txt"))

            copied = fs.copy.copy_file_if(
                src_fs, "/file1.txt", dst_fs, "/file1.txt", self.copy_if_condition
            )

            self.assertFalse(copied)
            self.assertTrue(dst_fs.exists("/file1.txt"))
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_file_if_dst_doesnt_exists(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            dst_dir = _create_sandbox_dir()

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            copied = fs.copy.copy_file_if(
                src_fs, "/file1.txt", dst_fs, "/file1.txt", self.copy_if_condition
            )

            self.assertTrue(copied)
            self.assertTrue(dst_fs.exists("/file1.txt"))
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_file_if_dst_is_newer(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            dst_dir = _create_sandbox_dir()
            dst_file1 = _touch(dst_dir, "file1.txt")
            _write_file(dst_file1)

            # ensure dst file is newer than src, changing its modification time
            _delay_file_utime(dst_file1, delta_sec=60)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            self.assertTrue(dst_fs.exists("/file1.txt"))

            copied = fs.copy.copy_file_if(
                src_fs, "/file1.txt", dst_fs, "/file1.txt", self.copy_if_condition
            )

            self.assertTrue(copied)
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_fs_if(self):
        try:
            dst_dir = _create_sandbox_dir()
            dst_file1 = _touch(dst_dir, "file1.txt")
            dst_file2 = _touch(dst_dir, "file2.txt")
            _write_file(dst_file1)
            _write_file(dst_file2)

            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            src_file2 = _touch(src_dir, "file2.txt")
            src_file3 = _touch(src_dir, "file3.txt")
            _write_file(src_file1)
            _write_file(src_file2)
            _write_file(src_file3)

            # ensure src_file1 is newer than dst_file1, changing its modification time
            # ensure dst_file2 is newer than src_file2, changing its modification time
            _delay_file_utime(src_file1, delta_sec=60)
            _delay_file_utime(dst_file2, delta_sec=60)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            self.assertTrue(dst_fs.exists("/file1.txt"))
            self.assertTrue(dst_fs.exists("/file2.txt"))

            copied = []

            def on_copy(src_fs, src_path, dst_fs, dst_path):
                copied.append(dst_path)

            fs.copy.copy_fs_if(
                src_fs, dst_fs, on_copy=on_copy, condition=self.copy_if_condition
            )

            self.assertTrue("/file1.txt" not in copied)
            self.assertTrue("/file2.txt" in copied)
            self.assertTrue("/file3.txt" in copied)
            self.assertTrue(dst_fs.exists("/file1.txt"))
            self.assertTrue(dst_fs.exists("/file2.txt"))
            self.assertTrue(dst_fs.exists("/file3.txt"))

            src_fs.close()
            dst_fs.close()

        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_dir_if(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            src_file2 = _touch(src_dir, os.path.join("one_level_down", "file2.txt"))
            _write_file(src_file2)

            dst_dir = _create_sandbox_dir()
            mkdirp(os.path.join(dst_dir, "target_dir"))
            dst_file1 = _touch(dst_dir, os.path.join("target_dir", "file1.txt"))
            _write_file(dst_file1)

            # ensure src file is newer than dst, changing its modification time
            _delay_file_utime(src_file1, delta_sec=60)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            copied = []

            def on_copy(src_fs, src_path, dst_fs, dst_path):
                copied.append(dst_path)

            fs.copy.copy_dir_if(
                src_fs,
                "/",
                dst_fs,
                "/target_dir/",
                on_copy=on_copy,
                condition=self.copy_if_condition,
            )

            self.assertEqual(copied, ["/target_dir/one_level_down/file2.txt"])
            self.assertTrue(dst_fs.exists("/target_dir/one_level_down/file2.txt"))

            src_fs.close()
            dst_fs.close()
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_dir_if_same_fs(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "src" + os.sep + "file1.txt")
            _write_file(src_file1)

            _create_sandbox_dir(home=src_dir)

            src_fs = open_fs("osfs://" + src_dir)

            copied = []

            def on_copy(src_fs, src_path, dst_fs, dst_path):
                copied.append(dst_path)

            fs.copy.copy_dir_if(
                src_fs, "/src", src_fs, "/dst", on_copy=on_copy, condition="newer"
            )

            self.assertEqual(copied, ["/dst/file1.txt"])
            self.assertTrue(src_fs.exists("/dst/file1.txt"))

            src_fs.close()

        finally:
            shutil.rmtree(src_dir)

    def test_copy_dir_if_multiple_files(self):
        try:
            src_dir = _create_sandbox_dir()
            src_fs = open_fs("osfs://" + src_dir)
            src_fs.makedirs("foo/bar")
            src_fs.makedirs("foo/empty")
            src_fs.touch("test.txt")
            src_fs.touch("foo/bar/baz.txt")

            dst_dir = _create_sandbox_dir()
            dst_fs = open_fs("osfs://" + dst_dir)

            fs.copy.copy_dir_if(src_fs, "/foo", dst_fs, "/", condition="newer")

            self.assertTrue(dst_fs.isdir("bar"))
            self.assertTrue(dst_fs.isdir("empty"))
            self.assertTrue(dst_fs.isfile("bar/baz.txt"))
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)


class TestCopyIfExists(unittest.TestCase):
    copy_if_condition = "exists"

    def test_copy_file_if_same_fs(self):
        src_fs = open_fs("mem://")
        src_fs.makedir("foo2").touch("exists")
        src_fs.makedir("foo1").touch("test1.txt")
        self.assertFalse(
            fs.copy.copy_file_if(
                src_fs,
                "foo1/test1.txt",
                src_fs,
                "foo2/test1.txt.copy",
                self.copy_if_condition,
            )
        )
        self.assertTrue(
            fs.copy.copy_file_if(
                src_fs, "foo1/test1.txt", src_fs, "foo2/exists", self.copy_if_condition
            )
        )
        self.assertFalse(src_fs.exists("foo2/test1.txt.copy"))

    def test_copy_file_if_dst_doesnt_exists(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            dst_dir = _create_sandbox_dir()

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            copied = fs.copy.copy_file_if(
                src_fs, "/file1.txt", dst_fs, "/file1.txt", self.copy_if_condition
            )

            self.assertFalse(copied)
            self.assertFalse(dst_fs.exists("/file1.txt"))
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_file_if_dst_exists(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            dst_dir = _create_sandbox_dir()
            dst_file1 = _touch(dst_dir, "file1.txt")
            _write_file(dst_file1)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            self.assertTrue(dst_fs.exists("/file1.txt"))

            copied = fs.copy.copy_file_if(
                src_fs, "/file1.txt", dst_fs, "/file1.txt", self.copy_if_condition
            )

            self.assertTrue(copied)
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_fs_if(self):
        try:
            dst_dir = _create_sandbox_dir()
            dst_file1 = _touch(dst_dir, "file1.txt")
            _write_file(dst_file1)

            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            src_file2 = _touch(src_dir, "file2.txt")
            _write_file(src_file1)
            _write_file(src_file2)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            self.assertTrue(dst_fs.exists("/file1.txt"))

            copied = []

            def on_copy(src_fs, src_path, dst_fs, dst_path):
                copied.append(dst_path)

            fs.copy.copy_fs_if(
                src_fs, dst_fs, on_copy=on_copy, condition=self.copy_if_condition
            )

            self.assertEqual(copied, ["/file1.txt"])
            self.assertTrue(dst_fs.exists("/file1.txt"))
            self.assertFalse(dst_fs.exists("/file2.txt"))

            src_fs.close()
            dst_fs.close()

        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_dir_if(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            src_file2 = _touch(src_dir, os.path.join("one_level_down", "file2.txt"))
            _write_file(src_file2)

            dst_dir = _create_sandbox_dir()
            mkdirp(os.path.join(dst_dir, "target_dir"))
            dst_file1 = _touch(dst_dir, os.path.join("target_dir", "file1.txt"))
            _write_file(dst_file1)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            copied = []

            def on_copy(src_fs, src_path, dst_fs, dst_path):
                copied.append(dst_path)

            fs.copy.copy_dir_if(
                src_fs,
                "/",
                dst_fs,
                "/target_dir/",
                on_copy=on_copy,
                condition=self.copy_if_condition,
            )

            self.assertEqual(copied, ["/target_dir/file1.txt"])
            self.assertFalse(dst_fs.exists("/target_dir/one_level_down/file2.txt"))

            src_fs.close()
            dst_fs.close()
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_dir_if_same_fs(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "src" + os.sep + "file1.txt")
            _write_file(src_file1)

            _create_sandbox_dir(home=src_dir)

            src_fs = open_fs("osfs://" + src_dir)

            copied = []

            def on_copy(src_fs, src_path, dst_fs, dst_path):
                copied.append(dst_path)

            fs.copy.copy_dir_if(
                src_fs, "/src", src_fs, "/dst", on_copy=on_copy, condition="newer"
            )

            self.assertEqual(copied, ["/dst/file1.txt"])
            self.assertTrue(src_fs.exists("/dst/file1.txt"))

            src_fs.close()

        finally:
            shutil.rmtree(src_dir)

    def test_copy_dir_if_multiple_files(self):
        try:
            src_dir = _create_sandbox_dir()
            src_fs = open_fs("osfs://" + src_dir)
            src_fs.makedirs("foo/bar")
            src_fs.makedirs("foo/empty")
            src_fs.touch("test.txt")
            src_fs.touch("foo/bar/baz.txt")

            dst_dir = _create_sandbox_dir()
            dst_fs = open_fs("osfs://" + dst_dir)

            fs.copy.copy_dir_if(src_fs, "/foo", dst_fs, "/", condition="newer")

            self.assertTrue(dst_fs.isdir("bar"))
            self.assertTrue(dst_fs.isdir("empty"))
            self.assertTrue(dst_fs.isfile("bar/baz.txt"))
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)


class TestCopyIfNotExists(unittest.TestCase):
    copy_if_condition = "not_exists"

    def test_copy_file_if_same_fs(self):
        src_fs = open_fs("mem://")
        src_fs.makedir("foo2").touch("exists")
        src_fs.makedir("foo1").touch("test1.txt")
        self.assertTrue(
            fs.copy.copy_file_if(
                src_fs,
                "foo1/test1.txt",
                src_fs,
                "foo2/test1.txt.copy",
                self.copy_if_condition,
            )
        )
        self.assertFalse(
            fs.copy.copy_file_if(
                src_fs, "foo1/test1.txt", src_fs, "foo2/exists", self.copy_if_condition
            )
        )
        self.assertTrue(src_fs.exists("foo2/test1.txt.copy"))

    def test_copy_file_if_dst_doesnt_exists(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            dst_dir = _create_sandbox_dir()

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            copied = fs.copy.copy_file_if(
                src_fs, "/file1.txt", dst_fs, "/file1.txt", self.copy_if_condition
            )

            self.assertTrue(copied)
            self.assertTrue(dst_fs.exists("/file1.txt"))
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_file_if_dst_exists(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            dst_dir = _create_sandbox_dir()
            dst_file1 = _touch(dst_dir, "file1.txt")
            _write_file(dst_file1)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            self.assertTrue(dst_fs.exists("/file1.txt"))

            copied = fs.copy.copy_file_if(
                src_fs, "/file1.txt", dst_fs, "/file1.txt", self.copy_if_condition
            )

            self.assertFalse(copied)
            self.assertTrue(dst_fs.exists("/file1.txt"))
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_fs_if(self):
        try:
            dst_dir = _create_sandbox_dir()
            dst_file1 = _touch(dst_dir, "file1.txt")
            _write_file(dst_file1)

            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            src_file2 = _touch(src_dir, "file2.txt")
            _write_file(src_file1)
            _write_file(src_file2)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            self.assertTrue(dst_fs.exists("/file1.txt"))

            copied = []

            def on_copy(src_fs, src_path, dst_fs, dst_path):
                copied.append(dst_path)

            fs.copy.copy_fs_if(
                src_fs, dst_fs, on_copy=on_copy, condition=self.copy_if_condition
            )

            self.assertEqual(copied, ["/file2.txt"])
            self.assertTrue(dst_fs.exists("/file1.txt"))
            self.assertTrue(dst_fs.exists("/file2.txt"))

            src_fs.close()
            dst_fs.close()

        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_dir_if(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "file1.txt")
            _write_file(src_file1)

            src_file2 = _touch(src_dir, os.path.join("one_level_down", "file2.txt"))
            _write_file(src_file2)

            dst_dir = _create_sandbox_dir()
            mkdirp(os.path.join(dst_dir, "target_dir"))
            dst_file1 = _touch(dst_dir, os.path.join("target_dir", "file1.txt"))
            _write_file(dst_file1)

            src_fs = open_fs("osfs://" + src_dir)
            dst_fs = open_fs("osfs://" + dst_dir)

            copied = []

            def on_copy(src_fs, src_path, dst_fs, dst_path):
                copied.append(dst_path)

            fs.copy.copy_dir_if(
                src_fs,
                "/",
                dst_fs,
                "/target_dir/",
                on_copy=on_copy,
                condition=self.copy_if_condition,
            )

            self.assertEqual(copied, ["/target_dir/one_level_down/file2.txt"])
            self.assertTrue(dst_fs.exists("/target_dir/file1.txt"))
            self.assertTrue(dst_fs.exists("/target_dir/one_level_down/file2.txt"))

            src_fs.close()
            dst_fs.close()
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)

    def test_copy_dir_if_same_fs(self):
        try:
            src_dir = _create_sandbox_dir()
            src_file1 = _touch(src_dir, "src" + os.sep + "file1.txt")
            _write_file(src_file1)

            _create_sandbox_dir(home=src_dir)

            src_fs = open_fs("osfs://" + src_dir)

            copied = []

            def on_copy(src_fs, src_path, dst_fs, dst_path):
                copied.append(dst_path)

            fs.copy.copy_dir_if(
                src_fs, "/src", src_fs, "/dst", on_copy=on_copy, condition="newer"
            )

            self.assertEqual(copied, ["/dst/file1.txt"])
            self.assertTrue(src_fs.exists("/dst/file1.txt"))

            src_fs.close()

        finally:
            shutil.rmtree(src_dir)

    def test_copy_dir_if_multiple_files(self):
        try:
            src_dir = _create_sandbox_dir()
            src_fs = open_fs("osfs://" + src_dir)
            src_fs.makedirs("foo/bar")
            src_fs.makedirs("foo/empty")
            src_fs.touch("test.txt")
            src_fs.touch("foo/bar/baz.txt")

            dst_dir = _create_sandbox_dir()
            dst_fs = open_fs("osfs://" + dst_dir)

            fs.copy.copy_dir_if(src_fs, "/foo", dst_fs, "/", condition="newer")

            self.assertTrue(dst_fs.isdir("bar"))
            self.assertTrue(dst_fs.isdir("empty"))
            self.assertTrue(dst_fs.isfile("bar/baz.txt"))
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)


class _FailingOpenMemoryFS(MemoryFS):
    """MemoryFS that refuses to open one particular file."""

    def __init__(self, fail_path):
        super(_FailingOpenMemoryFS, self).__init__()
        self._fail_path = normpath(fail_path)

    def openbin(self, path, mode="r", buffering=-1, **options):
        if normpath(path) == self._fail_path:
            raise fs.errors.PermissionDenied(path)
        return super(_FailingOpenMemoryFS, self).openbin(
            path, mode=mode, buffering=buffering, **options
        )


class _FailingMoveMemoryFS(MemoryFS):
    """MemoryFS that fails the first move targeting one particular path."""

    def __init__(self, fail_path):
        super(_FailingMoveMemoryFS, self).__init__()
        self._fail_path = normpath(fail_path)
        self._failed = False

    def move(self, src_path, dst_path, overwrite=False, preserve_time=False):
        if not self._failed and normpath(dst_path) == self._fail_path:
            self._failed = True
            raise fs.errors.InsufficientStorage(dst_path)
        return super(_FailingMoveMemoryFS, self).move(
            src_path, dst_path, overwrite=overwrite, preserve_time=preserve_time
        )


class TestTransactionalCopy(unittest.TestCase):
    def _make_source(self, tag=""):
        src_fs = open_fs("mem://")
        src_fs.makedirs("foo/bar")
        src_fs.makedirs("foo/empty")
        src_fs.writetext("test.txt", "hello" + tag)
        src_fs.writetext("foo/bar/baz.txt", "baz" + tag)
        return src_fs

    def _assert_no_transaction_leftovers(self, dst_fs):
        leftovers = [
            name
            for name in dst_fs.listdir("/")
            if "__fs_transaction" in name
        ]
        self.assertEqual(leftovers, [])

    def test_transactional_copy_fs(self):
        src_fs = self._make_source()
        dst_fs = open_fs("mem://")
        fs.copy.copy_fs(src_fs, dst_fs, transactional=True)

        self.assertTrue(dst_fs.isdir("foo/bar"))
        self.assertTrue(dst_fs.isdir("foo/empty"))
        self.assertEqual(dst_fs.readtext("test.txt"), "hello")
        self.assertEqual(dst_fs.readtext("foo/bar/baz.txt"), "baz")
        self._assert_no_transaction_leftovers(dst_fs)

    def test_transactional_copy_dir(self):
        src_fs = self._make_source()
        dst_fs = open_fs("mem://")
        fs.copy.copy_dir(
            src_fs, "/foo", dst_fs, "/dst", transactional=True
        )

        self.assertTrue(dst_fs.isdir("/dst/bar"))
        self.assertTrue(dst_fs.isdir("/dst/empty"))
        self.assertEqual(dst_fs.readtext("/dst/bar/baz.txt"), "baz")
        self._assert_no_transaction_leftovers(dst_fs)

    def test_transactional_overwrites_existing(self):
        src_fs = self._make_source()
        dst_fs = open_fs("mem://")
        dst_fs.makedirs("foo/bar")
        dst_fs.writetext("test.txt", "old")
        dst_fs.writetext("foo/bar/baz.txt", "old-baz")
        dst_fs.writetext("untouched.txt", "untouched")

        fs.copy.copy_fs(src_fs, dst_fs, transactional=True)

        self.assertEqual(dst_fs.readtext("test.txt"), "hello")
        self.assertEqual(dst_fs.readtext("foo/bar/baz.txt"), "baz")
        self.assertEqual(dst_fs.readtext("untouched.txt"), "untouched")
        self._assert_no_transaction_leftovers(dst_fs)

    def test_transactional_preserve_time(self):
        namespaces = ("details", "modified")
        modified = datetime.datetime(2001, 2, 3, 4, 5, 6)

        src_fs = open_fs("mem://")
        src_fs.makedirs("foo/bar")
        src_fs.writetext("foo/bar/baz.txt", "baz")
        for path in ("foo", "foo/bar", "foo/bar/baz.txt"):
            src_fs.setinfo(
                path, {"details": {"modified": calendar.timegm(modified.timetuple())}}
            )

        dst_fs = open_fs("mem://")
        fs.copy.copy_fs(src_fs, dst_fs, transactional=True, preserve_time=True)

        for path in ("foo", "foo/bar", "foo/bar/baz.txt"):
            self.assertEqual(
                dst_fs.getinfo(path, namespaces).modified,
                src_fs.getinfo(path, namespaces).modified,
            )
        self._assert_no_transaction_leftovers(dst_fs)

    def test_transactional_on_copy(self):
        src_fs = self._make_source()
        dst_fs = open_fs("mem://")
        copied = []

        def _on_copy(src, src_path, dst, dst_path):
            copied.append((src_path, dst_path))
            self.assertTrue(dst.isfile(dst_path))

        fs.copy.copy_fs(
            src_fs, dst_fs, transactional=True, on_copy=_on_copy
        )

        self.assertEqual(
            sorted(dst_path for _, dst_path in copied),
            ["/foo/bar/baz.txt", "/test.txt"],
        )

    def test_transactional_rollback_on_source_error(self):
        src_fs = _FailingOpenMemoryFS("/bad.txt")
        src_fs.makedirs("dir")
        src_fs.writetext("good.txt", "good")
        src_fs.writetext("bad.txt", "bad")
        dst_fs = open_fs("mem://")
        dst_fs.writetext("keep.txt", "old-content")

        with self.assertRaises(fs.errors.TransactionalSourceError) as context:
            fs.copy.copy_fs(src_fs, dst_fs, transactional=True)

        error = context.exception
        self.assertEqual(error.src_path, "/")
        self.assertEqual(error.dst_path, "/")
        self.assertTrue(error.completed_steps)
        self.assertIn("staged 'good.txt'", error.completed_steps)
        self.assertIsInstance(error, fs.errors.TransactionalCopyFailed)
        self.assertIsInstance(error.exc, fs.errors.PermissionDenied)

        self.assertEqual(dst_fs.listdir("/"), ["keep.txt"])
        self.assertEqual(dst_fs.readtext("keep.txt"), "old-content")
        self._assert_no_transaction_leftovers(dst_fs)

    def test_transactional_rollback_on_destination_error(self):
        src_fs = open_fs("mem://")
        for name in ("a.txt", "b.txt", "c.txt"):
            src_fs.writetext(name, "new-" + name)
        dst_fs = _FailingMoveMemoryFS("/b.txt")
        for name in ("a.txt", "b.txt", "c.txt"):
            dst_fs.writetext(name, "old-" + name)

        with self.assertRaises(fs.errors.TransactionalDestinationError) as context:
            fs.copy.copy_fs(src_fs, dst_fs, transactional=True)

        error = context.exception
        self.assertEqual(error.src_path, "/")
        self.assertEqual(error.dst_path, "/")
        self.assertTrue(
            all(step.startswith("staged") for step in error.completed_steps[:3])
        )
        self.assertIn("committed 'a.txt'", error.completed_steps)
        self.assertIsInstance(error.exc, fs.errors.InsufficientStorage)

        self.assertEqual(sorted(dst_fs.listdir("/")), ["a.txt", "b.txt", "c.txt"])
        for name in ("a.txt", "b.txt", "c.txt"):
            self.assertEqual(dst_fs.readtext(name), "old-" + name)
        self._assert_no_transaction_leftovers(dst_fs)

    def test_transactional_read_only_destination_fails_early(self):
        underlying = open_fs("mem://")
        dst_fs = read_only(underlying)
        src_fs = self._make_source()

        with self.assertRaises(fs.errors.TransactionalDestinationError):
            fs.copy.copy_fs(src_fs, dst_fs, transactional=True)

        self.assertEqual(underlying.listdir("/"), [])

    @unittest.skipIf(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        "permissions are bypassed when running as root",
    )
    def test_transactional_unwritable_destination_fails_early(self):
        dst_dir = _create_sandbox_dir()
        src_dir = _create_sandbox_dir()
        try:
            src_fs = open_fs("osfs://" + src_dir)
            src_fs.writetext("a.txt", "a")
            src_fs.writetext("b.txt", "b")
            dst_fs = open_fs("osfs://" + dst_dir)
            os.chmod(dst_dir, 0o550)

            with self.assertRaises(fs.errors.TransactionalDestinationError):
                fs.copy.copy_fs(src_fs, dst_fs, transactional=True)

            self.assertEqual(os.listdir(dst_dir), [])
        finally:
            os.chmod(dst_dir, 0o750)
            shutil.rmtree(dst_dir)
            shutil.rmtree(src_dir)

    def test_transactional_lock_contention(self):
        src_fs = self._make_source()
        dst_fs = open_fs("mem://")
        dst_fs.makedir("/__fs_transaction_lock__")

        with self.assertRaises(fs.errors.TransactionalDestinationError) as context:
            fs.copy.copy_fs(src_fs, dst_fs, transactional=True)

        self.assertEqual(context.exception.src_path, "/")
        self.assertEqual(context.exception.dst_path, "/")
        self.assertEqual(
            dst_fs.listdir("/"), ["__fs_transaction_lock__"]
        )

    def test_transactional_concurrent_copies(self):
        def _make_src(tag, count=300):
            src_fs = open_fs("mem://")
            for index in range(count):
                src_fs.writetext("f%03d.txt" % index, tag)
            return src_fs

        dst_fs = open_fs("mem://")
        barrier = threading.Barrier(2)
        results = []

        def _worker(tag):
            barrier.wait()
            try:
                fs.copy.copy_fs(_make_src(tag), dst_fs, transactional=True)
            except fs.errors.TransactionalCopyFailed:
                results.append("failed")
            else:
                results.append("succeeded:" + tag)

        thread1 = threading.Thread(target=_worker, args=("one",))
        thread2 = threading.Thread(target=_worker, args=("two",))
        thread1.start()
        thread2.start()
        thread1.join()
        thread2.join()

        self.assertEqual(len(results), 2)
        self.assertEqual(len([r for r in results if r.startswith("succeeded")]), 1)
        self.assertIn("failed", results)

        winner = results[0][-3:] if results[0].startswith("succeeded") else results[1][-3:]
        for index in range(300):
            self.assertEqual(dst_fs.readtext("f%03d.txt" % index), winner)
        self._assert_no_transaction_leftovers(dst_fs)

    def test_transactional_copy_osfs(self):
        src_dir = _create_sandbox_dir()
        dst_dir = _create_sandbox_dir()
        try:
            src_fs = open_fs("osfs://" + src_dir)
            src_fs.makedirs("foo/bar")
            src_fs.writetext("foo/bar/baz.txt", "baz")
            src_fs.writetext("top.txt", "new")
            dst_fs = open_fs("osfs://" + dst_dir)
            dst_fs.writetext("top.txt", "old")

            fs.copy.copy_fs(
                src_fs, dst_fs, transactional=True, preserve_time=True
            )

            self.assertEqual(
                open(os.path.join(dst_dir, "top.txt")).read(), "new"
            )
            self.assertTrue(
                os.path.isfile(os.path.join(dst_dir, "foo/bar/baz.txt"))
            )
            self.assertEqual(os.listdir(dst_dir).count("__fs_transaction_lock__"), 0)
        finally:
            shutil.rmtree(src_dir)
            shutil.rmtree(dst_dir)


if __name__ == "__main__":
    unittest.main()
