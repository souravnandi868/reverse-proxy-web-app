"""Capture exact-commit release evidence without deploying or packaging anything.

Run: python tests/validate_fix3.py --output /path/outside/checkout
On Linux, supply --nginx /usr/sbin/nginx. All required gates remain mandatory.
"""
import argparse
import glob
import json
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--nginx", default=shutil.which("nginx"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    if output == root or root in output.parents:
        parser.error("Evidence must be outside the source checkout.")
    output.mkdir(parents=True, exist_ok=True)
    def git(*arguments):
        return subprocess.check_output(["git", *arguments], cwd=root, text=True).strip()
    commit = git("rev-parse", "HEAD")
    if git("status", "--porcelain"):
        parser.error("Commit all source changes before release validation.")
    results = []
    evidence = output / "validation-attempt.txt"
    with evidence.open("w", encoding="utf-8") as log:
        log.write(f"Source commit: {commit}\nPlatform: {platform.platform()}\nPython: {sys.version}\n")
        def run(name, command, unavailable=None):
            log.write(f"\n=== {name} ===\nCommand: {json.dumps(command)}\n")
            if unavailable:
                code, content, status = None, unavailable, "NOT RUN"
            else:
                try:
                    completed = subprocess.run(command, cwd=root, stdout=subprocess.PIPE,
                                               stderr=subprocess.STDOUT, text=True, errors="replace")
                    code, content = completed.returncode, completed.stdout
                    status = "PASS" if code == 0 else "FAIL"
                except OSError as error:
                    code, content, status = None, str(error), "NOT RUN"
            log.write(content + f"\nExit code: {code if code is not None else 'N/A (not executed)'}\nStatus: {status}\n")
            log.flush()
            counts = re.findall(r"Ran (\d+) tests?", content)
            node_counts = re.findall(r"# tests (\d+)", content)
            results.append({"name": name, "command": command, "status": status, "exit_code": code,
                            "test_count": int((counts or node_counts)[-1]) if counts or node_counts else None})
            print(f"{name}: {status}", flush=True)
        py = sys.executable
        run("Django system check", [py, "manage.py", "check"])
        run("Migration drift", [py, "manage.py", "makemigrations", "--check", "--dry-run"])
        run("Django regressions", [py, "manage.py", "test", "--noinput", "--verbosity", "2"])
        run("Migration 0014", [py, "manage.py", "test", "proxies.test_captive_migration", "--noinput", "--verbosity", "2"])
        run("Node regressions", ["node", "--test", *sorted(glob.glob("tests/*.test.cjs", root_dir=root))])
        unavailable = None
        if platform.system() != "Linux":
            unavailable = "Required Linux NGINX host unavailable; Windows results cannot satisfy this gate."
        elif not args.nginx:
            unavailable = "NGINX binary unavailable."
        run("Linux NGINX version", [args.nginx or "/usr/sbin/nginx", "-V"], unavailable)
        for browser in (False, True):
            command = [py, "tests/nginx_captive_integration.py", args.nginx or "/usr/sbin/nginx"]
            if browser:
                command.append("--browser")
            run("Linux NGINX" + (" browser" if browser else " integration"), command, unavailable)
        run("Console browser regressions", [py, "manage.py", "test", "tests.browser_navigation", "--noinput", "--verbosity", "2"])
        run("Backup replication", [], "No replication implementation or isolated regression supplied; external gate unresolved.")
        run("Existing captive-user review", [], "Production was not accessed. Existing-user review is required before migration deployment.")
        clean = git("status", "--porcelain") == "" and git("rev-parse", "HEAD") == commit
        log.write(f"\nSource commit unchanged and clean: {clean}\n")
    ready = clean and all(result["status"] == "PASS" for result in results)
    report = {"status": "PASS" if ready else "BLOCKED", "exact_commit": commit,
              "migration_list": sorted(path.stem for path in (root / "proxies/migrations").glob("[0-9]*.py")),
              "results": results, "source_unchanged": clean, "package_created": False,
              "wheelhouse_included": False, "evidence": evidence.name}
    (output / "validation-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if ready:
        evidence.rename(output / "complete-validation.txt")
    print(f"Release validation: {report['status']}", flush=True)
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
