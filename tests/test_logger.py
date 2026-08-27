import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from helm.core.logger import FileOnlyFilter


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
