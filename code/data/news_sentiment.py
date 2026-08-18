#!/usr/bin/env python3
"""新闻/公告情绪因子（个股新闻情绪因子 - 方案1）
实时拉取候选股近期公告(F10东财接口)，标题关键词规则分 利好/利空/中性，
输出情绪分叠加到选股打分。纯url，零API Key。

用法: python -m data.news_sentiment 600519,688590,603106
输出: 每只股票 情绪分/利好条数/利空条数/最近公告标题
情绪分口径:
  +1 每条约 利好 关键词(业绩预增/净利润增长/中标/签订合同/回购/增持/重大合同/扭亏为盈/分红/高送转/战略合作)
  -1 每条约 利空 关键词(业绩预减/亏损/减持/诉讼/处罚/质押/立案/退市/商誉减值/被监管/关注函/问询函)
  0 中性
综合情绪分 = 利好条数 - 利空条数 (归一化到 -1..1 加进选股权重)
"""
import requests, json, sys, re
from pathlib import Path

HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

# 沪深识别前缀: 6开头沪(1.) 0/3开头深(0.)
def secid(code):
    return ("1." if code.startswith("6") else "0.") + code

# 利好/利空 关键词
POS_KW = ["业绩预增", "净利润增长", "业绩增长", "中标", "签订合同", "重大合同", "回购",
          "增持", "扭亏为盈", "分红", "高送转", "战略合作", "签订重大合同", "订单", "预增",
          "营业收入增长", "超预期", "盈利"]
NEG_KW = ["业绩预减", "净利润下降", "亏损", "减持", "诉讼", "处罚", "被立案", "立案",
          "质押", "退市", "商誉减值", "关注函", "问询函", "预亏", "下降", "违规", "风险提示",
          "终止", "被调查", "赔偿", "违约", "逾期"]


def fetch_announcements(code, limit=8):
    """东财 F10 公告接口"""
    url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    params = {"sr": "-1", "page_size": str(limit), "page_index": "1",
              "ann_type": "A", "client_source": "web", "f_node": "0",
              "s_node": "0", "stock_list": code}
    try:
        r = requests.get(url, params=params, headers=HDRS, timeout=10)
        j = r.json()
        lst = (j.get("data") or {}).get("list") or []
        return [(it.get("title", ""), it.get("notice_date", "")) for it in lst]
    except Exception as e:
        return [("", str(e))]


def classify(title):
    """标题关键词 → 情绪 (-1 利空 / +1 利好 / 0 中性)"""
    pos = sum(1 for kw in POS_KW if kw in title)
    neg = sum(1 for kw in NEG_KW if kw in title)
    if pos > neg:
        return 1
    if neg > pos:
        return -1
    return 0


def sentiment(code, limit=8):
    """个股情绪: 返回 (score, pos_cnt, neg_cnt, anns)"""
    anns = fetch_announcements(code, limit)
    pos = neg = 0
    scored = []
    for title, dt in anns:
        s = classify(title)
        if s > 0:
            pos += 1
        elif s < 0:
            neg += 1
        scored.append((title, dt, s))
    net = pos - neg
    score = net / max(pos + neg, 1)  # 归一化 -1..1
    return score, pos, neg, scored


if __name__ == "__main__":
    codes = sys.argv[1].split(",") if len(sys.argv) > 1 else ["600519"]
    for c in codes:
        sc, p, n, anns = sentiment(c.strip())
        print(f"== {c}  情绪分={sc:+.2f} (利好{p} / 利空{n})")
        for title, dt, s in anns[:4]:
            tag = "✔" if s > 0 else ("✘" if s < 0 else "-")
            print(f"   [{tag}] {title[:45]}")
