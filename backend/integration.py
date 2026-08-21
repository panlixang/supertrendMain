"""
集成增强功能的适配器 - 最小侵入式集成

使用方法：
1. 在 feed.py 导入: from integration import enhanced_signal_handler
2. 替换信号处理: 原来调用 evaluate 的地方改为 enhanced_signal_handler
"""

from typing import Optional
import logging

logger = logging.getLogger(__name__)


def enhanced_signal_handler(sig: dict, candles: list[dict], cfg,
                            candles_by_tf: dict = None, p: dict = None,
                            use_momentum: bool = True,
                            use_false_filter: bool = True,
                            use_adaptive: bool = True) -> dict:
    """增强版信号处理入口 - 向后兼容的包装器

    Args:
        sig: 信号字典
        candles: K线数据
        cfg: 配置对象
        candles_by_tf: 多周期K线（可选）
        p: 参数字典（可选）
        use_momentum: 是否启用动量突破检测
        use_false_filter: 是否启用假突破过滤
        use_adaptive: 是否启用自适应阈值

    Returns:
        与原 evaluate 相同格式的结果字典
    """
    try:
        # 尝试导入增强模块
        from regime_enhanced import (
            detect_momentum_breakout,
            detect_false_breakout,
            adaptive_thresholds,
            evaluate_enhanced
        )

        # 如果全部功能都关闭，回退到原版
        if not any([use_momentum, use_false_filter, use_adaptive]):
            from regime import evaluate
            return evaluate(sig, candles, cfg, candles_by_tf, p)

        # 使用增强版评估
        result = evaluate_enhanced(sig, candles, cfg, candles_by_tf, p)

        # 添加调试信息
        if result.get("momentum"):
            logger.info(f"[动量突破] {sig.get('tf')} {sig.get('type')} - "
                       f"{result['momentum']['reason']}")

        if result.get("filters", {}).get("false_breakout"):
            logger.info(f"[假突破过滤] {sig.get('tf')} {sig.get('type')} - 已拦截")

        if result.get("filters", {}).get("adaptive"):
            adj = result["filters"]["adaptive"]
            logger.info(f"[自适应调整] 波动率={adj.get('volatility')}%, "
                       f"倍数={adj.get('vol_ratio')}")

        return result

    except ImportError as e:
        logger.warning(f"增强模块未找到，回退到原版: {e}")
        from regime import evaluate
        return evaluate(sig, candles, cfg, candles_by_tf, p)
    except Exception as e:
        logger.error(f"增强评估失败，回退到原版: {e}")
        from regime import evaluate
        return evaluate(sig, candles, cfg, candles_by_tf, p)


