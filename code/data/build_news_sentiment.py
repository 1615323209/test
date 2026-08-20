#!/usr/bin/env python3
"""公告新闻情绪因子构建（日频）
输入:  D:/quant_data/announcements/{code}.jsonl —— 已采集公告库(带日期+标题, 243万条)
输出:  D:/quant_data/daily_news_sentiment/{code}.parquet —— (date, code, ann_cnt, pos_cnt, neg_cnt, sentiment)
口径: 关键词规则打分 —— 利好+1 利空-1 中性0, 聚合为当日每股 利好/利空条数 + 净情绪分[-1,1]
用法: python -m data.build_news_sentiment [--limit N]
"""
import json, re, sys, time
from pathlib import Path
import polars as pl

DATA = Path("D:/quant_data")
ANN_DIR = DATA / "announcements"
OUT = DATA / "daily_news_sentiment"
YEARS_RANGE = ("2021-01-01", "2025-12-31")  # 设计段+留出段

POS_PAT = re.compile(r"预增|预盈|扭亏|回购|增持|中标|签订合同|战略合作|分红|高送转|净利润增长|业绩增长|超预期|授予|激励")
NEG_PAT = re.compile(r"预减|预亏|亏损|减持|诉讼|处罚|立案|退市|质押|终止|违约|逾期|风险提示|被调查|问询|关注函")


def score_title(t: str):
    """标题情绪: 返回 (情绪, 利好词数, 利空词数)"""
    pos = len(POS_PAT.findall(t))
    neg = len(NEG_PAT.findall(t))
    if pos > neg:
        return 1, pos, neg
    if neg > pos:
        return -1, pos, neg
    return 0, pos, neg


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    OUT.mkdir(parents=True, exist_ok=True)
    files = sorted(ANN_DIR.glob("*.jsonl"))
    if limit:
        files = files[:limit]
    t0 = time.time()
    done = 0
    for f in files:
        code = f.stem
        out = OUT / f"{code}.parquet"
        if out.exists():
            done += 1
            continue  # 断点续跑
        rows = []
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        a = json.loads(line)
                        d = a.get("date", "")
                        t = a.get("title", "")
                        if not (YEARS_RANGE[0] <= d <= YEARS_RANGE[1]):
                            continue
                        s, p, n = score_title(t)
                        rows.append({"date": d, "code": code,
                                     "pos_cnt": p, "neg_cnt": n,
                                     "score": s})
                    except Exception:
                        continue
        except Exception:
            continue
        if not rows:
            continue
        df = pl.DataFrame(rows)
        df = df.group_by(["date", "code"]).agg(
            pl.col("pos_cnt").sum(),
            pl.col("neg_cnt").sum(),
            pl.col("score").sum(),
        ).with_columns(
            (pl.col("pos_cnt") + pl.col("neg_cnt")).alias("ann_cnt")
        ).with_columns(
            (pl.col("score") / pl.col("ann_cnt").clip(lower_bound=1)).alias("sentiment")
        ).select(["date", "code", "ann_cnt", "pos_cnt", "neg_cnt", "sentiment"])
        df.write_parquet(out, compression="zstd")
        done += 1
        if done % 500 == 0:
            print(f"  {done}/{len(files)} 完成, {time.time()-t0:.0f}s")
    print(f"[完成] 处理 {done} 只 -> {OUT}")


if __name__ == "__main__":
    main()
