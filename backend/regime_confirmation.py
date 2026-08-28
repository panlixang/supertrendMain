"""
信号确认机制 - 用"等1-2根"换更高胜率

核心思想：
    SuperTrend翻转当根下单，经常是这样的：
    - 看多翻转，开多，下一根回调打止损 ❌
    - 看空翻转，开空，下一根反弹打止损 ❌

    原因：翻转当根价格已经走了一段，追进去容易被反抽。

    解决方案：
    翻转后不立即下单，等1-2根确认：
    - 看多信号：等回踩不破超趋线 + 收阳线 → 再开多
    - 看空信号：等反抽不破超趋线 + 收阴线 → 再开空

    代价：错过最佳入场点5-10个点
    收益：假信号砍掉50%+，整体盈亏比更好

使用方式：
    1. 翻转当根：记录信号但不下单，标记为"待确认"
    2. 后续K线：检查是否满足确认条件
    3. 确认通过：按回踩后的价格下单（成本稍高但安全）
    4. 确认失败：信号作废，避免亏损
"""

from datetime import datetime, timedelta


class SignalConfirmationTracker:
    """信号确认跟踪器

    存储待确认的信号，每根新K线来时检查是否满足确认条件
    """

    def __init__(self):
        self.pending_signals = {}  # {(symbol, tf): {signal, flip_time, ...}}

    def add_pending(self, symbol: str, tf: str, sig: dict, st_line: float):
        """添加待确认信号

        Args:
            symbol: 交易对
            tf: 周期
            sig: 原始信号 {type, price, tf, score, ...}
            st_line: 超趋线位置（up/dn）
        """
        key = (symbol, tf)
        self.pending_signals[key] = {
            "signal": sig,
            "st_line": st_line,
            "flip_price": sig["price"],
            "flip_time": datetime.now(),
            "bars_waited": 0,
            "max_confirm_bars": 3,  # 最多等3根，超时作废
        }

    def check_confirmation(self, symbol: str, tf: str, candles: list[dict],
                          st: dict, cfg) -> dict:
        """检查待确认信号是否满足条件

        返回：{
            "confirmed": True/False,
            "action": "trade"/"wait"/"cancel",
            "reason": "...",
            "entry_price": 49500  # 确认后的建议入场价
        }
        """
        key = (symbol, tf)
        if key not in self.pending_signals:
            return {"confirmed": False, "action": "none", "reason": "无待确认信号"}

        pending = self.pending_signals[key]
        sig = pending["signal"]
        st_line = pending["st_line"]

        # 更新等待根数
        pending["bars_waited"] += 1

        # 超时作废
        if pending["bars_waited"] > pending["max_confirm_bars"]:
            del self.pending_signals[key]
            return {
                "confirmed": False,
                "action": "cancel",
                "reason": f"超时作废（等待{pending['bars_waited']}根）"
            }

        latest = candles[-1]
        prev = candles[-2] if len(candles) >= 2 else None

        # ===== 看多信号确认条件 =====
        if sig["type"] == "buy":
            # 条件1：价格回踩但未破超趋线
            pullback_ok = latest["l"] >= st_line * 0.998  # 允许2‰的假破

            # 条件2：当前K线收阳（或至少不是大阴线）
            bullish_close = latest["c"] >= latest["o"] or \
                           (latest["o"] - latest["c"]) / latest["o"] < 0.005

            # 条件3：价格没有暴涨（避免追高）
            not_runaway = latest["c"] < pending["flip_price"] * 1.03

            if pullback_ok and bullish_close and not_runaway:
                del self.pending_signals[key]
                return {
                    "confirmed": True,
                    "action": "trade",
                    "reason": f"回踩确认（低点{latest['l']:.2f} > 线{st_line:.2f}）",
                    "entry_price": latest["c"],
                    "bars_waited": pending["bars_waited"]
                }

            # 破线 → 信号作废
            if latest["c"] < st_line:
                del self.pending_signals[key]
                return {
                    "confirmed": False,
                    "action": "cancel",
                    "reason": f"破超趋线（收{latest['c']:.2f} < 线{st_line:.2f}），假信号"
                }

            # 暴涨 → 放弃（追不上了）
            if latest["c"] > pending["flip_price"] * 1.05:
                del self.pending_signals[key]
                return {
                    "confirmed": False,
                    "action": "cancel",
                    "reason": "涨幅过大，放弃追单"
                }

            # 还在等待区间
            return {
                "confirmed": False,
                "action": "wait",
                "reason": f"等待回踩确认（{pending['bars_waited']}/{pending['max_confirm_bars']}根）",
                "pending_price": latest["c"]
            }

        # ===== 看空信号确认条件 =====
        else:  # sell
            # 条件1：价格反抽但未破超趋线
            bounce_ok = latest["h"] <= st_line * 1.002

            # 条件2：当前K线收阴
            bearish_close = latest["c"] <= latest["o"] or \
                           (latest["c"] - latest["o"]) / latest["o"] < 0.005

            # 条件3：价格没有暴跌
            not_runaway = latest["c"] > pending["flip_price"] * 0.97

            if bounce_ok and bearish_close and not_runaway:
                del self.pending_signals[key]
                return {
                    "confirmed": True,
                    "action": "trade",
                    "reason": f"反抽确认（高点{latest['h']:.2f} < 线{st_line:.2f}）",
                    "entry_price": latest["c"],
                    "bars_waited": pending["bars_waited"]
                }

            # 破线 → 作废
            if latest["c"] > st_line:
                del self.pending_signals[key]
                return {
                    "confirmed": False,
                    "action": "cancel",
                    "reason": f"破超趋线（收{latest['c']:.2f} > 线{st_line:.2f}），假信号"
                }

            # 暴跌 → 放弃
            if latest["c"] < pending["flip_price"] * 0.95:
                del self.pending_signals[key]
                return {
                    "confirmed": False,
                    "action": "cancel",
                    "reason": "跌幅过大，放弃追单"
                }

            return {
                "confirmed": False,
                "action": "wait",
                "reason": f"等待反抽确认（{pending['bars_waited']}/{pending['max_confirm_bars']}根）",
                "pending_price": latest["c"]
            }

    def get_all_pending(self) -> dict:
        """获取所有待确认信号（供UI展示）"""
        return {
            f"{k[0]}_{k[1]}": {
                "symbol": k[0],
                "tf": k[1],
                "type": v["signal"]["type"],
                "price": v["flip_price"],
                "st_line": v["st_line"],
                "bars_waited": v["bars_waited"],
                "max_bars": v["max_confirm_bars"],
                "time": v["flip_time"].isoformat()
            }
            for k, v in self.pending_signals.items()
        }

    def clear_symbol(self, symbol: str):
        """清除某个交易对的所有待确认信号"""
        keys_to_del = [k for k in self.pending_signals if k[0] == symbol]
        for k in keys_to_del:
            del self.pending_signals[k]


