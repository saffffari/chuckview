"""Entry point for the frozen app. `--self-test` writes its report to a file beside the
executable as well as stdout, because a windowed Windows .exe has no console to print to."""

import sys

if "--self-test" in sys.argv:
    import contextlib
    import io
    from pathlib import Path

    from chuckview.app import main

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(sys.argv)
    report = buffer.getvalue() + f"exit {code}\n"
    try:
        Path(sys.executable).with_name("self-test.txt").write_text(report, encoding="utf-8")
    except OSError:
        pass
    if sys.stdout is not None:
        sys.stdout.write(report)
    raise SystemExit(code)

from chuckview.app import main  # noqa: E402

raise SystemExit(main(sys.argv))
