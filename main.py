import ctypes

from lol_helper.lcu import is_elevated, restart_as_admin
from lol_helper.ui import run_app


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    if not is_elevated():
        restart_as_admin()
    else:
        run_app()
