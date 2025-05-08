#!/usr/bin/env python3
import os
from fastmcp import FastMCP, tool
from synology_api import filestation
from backend.host.config import settings  # adjust import if packaging separately

fs = filestation.FileStation(
        settings.nas_host, settings.nas_user, settings.nas_pass, verify_ssl=False)

mcp = FastMCP("SynologyNAS")

@tool()
def list_directory(path:str="/") -> list[str]:
    data=fs.get_list(path)["data"]["files"]
    return [f["name"] for f in data]

@tool()
def delete_file(path:str)->str:
    fs.delete(path,True); return "deleted"

@tool()
def rename_file(path:str,new_name:str)->str:
    fs.rename(path,new_name); return "renamed"

@tool()
def copy_file(src:str,dest:str)->str:
    fs.copy_move(src,dest,"copy"); return "copied"

if __name__=="__main__":
    mcp.run(transport="stdio")
