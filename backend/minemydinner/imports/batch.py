import re

MAX_BATCH_BYTES = 1024 * 1024
MAX_BATCH_ITEMS = 50
SEPARATOR = "---"


class BatchFileError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _decode(data: bytes) -> str:
    # Windows Notepad saves "Unicode" files as UTF-16 with a byte order mark.
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    else:
        encodings = ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BatchFileError("unreadable_encoding")


def split_batch(data: bytes) -> list[str]:
    """Split a text file into one text per dish.

    Lines of only "---" separate dishes. Without such a line, blank lines do.
    """
    if len(data) > MAX_BATCH_BYTES:
        raise BatchFileError("too_large")
    text = _decode(data).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if any(line.strip() == SEPARATOR for line in lines):
        chunks, current = [], []
        for line in lines:
            if line.strip() == SEPARATOR:
                chunks.append("\n".join(current))
                current = []
            else:
                current.append(line)
        chunks.append("\n".join(current))
    else:
        chunks = re.split(r"\n\s*\n", text)
    items = [chunk.strip() for chunk in chunks if chunk.strip()]
    if not items:
        raise BatchFileError("empty")
    if len(items) > MAX_BATCH_ITEMS:
        raise BatchFileError("too_many_items")
    return items
