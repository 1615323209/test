"""akshare 板块资金历史接口探测"""
import sys, time

def t(name, fn):
    try:
        r = fn()
        print(f"✅ {name}: {type(r).__name__} shape={getattr(r, 'shape', 'N/A')}")
        if hasattr(r, 'head'):
            print(f"   列: {list(r.columns)[:10]}")
            print(f"   首行: {r.iloc[0].to_dict() if len(r) else 'empty'}")
            # 日期范围
            if '日期' in r.columns:
                print(f"   日期: {r['日期'].min()} ~ {r['日期'].max()}")
        return r
    except Exception as e:
        print(f"❌ {name}: {str(e)[:90]}")
        return None

import akshare as ak

# 1. 行业板块资金流历史
t("stock_sector_fund_flow_hist(sina)", lambda: ak.stock_sector_fund_flow_hist(symbol="电源设备"))
t("stock_sector_fund_flow_hist(symbol=银行)", lambda: ak.stock_sector_fund_flow_hist(symbol="银行"))
t("stock_sector_fund_flow_summary", lambda: ak.stock_sector_fund_flow_summary(symbol="电源设备", indicator="今日"))
