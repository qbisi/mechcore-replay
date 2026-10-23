#!/usr/bin/env python3
"""Aggregate tournament CI artifacts; never build or verify matches.

uv run --with pyyaml python .github/scripts/tournament_summary.py --help
"""
import argparse
from collections import Counter, defaultdict
import html
import json
import os
from pathlib import Path

from tournaments import CORPUS, ISSUES, match_path


def aggregate(selection, reports, corpus, simulator):
    expected = set(selection['matches'])
    if len(expected) != len(selection['matches']):
        raise ValueError('duplicate selected matches')
    for name in expected:
        match_path(name)
    found, notes, versions = {}, [], set()
    for path in sorted(reports.glob('*/results.json')):
        try:
            shard = json.loads(path.read_text())
            if shard['corpus_commit'] != corpus or shard['mechcore_commit'] != simulator:
                raise ValueError('provenance differs from this workflow run')
            versions.add(shard['game_version'])
            if shard.get('issue_reporting', {}).get('status') != 'complete':
                notes.append(f'{path.parent.name}: issue reporting incomplete')
            for result in shard['results']:
                name = result['match']
                if name not in expected or name in found:
                    notes.append(f'{path.parent.name}: unexpected or duplicate match {name}')
                    continue
                status = result['status']
                if status not in ('passed', 'diverged', 'error'):
                    raise ValueError(f'unknown status {status}')
                if status != 'error':
                    report = result['report']
                    passed = status == 'passed'
                    if (report.get('schema') != 'mechcore.verify-result'
                            or report.get('path') != name or report.get('valid') is not passed
                            or report.get('kind') not in (('match',) if passed else ('match', 'unreadable'))
                            or result.get('exit_code') != (0 if passed else 1)):
                        raise ValueError(f'incomplete whole-match evidence for {name}')
                found[name] = result
        except (OSError, ValueError, KeyError, TypeError) as error:
            notes.append(f'{path.parent.name}: {error}')
    if len(versions) > 1:
        notes.append('shards disagree on the game version')
    groups = defaultdict(list)
    for name in sorted(expected):
        groups[match_path(name).parts[1]].append(name)
    def counts(names):
        total = len(names)
        counted = Counter(found[name]['status'] for name in names if name in found)
        warning_matches, warning_rounds = 0, 0
        for name in names:
            warnings = sum(row.get('result') == 'warning' for row in
                           found.get(name, {}).get('report', {}).get('fights', {}).get('rounds', []))
            warning_matches += bool(warnings)
            warning_rounds += warnings
        return {'selected': total, 'reported': sum(counted.values()),
                **{status: counted[status] for status in ('passed', 'diverged', 'error')},
                'missing': total - sum(counted.values()), 'warning_matches': warning_matches,
                'warning_rounds': warning_rounds,
                'pass_rate': counted['passed'] / total if total else None}
    totals = counts(expected)
    return {'corpus_commit': corpus, 'mechcore_commit': simulator,
            'selection': {'base': selection.get('base', ''), 'match': selection.get('single', '')},
            'selection_known': True, 'game_versions': sorted(versions), 'complete': not notes and not totals['missing'],
            'totals': totals, 'groups': {group: counts(names) for group, names in sorted(groups.items())},
            'notes': notes, 'missing': sorted(expected - found.keys()),
            'results': [found[name] for name in sorted(found)]}


