# -*- coding: utf-8 -*-
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))


def diff(x, y, path=""):
    for k in (set(x) | set(y)):
        if k not in x:
            print(f"  [仅opt] {path}{k} = {y[k]}")
        elif k not in y:
            print(f"  [仅config] {path}{k} = {x[k]}")
        else:
            if isinstance(x[k], dict) and isinstance(y[k], dict):
                diff(x[k], y[k], path + k + ".")
            elif isinstance(x[k], list) and isinstance(y[k], list):
                if x[k] != y[k]:
                    print(f"  [不同] {path}{k}: config={x[k]}  opt={y[k]}")
            elif x[k] != y[k]:
                print(f"  [不同] {path}{k}: config={x[k]}  opt={y[k]}")


a = json.load(open(os.path.join(HERE, "backtest/SNDK/sndk_config.json"), encoding="utf-8"))
b = json.load(open(os.path.join(HERE, "backtest/SNDK/sndk_opt_config.json"), encoding="utf-8"))
print("===== sndk_config.json vs sndk_opt_config.json 差异 =====")
diff(a, b)
