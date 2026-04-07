import tkinter as tk
from tkinter import scrolledtext
import threading
import asyncio
import sys
import io
from lcu_driver import Connector
from lol_map import load_champion_map, load_spell_map

#全局变量

champion_map = load_champion_map()
spell_map = load_spell_map()
connector = Connector()

# 将日志发送到 GUI 的队列
log_queue = []

def gui_print(*args, **kwargs):
    # 将 print 的内容转为字符串
    sep = kwargs.get('sep', ' ')
    end = kwargs.get('end', '\n')
    text = sep.join(str(arg) for arg in args) + end
    log_queue.append(text)

def print_team_lineup(my_team, champion_map, spell_map):
    gui_print("\n当前阵容：")
    for member in my_team:
        champ_id = member['championId']
        champ_name = champion_map.get(str(champ_id), "未选") if champ_id != 0 else "未选"
        s1 = spell_map.get(str(member['spell1Id']), "?")
        s2 = spell_map.get(str(member['spell2Id']), "?")
        gui_print(f"{member['assignedPosition']:6} {member['gameName']:12} : {champ_name:10} ({s1}+{s2})")

def get_lineup_fingerprint(my_team):
    fingerprint = []
    for m in my_team:
        fingerprint.append((m['cellId'], m['championId'], m['spell1Id'], m['spell2Id']))
    return tuple(fingerprint)

async def get_player_history(connection, player_puuid, player_name, label="玩家", champion_name=""):
    if not player_puuid:
        return
    endpoint = f'/lol-match-history/v1/products/lol/{player_puuid}/matches'
    resp = await connection.request('get', endpoint)
    if resp.status != 200:
        gui_print(f"\n[提示] 无法获取 {label} {player_name} 的战绩 (Status: {resp.status})")
        return
    data = await resp.json()
    matches = data.get('games', {}).get('games', [])
    if not matches:
        gui_print(f"\n[提示] {label} {player_name} 暂无对局记录")
        return
    total_wins = 0
    total_kills = total_deaths = total_assists = 0
    game_count = min(20, len(matches))
    for match in matches[:game_count]:
        participant = match['participants'][0]
        stats = participant['stats']
        if stats['win']:
            total_wins += 1
        total_kills += stats['kills']
        total_deaths += stats['deaths']
        total_assists += stats['assists']
    win_rate = total_wins / game_count * 100
    if total_deaths == 0:
        avg_kda = (total_kills + total_assists)
    else:
        avg_kda = (total_kills + total_assists) / total_deaths
    score = (win_rate * 0.3) + (avg_kda * 10 * 0.7)
    if score >= 80:
        tag = "通天代"
    elif score >= 65:
        tag = "小代"
    elif score >= 50:
        tag = "上等马"
    elif score >= 35:
        tag = "中等马"
    elif score >= 20:
        tag = "下等马"
    else:
        tag = "牛马"
    if label == "敌方" and champion_name:
        display_name = f"{label} {champion_name}"
    else:
        display_name = f"{label} {player_name}"
    gui_print(f"\n[战绩] {label} {display_name} | 胜率 {win_rate:.1f}% | KDA {avg_kda:.2f} | 综合评分 {score:.1f} | 标签 {tag}")

