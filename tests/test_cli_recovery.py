"""Tests for the unlock and resume study recovery commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from wmw import cli
from wmw.airtable import UnknownSelectOptionError


@pytest.mark.parametrize("command", ["unlock", "resume"])
def test_recovery_parser_requires_study(command):
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([command])

    args = parser.parse_args([command, "--study", "ST001"])
    assert args.study == "ST001"
    assert args.func is getattr(cli, f"cmd_{command}")


@pytest.mark.parametrize("command", ["unlock", "resume"])
def test_recovery_updates_study_and_prints_next_step(command):
    args = cli._build_parser().parse_args([
        command, "--study", "ST001", "--studies-table", "MyStudies",
        "--samples-table", "MySamples", "--slurm",
    ])
    client = MagicMock()
    client.fetch_study_by_code.return_value = {"id": "recStudy"}
    with (
        patch("wmw.cli._require_airtable", return_value=client) as require,
        patch.object(cli.out, "info") as info,
    ):
        assert args.func(args) == 0

    require.assert_called_once_with(args, "MyStudies", "MySamples")
    client.fetch_study_by_code.assert_called_once_with("MyStudies", "ST001")
    client.set_study_status.assert_called_once_with("MyStudies", ["recStudy"], command)
    assert any("wmw process --batch ST001 --slurm" in str(call) for call in info.call_args_list)


@pytest.mark.parametrize("command", ["unlock", "resume"])
def test_recovery_rejects_missing_study(command):
    args = cli._build_parser().parse_args([command, "--study", "MISSING"])
    client = MagicMock()
    client.fetch_study_by_code.return_value = None
    with (
        patch("wmw.cli._require_airtable", return_value=client),
        patch.object(cli.out, "error") as error,
        pytest.raises(SystemExit, match="1"),
    ):
        args.func(args)

    client.set_study_status.assert_not_called()
    assert "MISSING" in str(error.call_args)


@pytest.mark.parametrize("command", ["unlock", "resume"])
def test_recovery_processes_requested_batch(command):
    args = cli._build_parser().parse_args([
        command, "--study", "ST001", "--process", "--slurm",
        "--studies-table", "MyStudies", "--samples-table", "MySamples",
        "--airtable-token", "test-token", "--base-id", "test-base",
    ])
    client = MagicMock()
    client.fetch_study_by_code.return_value = {"id": "recStudy"}
    with (
        patch("wmw.cli._require_airtable", return_value=client),
        patch("wmw.cli.cmd_process", return_value=7) as process,
    ):
        assert args.func(args) == 7

    client.set_study_status.assert_called_once_with("MyStudies", ["recStudy"], command)
    process_args = process.call_args.args[0]
    assert process_args.batch == "ST001"
    assert process_args.workflow == "preprocessing"
    assert process_args.only is False
    assert process_args.slurm is True
    assert process_args.studies_table == "MyStudies"
    assert process_args.samples_table == "MySamples"
    assert process_args.airtable_token == "test-token"
    assert process_args.base_id == "test-base"


@pytest.mark.parametrize("command", ["unlock", "resume"])
def test_recovery_fails_when_airtable_lacks_status(command):
    args = cli._build_parser().parse_args([command, "--study", "ST001", "--process"])
    client = MagicMock()
    client.fetch_study_by_code.return_value = {"id": "recStudy"}
    client.set_study_status.side_effect = UnknownSelectOptionError("unknown option")
    with (
        patch("wmw.cli._require_airtable", return_value=client),
        patch("wmw.cli.cmd_process") as process,
        pytest.raises(SystemExit),
    ):
        args.func(args)

    process.assert_not_called()


def test_recovery_with_unknown_study_does_not_process():
    args = cli._build_parser().parse_args(["resume", "--study", "NOPE", "--process"])
    client = MagicMock()
    client.fetch_study_by_code.return_value = None
    with (
        patch("wmw.cli._require_airtable", return_value=client),
        patch("wmw.cli.cmd_process") as process,
        pytest.raises(SystemExit),
    ):
        args.func(args)

    process.assert_not_called()
