import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from src import repairer, service, telemetry
from src.validator import EPUBCheckError


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    monkeypatch.delenv('EPUB_DOCTOR_TELEMETRY_ENABLED', raising=False)
    monkeypatch.delenv('EPUB_DOCTOR_TELEMETRY_PATH', raising=False)


@pytest.fixture
def storage(monkeypatch, tmp_path):
    path = tmp_path / 'logs' / 'events.jsonl'
    monkeypatch.setenv('EPUB_DOCTOR_TELEMETRY_ENABLED', '1')
    monkeypatch.setenv('EPUB_DOCTOR_TELEMETRY_PATH', str(path))
    return path


def events(storage):
    return [json.loads(line) for line in storage.read_text().splitlines()]


def report(valid=False):
    return dict(valid=valid, fatal_count=0, error_count=0 if valid else 2,
                warning_count=1, publication={'title': 'PRIVATE_TITLE'},
                messages=[{'ID': 'RSC-008', 'message': 'PRIVATE_MESSAGE',
                           'locations': [{'path': 'PRIVATE_INTERNAL_FILE'}]},
                          {'ID': 'OPF-014'}, {'ID': 'RSC-008'}])


def stub_inspect(monkeypatch, valid=False):
    validation = report(valid)
    monkeypatch.setattr(service, 'validate_epub', lambda path: validation)
    monkeypatch.setattr(service, 'diagnose_epub', lambda *args:
                        [] if valid else ['manifest_missing_referenced_resource'])
    return validation


@pytest.mark.parametrize('valid', [True, False])
def test_inspect_event(storage, monkeypatch, valid):
    validation = stub_inspect(monkeypatch, valid)
    result = service.inspect_epub('PRIVATE_INPUT.epub')
    event, = events(storage)
    assert result['publication'] is validation['publication']
    assert event['action'] == 'inspect'
    assert event['operation_completed'] is True
    assert event['original_valid'] is event['final_valid'] is valid
    assert event['repair_success'] is event['repair_attempted'] is None
    assert event['confirmed_safe_repairs'] == ([] if valid else ['manifest_missing_referenced_resource'])
    assert event['initial_epubcheck_codes'] == ['OPF-014', 'RSC-008']
    assert event['final_epubcheck_codes'] is None
    assert [event[k] for k in ('fatal_count', 'error_count', 'warning_count')] == [0, 0 if valid else 2, 1]
    assert event['schema_version'] == 2
    assert UUID(event['event_id']).version == 4
    assert datetime.fromisoformat(event['timestamp']).utcoffset() == timedelta(0)
    assert event['processing_time_ms'] >= 0
    assert event['file_size_bucket'] is None


@pytest.mark.parametrize('reason,success,attempted', [
    (None, True, True), ('already_valid', False, False),
    ('no_supported_repair', False, False), ('signatures_or_encryption', False, False),
    ('revalidation_failed', False, True), ('epubcheck_failure', False, None),
    ('output_write_failed', False, None), ('opf030_identifier_value_invalid', False, None),
])
def test_repair_event(storage, monkeypatch, reason, success, attempted):
    result = dict(original_valid=reason == 'already_valid',
                  detected=[] if attempted is False else ['mimetype_missing'],
                  repaired=['mimetype_missing'] if success else [],
                  final_valid=success or reason in ('already_valid', 'opf030_identifier_value_invalid'),
                  output_file='PRIVATE_OUTPUT.epub' if success else None,
                  remaining_messages=report()['messages'], reason=reason, detail='PRIVATE_DETAIL')
    monkeypatch.setattr(service, 'repair_epub', lambda *args, **kwargs: result)
    assert service.repair_epub_file('PRIVATE_INPUT.epub', 'PRIVATE_OUTPUT.epub') is result
    event, = events(storage)
    assert event['action'] == 'repair'
    assert event['repair_success'] is success
    assert event['repair_attempted'] is attempted
    assert event['refusal_reason'] == reason
    assert event['initial_epubcheck_codes'] is None
    assert event['final_epubcheck_codes'] is None
    assert event['fatal_count'] is event['error_count'] is event['warning_count'] is None


