import json
from subprocess import CompletedProcess

import pytest

from src import validator


def report(errors=0, warnings=0):
    return {"checker": {"nFatal": 0, "nError": errors, "nWarning": warnings},
            "messages": [], "publication": {}}


def fake_run(monkeypatch, payload, code=0):
    def run(command, **kwargs):
        from pathlib import Path
        assert command[:3] == ["java", "-Xmx512m", "-jar"]
        assert kwargs["timeout"] == validator.EPUBCHECK_TIMEOUT_SECONDS
        assert kwargs['stdout'] == kwargs['stderr'] == validator.subprocess.DEVNULL
        assert 'capture_output' not in kwargs
        if payload is not None:
            Path(command[-1]).write_text(payload)
        return CompletedProcess(command, code, "", "test stderr")
    monkeypatch.setattr(validator.subprocess, "run", run)


@pytest.mark.parametrize("code,errors,warnings,valid", [(0, 0, 0, True), (1, 1, 0, False), (0, 0, 1, True)])
def test_report(monkeypatch, tmp_path, code, errors, warnings, valid):
    path = tmp_path / "input.epub"
    path.touch()
    fake_run(monkeypatch, json.dumps(report(errors, warnings)), code)
    result = validator.validate_epub(str(path))
    assert result["valid"] is valid
    assert result["warning_count"] == warnings
    assert result["error_count"] == errors


@pytest.mark.parametrize("payload", [None, "{", "{}", '[]', json.dumps({**report(), "messages": [None]}), json.dumps({**report(), "checker": {"nFatal": "0", "nError": 0, "nWarning": 0}})])
def test_bad_report(monkeypatch, tmp_path, payload):
    path = tmp_path / "input.epub"
    path.touch()
    fake_run(monkeypatch, payload)
    with pytest.raises(validator.EPUBCheckError, match="JSON"):
        validator.validate_epub(str(path))


def test_execution_failure(monkeypatch, tmp_path):
    path = tmp_path / "input.epub"
    path.touch()
    fake_run(monkeypatch, None, 2)
    with pytest.raises(validator.EPUBCheckError, match="exit 2"):
        validator.validate_epub(str(path))


def test_oversized_report_is_rejected_before_parse_and_cleaned(monkeypatch, tmp_path):
    from pathlib import Path
    source = tmp_path / 'input.epub'
    source.touch()
    reports = []
    def run(command, **kwargs):
        path = Path(command[-1])
        reports.append(path)
        with path.open('wb') as output:
            output.truncate(validator.MAX_EPUBCHECK_REPORT_BYTES + 1)
        return CompletedProcess(command, 0)
    monkeypatch.setattr(validator.subprocess, 'run', run)
    monkeypatch.setattr(validator.json, 'load', lambda *args: pytest.fail('Oversized report was parsed'))
    with pytest.raises(validator.EPUBCheckError, match='report exceeds'):
        validator.validate_epub(str(source))
    assert not reports[0].parent.exists()


def test_report_at_size_limit_is_accepted(monkeypatch, tmp_path):
    source = tmp_path / 'input.epub'
    source.touch()
    payload = json.dumps(report())
    monkeypatch.setattr(validator, 'MAX_EPUBCHECK_REPORT_BYTES', len(payload.encode('utf-8')))
    fake_run(monkeypatch, payload)
    assert validator.validate_epub(str(source))['valid'] is True


@pytest.mark.parametrize('stage', ['initial', 'final'])
def test_oversized_report_never_publishes(monkeypatch, make_epub, tmp_path, stage):
    from pathlib import Path
    from src import service
    source = make_epub('missing')
    before = source.read_bytes()
    output = tmp_path / 'out.epub'
    real_run = validator.subprocess.run
    reports = []
    def run(command, **kwargs):
        reports.append(Path(command[-1]))
        if stage == 'initial' or len(reports) == 2:
            with reports[-1].open('wb') as target:
                target.truncate(validator.MAX_EPUBCHECK_REPORT_BYTES + 1)
            return CompletedProcess(command, 0)
        return real_run(command, **kwargs)
    monkeypatch.setattr(validator.subprocess, 'run', run)
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == 'epubcheck_failure'
    assert result['output_file'] is None and result['repaired'] == []
    assert source.read_bytes() == before
    assert set(tmp_path.iterdir()) == {source}
    assert len(reports) == (1 if stage == 'initial' else 2)
    assert all(not path.parent.exists() for path in reports)


def test_missing_java(monkeypatch, tmp_path):
    path = tmp_path / "input.epub"
    path.touch()
    def fail(*args, **kwargs):
        raise FileNotFoundError("java")
    monkeypatch.setattr(validator.subprocess, "run", fail)
    with pytest.raises(validator.EPUBCheckError, match="Java"):
        validator.validate_epub(str(path))


