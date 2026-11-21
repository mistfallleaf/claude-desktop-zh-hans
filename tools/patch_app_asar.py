#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_app_asar.py — Claude Desktop 壳层 app.asar 等长字节补丁工具
=================================================================

适用版本: Claude Desktop 6.28899.2（6.6395 同理，改 CONFIG 里的 chunk 名即可）

做三件事:
  1. 在 asar 内指定 JS 文件里做「等长字节替换」（补丁长度 = 原文长度，
     不够就在文件尾部补空格，保证文件 size 不变 → asar header 的 offset/size 不用动）
  2. 重算该文件在 asar header 里的 integrity（SHA256 + 4MB 分块哈希），
     等长替换 header JSON 片段
  3. 输出新 asar 的新 header 哈希（需要同步进 Claude.exe 的完整性标记，
     由 deploy.ps1 完成）

原理见 docs/integrity.md
"""
import argparse
import hashlib
import json
import struct
import sys

BLOCK_SIZE = 4194304  # 4MB，与 Electron asar integrity 一致


# ---------------------------------------------------------------- 基础读取
def read_header(raw: bytes):
    """返回 (header_json_dict, header_size, data_start, header_bytes)"""
    if len(raw) < 16:
        raise SystemExit("不是合法的 asar 文件")
    header_size = struct.unpack_from("<I", raw, 4)[0]
    string_size = struct.unpack_from("<i", raw, 12)[0]
    header_bytes = raw[16:16 + string_size]
    header = json.loads(header_bytes)
    data_start = 8 + header_size
    return header, header_size, data_start, header_bytes


def find_entry(header: dict, path: str) -> dict:
    node = header
    for part in path.split("/"):
        node = node["files"][part]
    return node


def file_integrity(data: bytes) -> dict:
    blocks = [hashlib.sha256(data[i:i + BLOCK_SIZE]).hexdigest()
              for i in range(0, len(data), BLOCK_SIZE)]
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(data).hexdigest(),
        "blockSize": BLOCK_SIZE,
        "blocks": blocks,
    }


# ---------------------------------------------------------------- 主流程
def patch(src: str, dst: str, entry_path: str, replacements, pad_tail: bool = True):
    raw = open(src, "rb").read()
    header, header_size, data_start, header_bytes = read_header(raw)
    entry = find_entry(header, entry_path)
    off, size = int(entry["offset"]), int(entry["size"])
    pos = data_start + off
    content = bytearray(raw[pos:pos + size])
    assert len(content) == size, "读取长度与 header 记录不一致"

    delta = 0
    for old, new in replacements:
        old_b = old.encode("utf-8") if isinstance(old, str) else old
        new_b = new.encode("utf-8") if isinstance(new, str) else new
        n = content.count(old_b)
        if n != 1:
            raise SystemExit(f"补丁目标必须唯一命中: {old!r} 命中 {n} 次")
        content = content.replace(old_b, new_b)
        delta += len(new_b) - len(old_b)

    # 等长处理：短了补空格，长了从尾部空白回收
    if delta < 0 and pad_tail:
        content += b" " * (-delta)
    elif delta > 0:
        tail = bytes(content[-delta:])
        if tail.strip():
            raise SystemExit("替换后变长且文件尾部没有空白可回收，请调整译文长度")
        content = content[:-delta]
    new_content = bytes(content)
    assert len(new_content) == size, "等长失败"

    # 重算 integrity 并等长替换 header 片段
    integ = entry.get("integrity")
    if integ is None:
        raise SystemExit("该文件在 header 里没有 integrity 字段（旧版 asar 无需此步）")
    new_integ = file_integrity(new_content)
    old_frag = json.dumps(integ, ensure_ascii=False, separators=(",", ":"))
    new_frag = json.dumps(new_integ, ensure_ascii=False, separators=(",", ":"))
    if len(old_frag) != len(new_frag):
        raise SystemExit(f"integrity 片段长度不一致: {len(old_frag)} vs {len(new_frag)}")
    htext = header_bytes.decode("utf-8")
    if htext.count(old_frag) != 1:
        raise SystemExit("header 中 integrity 片段无法唯一命中")
    new_header = htext.replace(old_frag, new_frag).encode("utf-8")
    assert len(new_header) == len(header_bytes)

    out = raw[:16] + new_header + raw[16 + len(header_bytes):pos] + new_content + raw[pos + size:]
    assert len(out) == len(raw)
    open(dst, "wb").write(out)

    new_header_hash = hashlib.sha256(new_header).hexdigest()
    print(f"[OK] 写出 {dst}")
    print(f"[OK] 文件新 SHA256: {new_integ['hash']}")
    print(f"[!!] 新 header 哈希（需同步进 Claude.exe 的 app.asar 标记）: {new_header_hash}")
    return new_header_hash


def main():
    ap = argparse.ArgumentParser(description="app.asar 等长字节补丁 + integrity 重建")
    ap.add_argument("--asar", required=True, help="原始 app.asar 路径")
    ap.add_argument("--out", required=True, help="输出 asar 路径")
    ap.add_argument("--entry", required=True, help="asar 内文件路径，如 .vite/build/index.chunk-XXXX.js")
    ap.add_argument("--old", action="append", required=True, help="待替换的原文（可多次）")
    ap.add_argument("--new", action="append", required=True, help="译文（与 --old 对应）")
    args = ap.parse_args()
    if len(args.old) != len(args.new):
        raise SystemExit("--old 与 --new 数量不一致")
    patch(args.asar, args.out, args.entry, list(zip(args.old, args.new)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
