#!/usr/bin/env python3
import os
from fastmcp import FastMCP
from synology_api import filestation, exceptions
from backend.host.config import settings  # adjust import if packaging separately

try:
    fs = filestation.FileStation(
        ip_address=settings.nas_host,          # e.g. "192.168.1.5"
        port=int(settings.nas_port),          # e.g. 5001  (add this to your settings)
        username=settings.nas_user,
        password=settings.nas_pass,
        secure=True,                     # HTTPS; set False if you’re on HTTP/5000
        cert_verify=False,               # skip SSL cert checks for self-signed NAS certs
    )
except exceptions.LoginError as e:
    print("Synology login failed:", e, "-- NAS tool disabled.")

mcp = FastMCP("SynologyNAS")

def _check():
    if fs is None:
        raise RuntimeError("NAS not connected; fix credentials.")
    
@mcp.tool()
def list_directory(path:str="/") -> list[str]:
    _check()
    data=fs.get_list(path)["data"]["files"]
    return [f["name"] for f in data]

@mcp.tool()
def delete_file(path:str)->str:
    _check()
    fs.delete(path,True); return "deleted"

@mcp.tool()
def rename_file(path:str,new_name:str)->str:
    _check()
    fs.rename(path,new_name); return "renamed"

@mcp.tool()
def copy_file(src:str,dest:str)->str:
    _check()
    fs.copy_move(src,dest,"copy"); return "copied"

if __name__=="__main__":
    mcp.run(transport="stdio")
