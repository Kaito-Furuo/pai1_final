"""MCP クライアントとしてサーバーに接続し、ツール一式を通しで叩く統合テスト。"""
import asyncio, json, os, sys, time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "scratch")
os.makedirs(OUT, exist_ok=True)


def show(name, res, save=None):
    texts, imgs = [], 0
    for c in res.content:
        if c.type == "text":
            texts.append(c.text)
        elif c.type == "image":
            imgs += 1
            if save:
                import base64
                open(os.path.join(OUT, f"{save}_{imgs}.jpg"), "wb").write(base64.b64decode(c.data))
    t = " | ".join(texts)
    print(f"[{name}] isError={res.isError} images={imgs} text={t[:400]}")
    return json.loads(texts[0]) if texts and texts[0].startswith("{") else None


async def main():
    params = StdioServerParameters(command="bash", args=["run_server.sh"], cwd=ROOT,
                                   env={**os.environ, "ROBOT_REALTIME": "0", "ROBOT_RECORD_DIR": OUT})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            t0 = time.time()
            show("reset", await s.call_tool("reset_scene", {"layout": "sorting"}))
            show("start_recording", await s.call_tool("start_recording", {"name": "clienttest"}))
            show("say", await s.call_tool("say", {"text": "赤いブロックを赤いトレイへ、青いブロックを青いトレイへ仕分けします"}))
            show("look top", await s.call_tool("look", {"camera": "top"}), save="top")
            det = show("detect", await s.call_tool("detect_objects", {}))
            ids = [o["id"] for o in det["objects"]]
            print("ids:", ids)
            plan = [(i, f"{i.split('_')[0]}_tray") for i in ids if i.startswith(("red_cube", "blue_cube"))]
            for obj, tray in plan:
                p = show(f"pick {obj}", await s.call_tool("pick", {"object_id": obj}), save=f"wrist_{obj}")
                if not p or not p.get("success"):
                    print("  !! pick failed, skipping"); continue
                show(f"place {tray}", await s.call_tool("place", {"target": tray, "dx": 0.0 if obj.endswith("_1") else 0.0,
                                                                 "dy": 0.0}), save=f"front_{obj}")
                show("verify", await s.call_tool("verify", {"object_id": obj, "target": tray}))
            show("bad pick", await s.call_tool("pick", {"object_id": "pink_cube"}))
            show("nudge", await s.call_tool("nudge_object", {"object_id": "green_cube", "dx": 0.05, "dy": -0.05}))
            show("home", await s.call_tool("home", {}))
            show("state", await s.call_tool("get_state", {}))
            show("stop_recording", await s.call_tool("stop_recording", {}))
            print(f"total {time.time()-t0:.1f}s")

asyncio.run(main())
