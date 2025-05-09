#!/usr/bin/env python3
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
    sessions: dict[str, ClientSession] = {}
    toolschema: list[dict] = []
    exit_stack = AsyncExitStack()

    try:
        for tag, cmd in SERVER_CMDS.items():
            # 1) Configure and spawn the MCP server subprocess
            params = StdioServerParameters(
                command=cmd[0],
                args=cmd[1:],
                env=os.environ.copy(),
                cwd=str(Path(__file__).resolve().parent),
            )
            r, w = await exit_stack.enter_async_context(stdio_client(params))

            # 2) Establish the MCP session handshake
            sess = await exit_stack.enter_async_context(ClientSession(r, w))
            await sess.initialize()
            sessions[tag] = sess

            # ---- STEP 1: grab the raw list_tools() response -------------
            raw = await sess.list_tools()

            # ---- STEP 2: extract the actual Tool objects list -----------
            tool_objs: list = []
            for key, val in raw:                       # raw is List[tuple]
                if key == "tools" and isinstance(val, list):
                    tool_objs = val                     # val is List[Tool]
                    break

            # ---- STEP 3: loop through each Tool instance --------------
            for tool in tool_objs:
                # `tool` is a fastmcp.tools.Tool (Pydantic model)
                if hasattr(tool, "model_dump"):
                    schema = tool.model_dump(exclude_none=True)
                else:
                    schema = tool.dict()               # fallback
                schema["name"] = tool.name             # ensure name is present
                toolschema.append(schema)

    except Exception:
        # on any startup failure, clean up whatever was opened
        await exit_stack.aclose()
        raise

    # ------------------------------------------------------------------
    # Debug print: what will the LLM actually see?
    print("\n=== Tools visible to the model ===")
    for t in toolschema:
        print(" •", t.get("name"))
    print("===================================\n")

    return sessions, toolschema, exit_stack

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
        for tag, sess in self.sessions.items():
            tools = await sess.list_tools()
            tool_names = [self._tool_name(t) for t in tools]
            print(f"[DEBUG] {tag} provides →", tool_names)  # ← add
            if tool_name in tool_names:
                return sess
        print("[DEBUG] no session exposes", tool_name)      # ← add
        return None

    # ---------- main -----------------------------------------------------
    async def handle(self, user_prompt: str) -> str:
        self.history.append({"role": "user", "content": user_prompt})

        for step in range(self.MAX_STEPS):
            resp = await self.ai.chat.completions.create(
                model="gpt-4o",
                messages=self.history,
                tools=self.tools,
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
                    # ----- parameter-alias normalisation -----------------------------
                    alias = (
                        args.pop("folder", None)
                        or args.pop("folder_path", None)
                        or args.pop("path", None)
                    )
                    if alias and "directory" not in args:
                        args["directory"] = alias
                    if "fileType" in args and "file_type" not in args:
                        args["file_type"] = args.pop("fileType")
                    # ---------- alias normalisation  ----------------------------------
                    # if "folder" in args and "directory" not in args:
                    #     args["directory"] = args.pop("folder")
                    # if "folder_path" in args and "directory" not in args:
                    #     args["directory"] = args.pop("folder_path")
                    # if "path" in args and "directory" not in args:
                    #     args["directory"] = args.pop("path")
                    # if "fileType" in args and "file_type" not in args:
                    #     args["file_type"] = args.pop("fileType")

                    sess = await self._find_session(tool_name)
                    if not sess:
                        result = f"(error: tool '{tool_name}' not found)"
                    else:
                        try:
                            result = await sess.call_tool(tool_name, args)
                        except Exception as e:
                            result = f"(tool execution error: {e})"

                    self.history.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(result),
                        }
                    )

                continue  # ask GPT again with results

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