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
    elif len(sys.argv) > 1 and sys.argv[1] == "tui":
        from .tui import run
        run()
    elif len(sys.argv) > 1 and sys.argv[1] == "retry":
        job_id = sys.argv[2] if len(sys.argv) > 2 else None
        if not job_id:
            print("Usage: python -m solvent retry <job_id>")
            sys.exit(1)
        from .stages import SolventStages
        from .treasury import Treasury
        from .guardrails import Guardrails
        from .stripe_client import StripeClient
        t = Treasury()
        s = SolventStages(treasury=t, guard=Guardrails(t), stripe=StripeClient())
        result = s.retry_job(job_id)
        import json; print(json.dumps(result, indent=2, default=str))
    elif len(sys.argv) > 1 and sys.argv[1] == "metrics":
        sys.argv.pop(1)
        from .metrics_cmd import main as metrics_main
        metrics_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "sessions":
        sys.argv.pop(1)
        from .sessions_cmd import main as sessions_main
        sessions_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "events":
        sys.argv.pop(1)
        from .events_cmd import main as events_main
        events_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "rate-limit":
        sys.argv.pop(1)
        from .rate_limit_cmd import main as rate_limit_main
        rate_limit_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "ledger":
        sys.argv.pop(1)
        from .ledger import main as ledger_main
        ledger_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "webhooks":
        from .webhook_log import WebhookLog
        import json
        wl = WebhookLog()
        sub = sys.argv[2] if len(sys.argv) > 2 else "stats"
        if sub == "stats":
            print(json.dumps(wl.stats(), indent=2))
        elif sub == "list":
            for row in wl.list_recent(20):
                print(f"{row['received_at_fmt']} [{row['status']}] {row['event_type']} {row['event_id'][:16]}")
        elif sub == "failed":
            for row in wl.list_failed():
                print(f"  {row['event_id'][:16]} {row['event_type']} err={row['error'][:60]}")
    else:
        from run_demo import main as demo_main
        demo_main()

from .cli import main


if __name__ == "__main__":
    main()
