#!/usr/bin/env python3
"""Hosted CI only: actual systemd version switch with synthetic connector.
Not an installer; no production credentials, network service or user data.
"""
import hashlib
import json
import signal
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]

def call(*args, check=True):
    p = subprocess.run(args, text=True, capture_output=True, timeout=20)
    if check and p.returncode:
        raise RuntimeError(f"{args[0]} failed ({p.returncode}): {p.stderr[-800:]}")
    return p

def require(condition, message):
    if not condition:
        raise AssertionError(message)

def main():
    require(os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
            and os.environ.get("RUNNER_TEMP")
            and os.geteuid() != 0
            and Path("/proc/1/comm").read_text().strip() == "systemd",
            "Hosted GitHub Linux runner only")
    suffix = secrets.token_hex(6)
    user = "dcr_" + suffix
    state_name = "dc-release-ci-" + suffix
    state = Path("/var/lib") / state_name
    app = Path("/opt") / state_name
    unit = state_name + ".service"
    unit_path = Path("/run/systemd/system") / unit
    require(not app.exists() and not state.exists() and not unit_path.exists(), "Fixture collision")
    require(call("getent", "passwd", user, check=False).returncode == 2, "Account collision")
    created_user = False
    created_app = False
    installed_unit = False
    other = subprocess.Popen(["/usr/bin/sleep", "120"])
    with tempfile.TemporaryDirectory(dir=os.environ["RUNNER_TEMP"]) as scratch:
        scratch = Path(scratch)
        def install(source, target, mode):
            call("sudo", "-n", "install", "-o", "root", "-g", "root", "-m", mode,
                 str(source), str(target))
        def status():
            return call("sudo", "-n", "systemctl", "is-active", unit, check=False).stdout.strip()
        def stop():
            call("sudo", "-n", "systemctl", "stop", unit)
        def read(name):
            return call("sudo", "-n", "cat", str(state / name)).stdout
        def health(version):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if status() == "active":
                    try:
                        invocation = call("sudo", "-n", "systemctl", "show", "-p", "InvocationID",
                                          "--value", unit).stdout.strip()
                        if invocation and read("health").strip() == version + " " + invocation:
                            pid = int(call("sudo", "-n", "systemctl", "show", "-p", "MainPID",
                                           "--value", unit).stdout)
                            if (pid > 0 and unit in Path(f"/proc/{pid}/cgroup").read_text()
                                    and status() == "active"
                                    and call("sudo", "-n", "systemctl", "show", "-p", "InvocationID",
                                             "--value", unit).stdout.strip() == invocation):
                                return True
                    except (OSError, RuntimeError, ValueError):
                        pass
                time.sleep(.15)
            return False
        def select(version):
            # Render an actual unit referencing an immutable version directory.
            nonlocal installed_unit
            root = app / version
            target = scratch / (version + ".service")
            call(sys.executable, str(ROOT / "service/render_service.py"),
                 "--app-root", str(root), "--service-user", user,
                 "--state-name", state_name, "--connector-entrypoint",
                 str(root / "connector"), "--output", str(target)) if not target.exists() else None
            # Atomic replace within the systemd unit directory; no enable.
            temporary = unit_path.with_name(unit_path.name + ".candidate")
            install(target, temporary, "0644")
            call("sudo", "-n", "mv", "-T", str(temporary), str(unit_path))
            installed_unit = True
            call("sudo", "-n", "systemctl", "daemon-reload")
            call("sudo", "-n", "systemd-analyze", "verify", str(unit_path))
        def start():
            return call("sudo", "-n", "systemctl", "start", unit, check=False)
        def snapshot():
            return {name: hashlib.sha256(read(name).encode()).hexdigest()
                    for name in ("synthetic-key", "devices.json", "recovery.json")}
        try:
            call("sudo", "-n", "useradd", "--system", "--no-create-home",
                 "--shell", "/usr/sbin/nologin", "--user-group", user)
            created_user = True
            call("sudo", "-n", "mkdir", "-m", "0755", str(app))
            created_app = True
            for version in ("v1", "v2", "broken"):
                root = app / version
                call("sudo", "-n", "mkdir", "-m", "0755", str(root))
                source = scratch / version
                body = ("#!/bin/sh\nset -eu\n"
                        'test "$DC_APP_ROOT" = "' + str(root) + '"\n'
                        'test "$(id -u)" != 0\n')
                if version == "broken":
                    body += "exit 42\n"
                else:
                    body += ('printf "%s %s\\n" "' + version + '" "$INVOCATION_ID" > "$DC_HOME/health.tmp"\n'
                             'mv "$DC_HOME/health.tmp" "$DC_HOME/health"\n'
                             'exec /usr/bin/sleep 120\n')
                source.write_text(body)
                install(source, root / "connector", "0755")
            select("v1")
            require(start().returncode == 0 and health("v1"), "Initial startup failed")
            for name, data in (("synthetic-key", "NOT_A_REAL_KEY"),
                               ("devices.json", '{"synthetic":true}'),
                               ("recovery.json", '{"state":"commit_unknown"}')):
                call("sudo", "-n", "-u", user, "/usr/bin/python3", "-c",
                     "import os,sys; fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);"
                     "os.write(fd,sys.argv[2].encode());os.fsync(fd);os.close(fd)",
                     str(state / name), data)
            before = snapshot()
            require(call("sudo", "-n", "-u", user, "test", "-w", str(app / "v1/connector"),
                         check=False).returncode == 1, "Service can modify code")
            stop()
            select("v2")
            require(start().returncode == 0 and health("v2"), "Healthy upgrade failed")
            require(snapshot() == before, "State modified by upgrade")
            stop()
            select("broken")
            start()
            require(not health("broken"), "Broken version incorrectly healthy")
            stop()
            select("v2")
            require(start().returncode == 0 and health("v2"), "Rollback failed")
            require(snapshot() == before, "State modified by rollback")
            # Simulate a controller crash across real service transitions.
            # The receipt belongs to the operator, outside service-writable state.
            pending = scratch / "pending.json"
            def record_pending():
                with pending.open("x") as handle:
                    json.dump({"previous": "v2", "candidate": "v1"}, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                fd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            def recover():
                require(json.loads(pending.read_text()) ==
                        {"previous": "v2", "candidate": "v1"}, "Invalid fixture receipt")
                stop()
                select("v2")
                require(start().returncode == 0 and health("v2"), "Crash rollback failed")
                require(snapshot() == before, "Crash rollback modified private state")
                pending.unlink()
            for phase in ("stopped", "selected", "started"):
                record_pending()
                pid = os.fork()
                if pid == 0:
                    try:
                        stop()
                        if phase != "stopped":
                            select("v1")
                        if phase == "started":
                            require(start().returncode == 0 and health("v1"), "Candidate failed")
                        os.kill(os.getpid(), signal.SIGKILL)
                    except BaseException:
                        os._exit(99)
                _, result = os.waitpid(pid, 0)
                require(os.WIFSIGNALED(result) and os.WTERMSIG(result) == signal.SIGKILL,
                        "Controller did not reach intended crash point")
                recover()
                print("PASS: controller SIGKILL after " + phase + "; prior service recovered", flush=True)
            require(other.poll() is None, "Unrelated process stopped")
            stop()
            # Rehearse removing only the service registration, retaining state and versions.
            call("sudo", "-n", "rm", "--", str(unit_path))
            installed_unit = False
            call("sudo", "-n", "systemctl", "daemon-reload")
            require(snapshot() == before, "Service removal destroyed state")
            require(all((app / v / "connector").exists() for v in ("v1", "v2", "broken")),
                    "Release versions lost")
            print("PASS: actual systemd install, healthy upgrade, failed startup, rollback, "
                  "service removal with state and versions retained", flush=True)
        finally:
            if installed_unit:
                call("sudo", "-n", "systemctl", "stop", unit, check=False)
                call("sudo", "-n", "systemctl", "reset-failed", unit, check=False)
                call("sudo", "-n", "rm", "-f", "--", str(unit_path), check=False)
            call("sudo", "-n", "rm", "-f", "--", str(unit_path) + ".candidate", check=False)
            call("sudo", "-n", "systemctl", "daemon-reload", check=False)
            # Destroy only this run's synthetic CI fixtures, never customer state.
            if created_app:
                call("sudo", "-n", "rm", "-rf", "--", str(app), check=False)
            if created_user:
                call("sudo", "-n", "rm", "-rf", "--", str(state), check=False)
                call("sudo", "-n", "userdel", user, check=False)
            if other.poll() is None:
                other.terminate()
            other.wait(timeout=5)

if __name__ == "__main__":
    main()
