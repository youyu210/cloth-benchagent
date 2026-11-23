import argparse
import json
from benchagent.common import ROOT, write
from benchagent.planner import plan_request
from benchagent.preflight import inspect
from benchagent.runner import execute

def main():
    p = argparse.ArgumentParser(description="C0 BenchAgent; planning never launches a simulation")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("preflight")
    a = sub.add_parser("plan")
    a.add_argument("request")
    a.add_argument("--llm", action="store_true")
    for action in ("run", "bench", "agent"):
        q = sub.add_parser(action)
        q.add_argument("--timeout", type=int, default=3600)
        if action == "run":
            q.add_argument("--frames", type=int, choices=(20, 50, 100), required=True)
        elif action == "bench":
            q.add_argument("--suite", choices=("default-mvp",), default="default-mvp")
        else:
            q.add_argument("request")
            q.add_argument("--llm", action="store_true")
    args = p.parse_args()
    if args.action == "preflight":
        result = inspect()
        write(ROOT / "docs/preflight_latest.json", result)
    elif args.action == "plan":
        result = plan_request(args.request, args.llm)
    else:
        plan = (plan_request(args.request, args.llm) if args.action == "agent" else
                {"action": "run", "scene": "default", "frames": args.frames} if args.action == "run" else
                {"action": "bench", "scene": "default", "suite_id": args.suite})
        result = execute(plan, args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("status") in ("FAIL", "PARTIAL", "SKIPPED") or result.get("ready") is False else 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        raise SystemExit(2)
