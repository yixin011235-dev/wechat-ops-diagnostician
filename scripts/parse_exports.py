#!/usr/bin/env python3
"""
parse_exports.py — 把微信公众号后台导出统一解析成 canonical 数据结构。

支持两种真实导出格式（微信后台目前混用，不保证统一）：
  1. 内容/阅读趋势（OLE2 二进制 .xls，xlslib 生成）
     单 sheet 三块横排：日度来源阅读 / 日度互动 / 单篇来源阅读
  2. 常读用户分布趋势（伪 .xls，实为 HTML 表格）
     城市层级 / 年龄 / 性别 / 常读用户数，按月

用法：
    python parse_exports.py <文件或目录> [<更多文件>...] [--out bundle.json]

设计原则：解析器只负责把"导出"变成"干净结构"，不做诊断。
诊断逻辑在 references/diagnosis_framework.md，调用方读取本脚本的输出后据此分析。
"""
import sys, os, io, json, argparse, warnings
warnings.filterwarnings("ignore")

KNOWN_CHANNELS = {"公众号消息", "朋友圈", "搜一搜", "聊天会话",
                  "公众号主页", "推荐", "其他", "全部"}


def sniff(path):
    """靠 magic bytes 判别真实格式，而非靠扩展名（微信伪 .xls 很常见）。"""
    with open(path, "rb") as f:
        head = f.read(8)
    if head[:4] == b"\xd0\xcf\x11\xe0":          # OLE2 复合文档 → 真 .xls
        return "ole2_tendency"
    if head[:5].lower() == b"<html":             # HTML 伪装成 .xls
        return "html_distribution"
    return "unknown"


# ---------- 格式 1：内容/阅读趋势（OLE2 三块） ----------
def parse_tendency(path):
    import pandas as pd
    raw = pd.read_excel(path, sheet_name=0, engine="xlrd", header=None)
    # 抓日期范围（在 row0 的块标题里：数据趋势概况(YYYY.MM.DD-YYYY.MM.DD)）
    date_range = None
    try:
        title = str(raw.iloc[0, 1])
        if "(" in title:
            date_range = title.split("(", 1)[1].rstrip(")")
    except Exception:
        pass
    data = raw.iloc[2:]   # row0=块标题, row1=表头, row2+=数据

    def block(cols, names, key_col, valid=None):
        b = data.iloc[:, cols].copy()
        b.columns = names
        b = b.dropna(subset=[key_col])
        if valid:                                   # 滤掉表头泄漏行
            b = b[b[valid[0]].isin(valid[1])]
        return b

    b1 = block([1, 2, 3], ["date", "channel", "readers"], "channel",
               valid=("channel", KNOWN_CHANNELS))
    b2 = block([5, 6, 7, 8, 9],
               ["date", "shares", "read_origin_clicks", "saves", "articles_published"],
               "date")
    b2 = b2[b2["date"].astype(str).str.match(r"\d{4}-\d{2}-\d{2}")]
    b3 = block([11, 12, 13, 14, 15],
               ["channel", "pub_date", "title", "readers", "read_share"], "title",
               valid=("channel", KNOWN_CHANNELS))

    def num(df, *c):
        for x in c:
            df[x] = pd.to_numeric(df[x], errors="coerce")
        return df
    b1 = num(b1, "readers")
    b2 = num(b2, "shares", "read_origin_clicks", "saves", "articles_published")
    b3 = num(b3, "readers", "read_share")

    return {
        "kind": "content",
        "date_range": date_range,
        "daily_by_channel": b1.to_dict("records"),
        "daily_engagement": b2.to_dict("records"),
        "per_article": b3.to_dict("records"),
    }


