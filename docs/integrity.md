# Claude Desktop 6.x 三层防篡改机制详解

改 asar 之所以反复失败，是因为新版有三层校验，层层递进。本文把每层的原理、报错特征和破解方法讲清楚。

---

## 第一层：asar header 里的每文件 integrity（内容校验）

### 原理

6.28899.2 起，asar 的 header JSON 里**每个文件条目**都带一个 `integrity` 对象：

```json
"index.chunk-XXXX.js": {
  "size": 8091535,
  "offset": "9328172",
  "integrity": {
    "algorithm": "SHA256",
    "hash": "4bfba45e...（整个文件的 SHA256）",
    "blockSize": 4194304,
    "blocks": ["271c56ba...", "ba38ab92..."]  // 每 4MB 一块的分块哈希
  }
}
```

Electron 启动时会对 asar 内被加载的文件重算 SHA256 / 分块哈希，和 header 里的记录比对。

### 报错特征

```
ASAR Integrity Violation: got a hash mismatch (0c29b7d0... vs 4bfba45e...)
```

**关键鉴别点**：这两个值都是**文件内容**的 SHA256——`got` 是你改过的文件的实际哈希，`expected` 是 header 里记录的原版哈希。用「整文件 SHA256」对号入座，就知道是哪个文件挂了。

### 破解方法：等长字节替换

直接改文件内容，哈希必然对不上。有三种路线：

| 路线 | 做法 | 评价 |
|---|---|---|
| A. 全量重打包 | asar pack 重新打包 | ion-dist 适用：新版 `@electron/asar pack` 会**自动重算并写入每个文件的 integrity**，一步到位 |
| B. 追加法 | 新内容追加到 payload 末尾，header 里改该文件的 offset/size/integrity | 上游社区补丁的做法，header 会变长 |
| C. **等长替换**（本项目采用） | 改内容时保持文件**总字节数不变**（译文比原文短就在文件尾部补空格，JS 合法） | header 的 offset/size 一个字节都不用动，只需把 header 里该文件的 `hash` 和 `blocks` 做等长替换（64 位十六进制换 64 位十六进制） |

路线 C 的巧妙之处：integrity 片段的 JSON 长度完全不变，header JSON 总长度不变，header 的**二进制布局**（pickle 长度前缀）也不用动。

工具：`tools/patch_app_asar.py` 自动完成「等长补丁 + integrity 重建」。

---

## 第二层：Claude.exe 里的 asar header 哈希标记

### 原理

Claude.exe 二进制里埋了两条明文记录（Electron 的 `ElectronAsarIntegrity` 机制）：

```
{"file":"resources\\app.asar","alg":"SHA256","value":"<64位十六进制>"}
{"file":"resources\\ion-dist.asar","alg":"SHA256","value":"<64位十六进制>"}
```

这个值是 **asar header JSON 的 SHA256**（不是文件内容）。header 变（第一层路线 B/C 都会动 header），这个值就必须同步，否则启动直接拒绝。

### 破解方法：定点等长改写

定位 marker 字符串，往后 64 字节就是旧哈希，原地写新哈希（都是 64 位十六进制，等长）。工具 `tools/deploy.ps1` 的 `Set-ExeAsarHash` 函数。

注意 marker 的几种历史格式（单/双反斜杠、带不带 `{"file":` 前缀），定位时多种变体都要试。

---

## 第三层：MSIX 包签名与机器策略（持久化之战）

### 原理

MSIX 包带签名，但**实测文件内容被修改不影响启动**（启动走的是 exe 内嵌哈希那层）。真正致命的是自动更新：更新器下载新版本 MSIX，应用退出时自动安装，**整个旧包被卸载，所有修改灰飞烟灭**。

### 破解方法：机器策略封顶版本

新版更新器读机器策略 `HKLM\SOFTWARE\Policies\Claude`，其中 `dangerousMaxVersion` 是版本封顶键——当前版本 ≥ 该值时更新器拒绝升级：

```bat
reg add "HKLM\SOFTWARE\Policies\Claude" /v dangerousMaxVersion /t REG_SZ /d 6.28899.2 /f
reg add "HKLM\SOFTWARE\Policies\Claude" /v disableAutoUpdates /t REG_SZ /d true /f
```

⚠️ `configLibrary` 里的 `disableAutoUpdates`（用户级配置）**拦不住** MSIX 包级更新，必须用 HKLM 机器策略。

以后想升级：删掉这两个注册表值 → 正常更新 → 重跑本流水线。

---

## 排坑速查

| 现象 | 原因 | 解法 |
|---|---|---|
| `ASAR Integrity Violation: got a hash mismatch (X vs Y)` | 改了文件内容，header integrity 没同步 | 用 patch_app_asar.py 等长替换并重建 integrity |
| 启动无任何日志、无窗口 | exe 内 asar 哈希标记未同步 | deploy.ps1 同步两个 marker |
| `Failed to read header size from app.asar` | app.asar 被更新器清零/损坏 | 从备份恢复原版 asar |
| 沙箱 Python `bytearray.replace` 静默失效 | 沙箱对内存原地修改的拦截 | 用不可变 `bytes.replace`（返回新对象） |
| 提权 bat 里变量为空 | bat 文件含中文，编码坏了 | bat 纯 ASCII |
| 提权 `powershell -File` 静默失败 | 脚本路径含空格 | 复制到无空格路径再 -File |
| 菜单还是英文但快捷键是中文 | 壳层 locale 没走 zh-Hans | 见 README 壳层补丁 |
