import io
import json

import pytest

from smart_search import cli


def result_for(body: bytes, encoding="utf-8"):
    return {"ok": True, "provider": "direct", "content_format": "raw_text",
            "content": body.decode(encoding), "encoding": encoding, "truncated": False}


@pytest.mark.parametrize("body, encoding", [
    (b"#include <assert.h>\n#define Py_PYTHON_H", "utf-8"),
    (b"line1\r\n\r\n\tline_2\r\n", "utf-8"),
    (b"\xef\xbb\xbf// UTF-8 BOM\n", "utf-8"),
    ("# café\r\n".encode("iso-8859-1"), "iso-8859-1"),
    ("// 中文\r\n".encode("utf-16"), "utf-16"),
    (b"", "utf-8"),
])
def test_raw_content_output_preserves_original_bytes_on_file_and_pipe(monkeypatch, tmp_path, body, encoding):
    data = result_for(body, encoding)
    output = tmp_path / "nested" / "source.h"
    byte_stream = io.BytesIO()
    # Simulate Windows text newline conversion and a different console encoding;
    # the raw fetch branch must bypass both for redirected stdout.
    stream = io.TextIOWrapper(byte_stream, encoding="ascii", newline="\r\n")
    monkeypatch.setattr(cli.sys, "stdout", stream)

    assert cli._print_result("fetch", data, "content", str(output)) == cli.EXIT_OK
    assert output.read_bytes() == body
    assert byte_stream.getvalue() == body


def test_raw_content_with_text_only_stdout_keeps_exact_text(monkeypatch):
    body = "\ufeff// 中文\r\n#define NAME_value 1"
    stream = io.StringIO(newline="")
    monkeypatch.setattr(cli.sys, "stdout", stream)
    data = result_for(body.encode("utf-8"))

    assert cli._print_result("fetch", data, "content") == cli.EXIT_OK
    assert stream.getvalue() == body
    assert cli._render("fetch", data, "content") == body


def test_raw_content_cli_command_uses_lossless_output(monkeypatch, tmp_path, capsysbinary):
    body = b"#include <assert.h>\r\n#define Py_PYTHON_H"

    async def fetch(url, timeout_seconds=None):
        return result_for(body)

    monkeypatch.setattr(cli.service, "fetch", fetch)
    output = tmp_path / "source.h"
    code = cli.main(["fetch", "https://example.org/source.h", "--format", "content", "--output", str(output)])
    assert code == cli.EXIT_OK
    assert output.read_bytes() == body
    assert capsysbinary.readouterr().out == body


def test_raw_fetch_json_still_roundtrips_content(monkeypatch, capsys):
    data = result_for(b"line1\r\n#define NAME_value 1")
    assert cli._print_result("fetch", data, "json") == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["content"] == data["content"]


def test_raw_fetch_error_keeps_normal_error_rendering(capsys):
    data = {"ok": False, "content_format": "raw_text", "content": "",
            "error_type": "quality_error", "error": "HTML response rejected"}
    assert cli._print_result("fetch", data, "content") == cli.EXIT_NETWORK_ERROR
    assert "HTML response rejected" in capsys.readouterr().out
