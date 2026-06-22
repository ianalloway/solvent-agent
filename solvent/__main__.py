"""SOLVENT CLI entry point: python3 -m solvent [serve|worker|telegram|doctor|pairing|...]"""

import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        sys.argv.pop(1)
        from .server import main as serve_main
        serve_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "worker":
        sys.argv.pop(1)
        from .worker import main as worker_main
        worker_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "tune":
        sys.argv.pop(1)
        from .improver import main as tune_main
        tune_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "reconcile":
        sys.argv.pop(1)
        from .reconcile import main as reconcile_main
        reconcile_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "doctor":
        sys.argv.pop(1)
        from .doctor import main as doctor_main
        doctor_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "telegram":
        sys.argv.pop(1)
        from .channels.telegram import main as telegram_main
        telegram_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "pairing":
        sys.argv.pop(1)
        from .pairing import main as pairing_main
        pairing_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "workspace":
        sys.argv.pop(1)
        from .workspace import main as workspace_main
        workspace_main()
    else:
        from run_demo import main as demo_main
        demo_main()

from .cli import main


if __name__ == "__main__":
    main()
