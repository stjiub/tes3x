"""Colour a job's log by line: section headers, warnings and errors stand out from progress text."""

import re

from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor

ERROR = re.compile(r"^(error|fatal)\b|Traceback \(most recent|\bfailed\b|^\s*\w*Error:", re.I)
WARNING = re.compile(r"\bwarning\b", re.I)

COLORS = {"warning": "#d9822b", "error": "#e0463c"}


def classify(line):
    """"header", "error", "warning", or None for ordinary text."""
    if line.startswith("== "):
        return "header"
    if ERROR.search(line):
        return "error"
    if WARNING.search(line):
        return "warning"
    return None


def format_for(kind):
    fmt = QTextCharFormat()
    if kind == "header":
        fmt.setFontWeight(QFont.Weight.Bold)
    elif kind in COLORS:
        fmt.setForeground(QColor(COLORS[kind]))
    return fmt


def append(log, text):
    """Add TEXT at the end of LOG, a line at a time so each gets its own format."""
    cursor = log.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    for line in text.splitlines(keepends=True):
        cursor.insertText(line, format_for(classify(line.rstrip("\r\n"))))
    log.setTextCursor(cursor)
