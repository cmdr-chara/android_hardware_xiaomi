#!/usr/bin/env python3
"""Native tests of the production UdfpsSensor::readFd method, without Android/device access.

The class fields and Android logging are stubbed; a scanf wrapper checks its
bounded-string precondition before calling the real parser. The method body is
taken verbatim from Sensor.cpp. This is not a full HAL build or a sysfs test.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def method(source):
    signature = 'bool UdfpsSensor::readFd(const int fd) {'
    start = source.index(signature)
    end = source.index('\n}\n', start) + 2
    return source[start:end]


HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <unistd.h>
#define ALOGE(...) ((void)0)
class UdfpsSensor {
 public:
  int mScreenX = -1;
  int mScreenY = -1;
  bool readFd(int fd);
};
// libc scanf interceptors do not reliably diagnose unterminated input.
// Check the production buffer's string precondition before invoking libc.
static int boundedSscanf(const char* buffer, const char* format, int* x, int* y, int* state) {
  assert(std::memchr(buffer, '\0', 512) != nullptr);
  return std::sscanf(buffer, format, x, y, state);
}
#define sscanf boundedSscanf
@METHOD@
#undef sscanf
static void check(const std::string& data, bool pressed, int x = 0, int y = 0) {
  FILE* file = tmpfile();
  assert(file != nullptr);
  assert(fwrite(data.data(), 1, data.size(), file) == data.size());
  assert(fflush(file) == 0);
  UdfpsSensor sensor;
  assert(sensor.readFd(fileno(file)) == pressed);
  if (pressed) {
    assert(sensor.mScreenX == x);
    assert(sensor.mScreenY == y);
  }
  fclose(file);
}
int main() {
  check("1", true);
  check("1\n", true);
  check("0\n", false);
  check("610,2210,1\n", true, 610, 2210);
  check("610,2210,0\n", false);
  check("-1\n", false);
  check("610,2210", false);
  check("not-a-state\n", false);
  check("", false);
  // A non-NUL-terminated read which fills the old buffer must not scan past it.
  check(std::string(512, ' '), false);
  check(std::string(4096, ' '), false);
  UdfpsSensor sensor;
  assert(!sensor.readFd(-1));
  int pipefd[2];
  assert(pipe(pipefd) == 0);
  assert(!sensor.readFd(pipefd[0])); // lseek on a pipe fails; never block in read.
  close(pipefd[0]);
  close(pipefd[1]);
  int dirfd = open("/", O_RDONLY | O_DIRECTORY);
  assert(dirfd >= 0);
  assert(!sensor.readFd(dirfd));
  close(dirfd);
  puts("PASS: 14 UDFPS read cases (native, ASan/UBSan)");
}
'''


class UdfpsReadTests(unittest.TestCase):
    def test_native_read_contract(self):
        compiler = os.environ.get('CXX', 'clang++')
        self.assertIsNotNone(shutil.which(compiler), f'{compiler} is required')
        source_path = Path(os.environ.get('UDFPS_SOURCE', ROOT / 'sensors/v2/Sensor.cpp'))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'read_test.cpp'
            binary = Path(directory) / 'read_test'
            source.write_text(HARNESS.replace('@METHOD@', method(source_path.read_text())))
            subprocess.run([compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra',
                            '-Werror', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            str(source), '-o', str(binary)], check=True, timeout=60)
            subprocess.run([str(binary)], check=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
