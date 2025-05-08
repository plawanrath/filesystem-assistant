#!/usr/bin/env python3
import os, shutil, fnmatch
from pathlib import Path
from fastmcp import FastMCP, tool

ICLOUD_ROOT = Path.home()/ "Library/Mobile Documents/com~apple~CloudDocs"
mcp = FastMCP("iCloudDrive")

def _p(rel:str)->Path:
    p=(ICLOUD_ROOT/rel.lstrip("/")).resolve()
    if not str(p).startswith(str(ICLOUD_ROOT)):
        raise ValueError("path outside iCloud")
    return p

@tool()
def list_files(path:str="") -> list[str]:
    return os.listdir(_p(path))

@tool()
def search_files(query:str, path:str="") -> list[str]:
    hits=[]
    for root,_,files in os.walk(_p(path)):
        hits += [os.path.join(root,f) for f in files if fnmatch.fnmatch(f,f"*{query}*")]
        if len(hits)>100: break
    return hits

@tool()
def copy_file(src_rel:str, dest_rel:str) -> str:
    shutil.copy2(_p(src_rel), _p(dest_rel)); return "copied"

@tool()
def delete_file(rel_path:str)->str:
    p=_p(rel_path); p.unlink(); return "deleted"

if __name__=="__main__":
    mcp.run(transport="stdio")
