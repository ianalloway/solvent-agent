"""SOLVENT CLI entry point: python3 -m solvent [serve|worker|telegram|doctor|pairing|...]"""

import sys

HELP = """\
SOLVENT — a self-funding analyst agent.

Usage: solvent <command> [options]
       solvent                 run the demo (interactive onboarding on first run)

Commands:
  (none)        run the batch demo / interactive session
  init          first-run setup: create dirs, DB, and workspace files
  status        live summary: balance, jobs, API key presence; --watch to auto-refresh
  upgrade       check for newer version on PyPI; --check exits 1 if outdated
  jobs          list/show/retry/cancel jobs (jobs --help for sub-commands)
  serve         webhooks + job API + hosted briefs
  worker        resume incomplete jobs, process the queue
  telegram      long-poll the Telegram bot
  finance       income statement, unit economics, runway, forecast (alias: report)
  tune          propose pricing improvements (--apply to commit)
  reconcile     Stripe <-> ledger drift check
  doctor        stack diagnostics
  pairing       manage Telegram DM pairing codes
  workspace     seed the agent workspace (SOUL/BRAIN/AGENTS)
  retry <id>    re-run a stuck job
  webhooks      inspect the webhook event log (stats|list|failed)
  help          show this message
  version       print the installed version

Run `solvent <command> --help` where supported for command-specific options.
"""


def _print_version():
    from . import __version__
    print(f"solvent {__version__}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("version", "--version", "-V"):
        _print_version()
        return
    if len(sys.argv) > 1 and sys.argv[1] in ("help", "--help", "-h"):
        print(HELP)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        sys.argv.pop(1)
        from .init import main as init_main
        init_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "status":
        sys.argv.pop(1)
        from .status import main as status_main
        status_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "jobs":
        sys.argv.pop(1)
        from .job_cmd import main as jobs_main
        jobs_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "upgrade":
        sys.argv.pop(1)
        from .upgrade import main as upgrade_main
        upgrade_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "serve":
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
        import json
        print(json.dumps(result, indent=2, default=str))
    elif len(sys.argv) > 1 and sys.argv[1] in ("finance", "report"):
        sys.argv.pop(1)
        from .finance import main as finance_main
        finance_main()
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
        # No subcommand: fall through to the demo / interactive CLI.
        from .upgrade import background_update_hint
        background_update_hint()
        from .cli import main as demo_main
        demo_main()


if __name__ == "__main__":
    main()
