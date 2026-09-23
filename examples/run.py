"""uv run --env-file .env python examples/run.py --url URL --goal 'A narrow goal'"""

import argparse

from jev_ultrafast import Agent

parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True)
parser.add_argument("--goal", action="append", required=True, help="Repeat for an ordered list of goals.")
parser.add_argument("--reasoning", action="store_true", help="Let System 2 review uncertain, stuck, and final steps.")
args = parser.parse_args()

with Agent(args.url, args.goal, reasoning=args.reasoning) as agent:
    for state in agent.run():
        counts = f"{len(state['history'])} actions  {len(state['reviews'])} reviews"
        print(f"{state['elapsed_ms']:>5} ms  {counts}  {state['status']}")
    print(state["page"]["url"])
