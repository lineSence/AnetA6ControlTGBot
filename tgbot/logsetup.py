import logging
import logging.handlers
import sys
from pathlib import Path

def setup(cfg):
    lg = logging.getLogger("tgbot")
    lg.setLevel(getattr(logging, str(cfg.log_level).upper(), logging.INFO))
    lg.propagate = False
    if lg.handlers:
        return lg
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    Path(cfg.log_file).parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        cfg.log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    sh = logging.StreamHandler(sys.stdout)
    fh.setFormatter(fmt)
    sh.setFormatter(fmt)
    lg.addHandler(fh)
    lg.addHandler(sh)
    return lg
