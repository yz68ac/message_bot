"""Configure runtime logging and attach validated instance IDs to existing events."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

log = logging.getLogger("wxbridge")


class InstanceLog(logging.LoggerAdapter):
    def process(self, message, kwargs):
        field = 'instance='+self.extra['instance']
        if 'metadata=' in message:
            return message.replace('metadata=', field+' metadata=', 1), kwargs
        return message+' '+field, kwargs


def problem_file(cfg):
    """Warnings and errors kept across restarts, only while some instance has tracing switched on.

    Lives next to the trace database, so the web console can show what went wrong before the last restart.
    """
    instances = getattr(cfg, 'instances', None)
    if not instances or not any(item.trace.enabled for item in instances.values()):
        return None
    try:
        from wxbridge.app.status.trace import PROBLEMS, trace_directory
        from wxbridge.storage.person_memory import private_directory
        handler = RotatingFileHandler(private_directory(trace_directory(cfg.account.memory_directory))/PROBLEMS,
                                      maxBytes=2 * 1024 * 1024, backupCount=1, encoding="utf-8")
    except (OSError, ValueError):
        return None
    handler.setLevel(logging.WARNING)
    # A full timestamp: the console cannot guess the year of an old line after a restart.
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    return handler


def setup_logging(cfg, verbose=True):
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in log.handlers[:]:
        handler.close()
        log.removeHandler(handler)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%m-%d %H:%M:%S")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    log.addHandler(stream)
    try:
        Path(cfg.account.log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(cfg.account.log_file, maxBytes=2 * 1024 * 1024,
                                 backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass
    problems = problem_file(cfg)
    if problems is not None:
        log.addHandler(problems)
