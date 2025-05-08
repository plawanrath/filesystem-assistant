#!/usr/bin/env python3
import os, shutil, fnmatch
from pathlib import Path
from fastmcp import FastMCP, tool

ROOT = Path.home()   # limit ops to user home for safety
mcp = FastMCP("LocalFS")

def _abs(p:str) -> Path:
    pth = (ROOT / p.lstrip("~/")).expanduser().resolve()
    if not str(pth).startswith(str(ROOT)):
        raise ValueError("Access outside home disallowed")
    return pth

@tool()
def list_directory(path:str="~") -> list[str]:
    """Return names in directory."""
    p=_abs(path)
    return os.listdir(p)

@tool()
def search_files(query:str, path:str="~") -> list[str]:
    """Recursively search by filename glob."""
    base=_abs(path)
    hits=[]
    for root,_,files in os.walk(base):
        hits += [os.path.join(root,f) for f in files if fnmatch.fnmatch(f, f"*{query}*")]
        if len(hits)>100: break
    return hits

@tool()
def rename_file(old_path:str, new_name:str) -> str:
    p=_abs(old_path); dest=p.with_name(new_name)
    os.rename(p, dest)
    return f"renamed to {dest}"

@tool()
def move_file(src:str, dest:str) -> str:
    shutil.move(_abs(src), _abs(dest))
    return "moved"

@tool()
def copy_file(src:str, dest:str) -> str:
    shutil.copy2(_abs(src), _abs(dest))
    return "copied"

@tool()
def delete_file(path:str) -> str:
    p=_abs(path)
    if p.is_dir(): shutil.rmtree(p)
    else: p.unlink()
    return "deleted"

if __name__=="__main__":
    mcp.run(transport="stdio")
