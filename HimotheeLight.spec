# Build this spec on Windows using "Build Windows EXE.bat".
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules('himotheelight') + collect_submodules('websocket')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('web', 'web'), ('browser-extension', 'browser-extension')],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HimotheeLight',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name='HimotheeLight')