# 全局实例（或者放到state.py里）
_tracker = SignalConfirmationTracker()


def evaluate_with_confirmation(sig: dict, symbol: str, candles: list[dict],
                               st: dict, cfg) -> dict:
    """带确认机制的信号评估

    工作流程：
    1. 新翻转信号 → 加入待确认队列，返回 trade=False
    2. 后续K线 → 检查确认条件
    3. 确认通过 → 返回 trade=True，执行器下单
    4. 确认失败 → 信号作废，避免亏损

    返回格式兼容 regime.evaluate()
    """
    tf = sig.get("tf")

    # 检查是否有待确认信号
    check = _tracker.check_confirmation(symbol, tf, candles, st, cfg)

    if check["action"] == "trade":
        # 确认通过，下单
        return {
            "trade": True,
            "reasons": [],
            "confirmation": {
                "confirmed": True,
                "bars_waited": check["bars_waited"],
                "reason": check["reason"],
                "entry_price": check["entry_price"]
            },
            "hidden": False
        }

    elif check["action"] == "wait":
        # 还在等待
        return {
            "trade": False,
            "reasons": [check["reason"]],
            "confirmation": {
                "confirmed": False,
                "waiting": True,
                "bars_waited": check["bars_waited"]
            },
            "hidden": False  # 显示"等待确认"状态
        }

    elif check["action"] == "cancel":
        # 确认失败，信号作废
        return {
            "trade": False,
            "reasons": [check["reason"]],
            "confirmation": {
                "confirmed": False,
                "cancelled": True
            },
            "hidden": True  # 静默取消
        }

    else:  # "none" - 新信号
        # 这是新翻转，加入待确认队列
        st_line = st["up"][-1] if sig["type"] == "buy" else st["dn"][-1]
        _tracker.add_pending(symbol, tf, sig, st_line)

        return {
            "trade": False,
            "reasons": [f"等待1-3根确认（降低假信号）"],
            "confirmation": {
                "confirmed": False,
                "pending": True,
                "bars_waited": 0
            },
            "hidden": False  # 显示"待确认"状态
        }


def get_pending_signals():
    """获取所有待确认信号（供WebSocket推送给前端）"""
    return _tracker.get_all_pending()
