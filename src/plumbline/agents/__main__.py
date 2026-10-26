"""python -m plumbline.agents ask "..." [--as USER] | eval | usage | serve

  ask     one message, as a user (roles from ops/roles.yaml, or PLUMBLINE_ROLES)
  eval    knowledge and routing evals; with PLUMBLINE_LLM_MODEL set, also through the model
  usage   adoption and cost from the audit log
  serve   Slack endpoint (needs SLACK_SIGNING_SECRET and SLACK_BOT_TOKEN)
"""
from __future__ import annotations

import argparse
import json
import os

from .agent import Context, handle


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m plumbline.agents")
    ap.add_argument("--warehouse", default="data/warehouse.duckdb")
    ap.add_argument("--agent-db", default="data/agent.duckdb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask"); a.add_argument("text"); a.add_argument("--as", dest="user", default=os.environ.get("USER", "cli"))
    sub.add_parser("eval")
    u = sub.add_parser("usage"); u.add_argument("--days", type=int, default=30)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=3000)
    args = ap.parse_args(argv)
    ctx = Context.from_env(".", args.warehouse, args.agent_db)
    if args.cmd == "ask":
        r = handle(ctx, args.user, args.text)
        print(r.text)
        return 0
    if args.cmd == "eval":
        from . import evals
        items = evals.load("evals/knowledge.yaml")
        out = {"routing": evals.run_routing(ctx.skills, "evals/routing.yaml")["routed_correctly"],
               "knowledge_quoted": {k: v for k, v in evals.run(ctx.index, items, split="test").items() if k != "failures"}}
        if ctx.model:
            out["knowledge_with_model"] = {k: v for k, v in evals.run(ctx.index, items, model=ctx.model, split="test").items()}
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "usage":
        from .usage import report
        print(json.dumps(report(ctx.open().agent_con, ctx.skills, args.days), indent=2, default=str))
        return 0
    from .slack import SlackApp, SlackPoster, serve
    app = SlackApp(ctx, os.environ.get("SLACK_SIGNING_SECRET", ""), SlackPoster(os.environ.get("SLACK_BOT_TOKEN", "")), run_async=True)
    print(f"listening on :{args.port}; point the Slack app's Request URL here through HTTPS")
    serve(app, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
