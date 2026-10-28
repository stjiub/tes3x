"""Small widget helpers shared by the Targets and Server pages."""

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QLabel, QPushButton


CAPABILITY_REASONS = {
    "ftp": "the Xbox's FTP server is not answering",
    "pull_logs": "neither FTP nor the in-game agent is available",
    "dashboard_control": "the XBMC4Gamers agent is not answering or is outdated",
    "commands": "no game with the in-game agent is connected",
    "agent_fetch": "no game with the in-game agent is connected",
    "installed_builds": "the Xbox's FTP server is not answering",
    "install_dashboard_agent": "the Xbox's FTP server is not answering",
    "install_dash": "neither the Xbox's FTP server nor the Dash is answering",
}


def heading(text, grow=4):
    label = QLabel(text)
    font = label.font()
    font.setPointSize(font.pointSize() + grow)
    font.setBold(True)
    label.setFont(font)
    return label


def monospace(widget):
    widget.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    return widget


def gate(button, features, needed):
    """Enable a button when the target offers `needed`; otherwise say what is missing."""
    allowed = needed in features
    button.setEnabled(allowed)
    tip = button.property("baseTip") or ""
    button.setToolTip(tip if allowed else f"{tip}\nUnavailable: {CAPABILITY_REASONS[needed]}"
                      .strip())


def tip_button(text, tip, handler):
    button = QPushButton(text)
    button.setProperty("baseTip", tip)
    button.setToolTip(tip)
    button.clicked.connect(handler)
    return button
