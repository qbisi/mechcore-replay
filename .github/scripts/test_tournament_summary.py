"""Batch report contracts; fixtures are fake reports, never simulated matches."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

import yaml
import tournaments as ci
import tournament_summary as summary
from test_tournaments import NAME, metadata, result, FakeGitHub


class SummaryTests(unittest.TestCase):
    def shard(self, root, name, rows, **overrides):
        value = metadata(rows[0])
        value.update(results=rows, issue_reporting={'status': 'complete'})
        value.update(overrides)
        path = root / name / 'results.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def test_batch_denominator_regions_errors_warnings_and_issue_links(self):
        names = [NAME, NAME.replace('2235', '2236'), NAME.replace('2235', '2237'),
                 NAME.replace('asia', 'europe').replace('2235', '2238')]
        passing = result(True, names[0])
        passing['report']['fights']['rounds'][1]['result'] = 'warning'
        failing = result(False, names[1])
        failing['issue_url'] = 'https://github.com/qbisi/mechcore/issues/1'
        error = {'match': names[3], 'status': 'error', 'error': 'timed out'}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.shard(root, 'shard-0', [passing, failing])
            self.shard(root, 'shard-1', [error])
            data = summary.aggregate({'matches': names}, root, 'a'*40, 'b'*40)
        self.assertEqual(data['totals'], {'selected': 4, 'reported': 3, 'passed': 1,
                         'diverged': 1, 'error': 1, 'missing': 1, 'warning_matches': 1,
                         'warning_rounds': 1, 'pass_rate': .25})
        self.assertFalse(data['complete'])
        self.assertEqual(data['groups']['2026-10-17-europe']['error'], 1)
        text = summary.markdown(data, 'https://example.com/run', {})
        self.assertIn('25.00%', text)
        self.assertIn('INCOMPLETE', text)
        self.assertIn(failing['issue_url'], text)
        self.assertIn(names[2], text)

    def test_complete_pass_and_empty_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty = summary.aggregate({'matches': []}, root, 'a'*40, '')
            self.assertTrue(empty['complete'])
            self.assertIn('N/A', summary.markdown(empty, 'run', {}))
            self.shard(root, 'shard-0', [result(True)])
            data = summary.aggregate({'matches': [NAME]}, root, 'a'*40, 'b'*40)
        self.assertTrue(data['complete'])
        self.assertEqual(data['totals']['pass_rate'], 1)

    def test_missing_or_failed_shards_cannot_be_one_hundred_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = summary.aggregate({'matches': [NAME]}, root, 'a'*40, 'b'*40)
            self.assertFalse(data['complete'])
            self.assertEqual(data['totals']['pass_rate'], 0)
            self.shard(root, 'shard-0', [result(True)], issue_reporting={'status': 'error'})
            data = summary.aggregate({'matches': [NAME]}, root, 'a'*40, 'b'*40)
            self.assertFalse(data['complete'])
            self.assertIn('issue reporting incomplete', data['notes'][0])

    def test_wrong_source_duplicate_or_contradictory_evidence_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.shard(root, 'shard-0', [result(True)], mechcore_commit='c'*40)
            data = summary.aggregate({'matches': [NAME]}, root, 'a'*40, 'b'*40)
            self.assertEqual(data['totals']['missing'], 1)
            self.assertFalse(data['complete'])
            self.shard(root, 'shard-0', [result(True)])
            self.shard(root, 'shard-1', [result(True)])
            data = summary.aggregate({'matches': [NAME]}, root, 'a'*40, 'b'*40)
            self.assertEqual(data['totals']['passed'], 1)
            self.assertFalse(data['complete'])
            wrong = result(True)
            wrong['exit_code'] = 1
            self.shard(root, 'shard-0', [wrong])
            (root/'shard-1/results.json').unlink()
            data = summary.aggregate({'matches': [NAME]}, root, 'a'*40, 'b'*40)
            self.assertFalse(data['complete'])
            self.assertEqual(data['totals']['passed'], 0)

    def test_report_persists_issue_link_and_failure_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'results.json'
            path.write_text(json.dumps(metadata(result())))
            api = FakeGitHub()
            with patch('sys.argv', ['tournaments.py', 'report', '--results', str(path)]), patch.object(ci, 'GitHub', return_value=api):
                self.assertEqual(ci.main(), 0)
            saved = json.loads(path.read_text())
            self.assertEqual(saved['issue_reporting']['status'], 'complete')
            self.assertEqual(saved['results'][0]['issue_url'], 'https://example.com/issue')
            with patch('sys.argv', ['tournaments.py', 'report', '--results', str(path)]), patch.object(ci, 'GitHub', return_value=api), patch.object(api, 'issues', side_effect=URLError('offline')):
                with self.assertRaises(URLError):
                    ci.main()
            self.assertEqual(json.loads(path.read_text())['issue_reporting']['status'], 'error')

    def test_cli_preserves_incomplete_summary_when_selection_and_build_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root/'out'
            env = {'GITHUB_SHA': 'a'*40, 'GITHUB_RUN_ID': '1', 'MECHCORE_SHA': '',
                   'GITHUB_STEP_SUMMARY': str(root/'actions.md'),
                   'JOB_RESULTS': '{"select":{"result":"failure"},"build":{"result":"skipped"}}'}
            argv = ['summary.py', '--selection', str(root/'missing.json'), '--out', str(output), '--reports', str(root/'shards')]
            with patch('sys.argv', argv), patch.dict(os.environ, env):
                self.assertEqual(summary.main(), 1)
            data = json.loads((output/'summary.json').read_text())
            self.assertFalse(data['selection_known'])
            self.assertIsNone(data['totals']['selected'])
            self.assertIn('unknown | N/A', (root/'actions.md').read_text())
            self.assertIn('INCOMPLETE', (output/'summary.md').read_text())

    def test_workflow_summary_runs_after_failed_jobs_without_write_token(self):
        root = Path(__file__).resolve().parents[2]
        workflow = yaml.load((root/'.github/workflows/tournaments.yml').read_text(), Loader=yaml.BaseLoader)
        job = workflow['jobs']['summary']
        self.assertEqual(job['if'], 'always()')
        self.assertEqual(set(job['needs']), {'select', 'build', 'verify'})
        self.assertNotIn('MECHCORE_ISSUES_TOKEN', str(job))
        self.assertEqual(job['steps'][-1]['if'], 'always()')
        self.assertEqual(job['steps'][-1]['with']['retention-days'], '90')


if __name__ == '__main__':
    unittest.main()
