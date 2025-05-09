#!/usr/bin/env python3
import os, shutil, fnmatch
from pathlib import Path
from fastmcp import FastMCP

ROOT = Path.home()          # never leave HOME
mcp  = FastMCP("LocalFS")

def _abs(p: str) -> Path:
    p = os.path.expanduser(p)
    return (Path(p) if os.path.isabs(p) else ROOT / p).resolve()

# ---------------------------------------------------------------------
#  canonical list_files  (ONE name, TWO parameters)  ←—— ★
# ---------------------------------------------------------------------
@mcp.tool(
    name="list_files",
    description="List directory contents. If file_type=='files' return only files."
)
def list_files(directory: str = "~", file_type: str = "all") -> list[str]:
    base = _abs(directory)
    try:
        entries = os.listdir(base)
    except FileNotFoundError:
        return []
    if file_type.lower() == "files":
        entries = [e for e in entries if os.path.isfile(base / e)]
    return entries

# ---------- thin alias wrappers (optional) --------------------------
@mcp.tool(name="listdir", description="Alias → list_files")
def listdir(path: str = "~"):
    return list_files(path, "all")

@mcp.tool(name="folder_files", description="Alias → list_files")
def folder_files(folder: str = "~", file_type: str = "all"):
    return list_files(folder, file_type)

@mcp.tool()
def search_files(query:str, path:str="~") -> list[str]:
    """Recursively search by filename glob."""
    base=_abs(path)
    hits=[]
    for root,_,files in os.walk(base):
        hits += [os.path.join(root,f) for f in files if fnmatch.fnmatch(f, f"*{query}*")]
        if len(hits)>100: break
    return hits

@mcp.tool()
def rename_file(old_path:str, new_name:str) -> str:
    p=_abs(old_path); dest=p.with_name(new_name)
    os.rename(p, dest)
    return f"renamed to {dest}"

@mcp.tool()
def move_file(src:str, dest:str) -> str:
    shutil.move(_abs(src), _abs(dest))
    return "moved"

@mcp.tool()
def copy_file(src:str, dest:str) -> str:
    shutil.copy2(_abs(src), _abs(dest))
    return "copied"

@mcp.tool()
def delete_file(path:str) -> str:
    p=_abs(path)
    if p.is_dir(): shutil.rmtree(p)
    else: p.unlink()
    return "deleted"

if __name__=="__main__":
    mcp.run(transport="stdio")
