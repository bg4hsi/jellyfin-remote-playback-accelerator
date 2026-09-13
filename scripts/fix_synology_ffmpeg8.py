#!/usr/bin/env python3
"""Repair the NAS detector's FFmpeg 7 -> 8 path; preserve a rollback copy."""
import os
import shutil
import signal
import subprocess
import sys
import time

path = '/usr/local/bin/jf-segment-status.py'
old = 'FFMPEG = "/var/packages/ffmpeg7/target/bin/ffmpeg"'
new = 'FFMPEG = "/var/packages/ffmpeg8/target/bin/ffmpeg"'
if os.geteuid() != 0:
    sys.exit('Run this script with sudo python3.')
if not os.path.isfile('/var/packages/ffmpeg8/target/bin/ffmpeg'):
    sys.exit('FFmpeg 8 not found; no changes made.')
with open(path, encoding='utf-8') as f:
    source = f.read()
if source.count(old) != 1:
    sys.exit('Expected original setting not found; no changes made.')
updated = source.replace(old, new, 1)
compile(updated, path, 'exec')
pids = []
for pid in os.listdir('/proc'):
    if not pid.isdigit():
        continue
    try:
        with open('/proc/' + pid + '/cmdline', 'rb') as f:
            args = f.read().split(b'\0')
        if len(args) > 1 and args[1] == path.encode() and b'python' in args[0]:
            pids.append(int(pid))
    except OSError:
        pass
if len(pids) != 1:
    sys.exit('Expected exactly one detector process; no changes made.')
backup = path + '.before-ffmpeg8-' + time.strftime('%Y%m%d-%H%M%S')
shutil.copy2(path, backup)
tmp = path + '.new-ffmpeg8'
with open(tmp, 'w', encoding='utf-8') as f:
    f.write(updated)
os.chmod(tmp, os.stat(path).st_mode & 0o777)
os.replace(tmp, path)
os.kill(pids[0], signal.SIGTERM)
for _ in range(50):
    if not os.path.exists('/proc/' + str(pids[0])):
        break
    time.sleep(0.1)
else:
    shutil.copy2(backup, path)
    sys.exit('Old detector did not stop; original file restored.')
with open('/var/log/jf-segment-status.log', 'ab') as log:
    proc = subprocess.Popen([sys.executable, path], stdin=subprocess.DEVNULL,
                            stdout=log, stderr=log, start_new_session=True)
time.sleep(1)
if proc.poll() is not None:
    shutil.copy2(backup, path)
    with open('/var/log/jf-segment-status.log', 'ab') as log:
        subprocess.Popen([sys.executable, path], stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True)
    sys.exit('Detector start failed; original file restored and restarted.')
print('Updated FFmpeg path and restarted detector. PID:', proc.pid)
print('Backup:', backup)
