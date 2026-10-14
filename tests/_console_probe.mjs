#!/usr/bin/env node
/* 控制台渲染探针（被 tests/contract_console_render_test.py 调用）。

   ⛔ 为什么要这个东西：2026-10-14 实测，控制台的 9 个数据面板**从未渲染过**
      —— `const evs` 声明在 stats 面板**之后**而被它的 IIFE 引用 ⇒ TDZ ⇒
      `load()` 抛错 ⇒ 因为是 async，抛错变成 **unhandled rejection**，
      浏览器控制台之外**毫无迹象**：卡片标题是静态 HTML、占位符是 "…"，
      页面看起来完全正常。肉眼看截图、看 DOM 结构都发现不了。

   ⛔ 探针必须与被测对象**同源**：这里跑的是 console/index.html 里**原样**
      抽出的脚本 + 合成 API 数据，不是另写一份"简化版渲染逻辑"。
      另写一份 ⇒ 页面改了而探针没改 ⇒ 闸恒绿（假仪表）。

   用法：
       node tests/_console_probe.mjs <script.mjs> <fixture.json>
   输出（stdout，单行 JSON）：
       {"exceptions": [...], "written": {...}, "text": {...}}
*/
import fs from "node:fs";

const [scriptPath, fixturePath] = process.argv.slice(2);
const FIX = JSON.parse(fs.readFileSync(fixturePath, "utf8"));

const written = {};        // id -> 写入内容的长度
const texts = {};          // id -> 写入内容前 300 字符（抽样断言用）
const listeners = new Map();
const treeHandlers = [];

function mk(id) {
  const kids = new Map();
  const e = {
    id, _html: "", _text: "", disabled: false, value: "{}", className: "",
    style: {}, dataset: {},
    classList: { toggle: () => {}, add: () => {}, remove: () => {}, contains: () => false },
    // ⛔ **最后一次写入**语义，不是累加：面板会被重绘（比如点文件后文件树
    //    重画一次），累加会把"14 条"读成"28 条"。用户在屏幕上看到的永远是
    //    最后一次写入的结果 —— 探针就该记那个。
    set innerHTML(v) {
      this._html = String(v);
      written[id] = this._html.length;
      texts[id] = this._html.slice(0, 4000);
    },
    get innerHTML() { return this._html; },
    set textContent(v) {
      this._text = String(v);
      written[id] = this._text.length;
      texts[id] = this._text.slice(0, 4000);
    },
    get textContent() { return this._text; },
    querySelector(sel) {                 // ⛔ 必须缓存：真实 DOM 里同一个
      const key = id + " " + sel;        // tbody 每次取到的是同一个对象
      if (!kids.has(key)) kids.set(key, mk(key));
      return kids.get(key);
    },
    querySelectorAll(sel) {
      if (sel === ".fn") {               // 从刚写入的 HTML 里还原节点
        const out = [];
        const re = /data-p="([^"]*)" data-dir="(\d)"/g;
        let m;
        while ((m = re.exec(this._html))) {
          // ⛔ 必须在循环内固化：闭包捕获 `m` 的话循环结束时它是 null
          const pp = m[1], dd = m[2];
          out.push({
            dataset: { p: pp, dir: dd },
            addEventListener: (t, fn) => {
              if (t === "click") treeHandlers.push({ p: pp, dir: dd, fn });
            },
          });
        }
        return out;
      }
      if (sel === "button") {            // 详略三档 / 检视器页签
        return ["compact", "standard", "verbose"].map((lv) => {
          const b = mk("seg-" + lv); b.dataset.level = lv; return b;
        });
      }
      return [];
    },
    addEventListener: (t, fn) => { listeners.set(id + ":" + t, fn); },
    closest: () => null,
  };
  return e;
}

const cache = new Map();
const get = (id) => { if (!cache.has(id)) cache.set(id, mk(id)); return cache.get(id); };

globalThis.document = {
  getElementById: get,
  querySelector: () => null,
  querySelectorAll: (sel) => (sel === "button"
    ? ["compact", "standard", "verbose"].map((lv) => {
        const b = mk("gseg-" + lv); b.dataset.level = lv; return b;
      })
    : []),
  body: { dataset: { detail: "standard" } },
};
globalThis.window = { addEventListener: () => {} };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.location = { origin: "http://127.0.0.1:8611" };
globalThis.performance = { now: () => 0 };
Object.defineProperty(globalThis, "navigator",
  { value: { clipboard: { writeText: async () => {} } }, configurable: true });
globalThis.setInterval = () => 0;

const exceptions = [];
process.on("unhandledRejection", (e) => exceptions.push(String((e && e.message) || e)));

globalThis.fetch = async (url) => {
  const u = String(url);
  if (u.includes("/api/capabilities")) {
    return { status: 200, json: async () => ({ capabilities: [], n: 0, n_total: 0 }) };
  }
  if (u.includes("/api/files")) return { status: 200, json: async () => FIX.files };
  if (u.includes("/api/file")) return { status: 200, json: async () => FIX.file };
  return { status: 200, json: async () => FIX.status };
};

try { await import(scriptPath); } catch (e) { exceptions.push(String((e && e.message) || e)); }
await new Promise((r) => setTimeout(r, 60));

// ── 模拟点击：不点就等于没走到（预览/详情/差异三条渲染路径）────────
try {
  const fh = treeHandlers.find((h) => h.dir === "0");
  if (fh) await fh.fn();
} catch (e) { exceptions.push(String((e && e.message) || e)); }

try {
  const seq = (FIX.status.events || []).filter((x) => x.kind === "gate.result").slice(-1)[0]?.seq;
  const fn = listeners.get("events tbody:click");
  if (fn && seq != null) {
    await fn({ target: { closest: () => ({ dataset: { seq: String(seq) } }) } });
  }
} catch (e) { exceptions.push(String((e && e.message) || e)); }

process.stdout.write(JSON.stringify({ exceptions, written, texts }));
