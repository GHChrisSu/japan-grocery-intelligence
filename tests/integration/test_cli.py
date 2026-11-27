import json
from threading import Event, Thread

import yaml
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from typer.testing import CliRunner

from grocery_intelligence.cli import app, build_scheduler
from grocery_intelligence.config import Settings
from grocery_intelligence.database import database, persist, register
from grocery_intelligence.parsing import observation


def test_cli_workflow(tmp_path, source_config, monkeypatch):
    monkeypatch.setenv('GROCERY_DATABASE', str(tmp_path / 'cli.sqlite'))
    monkeypatch.setenv('GROCERY_SOURCE_CONFIG', str(source_config))
    client = CliRunner()
    assert '14 configured, 0 enabled' in client.invoke(app, ['check-sources']).stdout
    assert 'マルエツ 千葉幸町店' in client.invoke(app, ['discover']).stdout
    assert 'No enabled sources' in client.invoke(app, ['scheduler']).stdout
    assert 'No enabled sources' in client.invoke(app, ['crawl', '--all']).stdout
    assert client.invoke(app, ['crawl', '--store', 'ok-chiba-saiwaicho']).exit_code == 1
    assert client.invoke(app, ['crawl']).exit_code == 2
    assert client.invoke(app, ['export', '--format', 'text']).exit_code == 2
    assert not json.loads(client.invoke(app, ['search', '牛乳']).stdout)
    draft = tmp_path / 'flyer.txt'
    draft.write_text('牛乳 1L 税込 213円\n')
    command = ['import-flyer', str(draft), '--store', 'ok-chiba-saiwaicho']
    assert json.loads(client.invoke(app, command).stdout)[0]['price_including_tax'] == '213'
    assert 'Stored 1' in client.invoke(app, [*command, '--accept-reviewed']).stdout
    assert 'Stored 0' in client.invoke(app, [*command, '--accept-reviewed']).stdout
    assert json.loads(client.invoke(app, ['history', '牛乳']).stdout)[0]['evidence_id']
    assert '牛乳' in client.invoke(app, ['export', '--format', 'csv']).stdout
    assert len(json.loads(client.invoke(app, ['export']).stdout)) == 1
    assert json.loads(client.invoke(app, ['compare', '牛乳']).stdout) == []
    assert json.loads(client.invoke(app, ['report']).stdout)['observations'] == 1
    draft.write_text('牛乳 ¥213')
    assert client.invoke(app, ['import-receipt', str(draft), '--store', 'ok-chiba-saiwaicho']).exit_code == 2


def test_compare_and_scheduler(tmp_path, monkeypatch, local_source):
    config = tmp_path / 'config.yaml'
    config.write_text(yaml.safe_dump([local_source.model_dump(mode='json')]))
    settings = Settings(database=tmp_path / 'cli.sqlite', source_config=config)
    monkeypatch.setenv('GROCERY_DATABASE', str(settings.database))
    jobs = build_scheduler(settings).get_jobs()
    assert len(jobs) == 1 and jobs[0].id == local_source.id
    record = observation(local_source, '牛乳 1L', '200', '216', source_type='html',
                         price_type='regular', jan_code='12345678', sales_channel='physical_store')
    with database(settings.database) as conn:
        persist(conn, register(conn, local_source), 'fixture', [record])
    compared = json.loads(CliRunner().invoke(app, ['compare', '牛乳']).stdout)
    assert {row['price'] for row in compared} == {'20', '21.6'}


def test_scheduled_run_writes_fixture_prices(tmp_path, local_source, monkeypatch):
    from grocery_intelligence import crawl

    config = tmp_path / 'config.yaml'
    config.write_text(yaml.safe_dump([local_source.model_dump(mode='json')]))
    settings = Settings(database=tmp_path / 'scheduled.sqlite', source_config=config)
    monkeypatch.setattr(crawl.time, 'sleep', lambda _: None)
    scheduler = build_scheduler(settings)
    finished = Event()
    events = []

    def on_complete(event):
        events.append(event)
        finished.set()

    scheduler.add_listener(on_complete, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    thread = Thread(target=scheduler.start, daemon=True)
    thread.start()
    try:
        assert finished.wait(8)
        assert events[0].exception is None
        assert events[0].retval == 'success'
    finally:
        if scheduler.running:
            scheduler.shutdown()
        thread.join(timeout=2)
