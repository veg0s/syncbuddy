# -*- mode: python ; coding: utf-8 -*-
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2026 Veg0s
#
# PyInstaller 打包配置
#
#   pyinstaller syncbuddy.spec --noconfirm
#
# 选用 onedir 而不是 onefile：onefile 每次启动要把 100MB 解压到临时目录，
# PySide6 尤其慢，托盘程序每次开机都等十几秒不能接受。
#
# wbsync/ 和 tools/ 以明文数据文件形式带上 —— 打包后没有独立 python，
# core.py 靠 `SyncBuddy.exe --run <脚本>` 在子进程里执行它们。

block_cipher = None

a = Analysis(
    ['syncbuddy.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('VERSION', '.'),
        ('config.example.json', '.'),
        ('icons', 'icons'),
        ('wbsync', 'wbsync'),
        ('tools', 'tools'),
    ],
    hiddenimports=[
        # 动态 import（在函数体里），静态分析抓不到
        'PySide6.QtSvg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 只用到 QtCore / QtGui / QtWidgets / QtSvg，其余全排掉，
        # 不然 PySide6 hook 会把整套 Qt 塞进来（300MB+）
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineQuick', 'PySide6.QtWebChannel',
        'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.QtQml',
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic', 'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtCharts', 'PySide6.QtDataVisualization',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtNetworkAuth',
        'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtPositioning',
        'PySide6.QtSensors', 'PySide6.QtSerialPort', 'PySide6.QtSerialBus',
        'PySide6.QtRemoteObjects', 'PySide6.QtScxml', 'PySide6.QtStateMachine',
        'PySide6.QtUiTools', 'PySide6.QtDesigner', 'PySide6.QtHelp',
        'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtSpatialAudio',
        'PySide6.QtTextToSpeech', 'PySide6.QtHttpServer', 'PySide6.QtGraphs',
        'PySide6.QtGraphsWidgets', 'PySide6.QtLocation', 'PySide6.QtConcurrent',
        # 标准库里用不到的
        # 注意：sqlite3 不能排（要读 workbuddy.db），
        #       email 也不能排（urllib.request 依赖它做 header 解析）
        'tkinter', 'pydoc', 'doctest', 'pdb', 'http.server', 'xmlrpc',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SyncBuddy',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # UPX 压缩过的 exe 容易被杀软误报
    console=False,          # 托盘程序，不要黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SyncBuddy',
)
