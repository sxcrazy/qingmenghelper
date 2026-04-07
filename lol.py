import asyncio
import os
from lcu_driver import Connector
from lol_map import load_champion_map, load_spell_map

champion_map = load_champion_map()
spell_map = load_spell_map()

connector = Connector()

# ========== 辅助函数：打印阵容 ==========
def print_team_lineup(my_team, champion_map, spell_map):
    print("\n当前阵容：")
    for member in my_team:
        champ_id = member['championId']
        champ_name = champion_map.get(str(champ_id), "未选") if champ_id != 0 else "未选"
        s1 = spell_map.get(str(member['spell1Id']), "?")
        s2 = spell_map.get(str(member['spell2Id']), "?")
        print(f"{member['assignedPosition']:6} {member['gameName']:12} : {champ_name:10} ({s1}+{s2})")

# ========== 辅助函数：阵容指纹 ==========
def get_lineup_fingerprint(my_team):
    fingerprint = []
    for m in my_team:
        fingerprint.append((
            m['cellId'],
            m['championId'],
            m['spell1Id'],
            m['spell2Id']
        ))
    return tuple(fingerprint)

# ========== 战绩查询与评分（修正版） ==========
async def get_player_history(connection, player_puuid, player_name, label="玩家", champion_name=""):
    if not player_puuid:
        return
    
    endpoint = f'/lol-match-history/v1/products/lol/{player_puuid}/matches'
    resp = await connection.request('get', endpoint)
    if resp.status != 200:
        print(f"\n[提示] 无法获取 {label} {player_name} 的战绩 (Status: {resp.status})")
        return
    
    data = await resp.json()
    matches = data.get('games', {}).get('games', [])
    if not matches:
        print(f"\n[提示] {label} {player_name} 暂无对局记录")
        return
    
    # 计算最近20场的胜率和平均KDA
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
    # 避免除零：如果死亡数为0，KDA按 (击杀+助攻) 计算（或设为超大值）
    if total_deaths == 0:
        avg_kda = (total_kills + total_assists)  # 死亡0次，KDA就是击杀+助攻
    else:
        avg_kda = (total_kills + total_assists) / total_deaths
    
    # 按你的权重计算：胜率*0.3 + KDA*0.7
    # 注意胜率是百分比（0~100），KDA可能超过10，需要适当归一化或直接加权
    # 为了让两者数量级接近，可以将胜率除以10，KDA直接使用
    # 这里简单直接加权，你可以后续调整系数
    score = (win_rate * 0.3) + (avg_kda *10 * 0.7)
    
    # 根据分数输出标签（可自定义）
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
    print(f"\n[战绩] {label} {display_name} | 胜率 {win_rate:.1f}% | KDA {avg_kda:.2f} | 综合评分 {score:.1f} | 标签 {tag}")

async def monitor_one_game(connection):

    # 2. 等待进入选人阶段
    print("等待进入选人阶段...")
    while True:
        resp = await connection.request('get', '/lol-champ-select/v1/session')
        if resp.status == 200:
            session = await resp.json()
            queue_names = {440: "灵活排位", 420: "单排/双排", 450: "极地大乱斗", 470: "海克斯大乱斗"}
            print(f"当前模式：{queue_names.get(session['queueId'], '未知')}")
            # 获取当前阶段和倒计时（修复未定义 phase）
            phase = session['timer']['phase']
            time_left_ms = session['timer']['adjustedTimeLeftInPhase']
            print(f"当前阶段：{phase}，剩余 {time_left_ms//1000} 秒")
            break
        await asyncio.sleep(1)

    # ========== 阶段1：一次性查询所有队友战绩（并发） ==========
    my_team = session['myTeam']
    print("\n正在查询队友战绩...")
    tasks = []
    for member in my_team:
        puuid = member.get('puuid')
        if puuid:
            task = asyncio.create_task(get_player_history(connection, puuid, member['gameName'], "队友", champion_map.get(str(member.get('championId', 0)), "?")))
            tasks.append(task)
    if tasks:
        await asyncio.gather(*tasks)
    print("\n===== 队友战绩查询完毕 =====")

    # ========== 阶段2：监听阵容变化 ==========
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
        # 不清屏，只打印分隔线
            print("\n" + "="*50)
            print("--- 阵容已更新 ---")
            print_team_lineup(my_team, champion_map, spell_map)
            last_fingerprint = current_fingerprint
        await asyncio.sleep(0.5)

    # ========== 阶段3：游戏开始，查询敌方战绩 ==========
    print("\n选人结束，等待游戏加载...")
    while True:
        flow_resp = await connection.request('get', '/lol-gameflow/v1/session')
        if flow_resp.status == 200:
            flow = await flow_resp.json()
            phase = flow.get('phase')
            if phase == 'InProgress':
                print("游戏已开始，正在查询敌方战绩...")
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
                    print("未找到敌方玩家信息")
                break
            elif phase == 'None':
                print("对局取消或已结束")
                break
        await asyncio.sleep(1)
# ========== 主体 ==========
@connector.ready
async def connect(connection):
    print("成功连接到英雄联盟客户端！")

    # 1. 获取召唤师信息（只一次）
    resp_sum = await connection.request('get', '/lol-summoner/v1/current-summoner')
    if resp_sum.status == 200:
        data = await resp_sum.json()
        name = data.get('gameName')
        level = data.get('summonerLevel')
        number = data.get('tagLine')
        xpnow = data.get('xpSinceLastLevel')
        xpnext = data.get('xpUntilNextLevel')
        print(f"你好，召唤师：{name}#{number}，等级 {level}，距下级还需 {xpnext - xpnow}")
    else:
        print("获取召唤师信息失败")

    # 无限循环，持续监测对局
    while True:
        try:
            await monitor_one_game(connection)
            print("\n===== 监测结束，等待下一局开始 =====")
        except Exception as e:
            print(f"监测过程出错: {e}，5秒后重试...")
            await asyncio.sleep(5)

connector.start()