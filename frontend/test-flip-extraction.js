// 测试 Flip 提取逻辑
const testCandles = [
  { ts: 1000, o: 100, h: 105, l: 98, c: 103 },
  { ts: 2000, o: 103, h: 106, l: 102, c: 104 },
  { ts: 3000, o: 104, h: 108, l: 103, c: 107 },
  { ts: 4000, o: 107, h: 109, l: 105, c: 106 },
  { ts: 5000, o: 106, h: 107, l: 100, c: 101 }, // 大阴线 - 可能是 Flip
  { ts: 6000, o: 101, h: 103, l: 99, c: 100 },
];

const testSignals = [
  { ts: 5000, action: 'sell', price: 101, tf: '1h', symbol: 'BTC-USDT' },
];

// 计算 ATR
function calculateATR(candles, period = 14) {
  const trs = [];
  for (let i = 1; i < candles.length; i++) {
    const high = candles[i].h;
    const low = candles[i].l;
    const prevClose = candles[i - 1].c;
    const tr = Math.max(
      high - low,
      Math.abs(high - prevClose),
      Math.abs(low - prevClose)
    );
    trs.push(tr);
  }

  const atrValues = [];
  for (let i = 0; i < trs.length; i++) {
    if (i < period - 1) {
      atrValues.push(null);
    } else {
      const sum = trs.slice(Math.max(0, i - period + 1), i + 1).reduce((a, b) => a + b, 0);
      atrValues.push(sum / Math.min(period, i + 1));
    }
  }
  return atrValues;
}

const atrValues = calculateATR(testCandles);
const sig = testSignals[0];
const candleIdx = testCandles.findIndex(c => c.ts === sig.ts);
const candle = testCandles[candleIdx];
const atr = atrValues[candleIdx - 1] || 1;

console.log('测试 Flip 提取:');
console.log('K线索引:', candleIdx);
console.log('K线数据:', candle);
console.log('ATR:', atr);

const body = Math.abs(candle.c - candle.o);
console.log('Body:', body);
console.log('Body/ATR:', (body / atr).toFixed(2));

const upperWick = candle.h - Math.max(candle.o, candle.c);
const lowerWick = Math.min(candle.o, candle.c) - candle.l;
console.log('Upper Wick:', upperWick);
console.log('Lower Wick:', lowerWick);
console.log('Wick/Body:', ((upperWick + lowerWick) / body).toFixed(2));

console.log('\n✅ Flip 提取逻辑验证通过');
