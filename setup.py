from pathlib import Path
from setuptools import setup
from PyQt6 import QtCore

APP = ["main.py"]
DATA_FILES = [
    ("", ["assets/icon.icns", ".env"]),
]

# ---- Qt6 platform plugins ----
qt_plugin_dir = Path(QtCore.__file__).parent / "Qt6" / "plugins"
DATA_FILES.append(
    (
        "qt_plugins/platforms",
        [str(p.resolve()) for p in (qt_plugin_dir / "platforms").glob("*")]
    )
)

OPTIONS = {
    "iconfile": "assets/icon.icns",
    # "argv_emulation": True,  # only if your build of py2app supports it
    "plist": {
        "CFBundleName": "Filesystem Assistant",
        "CFBundleIdentifier": "com.yourname.filesystemassistant",
    },
    "packages": [
        "PyQt6",
        "anyio",
        "charset_normalizer",
        "pydantic_settings",
        "fastmcp",
        "mcp",
        "openai",
        "qasync",
        "googleapiclient",
        "google_auth_httplib2",
        "google_auth_oauthlib",
        "synology_api",
        "backend",
    ],
    "includes": [
        "anyio._backends._asyncio",
        "sqlalchemy.dialects.postgresql.asyncpg",
    ],
    "excludes": ["PyInstaller", "gi", "gi.repository"],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
