#!/bin/bash
# ── 标准 Slurm 头(改成你的） ──────────────────────────────
#SBATCH --job-name=my-exp
#SBATCH --account=<超算账号>
#SBATCH --partition=<分区>
#SBATCH --gres=gpu:1
#SBATCH --time=04:00:00
#SBATCH --output=logs/%x_%j.log

# ── lawn 实验登记(以下 #EXP 行会被自动读入实验文档,对 Slurm 无副作用) ──
#EXP name: 简短实验名
#EXP goal: 一句话实验目标 —— 想验证什么 / 期望达到的指标
#EXP config: 关键配置,如 model=... lr=1e-4 bs=32 dataset=... 其它备注
#EXP smoke: false        # true=冒烟测试(不进看护/不自动修);也可 LAWN_SMOKE=1 sbatch ...
# 注:名字含 smoke/test/debug 或 --time≤15min 会被自动当作 smoke,可用上面这行显式覆盖。
# ──────────────────────────────────────────────────────────

set -euo pipefail

# 你的实验命令，例如：
# python train.py --config configs/exp.yaml