def test_missing_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        validator.validate_epub(str(tmp_path / "absent.epub"))


def test_missing_jar(monkeypatch, tmp_path):
    path = tmp_path / "input.epub"
    path.touch()
    monkeypatch.setattr(validator, "EPUBCHECK_JAR", tmp_path / "absent.jar")
    with pytest.raises(validator.EPUBCheckError, match="JAR"):
        validator.validate_epub(str(path))


def test_real_valid_epub(make_epub):
    result = validator.validate_epub(str(make_epub()))
    assert result["valid"], result["messages"]
    assert result["fatal_count"] == result["error_count"] == result["warning_count"] == 0


def test_timeout_preserves_error_contract_and_cleans_report(monkeypatch, tmp_path):
    from pathlib import Path
    from subprocess import TimeoutExpired
    path = tmp_path / 'input.epub'
    path.touch()
    reports = []
    original = TimeoutExpired(['java'], validator.EPUBCHECK_TIMEOUT_SECONDS,
                              output=b'PRIVATE_OUTPUT', stderr=b'PRIVATE_STDERR')
    def run(command, **kwargs):
        assert kwargs['timeout'] == 60
        reports.append(Path(command[-1]))
        reports[-1].write_text('partial report')
        raise original
    monkeypatch.setattr(validator.subprocess, 'run', run)
    with pytest.raises(validator.EPUBCheckError, match='timed out after 60 seconds') as caught:
        validator.validate_epub(str(path))
    assert caught.value.__cause__ is original
    assert 'PRIVATE' not in str(caught.value)
    assert not reports[0].parent.exists()


def test_actual_subprocess_timeout(monkeypatch, tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    path = tmp_path / 'input.epub'
    path.touch()
    reports = []
    real_run = subprocess.run
    def stalled(command, **kwargs):
        reports.append(Path(command[-1]))
        return real_run([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
    monkeypatch.setattr(validator, 'EPUBCHECK_TIMEOUT_SECONDS', 0.1)
    monkeypatch.setattr(validator.subprocess, 'run', stalled)
    with pytest.raises(validator.EPUBCheckError, match='timed out') as caught:
        validator.validate_epub(str(path))
    assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired)
    assert not reports[0].parent.exists()


@pytest.mark.parametrize('stage', ['initial', 'final'])
def test_service_repair_timeout_never_publishes(monkeypatch, make_epub, tmp_path, stage):
    from subprocess import TimeoutExpired
    from src import service
    source = make_epub('missing')
    before = source.read_bytes()
    output = tmp_path / 'out.epub'
    original = validator.subprocess.run
    reports = []
    def run(command, **kwargs):
        from pathlib import Path
        reports.append(Path(command[-1]))
        if stage == 'initial' or len(reports) == 2:
            assert kwargs['timeout'] == validator.EPUBCHECK_TIMEOUT_SECONDS
            raise TimeoutExpired(command, kwargs['timeout'])
        return original(command, **kwargs)
    monkeypatch.setattr(validator.subprocess, 'run', run)
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == 'epubcheck_failure'
    assert result['final_valid'] is None
    assert result['output_file'] is None and result['repaired'] == []
    assert source.read_bytes() == before and not output.exists()
    assert set(tmp_path.iterdir()) == {source}
    assert len(reports) == (1 if stage == 'initial' else 2)
    assert all(not path.parent.exists() for path in reports)


def test_inspect_timeout_propagates(monkeypatch, make_epub):
    from subprocess import TimeoutExpired
    from src import service
    def run(command, **kwargs):
        raise TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(validator.subprocess, 'run', run)
    with pytest.raises(validator.EPUBCheckError, match='timed out'):
        service.inspect_epub(str(make_epub()))


@pytest.mark.parametrize('stage', ['initial', 'final'])
def test_heap_or_os_memory_failure_never_publishes(monkeypatch, make_epub, tmp_path, stage):
    from src import service
    source = make_epub('missing')
    before = source.read_bytes()
    output = tmp_path / 'out.epub'
    real_run = validator.subprocess.run
    calls = []
    def run(command, **kwargs):
        assert command[:3] == ['java', '-Xmx512m', '-jar']
        calls.append(command)
        if stage == 'initial' or len(calls) == 2:
            return CompletedProcess(command, -9, '', 'memory failure')
        return real_run(command, **kwargs)
    monkeypatch.setattr(validator.subprocess, 'run', run)
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == 'epubcheck_failure'
    assert result['repaired'] == [] and result['output_file'] is None
    assert source.read_bytes() == before and not output.exists()
    assert set(tmp_path.iterdir()) == {source}
    assert len(calls) == (1 if stage == 'initial' else 2)