class EnhancedExecutorMixin:
    """执行器增强功能混入类 - 可选择性启用功能

    使用方法：
    class Executor(EnhancedExecutorMixin):
        def __init__(self, state, store):
            super().__init__(state, store)
            self.enable_enhanced_position = True  # 启用增强持仓
            self.enable_profit_protection = True   # 启用盈利保护
    """

    def _should_use_enhanced(self):
        """检查是否应该使用增强功能"""
        return getattr(self, 'enable_enhanced_position', False)

    async def _open_enhanced(self, sig: dict, profile: str = "normal"):
        """增强版开仓 - 使用智能止损"""
        try:
            from position_enhanced import enhanced_initial_stop, EnhancedPosition

            # 复用原有开仓逻辑，只替换止损计算
            cfg, symbol = self.cfg, self.store.symbol
            from executor import tf_allowed
            extra = {
                "kind": "open", "tf": sig["tf"], "sig_ts": sig["ts"],
                "sig_type": sig["type"], "grade": sig.get("grade"),
                "trigger": sig["price"], "profile": profile,
            }
            if not tf_allowed(cfg, sig.get("tf")):
                err = f"周期 {sig.get('tf')} 不在允许范围 {cfg.allow_tfs}"
                logger.warning(f"[拒绝开仓] {symbol} {err}")
                return await self._record({"ok": False, "error": err, "price": sig.get("price")}, extra)

            rules = self.state.rules_for(profile, self.store.symbol)

            # 转换为增强规则（如果需要）
            if not hasattr(rules, 'sl_buffer_atr'):
                from position_enhanced import EnhancedExitRules
                from dataclasses import fields
                # 复制原规则的字段
                rule_dict = {f.name: getattr(rules, f.name) for f in fields(rules)}
                rules = EnhancedExitRules(**rule_dict)

            await self._ensure_leverage(symbol)

            # 清理挂单
            try:
                stale = await self.trade_module.cancel_pending(symbol, cfg.category, sim=cfg.paper)
                if stale.get("cancelled"):
                    logger.warning(f"[开仓前清挂单] {symbol} {stale['cancelled']}")
            except Exception as e:
                logger.warning(f"[开仓前清挂单失败] {symbol}: {e}")

            last = self.store.ticker.last or sig["price"]
            px = self.regime_module.limit_price(sig, cfg, last)
            side = "buy" if sig["type"] == "buy" else "sell"

            margin, how = await self._resolve_margin()
            if margin is None:
                logger.warning(f"[跳过开仓] {symbol} {how}")
                return await self._record({"ok": False, "error": how, "price": px}, extra)

            r = await self.trade_module.place_order(
                symbol, side, px,
                margin_usdt=margin, leverage=cfg.leverage,
                category=cfg.category, mgn_mode=cfg.margin_mode,
                order_type="ioc",
                client_oid=f"o{sig['tf']}{sig['ts']}",
                sim=cfg.paper, ref_price=last,
                wait_fill=True, wait_sec=8.0,
            )

            order = await self._record(r, extra)
            if not r.get("ok") or not r.get("qty"):
                return order

            # 使用增强版止损
            filled = {**sig, "price": r["price"]}
            stop = enhanced_initial_stop(filled, rules, sig.get("atr"))

            # 创建增强持仓
            self.store.position = EnhancedPosition(
                symbol=r.get("symbol") or symbol,
                side="long" if side == "buy" else "short",
                tf=sig["tf"], entry=r["price"], qty=r["qty"],
                init_qty=r["qty"], stop=stop, leverage=cfg.leverage,
                entry_ts=r["ts"], order_id=r.get("orderId", ""),
                ct_val=r.get("ct_val", 1), profile=profile,
            )

            unit = "张" if cfg.category == "SWAP" else ""
            tag = "快进快出档" if profile == "quick" else "标准档"
            self.store.position.log(
                "open",
                f"开{'多' if side == 'buy' else '空'} {r['qty']}{unit} @ {r['price']}"
                f"（增强版止损 {stop}，保证金 {how}，{tag}）",
            )

            logger.info(f"[持仓建立-增强] {symbol} {self.store.position.side} @ {r['price']} "
                       f"止损 {stop} (ATR缓冲) 档位 {profile}")

            await self._push_position()
            return order

        except ImportError:
            logger.warning("增强版持仓模块未找到，使用原版开仓")
            return await self._open(sig, profile)
        except Exception as e:
            logger.error(f"增强版开仓失败: {e}，回退原版")
            return await self._open(sig, profile)

    async def _on_price_enhanced(self, price: float):
        """增强版价格处理 - 多级止盈 + 盈利保护"""
        pos = self.store.position
        if not pos or pos.qty <= 0 or not price:
            return

        try:
            from position_enhanced import check_enhanced, EnhancedPosition

            # 更新最高未实现盈利
            if isinstance(pos, EnhancedPosition):
                pos.update_max_unrealized(price)
                max_unreal = pos.max_unrealized_pct
            else:
                max_unreal = None

            # 使用增强检查
            act = check_enhanced(pos, price, self.rules_of(pos), max_unreal)

            if not act:
                return

            async with self._lock:
                pos = self.store.position
                if not pos or pos.qty <= 0:
                    return

                # 重新检查
                if isinstance(pos, EnhancedPosition):
                    pos.update_max_unrealized(price)
                    max_unreal = pos.max_unrealized_pct
                else:
                    max_unreal = None

                act = check_enhanced(pos, price, self.rules_of(pos), max_unreal)
                if not act:
                    return

                if act["action"] in ["tp1", "tp2", "tp3"]:
                    await self._take_profit_staged(pos, price, act)
                else:
                    await self._close(pos, act["reason"], price)

        except ImportError:
            # 回退到原版
            await self.on_price(price)
        except Exception as e:
            logger.error(f"增强版价格处理失败: {e}")
            await self.on_price(price)

    async def _take_profit_staged(self, pos, price: float, act: dict):
        """分级止盈处理"""
        try:
            from position_enhanced import apply_tp_enhanced
            import trade

            # 计算平仓量
            spec = await self._spec_of(pos)
            qty = trade._snap(pos.qty * act["ratio"] / 100, spec["lot_sz"])

            if qty <= 0:
                logger.info(f"[跳过止盈] 应平 {act['ratio']:.0f}% 不足最小变动单位")
                return

            qty = min(qty, pos.qty)

            # 执行平仓
            r = await self._reduce(pos, qty, price, act["reason"])
            if not r.get("ok"):
                logger.warning(f"[止盈失败] {r.get('error')}")
                await self._record(r, {"kind": act["action"], "tf": pos.tf,
                                      "reason": act["reason"]})
                return

            # 提取阶段号
            stage = 1
            if act["action"] == "tp2":
                stage = 2
            elif act["action"] == "tp3":
                stage = 3

            # 应用止盈
            apply_tp_enhanced(pos, r["price"], r["qty"], self.rules_of(pos), stage)

            await self._record(r, {
                "kind": act["action"],
                "tf": pos.tf,
                "reason": act["reason"],
                "profile": pos.profile,
                "stage": stage
            })

            logger.info(f"[第{stage}档止盈] {pos.symbol} 平 {r['qty']} @ {r['price']}，"
                       f"止损→{pos.stop}")

            # 全平收尾
            if pos.qty <= 0:
                self._finalize(pos, r["price"], f"第{stage}档止盈全平")

            await self._push_position()

        except Exception as e:
            logger.error(f"分级止盈失败: {e}")


