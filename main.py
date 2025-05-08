#!/usr/bin/env python3
import sys, os, json, asyncio
from pathlib import Path
from contextlib import AsyncExitStack

from PyQt6 import QtWidgets
from mcp import StdioServerParameters, ClientSession
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI
from qasync import QEventLoop, asyncSlot
from synology_api import filestation, exceptions

from backend.host.config import settings

# exit_stack: AsyncExitStack | None = None         # global to close later
# # keep the window in a global so it isn’t garbage-collected
# _win: QtWidgets.QWidget | None = None

# --------------------------------------------------------------------------------------
#  1.  TOOL PROCESS TABLE  (use the *same* venv Python via sys.executable)
# --------------------------------------------------------------------------------------
SERVER_CMDS = {
    "local":  [sys.executable, "-m", "backend.tools.tool_local_fs"],
    "gdrive": [sys.executable, "-m", "backend.tools.tool_gdrive"],
    "icloud": [sys.executable, "-m", "backend.tools.tool_icloud"],
}
def _synology_works() -> bool:
    try:
        fs = filestation.FileStation(
                settings.nas_host, int(settings.nas_port),
                settings.nas_user, settings.nas_pass,
                secure=settings.nas_secure, cert_verify=False)
        fs.get_info()          # cheap API call
        return True
    except:
        return False

if settings.nas_host and _synology_works():
    SERVER_CMDS["syno"] = [sys.executable, "-m", "backend.tools.tool_synology"]
else:
    print("⚠️  Skipping Synology tool – check credentials or 2-FA/auth.")
# if settings.nas_host:
#     SERVER_CMDS["syno"] = [sys.executable, "-m", "backend.tools.tool_synology"]

# --------------------------------------------------------------------------------------
#  2.  START ALL TOOL SERVERS — returns  (sessions, tool_schema, exit_stack)
# --------------------------------------------------------------------------------------
async def start_servers():
    sessions   = {}          # name ➜ ClientSession
    toolschema = []          # list of tool json for LLM
    exit_stack = AsyncExitStack()

    try:
        for name, cmd in SERVER_CMDS.items():
            params = StdioServerParameters(
                command=cmd[0],          # python interpreter
                args=cmd[1:],            # ['-m','backend.tools.tool_x']
                env=os.environ.copy(),   # inherit venv environment
                cwd=str(Path(__file__).resolve().parent)  # project root
            )
            # launch subprocess + stdio streams
            r, w = await exit_stack.enter_async_context(stdio_client(params))

            # establish MCP session handshake
            sess = await exit_stack.enter_async_context(ClientSession(r, w))
            await sess.initialize()

            # cache
            sessions[name] = sess
            toolschema.extend(await sess.list_tools())
    except Exception:
        # if any tool fails during startup, close whatever is open
        await exit_stack.aclose()
        raise

    return sessions, toolschema, exit_stack

# --------------------------------------------------------------------------------------
#  3.  ASSISTANT — uses pre-built sessions & tool schema (no event-loop abuse)
# --------------------------------------------------------------------------------------
class Assistant:
    def __init__(self, sessions: dict[str, ClientSession], schema: list[dict]):
        self.sessions = sessions
        self.schema   = schema
        self.ai       = AsyncOpenAI(api_key=settings.openai_api_key)
        self.history  = []

    async def handle(self, prompt: str) -> str:
        self.history.append({"role": "user", "content": prompt})

        while True:
            resp = await self.ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system",
                           "content": "You are a file assistant."}] + self.history,
                tools=self.schema,
                tool_choice="auto",
            )
            msg = resp.choices[0].message

            if msg.tool_call:
                tool_name = msg.tool_call.name
                args      = json.loads(msg.tool_call.arguments)

                # find the session that offers this tool
                for sess in self.sessions.values():
                    if any(t["name"] == tool_name for t in await sess.list_tools()):
                        result = await sess.call_tool(tool_name, args)
                        self.history.append({"role": "tool",
                                             "name": tool_name,
                                             "content": json.dumps(result)})
                        break
                continue  # LLM decides next step
            # final answer
            answer = msg.content
            self.history.append({"role": "assistant", "content": answer})
            return answer

# --------------------------------------------------------------------------------------
#  4.  SIMPLE QT CHAT WINDOW
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

        lay  = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self.out)
        lay.addWidget(self.inp)

    # utilities
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
#  5.  MAIN ASYNC ENTRY
# --------------------------------------------------------------------------------------
def qt_main() -> None:
    """Entry point: start Qt, bring up GUI, integrate with asyncio loop."""
    app = QtWidgets.QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    exit_stack: AsyncExitStack | None = None  # will hold tool contexts

    @asyncSlot()            # Qt signal → coroutine, must accept *args
    async def _graceful_quit(*_):
        """Close tool subprocesses, then stop the event loop."""
        if exit_stack is not None:
            await exit_stack.aclose()
        loop.stop()         # safe: still inside running loop

    # emit when last top-level window is closed
    app.lastWindowClosed.connect(_graceful_quit)

    async def bootstrap() -> None:
        nonlocal exit_stack
        global _win
        sessions, schema, exit_stack = await start_servers()
        _win = ChatWindow(Assistant(sessions, schema))
        _win.show()         # GUI visible — event loop keeps running

    with loop:
        # schedule bootstrap and run Qt/async loop until _graceful_quit stops it
        asyncio.ensure_future(bootstrap())
        loop.run_forever()


if __name__ == "__main__":
    qt_main()