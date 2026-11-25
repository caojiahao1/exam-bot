/* =========================================================================
 * exam-bot 页面注入脚本（无需油猴）
 * 由本地控制台通过 CDP 注入；通过 window.__EB 暴露控制接口
 * 取答案走 http://127.0.0.1:8778/api/query（严格题库）
 * ========================================================================= */
(function () {
  'use strict';
  if (window.__EB && window.__EB.ver) return;          // 防重复注入

  const API = 'http://127.0.0.1:8778/api/query';

  const CFG = {
    human: true,           // 拟人停顿
    humanMin: 3000,
    humanMax: 10000,
    autoNext: true,
    autoSave: true,
    guardSubmit: true,
    paused: false,
    running: false,
    debug: false,
  };

  const CTL = 'input,textarea,select,[contenteditable="true"],[role="radio"],[role="checkbox"]';
  const CHOICE = 'input[type=radio],input[type=checkbox],[role="radio"],[role="checkbox"]';
  const SELF_SEL = '#eb-panel,#eb-tip,[data-eb]';
  const BTN_SEL = 'button,input[type=button],input[type=submit],div,a,span,li,[class*=btn],[class*=Btn],[class*=button]';

  const NEXT_RX = /^(下一题|下一頁|下一页|下一步|继续|next)\s*$/i;
  const SAVE_RX = /^(保存|保存答案|暂存|保存并下一题|save)$/i;
  const BACK_RX = /^(上一题|上一頁|上一页|上一步|返回|back|prev|previous)$/i;
  const SUBMIT_RX = /交卷|提交试卷|提交答题|确认提交|submit\s*paper|submit\s*exam/i;

  const LOG = [];
  const RECORDS = [];
  let state = { phase: 'idle', total: 0, done: 0, last: '' };

  /* ---------------- 工具 ---------------- */
  const log = (m) => {
    LOG.push({ t: Date.now(), m: String(m) });
    if (LOG.length > 300) LOG.shift();
    if (CFG.debug) console.log('[exam-bot]', m);
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const rnd = (a, b) => a + Math.random() * (b - a);
  const stripSpace = s => String(s || '').replace(/[\s\u3000]+/g, '');
  const rxAny = (rx, t) => !!t && (rx.test(t) || rx.test(stripSpace(t)));
  const isSelf = el => { try { return !!(el && el.closest && el.closest(SELF_SEL)); } catch (e) { return false; } };

  function isDisplayed(el) {
    if (!el || !el.getBoundingClientRect) return false;
    try {
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return false;
      const st = getComputedStyle(el);
      return st.display !== 'none' && st.visibility !== 'hidden';
    } catch (e) { return false; }
  }
  function isVisible(el) {
    if (!isDisplayed(el)) return false;
    try { return !el.disabled && !/disabled/.test(String(el.className || '')); } catch (e) { return false; }
  }
  function labelOf(e) {
    if (!e) return '';
    return (e.innerText || '').trim() || (e.value || '').trim()
      || ((e.getAttribute && e.getAttribute('aria-label')) || '').trim()
      || ((e.getAttribute && e.getAttribute('title')) || '').trim();
  }

  function textOf(el) {
    if (!el) return '';
    const tips = el.querySelectorAll('.eb-tip');
    tips.forEach(t => { t.dataset.od = t.style.display; t.style.display = 'none'; });
    let t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    for (const f of el.querySelectorAll('iframe,frame')) {
      try { const b = f.contentDocument && f.contentDocument.body; if (b) t += ' ' + (b.innerText || '').replace(/\s+/g, ' ').trim(); } catch (e) { }
    }
    tips.forEach(x => { x.style.display = x.dataset.od || ''; });
    return t.replace(/\s+/g, ' ').trim();
  }

  function allDocs() {
    const out = [];
    (function walk(doc) { out.push(doc); for (const f of doc.querySelectorAll('iframe,frame')) { try { if (f.contentDocument) walk(f.contentDocument); } catch (e) { } } })(document);
    return out;
  }
  function qa(root, sel) {
    if (!root) return [];
    const own = root.ownerDocument || root;
    const out = (root.querySelectorAll ? Array.from(root.querySelectorAll(sel)) : []).filter(n => !isSelf(n));
    const seen = new Set([own]);
    (function walk(c) {
      for (const f of c.querySelectorAll('iframe,frame')) {
        try {
          const d = f.contentDocument;
          if (d && !seen.has(d)) { seen.add(d); out.push(...Array.from(d.querySelectorAll(sel)).filter(n => !isSelf(n))); walk(d); }
        } catch (e) { }
      }
    })(root);
    return out;
  }
  const ctlCount = el => qa(el, CTL).length;

  /* ---------------- 找题目块 ---------------- */
  const SELECTORS = ['.question', '.exam-question', '[class*="question"]', '[class*="Question"]',
    '[class*="subject"]', '[class*="Subject"]', '[class*="que-item"]', '[class*="q-item"]',
    '[class*="topic"]', '[data-questionid]', '[data-qid]', '.item', 'li.item', 'fieldset'];
  const BLOCK_STOP = ['.question-type-item', '.question', '.exam-question', '[class*="question-item"]',
    '[class*="questionItem"]', '[data-questionid]', '[class*="que-item"]'];

  function ascend(start, doc) {
    if (!start || isSelf(start)) return null;
    const body = doc.body || doc.documentElement;
    const isQL = el => el && el !== body && (qa(el, CHOICE).length >= 2 || qa(el, 'textarea,[contenteditable="true"]').length >= 1 || qa(el, 'input[type=text]').length >= 1) && textOf(el).length >= 8;
    let el = start, g = 0;
    while (el && el !== body && el !== doc.documentElement && g++ < 14) {
      if (isQL(el)) break;
      el = el.parentElement;
    }
    if (!el || el === body || el === doc.documentElement) return null;
    const stopSel = BLOCK_STOP.join(',');
    let best = el, p = el.parentElement, hop = 0;
    while (p && p !== body && hop++ < 12) {
      if (ctlCount(p) !== ctlCount(best)) break;
      if (stopSel && p.matches && p.matches(stopSel)) { best = p; break; }
      if (textOf(p).length > 900) break;
      best = p; p = p.parentElement;
    }
    return best;
  }

  function findBlocks() {
    const blocks = [];
    const push = (el, sel) => {
      if (!el || !el.querySelector || isSelf(el)) return;
      if (!ctlCount(el) && textOf(el).length < 15) return;
      if (blocks.some(b => b.el === el)) return;
      blocks.push({ el, sel });
    };
    for (const doc of allDocs()) {
      for (const sel of SELECTORS) {
        let els = [];
        try { els = doc.querySelectorAll(sel); } catch (e) { continue; }
        for (const el of els) push(el, sel);
      }
      for (const c of doc.querySelectorAll(CTL)) {
        if (isSelf(c)) continue;
        const el = ascend(c.parentElement, doc);
        if (el) push(el, 'auto');
      }
    }
    let out = blocks.filter(b => ctlCount(b.el) > 0 && !isSelf(b.el));
    // 排除页面内容区 / 登录表单
    out = out.filter(b => {
      const el = b.el;
      if (el.querySelector('input[type=password]')) return false;
      if (el.querySelector(':scope > h1, :scope > h2, :scope > h3')) {
        if (qa(el, CHOICE).length < 4) return false;
      }
      const txt = textOf(el);
      const nChoice = qa(el, CHOICE).length;
      const nText = qa(el, 'textarea,[contenteditable="true"],input[type=text]').length;
      const optLetters = (txt.match(/(?:^|\s)[A-H][\.．、\)）]\s*\S/g) || []).length;
      const asks = /[（(]\s*[)）]|____|？|\?/.test(txt);
      if (nChoice >= 2 && optLetters >= 2) return txt.length <= 1500;
      if (nChoice >= 2 && optLetters < 2 && txt.length <= 200 && asks) return true;
      if (nText >= 1) return txt.length <= 2000;
      return false;
    });
    out = out.filter(b => !out.some(o => o !== b && o.el.contains(b.el)));
    out.sort((a, b) => (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1);
    return out;
  }

  /* ---------------- 解析题目 ---------------- */
  function kindOf(n) {
    if (n.getAttribute && n.getAttribute('role') === 'radio') return 'radio';
    if (n.getAttribute && n.getAttribute('role') === 'checkbox') return 'checkbox';
    const t = (n.type || '').toLowerCase();
    if (t === 'radio') return 'radio';
    if (t === 'checkbox') return 'checkbox';
    if (n.tagName === 'TEXTAREA') return 'textarea';
    if (n.isContentEditable || (n.getAttribute && n.getAttribute('contenteditable') === 'true')) return 'ce';
    if (t === 'text' || !n.type) return 'text';
    return 'other';
  }
  function isChecked(n) {
    if (n.getAttribute && (n.getAttribute('role') === 'radio' || n.getAttribute('role') === 'checkbox'))
      return n.getAttribute('aria-checked') === 'true';
    return !!n.checked;
  }
  function detectType(el) {
    const k = qa(el, CTL).map(kindOf);
    const has = x => k.includes(x);
    if (has('radio')) return 'single';
    if (has('checkbox')) return 'multi';
    if (has('textarea') || has('ce')) return 'essay';
    if (has('text')) return 'fill';
    return 'unknown';
  }
  function optionContainer(node) {
    if (textOf(node) && qa(node, CHOICE).length === 0) return node;
    let cur = node.parentElement, g = 0;
    while (cur && g++ < 5) {
      const n = qa(cur, CHOICE).length;
      if (n === 1 && textOf(cur).length >= 1) return cur;
      if (n > 1) return null;
      cur = cur.parentElement;
    }
    return null;
  }
  function grabOptions(el, type) {
    if (type !== 'single' && type !== 'multi') return [];
    const nodes = qa(el, CHOICE), groups = [], seen = new Set();
    for (const node of nodes) {
      const container = optionContainer(node) || node.parentElement || node;
      if (seen.has(container)) continue;
      seen.add(container);
      const attr = node.getAttribute && (node.dataset.val || node.getAttribute('data-value'));
      const letter = (attr || String.fromCharCode(65 + groups.length)).toUpperCase();
      let text = textOf(container) || textOf(node) || String(node.value || '').slice(0, 40);
      const body = text.replace(/^[（(【\[]?\s*[A-Za-z]\s*[）)】\]\.、:：\s]+/, '').trim();
      groups.push({ letter, text, body, node, container });
    }
    return groups;
  }

  function norm2(s) {
    return String(s || '').replace(/[\s\u3000（）()\[\]【】「」『』"'“”‘’，。、；：！？,.;:!?\-—_·]/g, '').toLowerCase();
  }
  function bg(s) {
    const m = {};
    for (let i = 0; i < s.length - 1; i++) { const g = s.slice(i, i + 2); m[g] = (m[g] || 0) + 1; }
    if (s.length === 1) m[s] = 1;
    return m;
  }
  function sim(a, b) {
    const A = bg(norm2(a)), B = bg(norm2(b));
    if (!A || !B) return 0;
    let inter = 0;
    for (const [k, v] of Object.entries(A)) if (B[k]) inter += Math.min(v, B[k]);
    const union = Object.values(A).reduce((x, y) => x + y, 0) + Object.values(B).reduce((x, y) => x + y, 0) - inter;
    return union ? inter / union : 0;
  }

  function parseQuestion(block) {
    const el = block.el, type = detectType(el);
    const options = grabOptions(el, type);
    const blanks = qa(el, CTL).filter(n => ['text', 'textarea', 'ce'].includes(kindOf(n)));
    let stem = textOf(el);
    for (const o of options) if (o.text && o.text.length > 1) stem = stem.split(o.text).join(' ');
    stem = stem.replace(/\s+/g, ' ')
      .replace(/\d{1,3}\s*\/\s*\d{1,3}/g, ' ')
      .replace(/[（(]\s*(?:共\s*)?\d+(?:\.\d+)?\s*分\s*[)）]/g, ' ')
      .replace(/待检查|已检查|未作答|答题卡|本题得分|标准答案|考生[:：]/g, ' ')
      .replace(/(单选题|多选题|判断题|填空题|简答题|单选|多选|判断)/g, ' ')
      .trim().slice(0, 900);
    let clean = stem.replace(/^[（(]?\d{1,3}[）)\.、:：]\s*/, '').trim();
    if (clean.length < 6) {
      try {
        const lines = textOf(el).split(/(?:\s{2,}|[\n\r]+)/).map(s => s.trim()).filter(Boolean);
        const noOpt = lines.filter(l => !/^[A-H][\.．、\)）:：]/.test(l));
        const pool = (noOpt.length ? noOpt : lines).slice().sort((a, b) => b.length - a.length);
        clean = (pool[0] || stem).replace(/^[（(]?\d{1,3}[）)\.、:：]\s*/, '').trim();
      } catch (e) { }
    }
    return {
      type, stem: clean, options, blanks,
      uid: (norm2(clean) + '#' + options.length).slice(0, 120),
    };
  }

  /* ---------------- 取值 ---------------- */
  async function ask(q) {
    try {
      const r = await fetch(API, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stem: q.stem, type: q.type, options: q.options.map(o => o.body || o.text) }),
      });
      return await r.json();
    } catch (e) {
      return { answer: '', answer_texts: [], score: 0, source: 'ERR', why: String(e).slice(0, 80) };
    }
  }

  /* ---------------- 填写 ---------------- */
  function setNativeValue(el, v) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : el instanceof HTMLInputElement ? HTMLInputElement.prototype : null;
    if (!proto) { el.textContent = v; el.dispatchEvent(new Event('input', { bubbles: true })); return; }
    const d = Object.getOwnPropertyDescriptor(proto, 'value');
    if (d && d.set) d.set.call(el, v); else el.value = v;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
  function isSubmitEl(el) { return el ? rxAny(SUBMIT_RX, labelOf(el)) : false; }
  function safeClick(el, what) {
    if (!el) return false;
    if (CFG.guardSubmit && isSubmitEl(el)) { log('⛔ 拦截交卷点击：' + (what || '')); return false; }
    try { el.click(); return true; } catch (e) { return false; }
  }
  function clickOption(o) {
    const node = o.node || o.container;
    if (!node) return false;
    try { node.click(); } catch (e) { }
    if (!isChecked(node) && o.container && o.container !== node) {
      ['mousedown', 'mouseup', 'click'].forEach(t => {
        try { (o.container).dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })); } catch (e) { }
      });
    }
    if (!isChecked(node) && node.tagName === 'INPUT') {
      node.checked = true;
      node.dispatchEvent(new Event('change', { bubbles: true }));
    }
    return isChecked(node);
  }

  function fillAnswer(q, res) {
    const ans = String(res.answer || '').trim();
    if (!ans) return { filled: 0, note: '题库无此题' };
    let filled = 0, note = '';

    if (q.type === 'single' || q.type === 'multi') {
      const picked = [], seen = new Set();
      const add = o => { if (o && !seen.has(o)) { seen.add(o); picked.push(o); } };
      const nBank = Number(res.bank_noptions || 0), nExam = q.options.length;
      const letterSafe = !nBank || !nExam || nBank === nExam;

      // ① 选项原文匹配（最可靠）
      for (const t of (res.answer_texts || [])) {
        let best = null, bs = 0;
        for (const o of q.options) {
          const s = Math.max(sim(t, o.body || o.text), sim(t, o.text));
          if (s > bs) { bs = s; best = o; }
        }
        if (best && bs >= 0.45) add(best);
      }
      // ② 字母（选项数一致时才用）
      if (!picked.length && letterSafe) {
        const letters = (ans.match(/[A-H]/gi) || []).map(c => c.toUpperCase());
        if (letters.length && letters.every(L => q.options.some(o => o.letter === L))) {
          for (const L of letters) add(q.options.find(o => o.letter === L));
        }
      }
      if (!picked.length && !letterSafe) note = '选项数与题库不一致，跳过字母答案';

      if (q.type === 'single' && picked.length > 1) picked.length = 1;
      for (const o of picked) if (clickOption(o)) filled++;
      if (!filled) note = note || ('选项未命中: ' + ans.slice(0, 20));

    } else if (q.type === 'fill') {
      const vals = String(ans).split(/[|｜\n]+/).map(s => s.trim()).filter(Boolean);
      const strip = s => s.replace(/^[①②③④⑤⑥⑦⑧⑨⑩]\s*[、\.]?\s*/, '').replace(/^\d{1,3}\s*[、\.\)）:：]\s*/, '');
      if (q.blanks.length === 1) { setNativeValue(q.blanks[0], vals.join(' ')); filled = q.blanks[0].value ? 1 : 0; }
      else vals.forEach((v, i) => { if (q.blanks[i]) { setNativeValue(q.blanks[i], strip(v)); if (q.blanks[i].value) filled++; } });

    } else if (q.type === 'essay') {
      const el = q.blanks[0];
      if (el) { setNativeValue(el, ans); filled = ((el.value || el.innerText || '').trim().length > 0) ? 1 : 0; }
    }
    return { filled, note };
  }

  /* ---------------- 翻页 ---------------- */
  function nextCands() {
    return [...document.querySelectorAll(BTN_SEL)]
      .filter(e => isVisible(e) && !isSelf(e))
      .filter(e => rxAny(NEXT_RX, labelOf(e)) && !isSubmitEl(e))
      .sort((a, b) => (a.tagName === 'BUTTON' ? 0 : a.tagName === 'A' ? 1 : 2) - (b.tagName === 'BUTTON' ? 0 : b.tagName === 'A' ? 1 : 2));
  }
  function saveBtn() {
    if (!CFG.autoSave) return null;
    let learned = '';
    try { learned = localStorage.getItem('examBotSaveText') || ''; } catch (e) { }
    if (learned && (rxAny(BACK_RX, learned) || rxAny(NEXT_RX, learned))) learned = '';
    const cands = [...document.querySelectorAll(BTN_SEL)]
      .filter(e => isVisible(e) && !isSelf(e))
      .map(e => ({ e, t: labelOf(e) }))
      .filter(x => x.t && x.t.length <= 12)
      .filter(x => !isSubmitEl(x.e) && !rxAny(NEXT_RX, x.t) && !rxAny(BACK_RX, x.t))
      .filter(x => !/^[A-H][\.．、\)）]/.test(x.t) && !/^[0-9]{1,3}$/.test(x.t));
    if (learned) { const h = cands.find(x => x.t === learned); if (h) return h.e; }
    const h = cands.find(x => rxAny(SAVE_RX, x.t));
    return h ? h.e : null;
  }

  const bodySig = () => {
    try {
      const b = findBlocks()[0];
      if (!b) return '';
      const t = document.body.innerText || '';
      const prog = (t.match(/(\d{1,3})\s*\/\s*(\d{1,3})/) || []).slice(1).join('/');
      return prog + '¦' + parseQuestion(b).stem.slice(0, 48);
    } catch (e) { return ''; }
  };

  async function pauseGate() {
    if (!CFG.paused) return;
    const t0 = Date.now();
    while (CFG.paused) {
      await sleep(400);
      if (Date.now() - t0 > 1800000) { CFG.paused = false; break; }
    }
  }
  async function paced() {
    if (!CFG.human) { await sleep(rnd(80, 200)); return; }
    await sleep(rnd(CFG.humanMin, CFG.humanMax));
  }

  /* ---------------- 主流程 ---------------- */
  async function run() {
    if (CFG.running) return { ok: false, why: '已在运行' };
    CFG.running = true; CFG.paused = false;
    state = { phase: 'running', total: 0, done: 0, last: '' };
    const seen = new Set(RECORDS.map(r => r.uid));
    let guard = 0;

    try {
      while (CFG.running && guard++ < 500) {
        await pauseGate();
        if (!CFG.running) break;

        let blocks = findBlocks();
        for (let i = 0; i < 24 && !blocks.length; i++) { await sleep(250); blocks = findBlocks(); }
        if (!blocks.length) { log('没找到题目块，停止'); break; }

        const before = bodySig();
        let acted = 0;

        for (const b of blocks) {
          if (!CFG.running) break;
          const q = parseQuestion(b);
          if (!q.stem || q.stem.length < 4) continue;
          if (seen.has(q.uid)) continue;

          const res = await ask(q);
          await pauseGate();
          const fr = fillAnswer(q, res);
          seen.add(q.uid); acted++;
          RECORDS.push({ uid: q.uid, type: q.type, stem: q.stem.slice(0, 120),
                         answer: res.answer, source: res.source, filled: fr.filled, note: fr.note });
          state.done = RECORDS.length;
          state.last = (res.answer || '(空)') + ' ' + (res.source || '');
          log(`[${q.type}] ${q.stem.slice(0, 22)}… → ${res.answer || '(空)'} ${res.source} filled=${fr.filled}`);
          await paced();
        }

        if (!CFG.autoNext) break;
        if (!acted) {
          // 这一屏都答过了，尝试翻页
        }
        const sb = saveBtn();
        if (sb) { safeClick(sb, '保存'); await sleep(rnd(200, 500)); }

        const nb = nextCands()[0];
        if (!nb) {
          log(acted ? '✅ 已是最后一题（没有"下一题"了），请手动交卷' : '没有"下一题"按钮，结束');
          break;
        }
        if (!safeClick(nb, '下一题')) break;

        let moved = false;
        for (let i = 0; i < 30; i++) { await sleep(200); if (bodySig() !== before) { moved = true; break; } }
        if (!moved) { log('翻页未生效，结束'); break; }
      }
    } catch (e) {
      log('异常: ' + (e && e.message));
    } finally {
      CFG.running = false;
      state.phase = 'idle';
    }
    return { ok: true, done: RECORDS.length };
  }

  function stop() { CFG.running = false; CFG.paused = false; return true; }
  function pause(f) { CFG.paused = (typeof f === 'boolean') ? f : !CFG.paused; return CFG.paused; }

  async function answerCurrent() {
    const blocks = findBlocks();
    const out = [];
    for (const b of blocks) {
      const q = parseQuestion(b);
      if (!q.stem) continue;
      const res = await ask(q);
      const fr = fillAnswer(q, res);
      out.push({ stem: q.stem.slice(0, 60), answer: res.answer, source: res.source, filled: fr.filled });
    }
    return out;
  }

  /* ---------------- 对外接口 ---------------- */
  window.__EB = {
    ver: '2.0.0',
    CFG,
    findBlocks: () => findBlocks().length,
    diagnose: () => {
      const bs = findBlocks();
      const q = bs[0] ? parseQuestion(bs[0]) : null;
      return {
        url: location.href.slice(0, 120),
        blocks: bs.length,
        first: q ? { type: q.type, stem: q.stem.slice(0, 60), opts: q.options.length } : null,
        next: nextCands().length,
        nextLabel: nextCands()[0] ? labelOf(nextCands()[0]) : '',
        running: CFG.running,
        paused: CFG.paused,
        done: RECORDS.length,
      };
    },
    run, stop, pause, answerCurrent,
    status: () => ({ ...state, running: CFG.running, paused: CFG.paused, done: RECORDS.length, log: LOG.slice(-40) }),
    records: () => RECORDS.slice(),
    reset: () => { RECORDS.length = 0; LOG.length = 0; state = { phase: 'idle', total: 0, done: 0, last: '' }; return true; },
  };
  log('exam-bot 注入完成 v2.0.0');
})();