async def monitor_one_game(connection):
    gui_print("等待进入选人阶段...")
    while True:
        resp = await connection.request('get', '/lol-champ-select/v1/session')
        if resp.status == 200:
            session = await resp.json()
            queue_names = {440: "灵活排位", 420: "单排/双排", 450: "极地大乱斗", 470: "海克斯大乱斗"}
            gui_print(f"当前模式：{queue_names.get(session['queueId'], '未知')}")
            phase = session['timer']['phase']
            time_left_ms = session['timer']['adjustedTimeLeftInPhase']
            gui_print(f"当前阶段：{phase}，剩余 {time_left_ms//1000} 秒")
            break
        await asyncio.sleep(1)

    my_team = session['myTeam']
    gui_print("\n正在查询队友战绩...")
    tasks = []
    for member in my_team:
        puuid = member.get('puuid')
        if puuid:
            task = asyncio.create_task(get_player_history(connection, puuid, member['gameName'], "队友", champion_map.get(str(member.get('championId', 0)), "?")))
            tasks.append(task)
    if tasks:
        await asyncio.gather(*tasks)
    gui_print("\n===== 队友战绩查询完毕 =====")

    print_team_lineup(my_team, champion_map, spell_map)
    last_fingerprint = get_lineup_fingerprint(my_team)

    while True:
        resp = await connection.request('get', '/lol-champ-select/v1/session')
        if resp.status != 200:
            break
        session = await resp.json()
        my_team = session['myTeam']
        current_fingerprint = get_lineup_fingerprint(my_team)
        if current_fingerprint != last_fingerprint:
            gui_print("\n" + "="*50)
            gui_print("--- 阵容已更新 ---")
            print_team_lineup(my_team, champion_map, spell_map)
            last_fingerprint = current_fingerprint
        await asyncio.sleep(0.5)

    gui_print("\n选人结束，等待游戏加载...")
    while True:
        flow_resp = await connection.request('get', '/lol-gameflow/v1/session')
        if flow_resp.status == 200:
            flow = await flow_resp.json()
            phase = flow.get('phase')
            if phase == 'InProgress':
                gui_print("游戏已开始，正在查询敌方战绩...")
                game_data = flow.get('gameData', {})
                all_players = game_data.get('teamOne', []) + game_data.get('teamTwo', [])
                my_puuids = {m['puuid'] for m in my_team}
                enemy_tasks = []
                for player in all_players:
                    player_name = player.get('summonerName', '未知')
                    puuid = player.get('puuid')
                    if puuid and puuid not in my_puuids:
                        enemy_tasks.append(asyncio.create_task(get_player_history(connection, puuid, player_name, "敌方", champion_map.get(str(player.get('championId', 0)), "?"))))
                if enemy_tasks:
                    await asyncio.gather(*enemy_tasks)
                else:
                    gui_print("未找到敌方玩家信息")
                break
            elif phase == 'None':
                gui_print("对局取消或已结束")
                break
        await asyncio.sleep(1)

@connector.ready
async def connect(connection):
    gui_print("成功连接到英雄联盟客户端！")
    resp_sum = await connection.request('get', '/lol-summoner/v1/current-summoner')
# 1. 获取召唤师信息（带重试机制）
    max_retries = 3          # 最多尝试3次
    retry_delay = 3          # 每次失败后等待3秒再试

    for attempt in range(1, max_retries + 1):
        resp_sum = await connection.request('get', '/lol-summoner/v1/current-summoner')
        if resp_sum.status == 200:
            data = await resp_sum.json()
            name = data.get('gameName')
            level = data.get('summonerLevel')
            number = data.get('tagLine')
            xpnow = data.get('xpSinceLastLevel')
            xpnext = data.get('xpUntilNextLevel')
            print(f"你好，召唤师：{name}#{number}，等级 {level}，距下级还需 {xpnext - xpnow} 经验值")
            break          # 成功获取，跳出重试循环
        else:
            print(f"第 {attempt} 次获取召唤师信息失败 (状态码: {resp_sum.status})，{retry_delay}秒后重试...")
            if attempt < max_retries:
                await asyncio.sleep(retry_delay)
            else:
                print("多次获取召唤师信息失败，请检查客户端是否正常登录")
                return     # 彻底退出 connect 函数

def run_monitor():
    """在后台线程中运行 connector"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    connector.start()   # 启动并阻塞在线程中

class AutoMonitorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("清梦-英雄联盟助手beta0.1")
        self.root.geometry("800x600")

        # 创建文本框显示日志
        self.log_text = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Consolas", 10))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 底部状态栏
        self.status_var = tk.StringVar()
        self.status_var.set("状态：未连接客户端")
        status_bar = tk.Label(root, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # 启动监测线程
        self.monitor_thread = threading.Thread(target=run_monitor, daemon=True)
        self.monitor_thread.start()

        # 定时从队列中取日志并显示
        self.update_log()

    def update_log(self):
        # 将队列中的所有日志一次性显示
        while log_queue:
            line = log_queue.pop(0)
            self.log_text.insert(tk.END, line)
            self.log_text.see(tk.END)  # 自动滚动到底部
        # 更新状态
        self.status_var.set("状态：监测中...")
        self.root.after(100, self.update_log)

if __name__ == "__main__":
    root = tk.Tk()
    app = AutoMonitorGUI(root)
    root.mainloop()
