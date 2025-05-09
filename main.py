#!/usr/bin/env python3
from dataclasses import is_dataclass, asdict
import sys, os, json, asyncio
from pathlib import Path
from contextlib import AsyncExitStack
from dataclasses import is_dataclass
from typing import Any, Dict, List

from PyQt6 import QtWidgets
from mcp import StdioServerParameters, ClientSession
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI
from qasync import QEventLoop, asyncSlot
from synology_api import filestation

from backend.host.config import settings

_win: QtWidgets.QWidget | None = None
# --------------------------------------------------------------------------------------
# 1 - TOOL PROCESS TABLE
# --------------------------------------------------------------------------------------
SERVER_CMDS = {
    "local":  [sys.executable, "-m", "backend.tools.tool_local_fs"],
    "gdrive": [sys.executable, "-m", "backend.tools.tool_gdrive"],
    "icloud": [sys.executable, "-m", "backend.tools.tool_icloud"],
}


def _synology_works() -> bool:
    try:
        fs = filestation.FileStation(
            settings.nas_host,
            int(settings.nas_port),
            settings.nas_user,
            settings.nas_pass,
            secure=settings.nas_secure,
            cert_verify=False,
        )
        fs.get_info()
        return True
    except Exception:
        return False


if settings.nas_host and _synology_works():
    SERVER_CMDS["syno"] = [sys.executable, "-m", "backend.tools.tool_synology"]
else:
    print("⚠️  Skipping Synology tool – check credentials or 2-FA/auth.")

# --------------------------------------------------------------------------------------
# 2 - START ALL TOOL SERVERS  (returns sessions, toolschema, exit_stack)
# --------------------------------------------------------------------------------------
async def start_servers():
    """
    Launch each MCP tool server, collect their raw schemas (with
    name, description, parameters), and keep them alive via exit_stack.
    Returns: (sessions: dict[str,ClientSession],
              tool_schemas: list[dict],
              exit_stack: AsyncExitStack)
    """
    sessions: dict[str, ClientSession] = {}
    tool_schemas: list[dict]          = []
    exit_stack                        = AsyncExitStack()

    try:
        for tag, cmd in SERVER_CMDS.items():
            # 1) spawn the MCP subprocess over stdio
            params = StdioServerParameters(
                command=cmd[0],
                args=cmd[1:],
                env=os.environ.copy(),
                cwd=str(Path(__file__).resolve().parent),
            )
            reader, writer = await exit_stack.enter_async_context(
                stdio_client(params)
            )

            # 2) handshake → ClientSession
            sess = await exit_stack.enter_async_context(
                ClientSession(reader, writer)
            )
            await sess.initialize()
            sessions[tag] = sess

            # 3) pull the raw list_tools() response
            raw = await sess.list_tools()

            # 4) find the ("tools", [...]) tuple and extract list
            tool_list = []
            for key, val in raw:
                if key == "tools" and isinstance(val, list):
                    tool_list = val
                    break

            # 5) for each Tool instance produce a raw schema dict
            for tool in tool_list:
                # FastMCP’s Tool is a Pydantic model
                schema = (
                    tool.model_dump(exclude_none=True)
                    if hasattr(tool, "model_dump")
                    else tool.dict()
                )
                # ensure we rename MCP’s `inputSchema` → OpenAI’s `parameters`
                if "inputSchema" in schema:
                    schema["parameters"] = schema.pop("inputSchema")
                # inject name + description
                schema["name"]        = tool.name
                schema["description"] = schema.get("description", "")
                tool_schemas.append(schema)

    except Exception:
        # clean up on error
        await exit_stack.aclose()
        raise

    # debug: show the raw schemas you will wrap later
    print("\n=== Raw tool schemas collected ===")
    for s in tool_schemas:
        print(" •", s["name"])
    print("===================================\n")

    return sessions, tool_schemas, exit_stack

