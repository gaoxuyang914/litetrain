from __future__ import annotations

from pathlib import Path

from app.services.logs import LogStore, clean_log_text


def test_clean_log_text_strips_ansi_and_normalizes_carriage_returns():
    raw = "\x1b[34mtrain\x1b[0m: 10%\rprogress: 20%\n"
    cleaned = clean_log_text(raw)
    assert "\x1b" not in cleaned
    assert "\r" not in cleaned
    assert cleaned == "train: 10%\nprogress: 20%\n"


def test_incremental_byte_offsets_and_ansi_stripping(tmp_workdir):
    store = LogStore(tmp_workdir)
    store.append("task-1", "hello \x1b[32mworld\x1b[0m")
    store.append("task-1", "第二行中文日志")

    first = store.read_chunk("task-1", offset=0, limit=1024)
    assert "hello world" in first.content
    assert "第二行中文日志" in first.content
    assert first.eof is True
    assert first.next_offset == first.file_size

    second = store.read_chunk("task-1", offset=first.next_offset, limit=1024)
    assert second.content == ""
    assert second.eof is True


def test_read_chunk_respects_limit_and_eof(tmp_workdir):
    store = LogStore(tmp_workdir)
    store.append("task-2", "0123456789" * 1000)
    first = store.read_chunk("task-2", offset=0, limit=1024)
    assert first.next_offset == 1024
    assert first.eof is False
    assert len(first.content.encode("utf-8")) <= 1024

    rest = store.read_chunk("task-2", offset=first.next_offset, limit=20_000)
    assert rest.eof is True
    assert rest.next_offset == rest.file_size
    assert first.content + rest.content == ("0123456789" * 1000) + "\n"


def test_log_store_handles_missing_file_by_creating_it(tmp_workdir):
    store = LogStore(tmp_workdir)
    window = store.read_chunk("never-seen", offset=0)
    assert window.content == ""
    assert window.file_size == 0
    assert (tmp_workdir / "never-seen.log").exists()
