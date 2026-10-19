#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""恢复到纯净版：清掉 Cookie、保存路径记忆、下载记录，并卸载 curl_cffi。

已经下载到本地的文件【不会】删除。
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA_DIR = ROOT / "data"
FILES = ["cookie.txt", "config.json", "downloads.json"]


def main():
    print("=" * 56)
    print(" 恢复到纯净版（相当于刚拿到这个工具的状态）")
    print("=" * 56)
    print("")

    removed = 0
    for name in FILES:
        f = DATA_DIR / name
        if f.exists():
            f.unlink()
            print(f"  已删除 {name}")
            removed += 1
    if not removed:
        print("  没有需要删除的配置（已经是纯净状态）")

    print("")
    print("  正在卸载依赖 curl_cffi …")
    rc = subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", "curl_cffi"])
    if rc == 0:
        print("  curl_cffi 已卸载")
    else:
        print(f"  卸载返回码 {rc}（可能本来就没装）")

    print("")
    print("-" * 56)
    print("完成。现在是纯净状态：")
    print("  · 没有 Cookie")
    print("  · 没有保存路径记忆")
    print("  · 没有下载记录")
    print("  · 没有依赖 curl_cffi")
    print("")
    print("下次双击「安装依赖.bat」→「夸克下载.bat」，就是完整的新手流程。")
    print("已下载到本地的文件不受影响。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
