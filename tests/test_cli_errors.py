"""Tests for wmw.cli.main's top-level error handling.

A network or API failure should print one line and exit 1 — never a traceback.
"""

from __future__ import annotations

from unittest.mock import patch

import requests
from wmw import cli, ena


def _run(side_effect) -> tuple[int, str]:
    with patch("wmw.cli.cmd_status", side_effect=side_effect):
        rc = cli.main(["status"])
    return rc


def test_main_reports_an_ena_error_without_a_traceback(capsys):
    rc = _run(ena.ENAError("ENA rejected the query (400): Invalid study_accession"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "Invalid study_accession" in err
    assert "Traceback" not in err


def test_main_reports_a_network_failure_without_a_traceback(capsys):
    rc = _run(requests.exceptions.ConnectionError("name resolution failed"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "Network request failed" in err
    assert "Traceback" not in err


def test_main_reports_an_http_error_without_a_traceback(capsys):
    """HTTPError subclasses RequestException, so a raw one is caught too."""
    rc = _run(requests.exceptions.HTTPError("400 Client Error"))
    assert rc == 1
    assert "Network request failed" in capsys.readouterr().err


def test_main_still_lets_other_exceptions_through():
    """Only network/ENA failures are softened — real bugs keep their traceback."""
    import pytest

    with pytest.raises(ValueError):
        _run(ValueError("a genuine bug"))
