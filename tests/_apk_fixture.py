#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约测试用的**自建**最小 APK 生成器（纯标准库，不联网、不碰第三方）。

⛔ 为什么必须自建样本，而不是从网上抓一个 APK：
   ① REA 上游的 DISCLAIMER 明确限定：只分析自己拥有或获授权的目标。
      抓第三方闭源应用进仓库，既越权又引入许可证问题。
   ② 测试不能依赖外网可达性 —— 否则"测试通过"变成运气。
   ③ **自己造的样本才有先验真值**：包名、类数在构造时就已知，
      于是判据是"上游读数 == 我埋进去的数"，而不是
      "上游读数 == 上游读数"（后者是橡皮图章，见 V5 手册第 0 节）。

⛔⛔ 本文件记录的**四个真实踩过的坑**（改这里之前先读）：

   ① `ResXMLTree_attrExt.attributeStart` 是**相对 attrExt 起点**的偏移，
      不是相对 chunk 起点。attrExt 起点 = header(8)+line(4)+comment(4)
      = 16。漏掉这个 16 ⇒ 每个属性名都读成空串 ⇒ 判据永远匹配不上。

   ② `<manifest package="...">` 的属性名是字符串 **"package"**，不是 "name"。
      属性名取自 string pool 索引，池子里没登记这个词 ⇒ 解出来属性名是
      "name"，判据自然永远找不到 package。

   ③ DEX header 的 proto/field/method 三组各是 size+off ⇒ 一共 **6 个**
      u4，不是 4 个；少写两个会把后面所有字段整体前移 8 字节 ⇒
      class_defs 偏移变成天文数字。

   ④ `map_list` 必须落在 **data 段内部**（真实 dex 都放在 data 段末尾）。
      一度排在 data 段之前 ⇒ `map_off < data_off` ⇒ 严格解析器整份丢弃
      dex，`class_count` 变 0 —— 而 manifest 走另一条路径照样解出来，
      极易误判成"通道正常、只是这个包类少"。

   ⑤ 带 `code_off` 的 class_data_item 长度随 code_off 的 uleb 编码长度
      变化 ⇒ **不能先按占位长度算步长**。本实现让所有类共享同一个
      class_data_item（定长 4 字节全零），彻底避开这个耦合。
