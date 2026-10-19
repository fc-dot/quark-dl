#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查依赖是否装好，并打印中文结果。"""
import sys

ok = True
try:
    import curl_cffi
    print(f"  curl_cffi 版本 {curl_cffi.__version__}   [已安装 OK]")
except Exception:                                        # noqa: BLE001
    ok = False
    print("  curl_cffi  [未安装]")

print("")
if ok:
    print("依赖已就绪，可以双击「夸克下载.bat」使用了。")
else:
    print("依赖没装上。请检查：")
    print("  1) 是否装了 Python（命令行执行 python --version 能看到版本号）")
    print("  2) 装 Python 时是否勾选了 Add to PATH")
    print("  3) 网络是否正常，然后重新运行本文件")

sys.exit(0 if ok else 1)