# --------------------------------------------------------------------------------------
# 3 - ASSISTANT (handles GPT ↔ MCP tools)
# --------------------------------------------------------------------------------------
class Assistant:
    MAX_STEPS = 10

    def __init__(self, sessions: dict[str, ClientSession], tool_schema: list[dict]):
        self.sessions = sessions
        self.tools = tool_schema
        self.ai = AsyncOpenAI(api_key=settings.openai_api_key)
        self.history: list[dict] = [
            {
                "role": "system",
                "content": (
                    "You are Filesystem-GPT. Always use the provided tools "
                    "for file operations instead of guessing."
                ),
            }
        ]

    # ---------- helpers --------------------------------------------------
    @staticmethod
    def _tool_name(entry: Any) -> str | None:
        """
        Extract a tool’s canonical name from any shape Fast-MCP returns:
          • dict  ______________________________  {"name": ...}
          • (Tool, schema_dict) tuple  _________  (Tool, {...})
          • Tool object (fastmcp.tools.Tool) ____  Tool(...)
        Returns None for housekeeping entries like ('meta', None).
        """
        # 1) schema-dict already
        if isinstance(entry, dict):
            return entry.get("name")

        # 2) (tool_obj, schema_dict) tuple
        if (
            isinstance(entry, tuple)
            and len(entry) == 2
            and isinstance(entry[1], dict)
        ):
            tool_obj, _ = entry
            if hasattr(tool_obj, "name"):
                return tool_obj.name
            return None                     # (‘meta’, None) case

        # 3) plain Tool object
        if hasattr(entry, "name"):
            return entry.name

        return None

    async def _find_session(self, tool_name: str) -> ClientSession | None:
        """
        Return the first ClientSession whose MCP server registered `tool_name`.
        """
        for tag, sess in self.sessions.items():
            # 1) fetch the raw list_tools() response
            raw = await sess.list_tools()

            # 2) extract the list of Tool objects from the ("tools", [...]) tuple
            tool_objs: list = []
            for key, val in raw:
                if key == "tools" and isinstance(val, list):
                    tool_objs = val
                    break

            # 3) build a list of names for debugging & comparison
            available = []
            for tool in tool_objs:
                # fastmcp.tools.Tool has a `.name` attr
                if hasattr(tool, "name"):
                    available.append(tool.name)
                # fallback in case a dict ever sneaks in
                elif isinstance(tool, dict) and "name" in tool:
                    available.append(tool["name"])

            print(f"[DEBUG] {tag} provides →", available)

            # 4) if our desired tool_name is here, return this session
            if tool_name in available:
                return sess

        # nothing matched
        print("[DEBUG] no session exposes", tool_name)
        return None

    # ---------- main -----------------------------------------------------
    def wrap_tool_schemas(self, raw_schemas: list[dict]) -> list[dict]:
        """
        Given a list of raw tool schemas (each containing at least
        'name', 'description', and either 'parameters' or 'inputSchema'),
        return a list formatted for OpenAI Chat Completions API:
        [
            {
            "type": "function",
            "function": {
                "name": ...,
                "description": ...,
                "parameters": { ... }
            }
            },
            ...
        ]
        """
        wrapped: list[dict] = []
        for schema in raw_schemas:
            # pick the JSON schema under either key
            params = schema.get("parameters", schema.get("inputSchema", {}))
            wrapped.append({
                "type": "function",
                "function": {
                    "name":        schema["name"],
                    "description": schema.get("description", ""),
                    "parameters":  params,
                }
            })
        return wrapped


    async def handle(self, user_prompt: str) -> str:
        self.history.append({"role": "user", "content": user_prompt})

        for step in range(self.MAX_STEPS):
            # wrap_tool_schemas(...) from earlier
            callable_tools = self.wrap_tool_schemas(self.tools)

            resp = await self.ai.chat.completions.create(
                model="gpt-4o",
                messages=self.history,
                tools=callable_tools,
                tool_choice="auto",
            )
            msg = resp.choices[0].message
            print(f"🔹 step {step+1} raw →", msg)
            self.history.append(msg.model_dump(exclude_none=True))

            # collect tool calls
            calls = getattr(msg, "tool_calls", None) or []
            if getattr(msg, "tool_call", None):
                calls = [msg.tool_call]

            if calls:
                for call in calls:
                    tool_name = call.function.name
                    args = json.loads(call.function.arguments or "{}")
                    # … your alias normalization …

                    sess = await self._find_session(tool_name)
                    if not sess:
                        result_data = {"error": f"tool '{tool_name}' not found"}
                    else:
                        raw = await sess.call_tool(tool_name, args)
                        # ─── Convert raw result to pure Python ──────────────
                        if hasattr(raw, "model_dump"):
                            result_data = raw.model_dump(exclude_none=True)
                        elif hasattr(raw, "dict"):
                            result_data = raw.dict()
                        elif is_dataclass(raw):
                            result_data = asdict(raw)
                        else:
                            result_data = raw
                    # ─── Now it’s safe to JSON‐serialize ───────────────────
                    self.history.append({
                        "role":         "tool",
                        "tool_call_id": call.id,
                        "content":      json.dumps(result_data),
                    })

                continue  # feed GPT the updated history

            if msg.content:
                return msg.content

        return "⚠️  Sorry, I couldn’t complete that request."

# --------------------------------------------------------------------------------------
# 4 - SIMPLE QT CHAT WINDOW
# --------------------------------------------------------------------------------------
class ChatWindow(QtWidgets.QWidget):
    def __init__(self, assistant: Assistant):
        super().__init__()
        self.assistant = assistant
        self.setWindowTitle("Filesystem Assistant")
        self.resize(650, 420)

        self.out = QtWidgets.QTextEdit(readOnly=True)
        self.inp = QtWidgets.QLineEdit()
        self.inp.returnPressed.connect(self._submit)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self.out)
        lay.addWidget(self.inp)

    def _append(self, who, text):
        self.out.append(f"<b>{who}:</b> {text}")

    def _submit(self):
        text = self.inp.text().strip()
        if not text:
            return
        self.inp.clear()
        self._append("You", text)
        asyncio.create_task(self._answer(text))

    async def _answer(self, q):
        a = await self.assistant.handle(q)
        self._append("Assistant", a)


# --------------------------------------------------------------------------------------
# 5 - MAIN (Qt + asyncio via qasync)
# --------------------------------------------------------------------------------------
def qt_main() -> None:
    app  = QtWidgets.QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    exit_stack: AsyncExitStack | None = None

    async def _graceful_quit():
        if exit_stack:
            await exit_stack.aclose()
        loop.stop()

    # schedule coroutine when last window closes
    app.lastWindowClosed.connect(
        lambda: asyncio.ensure_future(_graceful_quit())
    )

    async def bootstrap():
        global _win                # keep a reference so GC can’t collect it
        nonlocal exit_stack
        sessions, schema, exit_stack = await start_servers()
        _win = ChatWindow(Assistant(sessions, schema))
        _win.show()

    with loop:
        asyncio.ensure_future(bootstrap())
        loop.run_forever()


if __name__ == "__main__":
    qt_main()