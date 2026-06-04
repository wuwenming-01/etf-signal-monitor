#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Railway + cron-job.org 云端部署版
支持云端运行和邮件发送
"""

import json
import os
import sqlite3
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
import warnings
warnings.filterwarnings('ignore')

import akshare as ak
import pandas as pd

# ========== 环境变量配置 ==========
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "1312319238@qq.com")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "skigptryotssfjce")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "1312319238@qq.com")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))

# ========== 常量配置 ==========
CONFIG = {
    "etf_code": "515980",
    "etf_name": "华富中证人工智能产业ETF",
    "index_code": "931071",
    "thresholds": {"buy": 75, "sell": 60}
}

# Railway环境变量
DB_PATH = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data") + "/signals.db"

def init_db():
    """初始化数据库"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS signals 
        (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, time TEXT, score REAL, 
         signal_type TEXT, etf_change REAL, rsi REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def get_etf_data():
    """获取ETF数据"""
    try:
        df = ak.fund_etf_spot_em()
        etf_df = df[df['代码'] == CONFIG['etf_code']]
        if etf_df.empty:
            return None
        return {
            "name": etf_df.iloc[0]['名称'],
            "change_pct": float(etf_df.iloc[0]['涨跌幅']),
            "turnover": float(etf_df.iloc[0]['成交额'])
        }
    except Exception as e:
        print(f"获取ETF数据失败: {e}")
        return None

def calculate_rsi(prices):
    """计算RSI"""
    if len(prices) < 15:
        return 50
    try:
        delta = pd.Series(prices).diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = (100 - 100 / (1 + rs)).iloc[-1]
        return float(rsi) if not pd.isna(rsi) else 50
    except:
        return 50

def calculate_factors(etf_info):
    """计算六因子"""
    change = etf_info['change_pct']
    turnover = etf_info['turnover']
    
    factors = {
        "景气因子": min(100, max(0, 50 + (5 if change >= 0 else -5))),
        "情绪因子": min(100, max(0, 50 + (15 if turnover > 1e8 else -10))),
        "性价比因子": min(100, max(0, 50 - (10 if change > 2 else -5))),
        "流动性因子": min(100, max(0, 50 + 10)),
        "风格因子": min(100, max(0, 50 + (10 if change > 1 else -5))),
        "舆情因子": min(100, max(0, 50 + 20))
    }
    return factors

def main():
    """主函数"""
    init_db()
    now = datetime.now()
    
    print(f"\n{'='*50}")
    print(f"  515980 ETF 六因子量化信号检测")
    print(f"  {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  运行环境: Railway Cloud")
    print(f"{'='*50}")
    
    # 获取ETF数据
    etf_info = get_etf_data()
    if not etf_info:
        print("❌ 获取数据失败")
        return {"status": "error"}
    
    print(f"✅ ETF: {etf_info['name']}")
    print(f"   涨跌幅: {etf_info['change_pct']}%")
    print(f"   成交额: {etf_info['turnover']/1e8:.2f}亿")
    
    # 计算RSI
    try:
        kline = ak.fund_etf_hist_sina(symbol=f"sh{CONFIG['etf_code']}").tail(60)
        prices = kline['close'].tolist()
        rsi = calculate_rsi(prices)
        print(f"✅ RSI: {rsi:.2f}")
    except Exception as e:
        print(f"⚠ RSI计算失败: {e}")
        rsi = 50
    
    # 计算因子
    factors = calculate_factors(etf_info)
    print("\n📊 六因子评分:")
    for name, value in factors.items():
        bar = "█" * int(value / 5) + "░" * (20 - int(value / 5))
        print(f"   {name:10s}: {value:5.1f} |{bar}|")
    
    # 加权评分
    weights = {'景气因子': 0.20, '情绪因子': 0.15, '性价比因子': 0.20,
               '流动性因子': 0.15, '风格因子': 0.15, '舆情因子': 0.15}
    score = sum(factors[k] * weights[k] for k in factors)
    
    # 生成信号
    if score > CONFIG['thresholds']['buy']:
        signal, signal_cn = "buy", "买入"
    elif score < CONFIG['thresholds']['sell']:
        signal, signal_cn = "sell", "卖出"
    else:
        signal, signal_cn = "hold", "观望"
    
    print(f"\n🎯 综合评分: {score:.1f} 分")
    print(f"📌 信号: 【{signal_cn}】")
    
    # 保存数据库
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('INSERT INTO signals (date, time, score, signal_type, etf_change, rsi) VALUES (?, ?, ?, ?, ?, ?)',
                    (now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), score, signal, etf_info['change_pct'], rsi))
        conn.commit()
        conn.close()
        print("✅ 数据已保存到数据库")
    except Exception as e:
        print(f"❌ 数据库保存失败: {e}")
    
    # 发送邮件
    if signal in ["buy", "sell"]:
        print(f"\n📧 发送邮件提醒...")
        success = send_email(score, signal_cn, factors, etf_info, rsi)
        if success:
            print("✅ 邮件发送成功!")
        else:
            print("❌ 邮件发送失败")
    
    result = {
        "status": "success",
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "score": round(score, 1),
        "signal": signal_cn,
        "factors": factors,
        "etf_info": etf_info,
        "rsi": round(rsi, 2)
    }
    
    print(f"\n{'='*50}")
    print(f"检测完成!")
    print(f"{'='*50}")
    
    return result

def send_email(score, signal, factors, etf_info, rsi):
    """发送邮件"""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = Header(f"【{signal}信号】515980 ETF量化信号提醒", 'utf-8')
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECEIVER
        
        color = 'green' if signal == '买入' else 'red'
        
        html = f"""
        <html><body>
        <h2>📊 515980 ETF 六因子量化信号</h2>
        <p><b>检测时间:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <p><b>运行环境:</b> Railway Cloud ☁️</p>
        <hr>
        <h3>🎯 综合评分: <span style="color:{color}">{score:.1f}分</span></h3>
        <h3>📌 信号类型: <span style="color:{color}">【{signal}】</span></h3>
        <hr>
        <h4>📊 六因子详情</h4>
        <table border="1" cellpadding="5">
            <tr><th>因子</th><th>评分</th></tr>
        """
        for k, v in factors.items():
            html += f"<tr><td>{k}</td><td>{v:.1f}</td></tr>"
        html += f"""
        </table>
        <hr>
        <h4>📈 市场数据</h4>
        <ul>
            <li>ETF: {etf_info['name']}</li>
            <li>涨跌幅: {etf_info['change_pct']}%</li>
            <li>成交额: {etf_info['turnover']/1e8:.2f}亿</li>
            <li>RSI: {rsi:.2f}</li>
        </ul>
        <hr>
        <p style="color:gray;font-size:12px">
        ⚠️ 本信号仅供参考，不构成投资建议。<br>
        股市有风险，投资需谨慎。
        </p>
        </body></html>
        """
        
        msg.attach(MIMEText(html, 'html', 'utf-8'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"邮件发送异常: {e}")
        return False

if __name__ == "__main__":
    main()
