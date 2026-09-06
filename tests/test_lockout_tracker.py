#!/usr/bin/env python3
"""Host contracts for unchanged production lockout methods with platform/clock stubs.

The real header and implementation are staged byte-for-byte. Only Android-only
headers, logging and the monotonic-clock provider are stubbed. This does not test
Binder sessions, credential validation, the vendor HAL or a physical fingerprint.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = 'aidl::android::hardware::biometrics::fingerprint'
HARNESS = r'''
#include "LockoutTracker.h"
#include "util/Util.h"
#include <cassert>
#include <cstring>
#include <new>
#include <cstdio>
using namespace aidl::android::hardware::biometrics::fingerprint;
int main() {
    using Mode = LockoutTracker::LockoutMode;
    // Fresh objects must not depend on allocator contents.
    alignas(LockoutTracker) unsigned char storage[sizeof(LockoutTracker)];
    std::memset(storage, 0xa5, sizeof(storage));
    auto* fresh = new (storage) LockoutTracker;
    assert(fresh->getMode() == Mode::kNone);
    assert(fresh->getLockoutTimeLeft() == 0);
    assert(fresh->toString().find("mFailedCount:0") != std::string::npos);
    fresh->~LockoutTracker();

    LockoutTracker tracker;
    for (int i = 1; i < LOCKOUT_TIMED_THRESHOLD; ++i) {
        tracker.addFailedAttempt();
        assert(tracker.getMode() == Mode::kNone);
    }
    tracker.addFailedAttempt();
    assert(tracker.getMode() == Mode::kTimed);
    assert(tracker.getLockoutTimeLeft() == LOCKOUT_TIMED_DURATION);
    LockoutTracker copied = tracker;
    assert(copied.getMode() == Mode::kTimed);
    assert(copied.getLockoutTimeLeft() == LOCKOUT_TIMED_DURATION);
    Util::now += (LOCKOUT_TIMED_DURATION - 1) * 1000000LL;
    assert(tracker.getMode() == Mode::kTimed);
    assert(tracker.getLockoutTimeLeft() == 1);
    Util::now += 1000000LL;
    assert(tracker.getMode() == Mode::kNone);
    assert(tracker.getLockoutTimeLeft() == 0);
    tracker.addFailedAttempt();
    assert(tracker.getMode() == Mode::kTimed);
    tracker.reset(false);
    assert(tracker.getMode() == Mode::kNone);
    tracker.addFailedAttempt();
    assert(tracker.getMode() == Mode::kTimed); // Counter was not cleared.
    tracker.reset(true);
    assert(tracker.getMode() == Mode::kNone);
    assert(tracker.getLockoutTimeLeft() == 0);
    for (int i = 1; i < LOCKOUT_PERMANENT_THRESHOLD; ++i) tracker.addFailedAttempt();
    assert(tracker.getMode() == Mode::kTimed);
    tracker.addFailedAttempt();
    assert(tracker.getMode() == Mode::kPermanent);
    Util::now += 100 * LOCKOUT_TIMED_DURATION * 1000000LL;
    assert(tracker.getMode() == Mode::kPermanent);
    tracker.reset(true);
    tracker.addFailedAttempt();
    assert(tracker.getMode() == Mode::kNone);
    puts("PASS: initialization, copy, timed/permanent thresholds, expiry and reset contracts");
}
'''


class LockoutTests(unittest.TestCase):
    def test_native_state_contract(self):
        compiler = os.environ.get('CXX', 'clang++')
        self.assertIsNotNone(shutil.which(compiler), f'{compiler} is required')
        source_root = Path(os.environ.get('LOCKOUT_SOURCE', ROOT / 'aidl/fingerprint'))
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            for name in ('LockoutTracker.h', 'LockoutTracker.cpp'):
                shutil.copyfile(source_root / name, stage / name)
            (stage / 'android').mkdir()
            # Match the existing transitive stream include for baseline reproduction.
            (stage / 'android/binder_to_string.h').write_text('#pragma once\n#include <sstream>\n')
            (stage / 'fingerprint.sysprop.h').write_text(
                '#pragma once\nnamespace android::fingerprint::xiaomi {}\n')
            (stage / 'Fingerprint.h').write_text(
                '#pragma once\n#include <iostream>\n#define LOG(level) std::clog\n')
            (stage / 'util').mkdir()
            (stage / 'util/Util.h').write_text(
                '#pragma once\n#include <cstdint>\nnamespace ' + NAMESPACE + ' {\n'
                'struct Util { inline static int64_t now = 1000000000LL;\n'
                'static int64_t getSystemNanoTime() { return now; }\n'
                'static bool hasElapsed(int64_t start, int64_t millis) {\n'
                'return (now - start) / 1000000LL >= millis; } }; }\n')
            (stage / 'test.cpp').write_text(HARNESS)
            binary = stage / 'test'
            subprocess.run([compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra',
                            '-Werror', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            '-I', str(stage), str(stage / 'LockoutTracker.cpp'),
                            str(stage / 'test.cpp'), '-o', str(binary)], check=True, timeout=60)
            subprocess.run([str(binary)], check=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
