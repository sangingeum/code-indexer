"""Logging-policy unit tests: noisy HTTP loggers demoted, results on stdout."""

from __future__ import annotations

import logging
import sys

import pytest

from code_indexer.logsetup import NOISY_LIBRARY_LOGGERS, configure_logging


@pytest.fixture(autouse=True)
def _restore_logging():
    """Snapshot/restore the logging state configure_logging touches."""
    before = {
        name: (logging.getLogger(name).level,
               logging.getLogger(name).propagate,
               list(logging.getLogger(name).handlers))
        for name in NOISY_LIBRARY_LOGGERS
    }
    yield
    for name, (level, propagate, handlers) in before.items():
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = propagate
        lg.handlers[:] = handlers


def test_default_demotes_noisy_loggers_to_warning():
    for name in NOISY_LIBRARY_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)
        logging.getLogger(name).propagate = True
    configure_logging(verbose=False)
    for name in NOISY_LIBRARY_LOGGERS:
        lg = logging.getLogger(name)
        assert lg.level == logging.WARNING, name
        assert lg.propagate is False, name
        assert lg.handlers, f"{name} has no stderr handler"


def test_verbose_raises_to_info():
    configure_logging(verbose=True)
    for name in NOISY_LIBRARY_LOGGERS:
        assert logging.getLogger(name).level == logging.INFO, name


def test_no_log_record_reaches_stdout(capsys):
    """The stderr handler must be attached, so nothing lands on stdout."""
    configure_logging(verbose=True)
    lg = logging.getLogger("httpx")
    lg.info("GET http://192.168.1.105:6333/collections — synthetic probe")
    captured = capsys.readouterr()
    assert captured.out == "", "log records leaked to stdout"
    assert "synthetic probe" in captured.err


def test_config_is_idempotent():
    configure_logging(verbose=False)
    first = list(logging.getLogger("httpx").handlers)
    configure_logging(verbose=False)
    assert list(logging.getLogger("httpx").handlers) == first
