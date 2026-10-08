from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.runtime.recovery import RecoveryCenter
from omnicrawler.services.application_service import ApplicationService
from omnicrawler.state import StateStore


class Site(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.server.hits.append((self.path, self.headers.get('If-None-Match')))  # type: ignore[attr-defined]
        if self.headers.get('If-None-Match'):
            self.send_response(304)
            self.end_headers()
            return
        links = '<a href="/a">A</a><a href="/a">A</a><a href="/b">B</a><a href="/denied">D</a><a href="/logout">L</a>'
        body = ('<html><body><h1>Page</h1>' + (links if self.path == '/' else '') + '</body></html>').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('ETag', '"unchanged"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # noqa: N802
        pass


@pytest.fixture
def site():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
    server.hits = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def task(tmp_path: Path, site, *, depth: int = 0, pages: int = 100, follow: str = '') -> Path:
    raw = {
        'project': {'name': 'discovery-coverage', 'workspace': str(tmp_path / 'work')},
        'source': {'kind': 'crawl', 'seeds': [f'http://127.0.0.1:{site.server_port}/'], 'follow_xpath': follow},
        'crawl': {'max_depth': depth, 'max_pages': pages, 'deny_patterns': ['/denied$'], 'concurrency': 2},
        'http': {'allow_private_network': True, 'resolve_dns': False, 'respect_robots': False,
                 'delay_seconds': 0, 'retries': 0},
        'extract': {'mode': 'html', 'fields': {'title': {'selector': 'h1', 'required': True}}},
        'updates': {'enabled': True},
    }
    path = tmp_path / 'task.yaml'
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    return path


def test_depth_boundary_lists_missing_links_and_safe_revisit_completes_them(site, tmp_path):
    path = task(tmp_path, site)
    initial = ApplicationService(path).run()
    center = RecoveryCenter(load_config(path))
    report = center.coverage(limit=2)
    assert initial['coverage']['observed_traversal'] == 'partial'
    assert report['site_coverage'] == 'unknown'
    assert report['known_discovery_gaps'] == 4
    assert report['gap_paths_total'] == 4  # repeated /a is one path, not two missing pages
    assert report['truncated'] and report['next_offset'] == 2
    rest = center.coverage(limit=2, offset=2)
    paths = report['gap_paths'] + rest['gap_paths']
    assert {row['decision'] for row in paths} == {'depth_limit', 'scope_rejected'}
    assert len({row['fingerprint'] for row in paths}) == 4
    parent = paths[0]['parent_fingerprint']
    with pytest.raises(ValueError, match='max_depth'):
        center.retry_discovery([parent])

    raw = yaml.safe_load(path.read_text())
    raw['crawl']['max_depth'] = 2
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    center = RecoveryCenter(load_config(path))
    assert center.retry_discovery([parent], run_id=initial['run_id'])['queued'] == 1
    before = list(site.hits)
    resumed = ApplicationService(path).run(resume=True)
    assert resumed['status'] == 'succeeded'
    assert ('/', None) in site.hits[len(before):], 'revisit must request a body even with an existing ETag'
    assert {url for url, _ in site.hits} == {'/', '/a', '/b'}
    current = center.coverage()
    assert current['known_discovery_gaps'] == 2  # real deny/write guards stay blocked
    historical = center.coverage(run_id=initial['run_id'])
    assert {row['decision'] for row in historical['gap_paths']} == {'scope_rejected'}
    with StateStore(center.database) as state:
        row = state.conn.execute('SELECT meta_json FROM frontier WHERE fingerprint=?', (parent,)).fetchone()
        assert 'rediscover' not in json.loads(row[0]), 'one revisit must not disable conditional requests forever'
        assert state.conn.execute("SELECT COUNT(*) FROM audit_events WHERE action='retry_discovery'").fetchone()[0] == 1


def test_page_budget_reports_unfinished_work_and_resume_keeps_prior_gaps_visible(site, tmp_path):
    path = task(tmp_path, site, depth=2, pages=1)
    first = ApplicationService(path).run()
    assert first['coverage']['workspace_frontier']['pending'] == 2
    assert first['coverage']['observed_traversal'] == 'partial'
    resumed = ApplicationService(path).run(resume=True, max_pages=10)
    assert resumed['coverage']['workspace_unfinished_requests'] == 0
    assert resumed['coverage']['historical_gap_paths'] == 2
    assert resumed['coverage']['observed_traversal'] == 'partial'


def test_follow_filter_is_visible_and_can_be_reconsidered_after_config_change(site, tmp_path):
    path = task(tmp_path, site, depth=2, follow='//a[@href="/a"]')
    first = ApplicationService(path).run()
    center = RecoveryCenter(load_config(path))
    report = center.coverage()
    assert {row['url'].rsplit('/', 1)[-1] for row in report['gap_paths']} == {'b', 'denied', 'logout'}
    assert all(row['decision'] == 'follow_filter' for row in report['gap_paths'])
    parent = report['gap_paths'][0]['parent_fingerprint']
    raw = yaml.safe_load(path.read_text())
    raw['source']['follow_xpath'] = ''
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    RecoveryCenter(load_config(path)).retry_discovery([parent], run_id=first['run_id'])
    ApplicationService(path).run(resume=True)
    assert '/b' in {url for url, _ in site.hits}
    assert '/denied' not in {url for url, _ in site.hits}
    assert '/logout' not in {url for url, _ in site.hits}


def test_revisit_preflight_is_atomic_and_rechecks_current_scope(site, tmp_path):
    path = task(tmp_path, site)
    ApplicationService(path).run()
    center = RecoveryCenter(load_config(path))
    parent = center.coverage()['gap_paths'][0]['parent_fingerprint']
    center.config.raw['crawl']['max_depth'] = 2
    with pytest.raises(ValueError, match='no recorded'):
        center.retry_discovery([parent, 'not-a-parent'])
    with StateStore(center.database) as state:
        assert state.pending_count() == 0
    center.config.raw['crawl']['deny_patterns'] = ['/$']
    with pytest.raises(ValueError, match='outside current scope'):
        center.retry_discovery([parent])
    with StateStore(center.database) as state:
        assert state.pending_count() == 0
        assert state.conn.execute("SELECT COUNT(*) FROM audit_events WHERE action='retry_discovery'").fetchone()[0] == 0


@pytest.mark.parametrize('limit,offset', [(0, 0), (1001, 0), (True, 0), (10, -1)])
def test_coverage_rejects_invalid_window_even_without_database(tmp_path, limit, offset):
    path = tmp_path / 'task.yaml'
    path.write_text('source: {seeds: [https://example.org]}', encoding='utf-8')
    with pytest.raises(ValueError):
        RecoveryCenter(load_config(path)).coverage(limit, offset=offset)


def test_prior_discovery_without_inventory_is_unknown_not_complete(tmp_path):
    with StateStore(tmp_path / 'state.sqlite3') as state:
        run_id = state.start_run('coverage', 'task.yaml')
        state.save_checkpoint(run_id, 'discover', 'step:unrecorded', {'stop_reason': 'depth_limit'})
        report = state.discovery_coverage(run_id)
        assert report['unrecorded_discovery_steps'] == 1
        assert report['observed_traversal'] == 'unknown' and report['site_coverage'] == 'unknown'
        with pytest.raises(ValueError, match='does not exist'):
            state.discovery_coverage('missing')


def test_cli_and_saved_task_actions_use_the_actual_discovery_ledger(site, tmp_path, capsys):
    import hashlib

    from omnicrawler.cli import main
    from omnicrawler.services.task_tools import TaskAction, execute

    path = task(tmp_path, site)
    run = ApplicationService(path).run()
    main(['recovery', '--config', str(path), 'coverage', '--run-id', run['run_id'], '--limit', '1'])
    payload = json.loads(capsys.readouterr().out)
    assert payload['known_discovery_gaps'] == 4 and len(payload['gap_paths']) == 1
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    report = execute(TaskAction('coverage', path, digest, {'run_id': run['run_id']}))
    assert report['known_discovery_gaps'] == 4
    with pytest.raises(ValueError, match='确认'):
        execute(TaskAction('retry-discovery', path, digest, {'fingerprints': [report['gap_paths'][0]['parent_fingerprint']]}))


def test_protected_manual_value_does_not_cut_off_link_discovery(site, tmp_path):
    from omnicrawler.core.models import FetchResult
    from omnicrawler.pipeline import Pipeline

    path = task(tmp_path, site)
    initial = ApplicationService(path).run()
    config = load_config(path)
    config.raw['crawl']['max_depth'] = 2
    config.raw['incremental']['skip_unchanged'] = False
    with Pipeline(config) as pipeline:
        record_id = pipeline.state.rows('SELECT record_id FROM records')[0]['record_id']
        pipeline.state.edit_record(record_id, 'title', 'Reviewed')
        row = pipeline.state.conn.execute('SELECT * FROM frontier').fetchone()
        request = pipeline.state._row_to_request(row)
        archive = pipeline.state.rows('SELECT raw_path FROM responses')[0]['raw_path']
        response = FetchResult(request, request.url, 200, {'content-type': 'text/html'}, Path(archive).read_bytes(), 0)
        pipeline._handle_result(initial['run_id'], response, 2)
        saved = json.loads(pipeline.state.rows('SELECT data_json FROM records WHERE record_id=?', (record_id,))[0]['data_json'])
        assert saved['title'] == 'Reviewed'
        assert pipeline.state.checkpoint(initial['run_id'], 'reprocess_candidate', request.fingerprint) is not None
        assert pipeline.state.pending_count() == 2, 'protecting a manual field must not lose discovered pages'


def test_redacted_parent_identity_cannot_produce_a_false_queued_report(site, tmp_path):
    from omnicrawler.core.models import CrawlRequest

    path = task(tmp_path, site, depth=2)
    center = RecoveryCenter(load_config(path))
    parent = CrawlRequest(f'http://127.0.0.1:{site.server_port}/', headers={'Authorization': 'test-only-credential'})
    child = CrawlRequest(parent.url + 'a')
    with StateStore(center.database) as state:
        run_id = state.start_run('discovery-coverage', str(path))
        state.enqueue(parent)
        state.mark_done(parent.fingerprint)
        state.save_discovery_edges(run_id, parent.fingerprint, [{'fingerprint': child.fingerprint,
            'url': child.url, 'decision': 'depth_limit', 'parent_fingerprint': parent.fingerprint}])
    with pytest.raises(ValueError, match='redacted headers'):
        center.retry_discovery([parent.fingerprint], run_id=run_id)
    with StateStore(center.database) as state:
        assert state.pending_count() == 0
        assert not state.rows("SELECT * FROM audit_events WHERE action='retry_discovery'")
