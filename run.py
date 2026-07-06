"""Corte Cenas entry point.

Wraps `app.main.main()` in a defensive crash handler that:
- writes the traceback + platform info to a log the user can find
- pops a native message box explaining what happened

Without this, when the app is frozen (`console=False`), a startup crash
just closes silently — no CMD, no log, no clue. This makes bug reports
possible in the wild.
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path


def _crash_log_path() -> Path:
    try:
        from platformdirs import user_log_dir
        base = Path(user_log_dir("CorteCenas"))
    except Exception:
        base = Path.home() / "AppData" / "Local" / "CorteCenas" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / "crash.log"


def _show_crash_dialog(log_path: Path, tb: str) -> None:
    """Message box with the log location. Uses ctypes so we don't need Qt
    (which may itself be what failed to load)."""
    try:
        import ctypes
        MB_ICONERROR = 0x10
        MB_OK = 0x0
        preview = tb.strip().splitlines()[-1] if tb.strip() else "(sem detalhes)"
        msg = (
            f"O Corte Cenas travou ao abrir.\n\n"
            f"Erro: {preview}\n\n"
            f"Um relatório completo foi salvo em:\n{log_path}\n\n"
            f"Envie o arquivo pra quem te passou o app pra investigar."
        )
        ctypes.windll.user32.MessageBoxW(0, msg, "Corte Cenas — Erro fatal",
                                        MB_OK | MB_ICONERROR)
    except Exception:
        pass


def _run() -> int:
    from app.main import main
    return main()


def _main() -> int:
    # Session log first thing — before Qt, before config — so even import
    # failures leave a trace in app.log alongside crash.log.
    try:
        from app.applog import setup as _setup_log
        _setup_log()
    except Exception:
        pass
    try:
        return _run()
    except SystemExit:
        raise
    except BaseException:
        tb = traceback.format_exc()
        log = _crash_log_path()
        try:
            import logging
            logging.getLogger("cortecenas").error("CRASH FATAL:\n%s", tb)
        except Exception:
            pass
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\n===== {datetime.now().isoformat()} =====\n")
                f.write(f"Python: {sys.version}\n")
                f.write(f"Executable: {sys.executable}\n")
                f.write(f"Frozen: {getattr(sys, 'frozen', False)}\n\n")
                f.write(tb)
        except Exception:
            pass
        _show_crash_dialog(log, tb)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
