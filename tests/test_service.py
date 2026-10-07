import ast
import subprocess
import sys
from pathlib import Path
from zipfile import BadZipFile

import pytest

from src import main, service, validator
from src.zip_inspection import UnsafeZipError


@pytest.mark.parametrize('mode,valid,diagnoses', [
    ('valid', True, []),
    ('missing', False, ['mimetype_missing']),
    ('incorrect', False, ['mimetype_incorrect_content']),
    ('extra', False, ['mimetype_has_extra_field']),
])
def test_real_inspection_read_only(make_epub, tmp_path, mode, valid, diagnoses):
    source = make_epub(mode)
    before = source.read_bytes()
    files = set(tmp_path.iterdir())
    result = service.inspect_epub(str(source))
    assert set(result) == {'valid', 'repairable', 'safe_repairs', 'fatal_count',
                           'error_count', 'warning_count', 'messages', 'publication'}
    assert result['valid'] is valid
    assert result['safe_repairs'] == diagnoses
    assert result['repairable'] is bool(diagnoses)
    assert all(type(result[key]) is int for key in
               ('fatal_count', 'error_count', 'warning_count'))
    assert isinstance(result['messages'], list)
    assert isinstance(result['publication'], dict)
    assert source.read_bytes() == before
    assert set(tmp_path.iterdir()) == files


def test_real_unsupported_inspection(tmp_path):
    source = tmp_path / 'invalid.epub'
    source.write_bytes(b'not a ZIP')
    result = service.inspect_epub(str(source))
    assert result['valid'] is False
    assert result['fatal_count'] + result['error_count'] > 0
    assert result['safe_repairs'] == []
    assert result['repairable'] is False
    assert source.read_bytes() == b'not a ZIP'
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.parametrize('valid,diagnoses', [(False, []), (False, ['mimetype_missing']),
                                            (True, []), (True, ['mimetype_missing'])])
def test_inspection_reuses_report_and_diagnosis(monkeypatch, valid, diagnoses):
    report = dict(valid=valid, fatal_count=2, error_count=3, warning_count=4,
                  messages=[{'ID': 'OTHER', 'message': 'preserve'}],
                  publication={'title': 'preserve'})
    calls = []
    def validate(path):
        calls.append(('validate', path))
        return report
    def diagnose(path, actual_report):
        assert actual_report is report
        calls.append(('diagnose', path))
        return diagnoses
    monkeypatch.setattr(service, 'validate_epub', validate)
    monkeypatch.setattr(service, 'diagnose_epub', diagnose)
    result = service.inspect_epub('book.epub')
    assert calls == [('validate', 'book.epub'), ('diagnose', 'book.epub')]
    assert result == {**report, 'safe_repairs': diagnoses, 'repairable': bool(diagnoses)}


def test_repair_delegates_unchanged(monkeypatch):
    expected = dict(original_valid=False, detected=['mimetype_missing'], repaired=[],
                    final_valid=False, output_file=None, remaining_messages=[{'ID': 'OTHER'}],
                    reason='unsafe_metadata_preservation', detail='preserve')
    def repair(source, output):
        assert (source, output) == ('in.epub', 'out.epub')
        return expected
    monkeypatch.setattr(service, 'repair_epub', repair)
    assert service.repair_epub_file('in.epub', 'out.epub') is expected


def test_real_successful_repair(make_epub, tmp_path):
    source = make_epub('missing')
    original = source.read_bytes()
    output = tmp_path / 'out.epub'
    result = service.repair_epub_file(str(source), str(output))
    assert result == dict(original_valid=False, detected=['mimetype_missing'],
                          repaired=['mimetype_missing'], final_valid=True,
                          output_file=str(output), remaining_messages=[], reason=None)
    assert source.read_bytes() == original
    assert service.inspect_epub(str(output))['valid'] is True


def test_real_refused_repair(make_epub, tmp_path):
    source = make_epub('missing')
    original = source.read_bytes()
    result = service.repair_epub_file(str(source), str(source))
    assert result['reason'] == 'output_is_input'
    assert result['repaired'] == []
    assert result['output_file'] is None
    assert source.read_bytes() == original
    assert set(tmp_path.iterdir()) == {source}


def test_missing_input(tmp_path):
    source, output = tmp_path / 'missing.epub', tmp_path / 'out.epub'
    with pytest.raises(FileNotFoundError):
        service.inspect_epub(str(source))
    assert service.repair_epub_file(str(source), str(output))['reason'] == 'input_read_failed'
    assert not output.exists()


@pytest.mark.parametrize('failure', ['jar', 'java', 'report'])
def test_environment_failures(monkeypatch, make_epub, tmp_path, failure):
    source = make_epub()
    if failure == 'jar':
        monkeypatch.setattr(validator, 'EPUBCHECK_JAR', tmp_path / 'absent.jar')
    else:
        def run(*args, **kwargs):
            if failure == 'java':
                raise FileNotFoundError('Java missing')
            return subprocess.CompletedProcess(args[0], 0, '', '')
        monkeypatch.setattr(validator.subprocess, 'run', run)
    with pytest.raises(validator.EPUBCheckError):
        service.inspect_epub(str(source))
    result = service.repair_epub_file(str(source), str(tmp_path / 'out.epub'))
    assert result['reason'] == 'epubcheck_failure'
    assert result['original_valid'] is result['final_valid'] is None
    assert result['output_file'] is None
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.parametrize('error', [BadZipFile('bad ZIP'), PermissionError('denied'),
                                   UnsafeZipError('invalid_zip_structure', 'unsafe')])
def test_diagnosis_failures_propagate(monkeypatch, error):
    monkeypatch.setattr(service, 'validate_epub', lambda path: {})
    def diagnose(*args):
        raise error
    monkeypatch.setattr(service, 'diagnose_epub', diagnose)
    with pytest.raises(type(error)) as caught:
        service.inspect_epub('in.epub')
    assert caught.value is error


@pytest.mark.parametrize('entrypoint', ['inspect', 'repair'])
def test_programming_errors_propagate(monkeypatch, entrypoint):
    def fail(*args):
        raise TypeError('programming error')
    monkeypatch.setattr(service, 'validate_epub' if entrypoint == 'inspect' else 'repair_epub', fail)
    with pytest.raises(TypeError, match='programming error'):
        if entrypoint == 'inspect':
            service.inspect_epub('in.epub')
        else:
            service.repair_epub_file('in.epub', 'out.epub')


def test_cli_uses_service(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['main', 'in.epub', 'out.epub'])
    def repair(source, output):
        assert (source, output) == ('in.epub', 'out.epub')
        return {'reason': 'test'}
    monkeypatch.setattr(main, 'repair_epub_file', repair)
    main.main()
    assert ast.literal_eval(capsys.readouterr().out) == {'reason': 'test'}


def test_real_cli_command(make_epub, tmp_path):
    source = make_epub('missing')
    output = tmp_path / 'out.epub'
    result = subprocess.run([sys.executable, '-m', 'src.main', str(source), str(output)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = ast.literal_eval(result.stdout)
    assert report['final_valid'] is True
    assert report['repaired'] == ['mimetype_missing']
    assert report['output_file'] == str(output)


def test_cli_usage(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['main'])
    with pytest.raises(SystemExit) as caught:
        main.main()
    assert caught.value.code == 1
    assert capsys.readouterr().out == 'Usage: python main.py <input.epub> <output.epub>\n'
