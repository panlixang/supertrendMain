# 查看43服务器10-13到10-14日志的方法

## 方法1: SSH直接登录服务器

```bash
# 登录43服务器
ssh root@43.108.10.84

# 进入日志目录
cd /root/supertrendMain/backend/logs/

# 查看目录下的所有文件
ls -lh

# 查看10月的已平仓交易日志
cat closed_202610.jsonl

# 查找AMD相关的交易
grep AMD closed_202610.jsonl

# 如果有信号日志文件，查看10-13和10-14的信号
grep -E "2026-10-13|2026-10-14" *.log | grep -i amd

# 查看主程序的运行日志（如果有）
tail -n 500 nohup.out | grep -i amd
```

## 方法2: 通过Python脚本远程获取（需要paramiko库）

```bash
# 安装paramiko
pip install paramiko

# 运行获取日志脚本
python fetch_43_logs.py
```

## 方法3: 使用SCP复制日志文件到本地

```bash
# 复制整个日志目录到本地
scp -r root@43.108.10.84:/root/supertrendMain/backend/logs/ ./43_logs/

# 或者只复制10月的日志
scp root@43.108.10.84:/root/supertrendMain/backend/logs/closed_202610.jsonl ./
```

## 需要查找的关键信息

1. **AMD在10-14的交易记录**
   - 开仓时间（应该在10-14 04:00左右）
   - 开仓价格（606.93）
   - 是买入(long)还是卖出(short)
   - 信号的评分(score)和等级(grade)

2. **信号产生的原因**
   - ER值是多少
   - 评分系统是否工作正常
   - 是否有过滤器拒绝信息

3. **与本地差异的原因**
   - 43服务器使用的K线数据源
   - 配置是否与API返回的一致
   - 代码版本是否与本地相同

## 预期在日志中找到什么

根据你的截图数据：
- 开仓时间：2026-10-14 04:00:01
- 开仓均价：606.93 USDT
- 平仓均价：591.77 USDT
- 持仓数量：1.65 AMD
- 盈亏：-26.3275 USDT

在日志中应该能找到这笔交易的详细信息，包括：
- 为什么会在04:00产生多单信号
- 这个信号的评分是多少（本地计算是1分，应该被拒绝）
- 是否有任何错误或异常信息
