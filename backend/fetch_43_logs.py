# -*- coding: utf-8 -*-
"""从43服务器获取10-13到10-14的日志"""
import paramiko
import json
from datetime import datetime

# 43服务器连接信息
HOST = "43.108.10.84"
PORT = 22
USERNAME = "root"  # 你需要提供正确的用户名
# 密码或密钥路径需要用户提供

print("=" * 80)
print("连接43服务器获取AMD日志")
print("=" * 80)

print("\n请提供SSH连接信息:")
print("服务器: 43.108.10.84")
print("用户名: ", end="")
username = input() or "root"
print("密码: ", end="")
import getpass
password = getpass.getpass()

try:
    # 建立SSH连接
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, PORT, username, password, timeout=10)

    print("\n✓ 连接成功！")

    # 1. 查看日志目录结构
    print("\n" + "=" * 80)
    print("日志目录结构")
    print("=" * 80)

    stdin, stdout, stderr = client.exec_command("ls -lh /root/supertrendMain/backend/logs/")
    log_files = stdout.read().decode('utf-8')
    print(log_files)

    # 2. 查找10月的日志文件
    print("\n查找AMD相关的日志...")
    stdin, stdout, stderr = client.exec_command(
        "ls /root/supertrendMain/backend/logs/ | grep -E '(202610|amd|AMD)'"
    )
    amd_logs = stdout.read().decode('utf-8').strip().split('\n')
    print(f"找到 {len(amd_logs)} 个相关文件: {amd_logs}")

    # 3. 读取closed_202610.jsonl（已平仓交易）
    print("\n" + "=" * 80)
    print("读取 closed_202610.jsonl")
    print("=" * 80)

    stdin, stdout, stderr = client.exec_command(
        "cat /root/supertrendMain/backend/logs/closed_202610.jsonl"
    )
    closed_log = stdout.read().decode('utf-8')

    if closed_log.strip():
        print("\n10月已平仓交易:")
        for line in closed_log.strip().split('\n'):
            if line.strip():
                try:
                    trade = json.loads(line)
                    if 'AMD' in trade.get('symbol', ''):
                        print(json.dumps(trade, indent=2, ensure_ascii=False))
                except:
                    pass

    # 4. 查找10-13和10-14的信号日志
    print("\n" + "=" * 80)
    print("查找信号日志")
    print("=" * 80)

    # 尝试不同的日志文件名
    for log_name in ['signals_202610.log', 'signals.log', 'app.log', 'trade.log']:
        stdin, stdout, stderr = client.exec_command(
            f"test -f /root/supertrendMain/backend/logs/{log_name} && echo 'exists'"
        )
        if stdout.read().decode('utf-8').strip() == 'exists':
            print(f"\n找到日志文件: {log_name}")

            # 读取10-13和10-14的日志
            stdin, stdout, stderr = client.exec_command(
                f"grep -E '(2026-10-13|2026-10-14|10-13|10-14)' /root/supertrendMain/backend/logs/{log_name} | grep -i amd | head -200"
            )
            logs = stdout.read().decode('utf-8')
            if logs.strip():
                print(logs)

    # 5. 查看运行中的进程日志
    print("\n" + "=" * 80)
    print("查看运行中的进程")
    print("=" * 80)

    stdin, stdout, stderr = client.exec_command("ps aux | grep -E '(python|main|trade)' | grep -v grep")
    processes = stdout.read().decode('utf-8')
    print(processes)

    # 6. 检查是否有nohup.out或其他输出日志
    print("\n" + "=" * 80)
    print("查找其他可能的日志文件")
    print("=" * 80)

    stdin, stdout, stderr = client.exec_command(
        "cd /root/supertrendMain/backend && ls -lh *.log nohup.out 2>/dev/null || echo 'no log files'"
    )
    other_logs = stdout.read().decode('utf-8')
    print(other_logs)

    # 7. 尝试读取最近修改的日志文件
    stdin, stdout, stderr = client.exec_command(
        "find /root/supertrendMain/backend/logs -name '*.log' -o -name '*.jsonl' | xargs ls -lt | head -10"
    )
    recent_logs = stdout.read().decode('utf-8')
    print("\n最近修改的日志文件:")
    print(recent_logs)

    client.close()
    print("\n✓ 完成")

except paramiko.AuthenticationException:
    print("\n✗ SSH认证失败，请检查用户名和密码")
except paramiko.SSHException as e:
    print(f"\n✗ SSH连接错误: {e}")
except Exception as e:
    print(f"\n✗ 错误: {e}")
    import traceback
    traceback.print_exc()
