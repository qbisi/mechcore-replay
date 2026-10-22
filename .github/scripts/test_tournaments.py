"""Exercise automation contracts using fake verify output; never simulate matches."""

from contextlib import contextmanager
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

import yaml

import tournaments as ci

NAME = 'tournaments/2026-10-17-asia/20261017--2235.yaml'


@contextmanager
def working_directory(path):
    before = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(before)


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()


def commit():
    command('git', 'add', '.')
    command('git', 'commit', '-m', 'test: seed automation input\n\nCo-Authored-By: GPT-6 <noreply@openai.com>')
    return command('git', 'rev-parse', 'HEAD')


def write_match(name=NAME):
    path = Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('kind: match\nbuild: test\n---\nkind: state\nround: 1\n')
    return path


def result(passed=False, name=NAME):
    return {'match': name, 'sha256': '123', 'build': 'test',
            'status': 'passed' if passed else 'diverged', 'exit_code': 0 if passed else 1,
            'report': {'schema': 'mechcore.verify-result', 'kind': 'match', 'valid': passed, 'path': name,
                       'error': None if passed else 'round 2 differs',
                       'fights': {'rounds': [{'round': 1, 'result': 'equal'},
                                             {'round': 2, 'result': 'equal' if passed else 'differs'}]}}}


def metadata(found):
    return {'results': [found], 'corpus_commit': 'a' * 40, 'mechcore_commit': 'b' * 40,
            'game_version': 'test', 'run_url': 'https://github.com/qbisi/mechcore-replay/actions/runs/1'}


class FakeGitHub:
    def __init__(self):
        self.kept = []
        self.calls = []

    def issues(self):
        return self.kept

    def create_issue(self, identity, body, title):
        item = {'number': len(self.kept) + 1, 'body': body, 'title': title,
                'html_url': 'https://example.com/issue', 'state': 'open'}
        self.calls.append(('create', body))
        # reconcile adds the newly created issue to its local index.
        return item

    def request(self, method, path, value=None):
        self.calls.append((method, copy.deepcopy(value)))
        assert method == 'PATCH'
        item = next(item for item in self.kept if item['number'] == int(path.rsplit('/', 1)[1]))
        item.update(value)
        return item


class SelectionTests(unittest.TestCase):
    def test_only_added_tournaments_and_no_rename_inference(self):
        with tempfile.TemporaryDirectory() as tmp, working_directory(tmp):
            command('git', 'init', '--initial-branch=master')
            command('git', 'config', 'user.name', 'Automation test')
            command('git', 'config', 'user.email', 'noreply@example.com')
            write_match()
            base = commit()
            Path(NAME).write_text('changed existing match\n')
            added = NAME.replace('2235', '2236')
            write_match(added)
            write_match('matches/test/spectator-r2.yaml')
            Path('README.md').write_text('irrelevant\n')
            commit()
            chosen = ci.select(base, '')
            self.assertEqual(chosen['matches'], [added])
            self.assertEqual(chosen['matrix']['include'][0]['matches'], [added])
            self.assertEqual(ci.select(command('git', 'rev-parse', 'HEAD'), '')['any'], False)
            self.assertEqual(ci.select('', NAME)['matches'], [NAME])
            self.assertEqual(len(ci.select('0' * 40, '')['matches']), 2)

    def test_unsafe_and_missing_inputs_are_rejected(self):
        for name in ['../match.yaml', 'matches/test/match.yaml', NAME + '/extra.yaml',
                     NAME.replace('asia', 'unknown'), NAME.replace('2235', 'bad name')]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                ci.match_path(name)
        with self.assertRaises(ValueError):
            ci.select('master', '')
        with self.assertRaises(ValueError):
            ci.select('', NAME.replace('2235', 'not-present'))

    def test_large_batch_stays_under_matrix_limit_without_losing_files(self):
        paths = [f'tournaments/2026-10-17-asia/{n}.yaml' for n in range(5000)]
        with patch.object(ci, 'git', return_value=('\0'.join(paths) + '\0').encode()):
            selected = ci.select('0' * 40, '')
        groups = selected['matrix']['include']
        self.assertLessEqual(len(groups), 128)
        self.assertEqual(sorted(p for group in groups for p in group['matches']), sorted(paths))


