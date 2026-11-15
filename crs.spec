# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec - onedir build:  pyinstaller crs.spec --noconfirm
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

hiddenimports = collect_submodules("keyring.backends")
datas = [("defaults", "defaults")]
for pkg in ("keyring", "openai"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

excludes = [
    "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtWebSockets", "PySide6.QtWebChannel", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSvgWidgets",
    "numpy", "pytest", "matplotlib", "pandas", "scipy", "IPython",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
# Claude Code 연결용 MCP 서버: 같은 폴더에 콘솔 실행 파일로 넣는다 (표준 입출력으로 통신해야 해서 console=True)
m = Analysis(
    ["mcp_main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes + ["PySide6.QtWidgets", "PySide6.QtGui"],
    noarchive=False,
)
pyz = PYZ(a.pure)
mpyz = PYZ(m.pure)
mexe = EXE(
    mpyz,
    m.scripts,
    [],
    exclude_binaries=True,
    name="VConceptStudio-mcp",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VConceptStudio",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # GUI app: no console window
)
coll = COLLECT(exe, a.binaries, a.datas, mexe, m.binaries, m.datas, strip=False, upx=False, name="VConceptStudio")
