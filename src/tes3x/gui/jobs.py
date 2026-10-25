"""A command the GUI runs: its process, its log and what to do when it ends."""


class Job:
    """One command, or one `run_steps` chain, with its own log. The window's single-command
    attributes (`process`, `command_kind`, ...) forward to the current job."""

    def __init__(self, log):
        self.log = log
        self.process = None
        self.kind = None
        self.target = None
        self.after = None
        self.conflict_retry = None
        self.space_retry = None
        self.partial = ""

    def finish(self):
        """Forget the process and the follow-ups; the log stays for reading."""
        self.process = None
        self.kind = None
        self.target = None
        self.after = None
        self.conflict_retry = None
        self.space_retry = None
