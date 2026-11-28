from unittest.mock import create_autospec

import pytest
from vmctl_core.backends import LibvirtBackend

import guest_exec
import guest_upload


@pytest.mark.parametrize(
    ("argv", "uri", "command"),
    [
        (["test-vm", "echo two words"], "qemu:///system", "echo two words"),
        (["--uri", "test:///default", "test-vm", "-h", "ignored"], "test:///default", "-h"),
    ],
)
def test_exec_cli(monkeypatch, capsys, argv, uri, command):
    backend = create_autospec(LibvirtBackend)
    backend.return_value.guest_exec.return_value = (23, "output", "error\n")
    monkeypatch.setattr(guest_exec, "LibvirtBackend", backend)

    assert guest_exec.main(argv) == 23

    backend.assert_called_once_with("test-vm", uri=uri)
    backend.return_value.guest_exec.assert_called_once_with("cmd.exe", ["/c", command])
    assert capsys.readouterr() == ("output", "error\n")


@pytest.mark.parametrize(
    ("options", "uri"), [([], "qemu:///system"), (["--uri", "test:///default"], "test:///default")]
)
def test_upload_cli(monkeypatch, capsys, tmp_path, options, uri):
    backend = create_autospec(LibvirtBackend)
    monkeypatch.setattr(guest_upload, "LibvirtBackend", backend)
    local = tmp_path / "test file.bin"
    local.write_bytes(b"\x00\xff\r\n")

    assert guest_upload.main(["test-vm", "C:/test file.bin", str(local), *options]) == 0

    backend.assert_called_once_with("test-vm", uri=uri)
    backend.return_value.guest_write_file.assert_called_once_with(
        "C:/test file.bin", b"\x00\xff\r\n"
    )
    assert capsys.readouterr() == (f"  {local} -> C:/test file.bin (4 bytes)\n", "")


@pytest.mark.parametrize("cli", [guest_exec, guest_upload])
def test_missing_operands_exit_one(cli):
    with pytest.raises(SystemExit) as error:
        cli.main(["test-vm"])
    assert error.value.code == 1
