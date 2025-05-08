#!/usr/bin/env python3
import sys, json, subprocess, asyncio, inspect
from pathlib import Path
from PyQt6 import QtWidgets, QtGui
from fastmcp import MCPClient
from modelcontextprotocol.client.stdio import stdio_client
from modelcontextprotocol import ClientSession
from openai import AsyncOpenAI
from backend.host.config import settings
from backend.tools import tool_local_fs, tool_gdrive, tool_icloud, tool_synology  # noqa: F401

# ---------- spawn servers ----------
SERVER_CMDS = {
    "local": ["python", "-m", "backend.tools.tool_local_fs"],
    "gdrive": ["python", "-m", "backend.tools.tool_gdrive"],
    "icloud": ["python", "-m", "backend.tools.tool_icloud"],
}
if settings.nas_host:
    SERVER_CMDS["syno"] = ["python","-m","backend.tools.tool_synology"]

async def start_servers():
    conns={}
    for name,cmd in SERVER_CMDS.items():
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        r,w = await stdio_client(proc)
        conns[name]=(proc,r,w)
    return conns

# ---------- LLM assistant ----------
class Assistant:
    def __init__(self, conns):
        self.conns=conns
        self.ai = AsyncOpenAI(api_key=settings.openai_api_key)
        self.history=[]
        # aggregate tool schemas
        self.tools=[]
        for _,(_,r,_) in conns.items():
            cli=ClientSession(r,None)
            self.tools += asyncio.run(cli.list_tools())

    async def handle(self,prompt:str)->str:
        self.history.append({"role":"user","content":prompt})
        while True:
            resp = await self.ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"system","content":"You are a file assistant."}]+self.history,
                tools=self.tools,
                tool_choice="auto")
            msg=resp.choices[0].message
            if msg.tool_call:
                name=msg.tool_call.name; args=json.loads(msg.tool_call.arguments)
                # find which conn exposes this tool
                for tag,(_,r,w) in self.conns.items():
                    cli=ClientSession(r,w)
                    lst=await cli.list_tools()
                    if any(t["name"]==name for t in lst):
                        res=await cli.call_tool(name,args)
                        self.history.append({"role":"tool","name":name,"content":json.dumps(res)})
                        break
                continue
            answer=msg.content
            self.history.append({"role":"assistant","content":answer})
            return answer

# ---------- Qt GUI ----------
class MainWindow(QtWidgets.QWidget):
    def __init__(self,assistant):
        super().__init__()
        self.assistant=assistant
        self.setWindowTitle("Filesystem Assistant")
        self.resize(640,400)
        layout=QtWidgets.QVBoxLayout(self)
        self.text=QtWidgets.QTextEdit(); self.text.setReadOnly(True)
        self.input=QtWidgets.QLineEdit(); self.input.returnPressed.connect(self.ask)
        layout.addWidget(self.text); layout.addWidget(self.input)

    def append(self,who,text):
        self.text.append(f"<b>{who}:</b> {text}")

    def ask(self):
        q=self.input.text().strip(); self.input.clear()
        if not q:return
        self.append("You",q)
        asyncio.create_task(self.get_answer(q))

    async def get_answer(self,q):
        a=await self.assistant.handle(q)
        self.append("Assistant",a)

async def main():
    conns=await start_servers()
    assistant=Assistant(conns)
    app=QtWidgets.QApplication(sys.argv)
    win=MainWindow(assistant); win.show()
    await asyncio.get_event_loop().run_in_executor(None, app.exec)

if __name__=="__main__":
    asyncio.run(main())
