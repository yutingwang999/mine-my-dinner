import pytest

from minemydinner.imports.batch import BatchFileError, split_batch


def assert_batch_error(data: bytes, reason: str) -> None:
    with pytest.raises(BatchFileError) as caught:
        split_batch(data)
    assert caught.value.reason == reason


def test_splits_on_separator_line():
    data = "番茄炒蛋\n鸡蛋 番茄\n---\n土豆丝\n土豆".encode()
    assert split_batch(data) == ["番茄炒蛋\n鸡蛋 番茄", "土豆丝\n土豆"]


def test_separator_keeps_blank_lines_inside_an_item():
    data = "番茄炒蛋\n\n鸡蛋 番茄\n  ---  \n土豆丝".encode()
    assert split_batch(data) == ["番茄炒蛋\n\n鸡蛋 番茄", "土豆丝"]


def test_without_separator_splits_on_blank_lines():
    assert split_batch("番茄炒蛋\n\n\n土豆丝".encode()) == ["番茄炒蛋", "土豆丝"]


def test_windows_line_endings():
    assert split_batch("番茄炒蛋\r\n\r\n土豆丝\r\n".encode()) == ["番茄炒蛋", "土豆丝"]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gb18030", "utf-16"])
def test_reads_common_encodings(encoding):
    assert split_batch("番茄炒蛋".encode(encoding)) == ["番茄炒蛋"]


def test_empty_and_oversized_files_are_rejected():
    assert_batch_error(b"  \n", "empty")
    assert_batch_error(b"\n---\n\n---\n", "empty")
    assert_batch_error(b"x" * (1024 * 1024 + 1), "too_large")
    assert_batch_error(b"\n---\n".join([b"x"] * 51), "too_many_items")


def test_fifty_items_are_allowed():
    assert len(split_batch(b"\n---\n".join([b"x"] * 50))) == 50
