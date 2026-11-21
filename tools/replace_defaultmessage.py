#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
replace_defaultmessage.py — 批量替换前端 JS 里硬编码的 defaultMessage 英文原文
================================================================================

适用: Claude Desktop 6.x（服务端动态 i18n 架构，本地词库无效的版本）

原理: 6.x 前端的界面文案是 6 万个 `defaultMessage:"英文"` 硬编码在 JS chunk 里，
      运行时用消息 id 去服务端 catalog 查翻译，查不到就回落 defaultMessage。
      账号 locale=en-US 时服务器只下发英文 → 界面永远英文。
      本脚本直接把 defaultMessage 的英文原文替换成中文，绕过服务端。

用法:
  python replace_defaultmessage.py <assets 目录>   # 如 ion-dist-extract/assets
  python replace_defaultmessage.py                 # 默认 ./ion-dist-extract/assets

依赖: en2zh.json（英文原文 -> 中文 的映射，放本脚本同目录）

替换后务必用 acorn 做语法校验（见 README），再打包回 asar。
"""
import json
import os
import sys

PROJ = os.path.dirname(os.path.abspath(__file__))
# en2zh.json 允许放 tools/ 或仓库根目录
_candidates = [
    os.path.join(PROJ, "en2zh.json"),
    os.path.join(os.path.dirname(PROJ), "en2zh.json"),
]
EN2ZH_PATH = next((p for p in _candidates if os.path.exists(p)), _candidates[0])
EN2ZH = json.load(open(EN2ZH_PATH, encoding="utf-8"))


def js_decode(s: str) -> str:
    """把 JS 字符串字面量内容解码成 Python str"""
    try:
        return json.loads('"' + s + '"')
    except Exception:
        return s


def js_encode(s: str) -> str:
    """把 Python str 编码成 JS 双引号字符串的字面量内容（不含引号）"""
    return json.dumps(s, ensure_ascii=False)[1:-1]


def replace_in_file(text: str):
    """返回 (新文本, 替换处数)"""
    out = text
    changed = 0
    idx = 0
    needle = "defaultMessage:"
    while True:
        i = out.find(needle, idx)
        if i < 0:
            break
        j = i + len(needle)
        while j < len(out) and out[j] in " \t":
            j += 1
        if j >= len(out) or out[j] not in "\"'":
            idx = j
            continue
        quote = out[j]
        k = j + 1
        buf = []
        closed = False
        while k < len(out):
            c = out[k]
            if c == "\\":                      # 转义序列整体保留
                buf.append(out[k:k + 2])
                k += 2
                continue
            if c == quote:
                closed = True
                break
            buf.append(c)
            k += 1
        if not closed:
            idx = j
            continue
        decoded = js_decode("".join(buf))
        zh = EN2ZH.get(decoded)
        if zh and zh != decoded:
            new_body = js_encode(zh)
            if quote == "'":                   # 单引号字面量要转义单引号
                new_body = new_body.replace("'", "\\'")
            newlit = quote + new_body + quote
            out = out[:j] + newlit + out[k + 1:]
            changed += 1
            idx = j + len(newlit)
        else:
            idx = k + 1
    return out, changed


def main():
    assets = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else \
        os.path.join(PROJ, "ion-dist-extract", "assets")
    if not os.path.isdir(assets):
        print("assets 目录不存在:", assets)
        return 1
    total_files = total_replaced = 0
    report = []
    for root, _dirs, files in os.walk(assets):
        for f in files:
            if not f.endswith(".js"):
                continue
            p = os.path.join(root, f)
            text = open(p, encoding="utf-8", errors="ignore").read()
            new_text, n = replace_in_file(text)
            if n:
                open(p, "w", encoding="utf-8").write(new_text)
                total_replaced += n
                report.append([os.path.relpath(p, assets), n])
            total_files += 1
    print(f"扫描文件: {total_files}")
    print(f"改动文件: {len(report)}")
    print(f"替换总数: {total_replaced}")
    json.dump(report, open(os.path.join(PROJ, "replace-report.json"), "w",
                           encoding="utf-8"), ensure_ascii=False)
    print("明细已写入 replace-report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
