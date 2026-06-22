import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

GRAPH = "/home/user/graphify/worked/atlas-ops/graph.json"
params = StdioServerParameters(command="/home/user/graphify/.venv/bin/graphify-mcp", args=[GRAPH])

async def main():
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("CONNECTED. Tools exposed to Claude Code:", [t.name for t in tools.tools])
            stats = await s.call_tool("graph_stats", {})
            print("\n[graph_stats]\n" + stats.content[0].text[:400])
            q = await s.call_tool("query_graph", {"question": "Which BuildingConnected pursuits map to ACC construction projects?"})
            print("\n[query_graph]\n" + q.content[0].text[:600])

asyncio.run(main())
