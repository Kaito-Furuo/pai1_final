"""Claude Code（claude CLI）をヘッドレスで起動し、MCP 経由でロボットを操作させるデモドライバ。

全イベント（アシスタントの発話・ツール呼び出し・結果）を壁時計つきで events.jsonl に記録し、
サーバー側は自動録画（ROBOT_AUTORECORD）する。後で scripts/compose_video.py が両者を合成する。

    .venv/bin/python scripts/run_claude_demo.py --name demo1 --layout basic \
        "赤いブロックを青いトレイに入れて、緑のブロックを赤いブロックの上に積んでください"
"""
import argparse, json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parser = argparse.ArgumentParser()
parser.add_argument("prompt")
parser.add_argument("--name", default="demo")
parser.add_argument("--layout", default="basic")
parser.add_argument("--out", default="", help="出力ディレクトリ（既定: recordings/<name>）")
parser.add_argument("--model", default="")
parser.add_argument("--realtime", default="1.0")
parser.add_argument("--render", default="640x480")
parser.add_argument("--max-turns", type=int, default=40)
parser.add_argument("--resume", default="", help="前回のセッション ID を継続する")
args = parser.parse_args()

out = args.out or os.path.join(ROOT, "recordings", args.name)
os.makedirs(out, exist_ok=True)
mcp_cfg = {"mcpServers": {"robot": {"command": "bash", "args": ["run_server.sh"], "env": {
    "ROBOT_REALTIME": args.realtime, "ROBOT_RENDER": args.render, "ROBOT_LAYOUT": args.layout,
    "ROBOT_RECORD_DIR": out, "ROBOT_AUTORECORD": args.name}}}}
cmd = ["claude", "-p", args.prompt, "--mcp-config", json.dumps(mcp_cfg), "--strict-mcp-config",
       "--allowedTools", "mcp__robot mcp__robot__*", "--permission-mode", "dontAsk",
       "--disallowedTools", "Bash Edit Write Glob Grep Read WebFetch WebSearch Agent NotebookEdit",
       "--output-format", "stream-json", "--verbose", "--max-turns", str(args.max_turns)]
if args.model:
    cmd += ["--model", args.model]
if args.resume:
    cmd += ["--resume", args.resume]
print(f"[driver] out={out}", file=sys.stderr)
t0 = time.time()
proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=open(os.path.join(out, "claude_stderr.log"), "w"),
                        text=True, bufsize=1)
events = open(os.path.join(out, "events.jsonl"), "w", encoding="utf-8")
session_id = None
for line in proc.stdout:
    now = time.time()
    line = line.strip()
    if not line:
        continue
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        print(f"[raw] {line[:200]}", file=sys.stderr)
        continue
    events.write(json.dumps(dict(t=now, ev=ev), ensure_ascii=False) + "\n")
    events.flush()
    typ = ev.get("type")
    if typ == "system" and ev.get("subtype") == "init":
        session_id = ev.get("session_id")
        print(f"[init] model={ev.get('model')} tools={len(ev.get('tools', []))} mcp={ev.get('mcp_servers')}", file=sys.stderr)
    elif typ == "assistant":
        for c in ev.get("message", {}).get("content", []):
            if c.get("type") == "text":
                print(f"[{now-t0:6.1f}s] Claude: {c['text'][:300]}", file=sys.stderr)
            elif c.get("type") == "tool_use":
                print(f"[{now-t0:6.1f}s] tool: {c.get('name')} {json.dumps(c.get('input'), ensure_ascii=False)[:160]}", file=sys.stderr)
    elif typ == "user":
        for c in ev.get("message", {}).get("content", []):
            if isinstance(c, dict) and c.get("type") == "tool_result":
                cc = c.get("content")
                txt = cc if isinstance(cc, str) else " ".join(x.get("text", "[image]") if x.get("type") == "text" else "[image]" for x in cc or [])
                print(f"[{now-t0:6.1f}s] result: {str(txt)[:160]}", file=sys.stderr)
    elif typ == "result":
        print(f"[result] {ev.get('subtype')} turns={ev.get('num_turns')} cost=${ev.get('total_cost_usd', 0):.3f} "
              f"duration={ev.get('duration_ms', 0)/1000:.0f}s session={session_id}", file=sys.stderr)
        print(f"[final] {str(ev.get('result', ''))[:600]}", file=sys.stderr)
proc.wait()
events.close()
with open(os.path.join(out, "run.json"), "w", encoding="utf-8") as f:
    json.dump(dict(name=args.name, prompt=args.prompt, layout=args.layout, session_id=session_id,
                   started=t0, finished=time.time(), returncode=proc.returncode), f, ensure_ascii=False, indent=1)
print(f"[driver] done rc={proc.returncode} in {time.time()-t0:.0f}s", file=sys.stderr)