def fail(*args, **kwargs):
    raise RuntimeError('PRIVATE_FAILURE')


@pytest.mark.parametrize('target', ['write_event', 'record', 'start', 'versions', 'file_size_bucket'])
@pytest.mark.parametrize('action', ['inspect', 'repair'])
def test_observer_failure_preserves_result(storage, monkeypatch, target, action):
    stub_inspect(monkeypatch)
    expected = {'reason': 'no_supported_repair'}
    monkeypatch.setattr(service, 'repair_epub', lambda *args, **kwargs: expected)
    monkeypatch.setattr(telemetry, target, fail)
    if action == 'inspect':
        assert service.inspect_epub('input.epub')['valid'] is False
    else:
        assert service.repair_epub_file('input.epub', 'output.epub') is expected
    if target in ('versions', 'file_size_bucket'):
        event, = events(storage)
        if target == 'versions':
            assert event['epub_doctor_version'] is event['epubcheck_version'] is None
        else:
            assert event['file_size_bucket'] is None


@pytest.mark.parametrize('action', ['inspect', 'repair'])
@pytest.mark.parametrize('broken_writer', [True, False])
def test_original_exception_preserved(storage, monkeypatch, action, broken_writer):
    original = EPUBCheckError('PRIVATE_EXCEPTION_PATH')
    def broken(*args, **kwargs):
        raise original
    monkeypatch.setattr(service, 'validate_epub' if action == 'inspect' else 'repair_epub', broken)
    if broken_writer:
        monkeypatch.setattr(telemetry, 'write_event', fail)
    with pytest.raises(EPUBCheckError) as caught:
        if action == 'inspect':
            service.inspect_epub('input.epub')
        else:
            service.repair_epub_file('input.epub', 'output.epub')
    assert caught.value is original
    if not broken_writer:
        event, = events(storage)
        assert event['operation_completed'] is False
        assert event['original_valid'] is event['final_valid'] is None
        assert 'PRIVATE' not in storage.read_text()


def test_privacy_allowlist(storage, monkeypatch):
    validation = stub_inspect(monkeypatch)
    validation['publication'].update(author='PRIVATE_AUTHOR', ISBN='PRIVATE_ISBN',
                                     identifier='PRIVATE_ID', url='https://PRIVATE_URL')
    validation['messages'].append({'ID': 'PRIVATE_CODE'})
    service.inspect_epub('PRIVATE_INPUT.epub')
    result = dict(original_valid=False, detected=['mimetype_missing', 'PRIVATE_DIAGNOSIS'],
                  repaired=[], final_valid=False, output_file='PRIVATE_OUTPUT.epub',
                  remaining_messages=validation['messages'], reason='PRIVATE_REASON',
                  detail='PRIVATE_DETAIL', input_path='PRIVATE_INPUT', user_id='PRIVATE_USER',
                  ip_address='PRIVATE_IP', file_hash='PRIVATE_HASH', publication=validation['publication'])
    monkeypatch.setattr(service, 'repair_epub', lambda *args, **kwargs: result)
    service.repair_epub_file('PRIVATE_INPUT.epub', 'PRIVATE_OUTPUT.epub')
    assert 'PRIVATE' not in storage.read_text()
    permitted = set('''schema_version event_id timestamp action operation_completed
    initial_epubcheck_codes final_epubcheck_codes confirmed_safe_repairs original_valid repair_attempted repair_success
    final_valid refusal_reason fatal_count error_count warning_count file_size_bucket
    processing_time_ms epub_doctor_version epubcheck_version'''.split())
    assert all(set(event) == permitted for event in events(storage))


@pytest.mark.parametrize('size,bucket', [(0, '<1MB'), (1024**2-1, '<1MB'),
    (1024**2, '1-5MB'), (5*1024**2-1, '1-5MB'), (5*1024**2, '5-20MB'),
    (20*1024**2, '20-50MB'), (50*1024**2, '>50MB')])
