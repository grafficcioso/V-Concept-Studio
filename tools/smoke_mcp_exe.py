"""CI: 빌드된 VConceptStudio-mcp.exe 를 공식 MCP 클라이언트로 띄워 데모 모드 생성까지 확인한다 (API 비용 없음)."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

root = Path(__file__).resolve().parent.parent
exe = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "dist" / "VConceptStudio" / "VConceptStudio-mcp.exe"
tmp = Path(tempfile.mkdtemp(prefix="vcs_mcp_"))
cfg = tmp / "cfg"
cfg.mkdir()
(cfg / "settings.json").write_text(json.dumps({"data_root": str(tmp / "데이터"), "demo_mode": True, "mcp_enabled": True}),
                                   encoding="utf-8")
img = tmp / "원본.png"
Image.new("RGB", (120, 160), (40, 90, 200)).save(img)
env = {**os.environ, "CRS_CONFIG_DIR": str(cfg)}


def a(obj, *names):
    return next(getattr(obj, n) for n in names if hasattr(obj, n))


async def main():
    params = StdioServerParameters(command=str(exe), args=[], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            print("server:", a(init, "server_info", "serverInfo"))
            tools = [t.name for t in (await s.list_tools()).tools]
            print("tools:", tools)
            st = json.loads((await s.call_tool("status", {})).content[0].text)
            print("status:", st)
            imp = json.loads((await s.call_tool("import_images", {"paths": [str(img)], "project": "스모크"})).content[0].text)
            res = await s.call_tool("generate", {"image_ids": imp["imported_ids"], "steps": ["front_view", "four_views"],
                                                 "wait_sec": 90})
            info = json.loads(res.content[1].text)
            print("generate:", info["state"], [x["function"] for x in info["results"]], info["errors"])
            assert info["state"] == "done" and len(info["results"]) == 2
            assert any(c.type == "image" for c in res.content)
            ex = json.loads((await s.call_tool("export_images", {"dest_folder": str(tmp / "정리"), "items": [
                {"id": info["results"][1]["id"], "filename": "스모크_사면도_01"}]})).content[0].text)
            assert ex["copied"], ex
    print("MCP EXE OK")


asyncio.run(asyncio.wait_for(main(), 180))
