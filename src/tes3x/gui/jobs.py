"""A command the GUI runs: its process, its log and what to do when it ends."""


class Job:
    """One command, or one `run_steps` chain, with its own log. The window's single-command
    attributes (`process`, `command_kind`, ...) forward to the current job."""

    def __init__(self, log, page=None, banner=None):
        self.log = log
        self.page = page or log  # what the tab shows: the log under the banner
        self.banner = banner
        self.waiting = False  # a question in the banner is unanswered
        self.process = None
        self.kind = None
        self.target = None
        self.profile = None
        self.builds = False  # a pipeline run, which owns the profile's build folder until it uploads
        self.stage = None
        self.progress = None
        self.after = None
        self.conflict_retry = None
        self.space_retry = None
        self.partial = ""

    @property
    def running(self):
        return self.process is not None or self.kind is not None

    @property
    def busy(self):
        return self.running or self.waiting

    def finish(self):
        """Forget the process and the follow-ups; the log stays for reading."""
        self.process = None
        self.kind = None
        self.target = None
        self.after = None
        self.conflict_retry = None
        self.space_retry = None
