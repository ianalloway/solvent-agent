"""SOLVENT CLI entry point: python3 -m solvent [serve|worker|tune|reconcile|demo]"""

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
    else:
        from run_demo import main as demo_main
        demo_main()


if __name__ == "__main__":
    main()