"""
from __future__ import annotations

import hashlib
import struct
import zipfile
import zlib

# ───────────────────────────── AXML ─────────────────────────────
RES_STRING_POOL_TYPE = 0x0001
RES_XML_TYPE = 0x0003
RES_XML_START_NAMESPACE = 0x0100
RES_XML_END_NAMESPACE = 0x0101
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103

NO_NS = 0xFFFFFFFF
ANDROID_NS = "http://schemas.android.com/apk/res/android"


def _uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _pad4(b: bytes) -> bytes:
    while len(b) % 4:
        b += b"\x00"
    return b


def build_string_pool(strings: list[str]) -> bytes:
    """ResStringPool（UTF-16 / flags=0）—— aapt2 默认形态。"""
    data = bytearray()
    offsets = []
    for s in strings:
        offsets.append(len(data))
        n = len(s.encode("utf-16-le")) // 2
        if n > 0x7FFF:
            data += struct.pack("<HH", (n >> 16) | 0x8000, n & 0xFFFF)
        else:
            data += struct.pack("<H", n)
        data += s.encode("utf-16-le")
        data += b"\x00\x00"
    data = _pad4(bytes(data))
    header_size = 28
    strings_start = header_size + 4 * len(strings)
    total = strings_start + len(data)
    out = struct.pack("<HHI", RES_STRING_POOL_TYPE, header_size, total)
    out += struct.pack("<5I", len(strings), 0, 0, strings_start, 0)
    for off in offsets:
        out += struct.pack("<I", off)
    return out + data


def _node(ctype: int, line: int, payload: bytes) -> bytes:
    """ResXMLTree_node = header(8) + lineNumber(4) + comment(4) + payload。

    ⛔ chunk **type 必须逐节点给**，不能写死成 0 —— 写死之后整棵树变成
       一串 NULL chunk，解析器根本找不到 <manifest>（实测读到 pkg=None）。
    """
    return (struct.pack("<HHI", ctype, 16, 16 + len(payload))
            + struct.pack("<II", line, NO_NS) + payload)


def _attr(name_idx: int, raw_idx: int, ns_idx: int = NO_NS) -> bytes:
    """ResXMLTree_attribute（20 字节），dataType = 0x03（字符串）。"""
    return (struct.pack("<III", ns_idx, name_idx, raw_idx)
            + struct.pack("<HBBI", 8, 0, 0x03, 0))


def _elem(name_idx: int, attrs: list[bytes]) -> bytes:
    return _node(
        RES_XML_START_ELEMENT, 2,
        struct.pack("<II", NO_NS, name_idx)
        + struct.pack("<HHHHHH", 20, 20, len(attrs), 0, 0, 0)
        + b"".join(attrs))


def _end_elem(name_idx: int) -> bytes:
    return _node(RES_XML_END_ELEMENT, 2,
                 struct.pack("<II", NO_NS, name_idx))


def build_axml(package: str, activity: str) -> bytes:
    """<manifest package=…><application><activity android:name=…/></application></manifest>"""
    pool: list[str] = []

    def sid(s: str) -> int:
        if s not in pool:
            pool.append(s)
        return pool.index(s)

    #: ⛔ 全部字符串在 build_string_pool **之前**登记完。
    i_pkg_val = sid(package)
    i_manifest = sid("manifest")
    i_application = sid("application")
    i_activity = sid("activity")
    i_attr_name = sid("name")
    i_attr_version = sid("versionName")
    i_attr_package = sid("package")
    i_ns_prefix = sid("android")
    i_ns_uri = sid(ANDROID_NS)
    i_act_val = sid(activity)
    i_ver_val = sid("1.0")

    sp = build_string_pool(pool)
    a_manifest = [_attr(i_attr_version, i_ver_val),
                  _attr(i_attr_package, i_pkg_val)]
    a_activity = [_attr(i_attr_name, i_act_val, ns_idx=i_ns_uri)]

    body = (_node(RES_XML_START_NAMESPACE, 1,
                  struct.pack("<II", i_ns_prefix, i_ns_uri))
            + _elem(i_manifest, a_manifest)
            + _elem(i_application, [])
            + _elem(i_activity, a_activity)
            + _end_elem(i_activity)
            + _end_elem(i_application)
            + _end_elem(i_manifest)
            + _node(RES_XML_END_NAMESPACE, 1,
                    struct.pack("<II", i_ns_prefix, i_ns_uri)))

    return struct.pack("<HHI", RES_XML_TYPE, 8,
                       8 + len(sp) + len(body)) + sp + body


# ───────────────────────────── DEX ─────────────────────────────
DEX_MAGIC = b"dex\n035\x00"
HEADER_SIZE = 112
ACC_PUBLIC = 0x0001


def _map_list(map_off: int, str_off: int, type_off: int, class_off: int,
              n_str: int, n_types: int, n_classes: int,
              data_off: int, data_len: int) -> bytes:
    """map_list：声明各 section 的位置。

    ⛔ class_data_item（0x2000）紧跟 code_item，而这里没有 code_item
       ⇒ 它就在 data 段末尾、map_list 之前。
    """
    items = [
        (0x0000, 1, 0),                             #: header_item
        (0x0001, n_str, str_off),                   #: string_id_item
        (0x0002, n_types, type_off),                #: type_id_item
        (0x0006, n_classes, class_off),             #: class_def_item
        (0x2000, n_classes, data_off + data_len - 4 * n_classes),
        (0x1000, 1, map_off),                       #: map_list
    ]
    out = struct.pack("<I", len(items))
    for t, c, o in items:
        out += struct.pack("<HHII", t, 0, c, o)
    return out


def build_dex(classes: list[tuple[str, str]]) -> bytes:
    """classes: [(类描述符, 父类描述符)] → 最小可加载 dex 的字节。

    布局（连续 + 4 字节对齐）：
        header(112) → string_ids → type_ids → class_defs
              → [data 段: string_data_item… , class_data_item…, map_list]
    """
    pool: list[str] = []

    def sid(s: str) -> int:
        if s not in pool:
            pool.append(s)
        return pool.index(s)

    tids: list[int] = []

    def tid(desc: str) -> int:
        i = sid(desc)
        if i not in tids:
            tids.append(i)
        return tids.index(i)

    for cn, sn in classes:
        tid(cn)
        tid(sn)

    # ── data 段 ①：string_data_item（MUTF-8，全 ASCII）──
    str_data = bytearray()
    str_rel: list[int] = []
    for s in pool:
        str_rel.append(len(str_data))
        enc = s.encode("utf-8")
        str_data += _uleb(len(s)) + enc + b"\x00"
    str_data = _pad4(bytes(str_data))

    # ── data 段 ②：所有类共享一个**定长** class_data_item（4 字节全零）
    #:   = static_fields=0, instance_fields=0, direct_methods=0,
    #:     virtual_methods=0
    #:
    #: ⛔ 定长是关键：若带 code_off，其 uleb 编码长度随地址变化，
    #:    「按占位长度算步长」就会错位（坑 ⑤）。
    cd = _uleb(0) + _uleb(0) + _uleb(0) + _uleb(0)
    cd_len = len(cd)
    data_body = str_data + cd

    # ── 偏移（只依赖计数）──
    n_str, n_types = len(pool), len(tids)
    n_classes = len(classes)
    str_ids_off = HEADER_SIZE
    type_ids_off = str_ids_off + 4 * n_str
    class_defs_off = type_ids_off + 4 * n_types
    ids_len = 4 * n_str + 4 * n_types + 32 * n_classes
    data_off = (HEADER_SIZE + ids_len + 3) & ~3
    pad_len = data_off - (HEADER_SIZE + ids_len)
    cd_abs = data_off + len(str_data)
    map_off = data_off + len(data_body)

    str_ids = b"".join(struct.pack("<I", data_off + o) for o in str_rel)
    type_ids = b"".join(struct.pack("<I", i) for i in tids)
    class_defs = b"".join(
        struct.pack("<IIIIIIII", tid(cn), ACC_PUBLIC, tid(sn),
                    0, 0, 0, cd_abs, 0)
        for cn, sn in classes)

    ids = str_ids + type_ids + class_defs
    map_list = _map_list(map_off, str_ids_off, type_ids_off, class_defs_off,
                         n_str, n_types, n_classes, data_off, len(data_body))
    body = ids + (b"\x00" * pad_len) + data_body + map_list
    file_size = HEADER_SIZE + len(body)
    data_size = len(data_body) + len(map_list)

    header = DEX_MAGIC
    header += b"\x00" * 4                       #: checksum 稍后填
    header += b"\x00" * 20                      #: signature 稍后填
    header += struct.pack("<II", file_size, HEADER_SIZE)
    header += struct.pack("<I", 0x12345678)     #: endian_tag
    header += struct.pack("<II", 0, 0)           #: link_size / link_off
    header += struct.pack("<I", map_off)
    header += struct.pack("<II", n_str, str_ids_off)
    header += struct.pack("<II", n_types, type_ids_off)
    #: ⛔ 这里是 **6 个** u4（proto/field/method 的 size+off），不是 4 个。
    header += struct.pack("<IIIIII", 0, class_defs_off, 0, class_defs_off,
                          0, class_defs_off)
    header += struct.pack("<II", n_classes, class_defs_off)
    header += struct.pack("<II", data_size, data_off)

    #: ⛔⛔ **顺序有依赖**：DEX 的 signature 覆盖 [32:]，
    #:    而 checksum 覆盖 [12:] —— 也就是**包含 signature 本身**。
    #:    先算 checksum 再填 signature ⇒ checksum 恒错（实测 adler ok=False），
    #:    严格解析器直接把整份 dex 判为损坏并静默丢弃 ⇒ class_count=0。
    blob = header + body
    sig = hashlib.sha1(blob[32:]).digest()
    blob = blob[:12] + sig + blob[32:]
    ck = zlib.adler32(blob[12:]) & 0xFFFFFFFF
    return blob[:8] + struct.pack("<I", ck) + blob[12:]


def build_apk(path: str, package: str = "com.example.selftest",
              activity: str = ".MainActivity", n_classes: int = 3
              ) -> list[tuple[str, str]]:
    """写出一个自建 APK，返回 [(类描述符, 父类描述符)]（即先验真值）。"""
    classes = [(f"L{package.replace('.', '/')}/C{i};", "Ljava/lang/Object;")
               for i in range(n_classes)]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", build_axml(package, activity))
        z.writestr("classes.dex", build_dex(classes))
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n\n")
    return classes


if __name__ == "__main__":
    import sys
    cs = build_apk(sys.argv[1])
    print(f"built {sys.argv[1]} with {len(cs)} classes: {cs}")