class VerificationTests(unittest.TestCase):
    def test_fake_binary_full_report_and_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp, working_directory(tmp):
            path = write_match()
            binary = Path('fake-mechcore').resolve()
            binary.write_text(f'#!{sys.executable}\nimport json,sys\nprint(json.dumps({result()["report"]!r}))\nprint("warning retained", file=sys.stderr)\nsys.exit(1)\n')
            binary.chmod(0o755)
            found = ci.run_match(binary, str(path), Path('reports'), 5)
            self.assertEqual(found['status'], 'diverged')
            self.assertEqual(found['report']['fights']['rounds'][0]['result'], 'equal')
            self.assertEqual(found['report']['fights']['rounds'][1]['result'], 'differs')
            self.assertEqual(Path('reports/' + NAME.removesuffix('.yaml') + '.stderr.log').read_text(), 'warning retained\n')
            # A zero exit with a failing report must not be treated as success.
            binary.write_text(binary.read_text().replace('sys.exit(1)', 'sys.exit(0)'))
            self.assertEqual(ci.run_match(binary, str(path), Path('reports'), 5)['status'], 'error')

    def test_crash_and_timeout_do_not_fabricate_divergence(self):
        with tempfile.TemporaryDirectory() as tmp, working_directory(tmp):
            write_match()
            self.assertEqual(ci.run_match(Path('/nonexistent/mechcore'), NAME, Path('reports'), 1)['status'], 'error')
            timeout = subprocess.TimeoutExpired('fake', 1, output=b'partial stdout', stderr=b'partial stderr')
            with patch.object(ci.subprocess, 'run', side_effect=timeout):
                found = ci.run_match(Path('/fake'), NAME, Path('reports'), 1)
            self.assertEqual(found['status'], 'error')
            self.assertIn('exceeded', found['error'])
            self.assertEqual(Path('reports/' + NAME.removesuffix('.yaml') + '.stdout.jsonl').read_text(), 'partial stdout')


class IssueTests(unittest.TestCase):
    def test_one_issue_for_all_rounds_reopen_then_close_only_full_pass(self):
        api = FakeGitHub()
        ci.reconcile(api, metadata(result()))
        ci.reconcile(api, metadata(result()))
        self.assertEqual(len(api.kept), 1)
        self.assertEqual(sum(call[0] == 'create' for call in api.calls), 1)
        issue = api.kept[0]
        issue['body'] += '\nHuman research notes must survive.\n'
        # Fixing the first round while another fails must leave the issue open.
        found = result()
        found['report']['error'] = 'round 8 differs'
        ci.reconcile(api, metadata(found))
        self.assertEqual(issue['state'], 'open')
        ci.reconcile(api, metadata(result(True)))
        self.assertEqual(issue['state'], 'closed')
        self.assertEqual(issue['state_reason'], 'completed')
        self.assertIn('Human research notes must survive.', issue['body'])
        ci.reconcile(api, metadata(result()))
        self.assertEqual(issue['state'], 'open')
        self.assertEqual(len(api.kept), 1)

    def test_pass_without_issue_is_noop_and_errors_do_not_close(self):
        api = FakeGitHub()
        ci.reconcile(api, metadata(result(True)))
        self.assertEqual(api.calls, [])
        ci.reconcile(api, metadata(result()))
        api.calls.clear()
        found = {'match': NAME, 'status': 'error'}
        self.assertEqual(ci.reconcile(api, metadata(found)), 1)
        self.assertEqual(api.calls, [])
        self.assertEqual(api.kept[0]['state'], 'open')

    def test_contradictory_success_cannot_resolve_issue(self):
        api = FakeGitHub()
        found = result()
        found['status'] = 'passed'
        with self.assertRaises(ValueError):
            ci.reconcile(api, metadata(found))
        found = result(True)
        found['exit_code'] = 1
        with self.assertRaises(ValueError):
            ci.reconcile(api, metadata(found))
        self.assertEqual(api.calls, [])

    def test_lost_creation_response_refreshes_marker_before_retry(self):
        api = ci.GitHub('fake-token')
        identity = ci.marker(NAME)
        kept = {'body': identity, 'number': 7}
        calls = []
        def request(method, path, value=None, attempts=4):
            calls.append(method)
            if method == 'POST':
                raise URLError('connection lost after server created issue')
            return [] if calls == ['GET'] else [kept]
        with patch.object(api, 'request', side_effect=request), patch.object(ci.time, 'sleep'):
            self.assertEqual(api.create_issue(identity, identity, 'match'), kept)
        self.assertEqual(calls, ['GET', 'POST', 'GET'])

    def test_transient_api_query_retries(self):
        api = ci.GitHub('fake-token')
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b'[]')
        with patch.object(ci, 'urlopen', side_effect=[URLError('temporary'), response]) as call, patch.object(ci.time, 'sleep'):
            self.assertEqual(api.request('GET', 'repos/qbisi/mechcore/issues'), [])
        self.assertEqual(call.call_count, 2)


class WorkflowTests(unittest.TestCase):
    def test_workflow_contract(self):
        root = Path(__file__).resolve().parents[2]
        workflow = yaml.load((root / '.github/workflows/tournaments.yml').read_text(), Loader=yaml.BaseLoader)
        trigger = workflow['on']
        self.assertEqual(trigger['push']['branches'], ['master'])
        self.assertEqual(trigger['push']['paths'], ['tournaments/**/*.yaml'])
        self.assertNotIn('schedule', trigger)
        build = workflow['jobs']['build']['steps']
        checkout = build[0]['with']
        self.assertEqual(checkout['repository'], 'qbisi/mechcore')
        self.assertEqual(checkout['ref'], 'master')
        self.assertIn('cargo build --release -p mechcore --no-default-features', str(build))
        verify = workflow['jobs']['verify']['steps']
        report = next(step for step in verify if step.get('name', '').startswith('Open or update'))
        self.assertIn('!cancelled()', report['if'])
        self.assertIn('MECHCORE_ISSUES_TOKEN', report['env'])


if __name__ == '__main__':
    unittest.main()