def create_enhanced_executor(state, store):
    """工厂函数：创建增强版执行器

    优先使用增强功能，失败时自动回退
    """
    try:
        from executor import Executor

        # 动态创建混入类
        class EnhancedExecutor(EnhancedExecutorMixin, Executor):
            def __init__(self, state, store):
                Executor.__init__(self, state, store)
                self.enable_enhanced_position = True
                self.enable_profit_protection = True
                self.trade_module = __import__('trade')
                self.regime_module = __import__('regime')

            async def on_signal(self, sig: dict, gate: dict):
                """与原版同一套周期硬闸门，开仓可走增强版。"""
                async with self._lock:
                    opener = (self._open_enhanced if self._should_use_enhanced()
                              else self._open)
                    return await self._handle_signal(sig, gate, opener)

            async def on_price(self, price: float):
                """优先使用增强版价格处理"""
                if self._should_use_enhanced():
                    await self._on_price_enhanced(price)
                else:
                    await Executor.on_price(self, price)

        return EnhancedExecutor(state, store)

    except Exception as e:
        logger.error(f"创建增强执行器失败: {e}，使用原版")
        from executor import Executor
        return Executor(state, store)


# 配置预设
PRESETS = {
    "btc": {
        "name": "BTC主流币配置",
        "exit_rules": {
            "tp1_pct": 0.8, "tp1_ratio": 30,
            "tp2_pct": 1.5, "tp2_ratio": 40,
            "tp3_pct": 3.0, "tp3_ratio": 30,
            "sl_min_pct": 1.0,
            "sl_buffer_atr": 0.5,
        },
        "regime": {
            "er_min": 0.15,
            "momentum_detection": True,
            "false_breakout_filter": True,
        }
    },
    "altcoin": {
        "name": "山寨币配置",
        "exit_rules": {
            "tp1_pct": 1.5, "tp1_ratio": 30,
            "tp2_pct": 3.0, "tp2_ratio": 40,
            "tp3_pct": 5.0, "tp3_ratio": 30,
            "sl_min_pct": 1.5,
            "sl_buffer_atr": 0.8,
        },
        "regime": {
            "er_min": 0.12,
            "momentum_detection": True,
            "false_breakout_filter": True,
            "adaptive_thresholds": True,
        }
    },
    "conservative": {
        "name": "保守配置",
        "exit_rules": {
            "tp1_pct": 0.6, "tp1_ratio": 40,
            "tp2_pct": 1.2, "tp2_ratio": 40,
            "tp3_pct": 2.0, "tp3_ratio": 20,
            "sl_min_pct": 0.8,
            "sl_buffer_atr": 0.3,
        },
        "regime": {
            "er_min": 0.20,
            "min_score": 2,
            "momentum_detection": True,
            "false_breakout_filter": True,
        }
    }
}


def apply_preset(state, symbol: str, preset_name: str):
    """应用预设配置到指定品种

    Args:
        state: AppState实例
        symbol: 品种代码，如 "BTC-USDT"
        preset_name: 预设名称，可选 "btc", "altcoin", "conservative"
    """
    if preset_name not in PRESETS:
        logger.error(f"未知预设: {preset_name}，可选: {list(PRESETS.keys())}")
        return False

    preset = PRESETS[preset_name]
    store = state.stores.get(symbol)

    if not store:
        logger.error(f"品种不存在: {symbol}")
        return False

    try:
        # 应用止盈止损规则
        from position_enhanced import EnhancedExitRules
        exit_cfg = preset["exit_rules"]
        store.exit_rules = EnhancedExitRules(**exit_cfg)

        # 应用闸门配置
        regime_cfg = preset["regime"]
        if store.cfg:
            for key, value in regime_cfg.items():
                if hasattr(store.cfg, key):
                    setattr(store.cfg, key, value)

        logger.info(f"[配置应用] {symbol} 使用预设: {preset['name']}")
        state.save_settings()
        return True

    except Exception as e:
        logger.error(f"应用预设失败: {e}")
        return False
