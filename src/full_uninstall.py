#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""彻底卸载：卸载依赖 + 清掉所有配置 + 删除下载记录里那些文件和空目录。

只删 downloads.json 里登记过的文件，不会乱删别的。
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA_DIR = ROOT / "data"
HISTORY = DATA_DIR / "downloads.json"
FILES = ["cookie.txt", "config.json", "downloads.json"]


def prune_empty_dirs(start: Path):
    """从 start 往上删空目录，最多到 stop_at（含）。"""
    cur = start.resolve()
    for _ in range(10):
        try:
            if not cur.is_dir() or any(cur.iterdir()):
                break
            if len(cur.parts) <= 1:      # 绝不删盘符根目录
                break
            cur.rmdir()
            print(f"  已删除空目录 {cur}", flush=True)
            cur = cur.parent
        except Exception:                                # noqa: BLE001
            break


def main():
    print("=" * 56)
    print(" 彻底卸载：依赖 + 配置 + 已下载的文件")
    print("=" * 56)
    print("")

    # 1) 已下载的文件
    files = []
    if HISTORY.exists():
        try:
            files = json.loads(HISTORY.read_text(encoding="utf-8-sig")) or []
        except Exception:                                # noqa: BLE001
            files = []

    if files:
        print(f"  下载记录里有 {len(files)} 个文件：")
        for p in files:
            print(f"    {p}")
        print("")
        ans = ""
        try:
            ans = input("  确认全部删除？（输入 y 删除，其他键取消）：").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans == "y":
            for p in files:
                f = Path(p)
                try:
                    if f.exists():
                        f.unlink()
                        print(f"  已删除 {f.name}")
                        prune_empty_dirs(f.parent)
                except Exception as e:                   # noqa: BLE001
                    print(f"  删不掉 {p}：{e}")
        else:
            print("  已跳过删除文件")
    else:
        print("  没有下载记录，跳过删除文件")

    # 2) 配置
    print("")
    for name in FILES:
        f = DATA_DIR / name
        if f.exists():
            f.unlink()
            print(f"  已删除 {name}")

    # 3) 依赖
    print("")
    print("  正在卸载依赖 curl_cffi …", flush=True)
    rc = subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", "curl_cffi"])
    print("  curl_cffi 已卸载" if rc == 0 else "  卸载返回码 " + str(rc) + "（可能本来就没装）")

    print("")
    print("-" * 56)
    print("完成。工具本身还留着（quark.py / bat / 说明），想彻底不要了就整个文件夹删掉。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
