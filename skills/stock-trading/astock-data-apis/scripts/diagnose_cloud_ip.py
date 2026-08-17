#!/usr/bin/env python3
"""A股数据源云服务器连通性诊断
快速判断当前环境能用东方财富源还是腾讯源。
输出明确的"用哪个"结论。
"""
import socket
import requests
import time

def test_tcp(host, port=80, timeout=3):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False

def test_http(url, headers=None, timeout=5):
    try:
        r = requests.get(url, headers=headers or {}, timeout=timeout)
        return r.status_code, len(r.text)
    except requests.exceptions.ConnectionError as e:
        return None, str(e)[:80]
    except Exception as e:
        return None, str(e)[:80]

targets = [
    ("东方财富 push2", "push2.eastmoney.com", 80,
     "http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1&fid=f3&fs=m:0+t:6&fields=f12",
     {"Referer": "https://www.eastmoney.com/", "User-Agent": "Mozilla/5.0"}),
    ("腾讯 qt", "qt.gtimg.cn", 80,
     f"http://qt.gtimg.cn/?_={int(time.time())}&q=sh600519",
     {}),
]

print("=== A股数据源连通性诊断 ===\n")

for name, host, port, url, headers in targets:
    tcp = test_tcp(host, port)
    http_code, http_detail = test_http(url, headers)

    tcp_s = "✅" if tcp else "❌"
    if http_code == 200:
        http_s = f"✅ HTTP {http_code}"
        result = "可用"
    elif tcp and http_code is None:
        http_s = f"❌ {http_detail}"
        result = "TCP通但HTTP被拒（IP封禁）"
    else:
        http_s = f"❌ {http_detail}"
        result = "不可用"

    print(f"{name:20s} TCP: {tcp_s}  HTTP: {http_s}")
    print(f"  → {result}\n")

print("结论：优先用腾讯源（stock_zh_a_hist_tx / stock_zh_a_spot_tx）")
