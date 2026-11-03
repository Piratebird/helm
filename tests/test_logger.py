import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from helm.logger import FileOnlyFilter


def test_file_only_filter_drops_file_only_records():
    filt = FileOnlyFilter()
    record = logging.LogRecord("test", logging.WARNING, __file__, 1, "hidden", (), None)
    record.file_only = True
    assert not filt.filter(record)


def test_file_only_filter_keeps_normal_records():
    filt = FileOnlyFilter()
    record = logging.LogRecord("test", logging.WARNING, __file__, 1, "visible", (), None)
    assert filt.filter(record)


def test_file_only_records_skipped_by_console_but_kept_by_file(capsys):
    from io import StringIO

    stream = StringIO()
    buffer = logging.StreamHandler(stream)
    buffer.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.addFilter(FileOnlyFilter())

    logger = logging.getLogger("test_file_only_console")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(console_handler)
    logger.addHandler(buffer)

    logger.warning("visible warning")
    logger.warning("hidden warning", extra={"file_only": True})

    out, err = capsys.readouterr()
    assert "visible warning" in err
    assert "hidden warning" not in err

    buffer.flush()
    captured = stream.getvalue()
    assert "visible warning" in captured
    assert "hidden warning" in captured


def test_importing_logging_does_not_create_files(tmp_path):
    import subprocess

    environment = os.environ.copy()
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src"))
    environment.update(
        PYTHONPATH=root, HELM_CONFIG_DIR=str(tmp_path / "config"), HELM_STATE_DIR=str(tmp_path / "state")
    )
    result = subprocess.run(
        [sys.executable, "-c", "import helm.cli; import helm.search.lite_fetcher"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "config").exists()


def test_logging_reconfigures_after_path_override(monkeypatch, tmp_path):
    from helm.logger import configure_logging, get_logger

    first, second = tmp_path / "first", tmp_path / "second"
    monkeypatch.setenv("HELM_STATE_DIR", str(first))
    configure_logging()
    get_logger("helm.test.paths").info("first-message")
    monkeypatch.setenv("HELM_STATE_DIR", str(second))
    configure_logging()
    get_logger("helm.test.paths").info("second-message")
    assert "first-message" in (first / "helm.log").read_text()
    assert "second-message" not in (first / "helm.log").read_text()
    assert "second-message" in (second / "helm.log").read_text()


def test_formatters_redact_exception_and_structured_extras(monkeypatch):
    from helm.logger import ConsoleFormatter, JSONFormatter, PlainTextFormatter

    monkeypatch.setenv("JACKETT_API_KEY", "private-key-123")
    try:
        raise ValueError("https://user:pass@example.test/search?apikey=unknown-token&x=1 private-key-123")
    except ValueError:
        record = logging.LogRecord(
            "helm.test", logging.ERROR, __file__, 1, "request failed private-key-123", (), sys.exc_info()
        )
    record.details = {"password": "extra-password", "url": "https://example.test/?token=other-secret"}
    for formatter in (ConsoleFormatter(), JSONFormatter(), PlainTextFormatter()):
        formatted = formatter.format(record)
        assert "private-key-123" not in formatted
        assert "unknown-token" not in formatted
        assert "user:pass" not in formatted
        assert "other-secret" not in formatted
        assert "extra-password" not in formatted
    import json

    assert json.loads(JSONFormatter().format(record))["details"]["password"] == "***REDACTED***"


def test_tail_reads_only_bounded_suffix_and_redacts(tmp_path, monkeypatch):
    from helm.logger import tail_log

    path = tmp_path / "large.log"
    path.write_text("old line\n" * 200000 + "apikey=old-unknown-secret\nlast line\n")
    monkeypatch.setenv("JACKETT_API_KEY", "known-secret")
    result = tail_log(path, lines=2, max_bytes=256)
    assert "old-unknown-secret" not in result
    assert result.endswith("last line\n")
    assert len(result.splitlines()) == 2
    assert tail_log(path, lines=0) == ""


def test_tail_handles_a_single_oversized_record(tmp_path):
    from helm.logger import tail_log

    path = tmp_path / "huge.log"
    path.write_text("x" * 10000)
    assert len(tail_log(path, max_bytes=64).encode()) <= 65


def test_missing_prowlarr_key_goes_to_the_log_file_only(tmp_path, monkeypatch, capsys):
    import logging

    from helm.logger import configure_logging
    from helm.search import rss_fetcher

    monkeypatch.setenv("HELM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("HELM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("PROWLARR_API_KEY", raising=False)
    monkeypatch.delenv("HELM_VERBOSE", raising=False)
    monkeypatch.setattr(rss_fetcher, "get_secret", lambda key: None)
    configure_logging()
    try:
        assert rss_fetcher.search_prowlarr("ubuntu") == []
    finally:
        for handler in logging.getLogger().handlers:
            handler.flush()
    assert "Prowlarr API key" not in capsys.readouterr().err
    assert "Prowlarr API key not configured" in (tmp_path / "helm.log").read_text()
    configure_logging(file_logging=False)