def test_size_boundaries(tmp_path, size, bucket):
    path = tmp_path / 'sparse.epub'
    with path.open('wb') as output:
        output.truncate(size)
    assert telemetry.file_size_bucket(path) == bucket


@pytest.mark.parametrize('enabled,path_present', [(None, True), ('0', True), ('true', True), ('1', False)])
def test_disabled(monkeypatch, tmp_path, enabled, path_present):
    path = tmp_path / 'never.jsonl'
    if enabled is not None:
        monkeypatch.setenv('EPUB_DOCTOR_TELEMETRY_ENABLED', enabled)
    if path_present:
        monkeypatch.setenv('EPUB_DOCTOR_TELEMETRY_PATH', str(path))
    stub_inspect(monkeypatch)
    monkeypatch.setattr(telemetry, 'file_size_bucket', fail)
    monkeypatch.setattr(telemetry, 'versions', fail)
    service.inspect_epub('input.epub')
    assert telemetry.start() is None
    assert not path.exists()


@pytest.mark.parametrize('failure', ['serialization', 'directory', 'open'])
def test_storage_failures(storage, monkeypatch, failure):
    stub_inspect(monkeypatch)
    if failure == 'serialization':
        monkeypatch.setattr(telemetry.json, 'dumps', fail)
    elif failure == 'directory':
        monkeypatch.setattr(Path, 'mkdir', fail)
    else:
        monkeypatch.setattr(telemetry.os, 'open', fail)
    assert service.inspect_epub('input.epub')['valid'] is False


def test_concurrent_jsonl(storage):
    def emit(_):
        telemetry.record('inspect', 'unknown.epub', telemetry.start(), validation=report(), completed=True)
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(emit, range(120)))
    result = events(storage)
    assert len(result) == 120
    assert len({event['event_id'] for event in result}) == 120
    assert all(event['schema_version'] == 2 for event in result)


def test_monotonic_nonnegative(storage, monkeypatch):
    monkeypatch.setattr(telemetry.time, 'monotonic_ns', lambda: 10)
    telemetry.record('inspect', 'unknown.epub', 20)
    assert events(storage)[0]['processing_time_ms'] == 0


def test_real_pipeline(storage, make_epub, tmp_path):
    source = make_epub('missing')
    before = source.read_bytes()
    assert service.inspect_epub(str(source))['safe_repairs'] == ['mimetype_missing']
    output = tmp_path / 'repaired.epub'
    result = service.repair_epub_file(str(source), str(output))
    assert result['repaired'] == ['mimetype_missing']
    assert output.exists() and source.read_bytes() == before
    inspection, repair = events(storage)
    assert inspection['file_size_bucket'] == '<1MB'
    assert 'PKG-006' in inspection['initial_epubcheck_codes']
    assert 'PKG-006' in repair['initial_epubcheck_codes']
    assert repair['repair_success'] is True
    assert repair['final_epubcheck_codes'] == []


@pytest.mark.parametrize('fixture,reason', [('missing.epub', 'no_supported_repair'),
                                           ('unrelated-error.epub', 'revalidation_failed')])
def test_real_refusal_and_revalidation(storage, tmp_path, monkeypatch, fixture, reason):
    reports = []
    validate = repairer.validate_epub
    def tracked(path):
        result = validate(path)
        reports.append(result)
        return result
    monkeypatch.setattr(repairer, 'validate_epub', tracked)
    source = Path(__file__).parent / 'fixtures' / 'rsc008' / fixture
    before = source.read_bytes()
    output = tmp_path / 'out.epub'
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == reason
    assert source.read_bytes() == before and not output.exists()
    event, = events(storage)
    assert event['repair_success'] is False
    assert event['refusal_reason'] == reason
    assert event['initial_epubcheck_codes'] == telemetry.validation_codes(reports[0])
    assert event['final_epubcheck_codes'] == (telemetry.validation_codes(reports[1])
                                             if len(reports) == 2 else None)
    assert len(reports) == (2 if reason == 'revalidation_failed' else 1)
    if reason == 'revalidation_failed':
        assert event['repair_attempted'] is True
        assert event['confirmed_safe_repairs'] == ['manifest_missing_referenced_resource']


