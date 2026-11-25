# -*- coding: utf-8 -*-
"""
题库查询引擎（零依赖）
严格模式：只认题库，命中不了就返回空，不猜不编。
"""
import os, re, sqlite3, threading

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "bank.sqlite3")

_PUNCT = re.compile(r"[\s\u3000（）()\[\]【】「」『』\"'\u201c\u201d\u2018\u2019，。、；：！？,.;:!?\-—_\u00b7]")
STRICT_MIN = 0.85

_cache = {"mtime": 0.0, "rows": []}
_lock = threading.Lock()


def norm(s):
    return _PUNCT.sub("", (s or "")).lower()


def _bigrams(s):
    m = {}
    for i in range(len(s) - 1):
        g = s[i:i + 2]
        m[g] = m.get(g, 0) + 1
    if len(s) == 1:
        m[s] = 1
    return m


def sim(a, b):
    A, B = _bigrams(norm(a)), _bigrams(norm(b))
    if not A or not B:
        return 0.0
    inter = sum(min(v, B.get(k, 0)) for k, v in A.items())
    union = sum(A.values()) + sum(B.values()) - inter
    return inter / union if union else 0.0


def _split_options(raw):
    raw = raw or ""
    if "\n" in raw:
        parts = raw.split("\n")
    else:
        parts = re.split(r"[；;|｜]", raw)
    return [p.strip() for p in parts if p.strip()]


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS qa (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT DEFAULT '',
        stem TEXT NOT NULL,
        nkey TEXT NOT NULL UNIQUE,
        answer TEXT NOT NULL,
        answer_letters TEXT DEFAULT '',
        options TEXT DEFAULT '',
        source TEXT DEFAULT '',
        weight REAL DEFAULT 1.0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    con.commit()
    con.close()


def load_rows():
    if not os.path.exists(DB_PATH):
        return []
    mt = os.path.getmtime(DB_PATH)
    with _lock:
        if _cache["rows"] and _cache["mtime"] == mt:
            return _cache["rows"]
        con = sqlite3.connect(DB_PATH)
        try:
            data = con.execute(
                "SELECT id,type,stem,answer,answer_letters,options,weight FROM qa ORDER BY weight DESC"
            ).fetchall()
        finally:
            con.close()
        rows = []
        for r in data:
            rows.append({
                "id": r[0], "type": r[1], "stem": r[2], "answer": r[3],
                "letters": r[4], "options": r[5], "weight": r[6],
                "nkey": norm(r[2]),
                "opts": _split_options(r[5]),
            })
        _cache["rows"], _cache["mtime"] = rows, mt
        return rows


def rows_count():
    return len(load_rows())


def resolve_texts(row, answer):
    ans = (answer or "").strip()
    opts = row.get("opts") or []
    # 纯字母码才当选项序号
    if ans and re.fullmatch(r"[A-Za-z](?:[\s,，、;；/]*[A-Za-z])*", ans) and opts:
        out = []
        for ch in re.findall(r"[A-Za-z]", ans.upper()):
            i = ord(ch) - 65
            if 0 <= i < len(opts):
                out.append(opts[i])
        if out:
            return out
    if ans:
        parts = [x.strip() for x in re.split(r"[|｜\n]+", ans) if x.strip()]
        return parts or [ans]
    return []


def query(stem, qtype="", options=None, k=4):
    """返回 {answer, answer_texts, score, source, bank_noptions}"""
    stem = (stem or "").strip()
    if len(stem) < 4:
        return {"answer": "", "answer_texts": [], "score": 0.0, "source": "none", "bank_noptions": 0}

    rows = load_rows()
    key = norm(stem)
    exact = [r for r in rows if r["nkey"] == key]
    cands = exact
    if not cands:
        scored = [(sim(stem, r["stem"]), r) for r in rows]
        scored = [x for x in scored if x[0] >= 0.42]
        scored.sort(key=lambda x: -x[0])
        cands = [r for _, r in scored[:k]]
        best_scores = {r["id"]: s for s, r in scored[:k]}
    else:
        best_scores = {r["id"]: 1.0 for r in exact}

    if not cands:
        return {"answer": "", "answer_texts": [], "score": 0.0, "source": "none", "bank_noptions": 0}

    best = cands[0]
    score = best_scores.get(best["id"], 0.0)
    if score < STRICT_MIN:
        return {"answer": "", "answer_texts": [], "score": round(score, 4),
                "source": "none", "bank_noptions": 0}

    texts = resolve_texts(best, best["answer"])
    return {
        "answer": best["answer"],
        "answer_texts": texts,
        "score": round(score, 4),
        "source": "kb-exact" if score >= 0.999 else "kb-sim",
        "bank_noptions": len(best.get("opts") or []),
        "matched_stem": best["stem"][:80],
    }


def import_rows(items, source="import"):
    """
    items: [{type, stem, answer, options}]
    返回 (新增, 更新)
    """
    init_db()
    con = sqlite3.connect(DB_PATH)
    new = upd = 0
    for it in items:
        stem = (it.get("stem") or "").strip()
        if len(stem) < 4:
            continue
        ans = (it.get("answer") or "").strip()
        opts = it.get("options") or ""
        if isinstance(opts, (list, tuple)):
            opts = "；".join(opts)
        key = norm(stem)
        qtype = it.get("type") or "未分类"
        letters = "" if qtype == "判断题" else (ans or "")
        dup = con.execute("SELECT id FROM qa WHERE nkey=?", (key,)).fetchone()
        if dup:
            con.execute("UPDATE qa SET type=?, answer=?, answer_letters=?, options=? WHERE id=?",
                        (qtype, ans, letters, opts, dup[0]))
            upd += 1
        else:
            con.execute("""INSERT OR IGNORE INTO qa(type,stem,nkey,answer,answer_letters,options,source,weight)
                           VALUES(?,?,?,?,?,?,?,1.0)""",
                        (qtype, stem, key, ans, letters, opts, source))
            new += 1
    con.commit()
    con.close()
    with _lock:
        _cache["mtime"] = 0.0
    return new, upd


def export_rows():
    rows = load_rows()
    return [{"id": r["id"], "type": r["type"], "stem": r["stem"],
             "answer": r["answer"], "options": r["opts"]} for r in rows]


if __name__ == "__main__":
    init_db()
    print("题库文件:", DB_PATH)
    print("题目数:", rows_count())
    r = query("有限空间作业必须先通风、再检测、后作业。", "判断题", ["正确", "错误"])
    print("测试查询:", r)