def markdown(data, run_url, jobs):
    def rate(count):
        return f"{count['pass_rate']:.2%}" if count['pass_rate'] is not None else 'N/A'
    def cells(count):
        return ' | '.join(str(count[key]) for key in
                          ('selected', 'passed', 'diverged', 'error', 'missing', 'warning_matches')) + ' | ' + rate(count)
    corpus = data['corpus_commit']
    simulator = (f"[{data['mechcore_commit']}](https://github.com/{ISSUES}/commit/{data['mechcore_commit']})"
                 if data['mechcore_commit'] else 'unavailable')
    whole = cells(data['totals']) if data['selection_known'] else 'unknown | N/A | N/A | N/A | N/A | N/A | N/A'
    lines = ['# Tournament verification report', '',
             f"Status: **{'complete' if data['complete'] else 'INCOMPLETE'}**. [Workflow and full artifacts]({run_url}).", '',
             f"- Selection: {'single match ' + data['selection']['match'] if data['selection']['match'] else 'added tournament files since ' + (data['selection']['base'] or 'unknown base')}",
             f'- Corpus: [{corpus}](https://github.com/{CORPUS}/commit/{corpus})',
             f"- Simulator: {simulator} (master release, simulator backend)",
             f"- Game version: {', '.join(data['game_versions']) or 'unavailable'}", '',
             '| Scope | Selected | Passed | Diverged | Execution/input errors | Missing | With warnings | Pass rate |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
             '| **Whole batch** | ' + whole + ' |']
    lines += [f'| {group} | {cells(count)} |' for group, count in data['groups'].items()]
    lines += ['', 'Pass rate = complete passing matches / all selected matches. Missing reports and errors stay in the denominator.',
              'Warnings follow the verifier contract; a passing match can contain warning/skipped fight comparisons.',
              f"Warning rounds: {data['totals']['warning_rounds']}. This is a report of this selection, not the entire archive.", '',
              'Job results: ' + ', '.join(f'{name}={value["result"]}' for name, value in sorted(jobs.items())) + '.', '']
    if data['notes'] or data['missing']:
        lines += ['## Incomplete evidence', '']
        lines += ['- ' + html.escape(note).replace('|', '&#124;') for note in data['notes']]
        lines += ['- Missing: `' + name + '`' for name in data['missing']]
        lines.append('')
    failures = [row for row in data['results'] if row['status'] != 'passed']
    if failures:
        lines += ['## Matches requiring attention', '', '| Match | Result | Issue | First diagnostic |', '| --- | --- | --- | --- |']
        for row in failures:
            issue = f"[match issue]({row['issue_url']})" if row.get('issue_url') else 'Not reported'
            diagnostic = html.escape(str(row.get('error') or row.get('report', {}).get('error') or '')[:300]).replace('|', '&#124;').replace('\n', ' ')
            lines.append(f"| [{Path(row['match']).stem}](https://github.com/{CORPUS}/blob/{corpus}/{row['match']}) | {row['status']} | {issue} | {diagnostic} |")
    linked = [row for row in data['results'] if row.get('issue_url') and row['status'] == 'passed']
    if linked:
        lines += ['', '## Passing matches with associated issues', '']
        lines += [f"- [{Path(row['match']).stem}]({row['issue_url']})" for row in linked]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', type=Path, default=Path('reports/selection.json'))
    parser.add_argument('--reports', type=Path, default=Path('reports/shards'))
    parser.add_argument('--out', type=Path, default=Path('reports/summary'))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    corpus = os.environ['GITHUB_SHA']
    run_url = f"https://github.com/{CORPUS}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    jobs = json.loads(os.environ.get('JOB_RESULTS', '{}'))
    if args.selection.exists():
        data = aggregate(json.loads(args.selection.read_text()), args.reports, corpus, os.environ.get('MECHCORE_SHA', ''))
    else:
        data = aggregate({'matches': []}, args.reports, corpus, os.environ.get('MECHCORE_SHA', ''))
        data['complete'] = False
        data['selection_known'] = False
        data['totals'] = {key: None for key in data['totals']}
        data['notes'].append('Selection artifact unavailable: batch size and pass rate are unknown')
    if any(job['result'] in ('failure', 'cancelled') for name, job in jobs.items() if name != 'verify'):
        data['complete'] = False
        data['notes'].append('Selection or build did not complete successfully')
    data.update(run_url=run_url, jobs={name: job['result'] for name, job in jobs.items()})
    text = markdown(data, run_url, jobs)
    (args.out / 'summary.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    (args.out / 'summary.md').write_text(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write(text)
    print(json.dumps(data['totals']))
    return int(not data['complete'] or bool(data['totals']['error']))


if __name__ == '__main__':
    raise SystemExit(main())
