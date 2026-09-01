# -*- coding: utf-8 -*-
import json
import os

d = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '_sweep_btc.json'), encoding='utf-8'))

s3 = d['stage3']
# 只看 p=11×4 er=0.12/0.1/0.25 家族的所有分数档，按 pnl 排序看梯度
fam = [x for x in s3 if x['tag'].startswith('p=11×4 er=0.12/0.1/0.25')]
fam = sorted(fam, key=lambda x: x['pnl'], reverse=True)

print(f'p=11×4 er=0.12/0.1/0.25 分数档全谱 ({len(fam)}组)\n')
# 按 (full,half,alert) 组合去重展示梯度（m_score 无影响，取每组第一个）
seen = {}
for x in fam:
    import re
    m = re.search(r'score=d:([\d.]+)/([\d.]+)/([\d.]+)', x['tag'])
    k = (m.group(1), m.group(2), m.group(3))
    if k not in seen:
        seen[k] = x
rows = sorted(seen.values(), key=lambda x: x['pnl'], reverse=True)
for x in rows:
    print(f"  {x['tag']} | pnl={x['pnl']}U n={x['trades']} wr={x['wr']}% PF={x['pf']} dd={x['dd']}%")

# stage4: TP 梯度（基于 40/40/40 m0），确认最优 TP
print('\n=== stage4 p=11×4 er=0.12/0.1/0.25 score=d:40/40/40 m0 各 TP 梯度 ===')
s4 = [x for x in d['stage4'] if x['tag'].startswith('p=11×4 er=0.12/0.1/0.25 score=d:40/40/40 m0')]
s4 = sorted(s4, key=lambda x: x['pnl'], reverse=True)
import re
seen4 = {}
for x in s4:
    m = re.search(r'tp=([\d.]+)/([\d.]+)/([\d.]+)', x['tag'])
    k = (m.group(1), m.group(2), m.group(3))
    if k not in seen4:
        seen4[k] = x
for x in sorted(seen4.values(), key=lambda x: x['pnl'], reverse=True):
    print(f"  {x['tag']} | pnl={x['pnl']}U n={x['trades']} wr={x['wr']}% PF={x['pf']} dd={x['dd']}%")