# ---------- 格式 2：常读分布趋势（伪 HTML） ----------
def parse_distribution(path):
    import pandas as pd
    txt = None
    for enc in ("utf-8", "gbk", "utf-16", "latin-1"):
        try:
            txt = open(path, "rb").read().decode(enc); break
        except Exception:
            continue
    cols = list(pd.read_html(io.StringIO(txt))[0].columns)
    section = cols[0][0] if cols else ""

    def field(name):                                # 数据全在列 tuple 的 [2:] 里
        for c in cols:
            if c[1] == name:
                return list(c[2:])
        return None

    def pct(v):
        try:
            return round(float(str(v).strip("%")), 2)
        except Exception:
            return None

    # 常读用户数变化：时间 / 常读用户数 / 常读用户比例
    if "常读用户数" in section or field("常读用户数"):
        months = field("时间"); cnt = field("常读用户数"); ratio = field("常读用户比例")
        rows = [{"month": m, "count": int(c), "ratio_pct": pct(r)}
                for m, c, r in zip(months, cnt, ratio)]
        return {"kind": "regular_count", "rows": rows}

    # 城市/年龄/性别：时间 / 用户类型 / <维度> / 人数 / 占比
    dim_field = next((c[1] for c in cols
                      if c[1] in ("用户所在城市", "用户年龄", "用户性别")), None)
    dim_name = {"用户所在城市": "city_tier",
                "用户年龄": "age", "用户性别": "gender"}.get(dim_field, "unknown")
    months = field("时间"); dim = field(dim_field)
    cnt = field("人数"); p = field("占比")
    rows = [{"month": m, "bucket": d, "count": int(c), "pct": pct(pp)}
            for m, d, c, pp in zip(months, dim, cnt, p)]
    return {"kind": dim_name, "rows": rows}


def parse_file(path):
    fmt = sniff(path)
    if fmt == "ole2_tendency":
        return parse_tendency(path)
    if fmt == "html_distribution":
        return parse_distribution(path)
    raise ValueError(f"无法识别格式: {path}")


def build_bundle(paths):
    bundle = {"content": {"daily_by_channel": [], "daily_engagement": [], "per_article": []},
              "users": {"regular_count": [], "city_tier": [], "age": [], "gender": []},
              "meta": {"files": [], "content_date_ranges": []}}
    for p in paths:
        try:
            r = parse_file(p)
        except Exception as e:
            bundle["meta"]["files"].append({"file": os.path.basename(p), "error": str(e)})
            continue
        bundle["meta"]["files"].append({"file": os.path.basename(p), "kind": r["kind"]})
        if r["kind"] == "content":
            bundle["content"]["daily_by_channel"] += r["daily_by_channel"]
            bundle["content"]["daily_engagement"] += r["daily_engagement"]
            bundle["content"]["per_article"] += r["per_article"]
            if r.get("date_range"):
                bundle["meta"]["content_date_ranges"].append(r["date_range"])
        elif r["kind"] == "regular_count":
            bundle["users"]["regular_count"] += r["rows"]
        elif r["kind"] in ("city_tier", "age", "gender"):
            bundle["users"][r["kind"]] += r["rows"]
    return bundle


def summarize(b):
    """打印一个体检摘要，方便人快速核对解析是否正确。"""
    c = b["content"]; u = b["users"]
    print("== 解析摘要 ==")
    for f in b["meta"]["files"]:
        tag = f.get("kind", "ERROR: " + f.get("error", ""))
        print(f"  {f['file']}  ->  {tag}")
    if b["meta"]["content_date_ranges"]:
        print("  内容日期范围:", ", ".join(b["meta"]["content_date_ranges"]))
    print(f"  日度来源阅读 {len(c['daily_by_channel'])} 行 | "
          f"日度互动 {len(c['daily_engagement'])} 行 | 单篇 {len(c['per_article'])} 行")

    # 渠道结构
    import collections
    mix = collections.Counter()
    for r in c["daily_by_channel"]:
        if r["channel"] != "全部" and r["readers"]:
            mix[r["channel"]] += r["readers"]
    tot = sum(mix.values())
    if tot:
        print("  阅读来源结构:",
              " ".join(f"{k} {v/tot*100:.1f}%" for k, v in mix.most_common()))
    if u["regular_count"]:
        rc = u["regular_count"]
        print("  常读用户数:", " ".join(f"{r['month']}={r['count']}" for r in rc))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    files = []
    for p in a.paths:
        if os.path.isdir(p):
            files += [os.path.join(p, f) for f in sorted(os.listdir(p))
                      if f.lower().endswith((".xls", ".xlsx"))]
        else:
            files.append(p)
    bundle = build_bundle(files)
    summarize(bundle)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(bundle, f, ensure_ascii=False, indent=2)
        print("  写出:", a.out)


if __name__ == "__main__":
    main()
