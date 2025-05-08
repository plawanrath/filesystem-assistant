#!/usr/bin/env python3
import os, io, json, pathlib
from pathlib import Path
from fastmcp import FastMCP
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.auth.transport.requests import Request  

SCOPES = ["https://www.googleapis.com/auth/drive"]
HOME = Path.home()/".filesystem_assistant"
HOME.mkdir(exist_ok=True)
TOKEN_PATH = HOME/"gdrive_token.json"
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET_JSON")

def _creds():
    creds=None
    if TOKEN_PATH.exists():
        creds=Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH,"w") as f: f.write(creds.to_json())
    return creds

service = build("drive","v3",credentials=_creds(),cache_discovery=False)
mcp = FastMCP("GoogleDrive")

@mcp.tool()
def list_files(folder_id:str="root") -> list[dict]:
    """List name,id,mimeType in a folder."""
    q=f"'{folder_id}' in parents and trashed=false"
    res=service.files().list(q=q,fields="files(id,name,mimeType)").execute()
    return res["files"]

@mcp.tool()
def search_files(query:str) -> list[dict]:
    res=service.files().list(q=f"name contains '{query}' and trashed=false",
                             fields="files(id,name,mimeType)").execute()
    return res["files"]

@mcp.tool()
def rename_file(file_id:str,new_name:str)->str:
    service.files().update(fileId=file_id,body={"name":new_name}).execute()
    return "renamed"

@mcp.tool()
def delete_file(file_id:str)->str:
    service.files().delete(fileId=file_id).execute()
    return "deleted"

@mcp.tool()
def download_file(file_id:str)->str:
    """Download to temp dir; return local path."""
    request=service.files().get_media(fileId=file_id)
    fname=service.files().get(fileId=file_id,fields="name").execute()["name"]
    path=str(HOME/fname)
    with open(path,"wb") as fh:
        downloader=MediaIoBaseDownload(fh,request)
        done=False
        while not done:
            _,done=downloader.next_chunk()
    return path

@mcp.tool()
def upload_file(local_path:str, dest_folder_id:str="root")->str:
    file_metadata={"name":Path(local_path).name,"parents":[dest_folder_id]}
    media=MediaFileUpload(local_path, resumable=True)
    service.files().create(body=file_metadata,media_body=media).execute()
    return "uploaded"

if __name__=="__main__":
    mcp.run(transport="stdio")
