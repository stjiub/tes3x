"""A question shown inside a job's output tab, so several jobs can wait without stacking dialogs."""

from .common import QHBoxLayout, QLabel, QPushButton, Qt, QWidget


class PromptBanner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("promptBanner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("#promptBanner { background: palette(alternate-base); "
                           "border: 1px solid palette(mid); border-radius: 4px; }")
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.yes = QPushButton("Yes")
        self.no = QPushButton("No")
        layout = QHBoxLayout(self)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.yes)
        layout.addWidget(self.no)
        self.on_yes = None
        self.on_done = None
        self.yes.clicked.connect(lambda: self.answer(True))
        self.no.clicked.connect(lambda: self.answer(False))
        self.hide()

    def ask(self, text, on_yes, on_done):
        """Show `text`; Yes runs `on_yes`, either answer then runs `on_done`."""
        self.label.setText(text)
        self.on_yes, self.on_done = on_yes, on_done
        self.show()

    def answer(self, yes):
        on_yes, on_done = self.on_yes, self.on_done
        self.on_yes = self.on_done = None
        self.hide()
        if on_done:
            on_done()
        if yes and on_yes:
            on_yes()
