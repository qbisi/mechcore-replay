#!/usr/bin/env python3
"""Tournament CI: select additions, verify whole matches, reconcile match issues.

Run with `uv run --with pyyaml python .github/scripts/tournaments.py --help`.
The workflow builds mechcore master once; this script never builds or uses a
game backend. Raw reports and stderr stay in the workflow artifacts.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml

BEGIN = "<!-- tournament-verification:start -->"
END = "<!-- tournament-verification:end -->"
CORPUS = "qbisi/mechcore-replay"
ISSUES = "qbisi/mechcore"


def match_path(value):
    path = PurePosixPath(value)
    if (len(path.parts) != 3 or path.parts[0] != "tournaments"
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}-(asia|europe|americas)", path.parts[1])
            or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.yaml", path.name)):
        raise ValueError(f"not a tournament match path: {value!r}")
    return path


def git(*args):
    return subprocess.check_output(["git", *args])


def select(base, single):
    if single:
        paths = [str(match_path(single))]
        if not Path(single).is_file():
            raise ValueError(f"match does not exist: {single}")
    else:
        if not re.fullmatch(r"[0-9a-f]{40}", base):
            raise ValueError("base must be a full 40-character corpus commit SHA")
        if base == "0" * 40:
            data = git("ls-tree", "-r", "--name-only", "-z", "HEAD", "--", "tournaments/")
        else:
            # Fetch only two trees, not the old native archive's full history.
            if subprocess.run(["git", "cat-file", "-e", f"{base}^{{commit}}"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                git("fetch", "--no-tags", "--depth=1", "origin", base)
            data = git("diff", "--no-renames", "--diff-filter=A", "--name-only", "-z", base,
                       "HEAD", "--", "tournaments/")
        paths = sorted(str(match_path(name)) for name in data.decode().split("\0")
                       if name.endswith(".yaml"))
    size = max(16, math.ceil(len(paths) / 128))
    matrix = {"include": [{"shard": index // size, "matches": paths[index:index + size]}
                          for index in range(0, len(paths), size)]}
    return {"matches": paths, "matrix": matrix, "any": bool(paths)}


def run_match(binary, name, directory, timeout):
    path = Path(str(match_path(name)))
    destination = directory / path.with_suffix("")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = {"match": name, "status": "error"}
    stdout, stderr, code = "", "", None
    try:
        data = path.read_bytes()
        header = next(yaml.load_all(data, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)))
        if not isinstance(header, dict) or header.get("kind") != "match":
            raise ValueError("tournament input is not a match document")
        result.update(sha256=hashlib.sha256(data).hexdigest(), build=header.get("build"))
        process = subprocess.run([str(binary), "verify", "--backend", "simulator", name],
                                 capture_output=True, text=True, timeout=timeout)
        stdout, stderr, code = process.stdout, process.stderr, process.returncode
        lines = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            raise ValueError(f"expected one verify report, got {len(lines)}")
        report = lines[0]
        if (not isinstance(report, dict) or report.get("schema") != "mechcore.verify-result"
                or type(report.get("valid")) is not bool
                or report.get("kind") not in ("match", "unreadable")
                or report.get("path") != name
                or code != (0 if report["valid"] else 1)
                or (report["valid"] and report["kind"] != "match")):
            raise ValueError("verify report and exit status do not describe a completed match check")
        result.update(status="passed" if report["valid"] else "diverged", report=report)
    except subprocess.TimeoutExpired as error:
        stdout = (error.stdout or b"").decode() if isinstance(error.stdout, bytes) else error.stdout or ""
        stderr = (error.stderr or b"").decode() if isinstance(error.stderr, bytes) else error.stderr or ""
        result["error"] = f"match verify exceeded {timeout} seconds"
    except (OSError, ValueError, StopIteration, yaml.YAMLError) as error:
        result["error"] = str(error)
    result["exit_code"] = code
    Path(str(destination) + ".stdout.jsonl").write_text(stdout)
    Path(str(destination) + ".stderr.log").write_text(stderr)
    Path(str(destination) + ".exit-code").write_text(str(code) + "\n")
    Path(str(destination) + ".json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"{result['status']}: {name}", flush=True)
    return result


class GitHub:
    def __init__(self, token):
        if not token:
            raise ValueError("MECHCORE_ISSUES_TOKEN is required: grant Issues read/write on qbisi/mechcore")
        self.token = token

    def request(self, method, path, value=None, attempts=4):
        body = None if value is None else json.dumps(value).encode()
        for attempt in range(attempts):
            request = Request("https://api.github.com/" + path, data=body, method=method,
                              headers={"Authorization": f"Bearer {self.token}",
                                       "Accept": "application/vnd.github+json",
                                       "X-GitHub-Api-Version": "2022-11-28",
                                       "Content-Type": "application/json"})
            try:
                with urlopen(request, timeout=30) as response:
                    return json.load(response)
            except HTTPError as error:
                retry = error.code in (429, 500, 502, 503, 504) or (
                    error.code == 403 and (error.headers.get("Retry-After")
                                          or error.headers.get("X-RateLimit-Remaining") == "0"))
                if not retry or attempt == attempts - 1:
                    raise
            except (URLError, TimeoutError, ConnectionError):
                if attempt == attempts - 1:
                    raise
            time.sleep(min(2 ** attempt, 8))

    def issues(self):
        found, page = [], 1
        while True:
            items = self.request("GET", f"repos/{ISSUES}/issues?state=all&labels=match&sort=created&direction=asc&per_page=100&page={page}")
            found.extend(item for item in items if "pull_request" not in item)
            if len(items) < 100:
                return found
            page += 1

    def create_issue(self, identity, body, title):
        # A lost POST response may already have created the issue. Refresh the
        # stable match marker before retrying; never blindly retry creation.
        for attempt in range(4):
            existing = next((item for item in self.issues() if identity in (item.get("body") or "")), None)
            if existing:
                return existing
            try:
                return self.request("POST", f"repos/{ISSUES}/issues",
                                    {"title": title, "body": body, "labels": ["match"]}, attempts=1)
            except HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise
            except (URLError, TimeoutError, ConnectionError):
                if attempt == 3:
                    raise
            time.sleep(2 ** attempt)


def marker(name):
    return f"<!-- mechcore-tournament-match:{match_path(name).stem} -->"


def issue_body(result, metadata):
    name = result["match"]
    report = result["report"]
    link = f"https://github.com/{CORPUS}/blob/{metadata['corpus_commit']}/{quote(name)}"
    lines = [BEGIN, f"Match: [{Path(name).stem}]({link})", "",
             f"Simulator: [mechcore master `{metadata['mechcore_commit']}`]"
             f"(https://github.com/{ISSUES}/commit/{metadata['mechcore_commit']}); release build, simulator backend.",
             f"Corpus commit: `{metadata['corpus_commit']}`; match SHA-256: `{result['sha256']}`.",
             f"Match build: `{result.get('build') or 'omitted'}`; simulator game version: `{metadata['game_version']}`.",
             f"Full JSON, stdout and stderr: [workflow artifacts]({metadata['run_url']}).", "",
             "Latest whole-match result: **" + result["status"] + "**."]
    if report.get("error"):
        lines += ["", "First reported failure:", "", "```text", str(report["error"])[:4000], "```"]
    failed = [item for item in (report.get("fights") or {}).get("rounds", [])
              if item.get("result") not in ("equal", "warning")]
    if failed:
        lines += ["", "| round | result |", "| ---: | --- |"]
        lines += [f"| {item['round']} | {item['result']} |" for item in failed]
    lines += ["", "Reproduce with the pinned simulator commit and linked match:", "", "```sh",
              "git clone https://github.com/qbisi/mechcore.git", "cd mechcore",
              f"git checkout {metadata['mechcore_commit']}",
              "cargo build --release -p mechcore --no-default-features",
              f"curl -fL https://raw.githubusercontent.com/{CORPUS}/{metadata['corpus_commit']}/{quote(name)} -o match.yaml",
              "target/release/mechcore verify --backend simulator match.yaml", "```", "",
              "Resolution requires the complete match command to exit 0 with a `kind: match`, `valid: true` report. "
              "Fixing only the first failing round is not enough. Re-run Tournament matches in mechcore-replay "
              f"with `match` set to `{name}` to check current mechcore master; CI closes this issue only after that full check passes. "
              "Warnings retain mechcore verify's documented semantics and are preserved in the artifacts.", END]
    return "\n".join(lines)


def reconcile(api, metadata):
    issues = api.issues()
    errors = 0
    for result in metadata["results"]:
        if result["status"] == "error":
            errors += 1
            print(f"infrastructure/input error; no issue mutation: {result['match']}")
            continue
        report = result["report"]
        passed = result["status"] == "passed"
        if (result["status"] not in ("passed", "diverged")
                or report.get("schema") != "mechcore.verify-result"
                or type(report.get("valid")) is not bool or report["valid"] != passed
                or report.get("path") != result["match"]
                or result.get("exit_code") != (0 if passed else 1)
                or report.get("kind") not in (("match",) if passed else ("match", "unreadable"))):
            raise ValueError(f"incomplete or contradictory whole-match evidence: {result['match']}")
        identity = marker(result["match"])
        issue = next((item for item in issues if identity in (item.get("body") or "")), None)
        if not issue and result["status"] == "passed":
            continue
        block = issue_body(result, metadata)
        if not issue:
            issue = api.create_issue(identity, identity + "\n\n" + block,
                                     f"match: {Path(result['match']).stem} does not verify")
            issues.append(issue)
        result["issue_url"] = issue["html_url"]
        old = issue.get("body") or ""
        if BEGIN in old and END in old:
            body = old[:old.index(BEGIN)] + block + old[old.index(END) + len(END):]
        else:
            body = old + "\n\n" + block
        state = "closed" if passed else "open"
        if body != old or issue.get("state") != state:
            api.request("PATCH", f"repos/{ISSUES}/issues/{issue['number']}",
                        {"body": body, "state": state,
                         "state_reason": "completed" if passed else "reopened"})
        result["issue_state"] = state
        print(f"{'resolved' if passed else 'open'}: {issue['html_url']}", flush=True)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    selection = commands.add_parser("select")
    selection.add_argument("--base", default="")
    selection.add_argument("--match", default="")
    verification = commands.add_parser("verify")
    verification.add_argument("--binary", type=Path, required=True)
    verification.add_argument("--build-dir", type=Path, required=True)
    verification.add_argument("--matches", type=json.loads, required=True)
    verification.add_argument("--out", type=Path, default=Path("reports"))
    verification.add_argument("--timeout", type=int, default=600)
    publication = commands.add_parser("report")
    publication.add_argument("--results", type=Path, default=Path("reports/results.json"))
    args = parser.parse_args()
    if args.command == "select":
        if args.base and args.match:
            raise ValueError("select either a base commit or a single match, not both")
        chosen = select(args.base, args.match)
        chosen.update(base=args.base, single=args.match)
        print(json.dumps(chosen))
        Path("reports").mkdir(exist_ok=True)
        Path("reports/selection.json").write_text(json.dumps(chosen, indent=2) + "\n")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                output.write(f"any={str(chosen['any']).lower()}\nmatrix={json.dumps(chosen['matrix'])}\n")
        return 0
    if args.command == "report":
        metadata = json.loads(args.results.read_text())
        try:
            errors = reconcile(GitHub(os.environ.get("MECHCORE_ISSUES_TOKEN")), metadata)
            metadata["issue_reporting"] = {"status": "complete"}
            return int(bool(errors))
        except Exception as error:
            metadata["issue_reporting"] = {"status": "error", "error": str(error)}
            raise
        finally:
            args.results.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    args.out.mkdir(parents=True, exist_ok=True)
    metadata = {"corpus_commit": os.environ["GITHUB_SHA"],
                "mechcore_commit": (args.build_dir / "mechcore-commit").read_text().strip(),
                "game_version": (args.build_dir / "GAME_VERSION").read_text().strip(),
                "run_url": f"https://github.com/{CORPUS}/actions/runs/{os.environ['GITHUB_RUN_ID']}"}
    with ThreadPoolExecutor(max_workers=2) as workers:
        metadata["results"] = list(workers.map(lambda name: run_match(args.binary.resolve(), name, args.out, args.timeout), args.matches))
    (args.out / "results.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    totals = {status: sum(item["status"] == status for item in metadata["results"])
              for status in ("passed", "diverged", "error")}
    print(json.dumps(totals))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f"mechcore master `{metadata['mechcore_commit']}` (release): {totals}\n")
    return int(bool(totals["diverged"] or totals["error"]))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, URLError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
