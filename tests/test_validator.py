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
