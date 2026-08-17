#!/usr/bin/env python3
"""方向二：Alpha360 训练（1D-CNN 从 30天×8特征 学 Alpha）

防过拟合三件套：dropout + weight decay + early stopping
walk-forward：train 2021-2024 / val 2025-2026（val 只评估，绝不进训练）
输出: D:/quant_data/alpha360/alpha360_model.pt + pred_train.csv / pred_val.csv（日期×代码×预测）

用法:
  python -m loop.train_alpha360 --epochs 15 --batch 1024
"""
import sys, os, time, argparse, json
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")  # Anaconda 下 torch/numpy OpenMP 冲突
from pathlib import Path
import numpy as np
import pandas as pd
import polars as pl
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

OUT = Path(r"D:\quant_data\alpha360")

class AlphaCNN(nn.Module):
    """1D-CNN：输入 [B, 30, F] → 单值 Alpha"""
    def __init__(self, n_feat=8, n_window=30):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(n_feat, 32, kernel_size=5, padding=2), nn.ReLU(), nn.Dropout(0.2),
            nn.Conv1d(32, 64, kernel_size=3, padding=1), nn.ReLU(), nn.Dropout(0.2),
            nn.Conv1d(64, 64, kernel_size=3, padding=1), nn.ReLU(),
        )
        self.head = nn.Sequential(nn.AdaptiveAvgPool1d(1), nn.Flatten(), nn.Linear(64, 1))

    def forward(self, x):
        x = x.transpose(1, 2)  # [B, F, T]
        return self.head(self.conv(x)).squeeze(-1)

def daily_ic(pred, y, dates):
    """按日期横截面 Spearman IC → (ic_mean, icir)"""
    df = pd.DataFrame({"d": dates, "p": pred, "y": y}).dropna()
    ics = df.groupby("d").apply(
        lambda g: g["p"].corr(g["y"], method="spearman") if len(g) > 5 else np.nan,
        include_groups=False).dropna()
    if len(ics) < 30:
        return 0.0, 0.0, len(ics)
    m, s = ics.mean(), ics.std()
    return float(m), float(m / s if s else 0), len(ics)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--batch", type=int, default=1024)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--patience", type=int, default=4)
    args = ap.parse_args()

    t0 = time.time()
    print("=== Alpha360 训练（PIT 严格版） ===")
    print(f"[1/4] 加载数据...")
    # 设计段训练 / 2024 内层留出验证（early stopping 基准）
    # 2025-2026 真验证集只在 L3 复核时用，禁止参与训练与选模（L1 文档第六章第 6 条）
    tx = np.load(OUT / "design_x.npy"); ty = np.load(OUT / "design_y.npy")
    vx = np.load(OUT / "holdout_x.npy"); vy = np.load(OUT / "holdout_y.npy")
    tm = json.load(open(OUT / "design_meta.json"))
    vm = json.load(open(OUT / "holdout_meta.json"))
    print(f"  design {tx.shape} holdout {vx.shape} ({time.time()-t0:.0f}s)")

    train_dl = DataLoader(TensorDataset(torch.tensor(tx), torch.tensor(ty)),
                          batch_size=args.batch, shuffle=True)
    val_dl = DataLoader(TensorDataset(torch.tensor(vx), torch.tensor(vy)),
                        batch_size=args.batch, shuffle=False)

    print(f"[2/4] 模型初始化 (CPU: {torch.get_num_threads()} threads)")
    model = AlphaCNN(n_feat=tx.shape[2], n_window=tx.shape[1])
    opt = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-4)
    loss_fn = nn.MSELoss()

    best_val_ic, best_state, patience = -1e9, None, 0
    for ep in range(1, args.epochs + 1):
        model.train()
        tl, n = 0.0, 0
        for xb, yb in train_dl:
            opt.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            opt.step()
            tl += loss.item() * len(xb); n += len(xb)
        # 评估
        model.eval()
        with torch.no_grad():
            tp = torch.cat([model(xb) for xb, _ in train_dl]).numpy()
            vp = torch.cat([model(xb) for xb, _ in val_dl]).numpy()
        t_ic, t_icir, _ = daily_ic(tp, ty, tm["dates"])
        v_ic, v_icir, vn = daily_ic(vp, vy, vm["dates"])
        print(f"  ep{ep:02d} loss={tl/n:.5f} | train IC={t_ic:+.4f} ICIR={t_icir:+.3f} | val IC={v_ic:+.4f} ICIR={v_icir:+.3f} ({time.time()-t0:.0f}s)")
        if v_ic > best_val_ic:
            best_val_ic, best_state = v_ic, {k: v.clone() for k, v in model.state_dict().items()}
            patience = 0
        else:
            patience += 1
            if patience >= args.patience:
                print(f"  early stop @ ep{ep}")
                break

    print(f"[3/4] 保存最优模型 (val IC={best_val_ic:+.4f})")
    if best_state:
        model.load_state_dict(best_state)
    torch.save(model.state_dict(), OUT / "alpha360_model.pt")

    print(f"[4/4] 输出三段预测（design/holdout/valid；valid 只推理，L3 复核用）...")
    model.eval()
    with torch.no_grad():
        dp = torch.cat([model(xb) for xb, _ in train_dl]).numpy()
        hp = torch.cat([model(xb) for xb, _ in val_dl]).numpy()
        # 真验证集 2025-2026：只推理，不参与训练/early stopping/选模
        vx2 = np.load(OUT / "valid_x.npy")
        vm2 = json.load(open(OUT / "valid_meta.json"))
        valid_dl = DataLoader(TensorDataset(torch.tensor(vx2), torch.zeros(len(vx2))),
                              batch_size=args.batch, shuffle=False)
        vp = torch.cat([model(xb) for xb, _ in valid_dl]).numpy()
    tr = pd.DataFrame({"日期": tm["dates"], "股票代码": tm["codes"], "alpha360": dp})
    ho = pd.DataFrame({"日期": vm["dates"], "股票代码": vm["codes"], "alpha360": hp})
    va = pd.DataFrame({"日期": vm2["dates"], "股票代码": vm2["codes"], "alpha360": vp})
    tr.to_csv(OUT / "pred_design.csv", index=False)
    ho.to_csv(OUT / "pred_holdout.csv", index=False)
    va.to_csv(OUT / "pred_valid.csv", index=False)
    h_ic, h_icir, hn = daily_ic(hp, vy, vm["dates"])
    print(f"=== 完成 {time.time()-t0:.0f}s | holdout(2024) IC={h_ic:+.4f} ICIR={h_icir:+.3f} ({hn}天) ===")

if __name__ == "__main__":
    main()