def test_diagnosis_exception_preserves_partial_evidence(storage, monkeypatch):
    validation = stub_inspect(monkeypatch)
    original = TypeError('PRIVATE_PROGRAMMING_ERROR')
    def broken(*args, **kwargs):
        raise original
    monkeypatch.setattr(service, 'diagnose_epub', broken)
    with pytest.raises(TypeError) as caught:
        service.inspect_epub('PRIVATE_INPUT')
    assert caught.value is original
    event, = events(storage)
    assert event['operation_completed'] is False
    assert event['original_valid'] is validation['valid']
    assert event['confirmed_safe_repairs'] == []
    assert 'PRIVATE' not in storage.read_text()


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('mode,expected_calls', [('valid', 1), ('missing', 2)])
def test_no_extra_validation(storage, make_epub, tmp_path, monkeypatch, enabled, mode, expected_calls):
    source = make_epub(mode)
    calls = []
    original = repairer.validate_epub
    def validate(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(repairer, 'validate_epub', validate)
    if not enabled:
        monkeypatch.setenv('EPUB_DOCTOR_TELEMETRY_ENABLED', '0')
    result = service.repair_epub_file(str(source), str(tmp_path / 'out.epub'))
    assert len(calls) == expected_calls
    assert calls[0] == str(source)
    assert set(result) == {'original_valid', 'detected', 'repaired', 'final_valid',
                           'output_file', 'remaining_messages', 'reason'}
    if enabled:
        event, = events(storage)
        assert event['initial_epubcheck_codes'] == ([] if mode == 'valid' else ['PKG-006'])
        assert event['final_epubcheck_codes'] == (None if mode == 'valid' else [])
    else:
        assert not storage.exists()


@pytest.mark.parametrize('reason', ['output_is_input', 'no_supported_repair', 'already_valid'])
def test_initial_evidence_on_early_return(storage, make_epub, tmp_path, monkeypatch, reason):
    source = make_epub('missing')
    initial = report(valid=reason == 'already_valid')
    calls = []
    def validate(path):
        calls.append(path)
        return initial
    monkeypatch.setattr(repairer, 'validate_epub', validate)
    monkeypatch.setattr(repairer, 'diagnose_epub', lambda *args:
                        [] if reason == 'no_supported_repair' else ['mimetype_missing'])
    # Use codes with no special proof helper, but distinct sorted initial IDs.
    initial['messages'] = [{'ID': 'PKG-006'}, {'ID': 'RSC-005'}, {'ID': 'PKG-006'}]
    output = source if reason == 'output_is_input' else tmp_path / 'out.epub'
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == reason
    assert calls == [str(source)]
    event, = events(storage)
    assert event['initial_epubcheck_codes'] == ['PKG-006', 'RSC-005']
    assert event['final_epubcheck_codes'] is None


@pytest.mark.parametrize('final_valid', [True, False])
def test_distinct_stage_codes_and_privacy(storage, make_epub, tmp_path, monkeypatch, final_valid):
    source = make_epub('missing')
    initial = report()
    initial['messages'] = [{'ID': 'PKG-006', 'message': 'PRIVATE_INITIAL',
                            'locations': [{'path': 'PRIVATE_INITIAL_FILE'}]},
                           {'ID': 'private/path.epub'}, {'ID': 'PKG-006'}]
    final = report(final_valid)
    final['messages'] = [{'ID': 'RSC-005', 'message': 'PRIVATE_FINAL',
                          'locations': [{'path': 'PRIVATE_FINAL_FILE'}]},
                         {'ID': 'https://PRIVATE_URL'}, {'ID': 'bad'}]
    reports = iter([initial, final])
    calls = []
    def validate(path):
        calls.append(path)
        return next(reports)
    monkeypatch.setattr(repairer, 'validate_epub', validate)
    monkeypatch.setattr(repairer, 'diagnose_epub', lambda *args: ['mimetype_missing'])
    result = service.repair_epub_file(str(source), str(tmp_path / 'out.epub'))
    assert len(calls) == 2
    assert result['reason'] == (None if final_valid else 'revalidation_failed')
    event, = events(storage)
    assert event['initial_epubcheck_codes'] == ['PKG-006']
    assert event['final_epubcheck_codes'] == ['RSC-005']
    assert 'PRIVATE' not in storage.read_text()


@pytest.mark.parametrize('phase', ['initial', 'final'])
def test_transfer_failure_is_fail_open(storage, make_epub, tmp_path, monkeypatch, phase):
    source = make_epub('missing')
    codes = telemetry.validation_codes
    calls = []
    def broken_capture(validation):
        calls.append(validation)
        if len(calls) == (1 if phase == 'initial' else 2):
            raise ValueError('PRIVATE_TRANSFER_FAILURE')
        return codes(validation)
    monkeypatch.setattr(telemetry, 'validation_codes', broken_capture)
    result = service.repair_epub_file(str(source), str(tmp_path / 'out.epub'))
    assert result['repaired'] == ['mimetype_missing']
    event, = events(storage)
    assert event['repair_success'] is True
    assert event[phase + '_epubcheck_codes'] is None
    assert 'PRIVATE' not in storage.read_text()


def test_private_observer_exception_does_not_change_engine(make_epub, tmp_path):
    source = make_epub('missing')
    result = repairer._repair_epub(str(source), str(tmp_path / 'out.epub'),
                                  _validation_observer=fail)
    assert result['repaired'] == ['mimetype_missing']
    assert result['final_valid'] is True


def test_revalidation_execution_failure_has_no_final_codes(storage, make_epub, tmp_path, monkeypatch):
    source = make_epub('missing')
    original = repairer.validate_epub
    calls = []
    def validate(path):
        calls.append(path)
        if len(calls) == 2:
            raise EPUBCheckError('PRIVATE_CHECKER_FAILURE')
        return original(path)
    monkeypatch.setattr(repairer, 'validate_epub', validate)
    result = service.repair_epub_file(str(source), str(tmp_path / 'out.epub'))
    assert result['reason'] == 'epubcheck_failure'
    event, = events(storage)
    assert event['initial_epubcheck_codes'] == ['PKG-006']
    assert event['final_epubcheck_codes'] is None
    assert 'PRIVATE' not in storage.read_text()


def test_real_rsc008_success_preserves_initial_codes(storage, tmp_path, monkeypatch):
    source = Path(__file__).parent / 'fixtures/rsc008/png.epub'
    reports = []
    validate = repairer.validate_epub
    def tracked(path):
        result = validate(path)
        reports.append(result)
        return result
    monkeypatch.setattr(repairer, 'validate_epub', tracked)
    result = service.repair_epub_file(str(source), str(tmp_path / 'out.epub'))
    assert result['repaired'] == ['manifest_missing_referenced_resource']
    assert len(reports) == 2
    event, = events(storage)
    assert event['initial_epubcheck_codes'] == telemetry.validation_codes(reports[0])
    assert 'RSC-008' in event['initial_epubcheck_codes']
    assert event['final_epubcheck_codes'] == []
    assert result['remaining_messages'] == []


def test_inspect_calls_validator_once(storage, monkeypatch):
    validation = stub_inspect(monkeypatch)
    calls = []
    def validate(path):
        calls.append(path)
        return validation
    monkeypatch.setattr(service, 'validate_epub', validate)
    service.inspect_epub('input.epub')
    assert calls == ['input.epub']


def test_engine_exception_with_observer_failure_propagates(storage, make_epub, tmp_path, monkeypatch):
    original = TypeError('PRIVATE_ENGINE_FAILURE')
    def broken(*args):
        raise original
    monkeypatch.setattr(repairer, 'diagnose_epub', broken)
    monkeypatch.setattr(telemetry, 'validation_codes', fail)
    monkeypatch.setattr(telemetry, 'write_event', fail)
    with pytest.raises(TypeError) as caught:
        service.repair_epub_file(str(make_epub('missing')), str(tmp_path / 'out.epub'))
    assert caught.value is original
