import asyncio
import os
from lcu_driver import Connector
from lol_map import load_champion_map, load_spell_map

champion_map = load_champion_map()
spell_map = load_spell_map()
connector = Connector()

log_queue = []
is_monitoring = False

def gui_print(*args, **kwargs):
    sep = kwargs.get('sep', ' ')
    end = kwargs.get('end', '\n')
    text = sep.join(str(arg) for arg in args) + end
    log_queue.append(text)

def print_team_lineup(my_team, champion_map, spell_map):
    gui_print("\n当前阵容：")
    for member in my_team:
        champ_id = member.get('championId', 0)
        champ_name = champion_map.get(str(champ_id), "未选") if champ_id != 0 else "未选"
        s1 = spell_map.get(str(member.get('spell1Id', 0)), "?")
        s2 = spell_map.get(str(member.get('spell2Id', 0)), "?")
        pos = member.get('assignedPosition', '')
        gname = member.get('gameName', member.get('summonerName', ''))
        gui_print(f"{pos:6} {gname[:12]:12} : {champ_name:10} ({s1}+{s2})")

def get_lineup_fingerprint(my_team):
    fingerprint = []
    for m in my_team:
        fingerprint.append((m.get('cellId'), m.get('championId'), m.get('spell1Id'), m.get('spell2Id')))
    return tuple(fingerprint)

async def get_player_history(connection, player_puuid, player_name, label="玩家", champion_name=""):
    if not player_puuid:
        return
    try:
        endpoint = f'/lol-match-history/v1/products/lol/{player_puuid}/matches'
        resp = await connection.request('get', endpoint)
        if resp.status != 200:
            gui_print(f"[提示] 无法获取 {label} {player_name} 的战绩 (Status: {resp.status})")
            return
        data = await resp.json()
        matches = data.get('games', {}).get('games', [])
        game_count = min(20, len(matches))
        if game_count == 0:
            gui_print(f"[提示] {label} {player_name} 暂无对局记录")
            return
            
        total_wins = 0
        total_kills = total_deaths = total_assists = 0
            
        for match in matches[:game_count]:
            participant = match['participants'][0]
            stats = participant['stats']
            if stats.get('win'):
                total_wins += 1
            total_kills += stats.get('kills', 0)
            total_deaths += stats.get('deaths', 0)
            total_assists += stats.get('assists', 0)
            
        win_rate = total_wins / game_count * 100
        avg_kda = float(total_kills + total_assists) if total_deaths == 0 else (total_kills + total_assists) / total_deaths
            
        score = (win_rate * 0.3) + (avg_kda * 10 * 0.7)
        if score >= 80: tag = "通天代"
        elif score >= 65: tag = "小代"
        elif score >= 50: tag = "上等马"
        elif score >= 35: tag = "中等马"
        elif score >= 20: tag = "下等马"
        else: tag = "牛马"
            
        display_name = f"[{champion_name}] {player_name}" if (label == "敌方" and champion_name and champion_name != "?") else f"{player_name}"
        gui_print(f"[战绩] {label} {display_name} | 胜率 {win_rate:.1f}% | KDA {avg_kda:.2f} | 评分 {score:.1f} | {tag}")
    except Exception as e:
        gui_print(f"[提示] 查询 {label} {player_name} 时发生错误")

async def monitor_one_game(connection):
    global is_monitoring
    gui_print("\n等待进入选人阶段...")
    
    # 加入检测断路器
    error_count = 0
    while is_monitoring:
        try:
            resp = await connection.request('get', '/lol-gameflow/v1/session')
            error_count = 0  # 只要成功一次，证明客户端没死，立刻清零
            if resp.status == 200:
                data = await resp.json()
                if data.get('phase') == 'ChampSelect':
                    break
        except Exception:
            error_count += 1 # 没拿到数据，错误+1
            if error_count >= 3: # 连续3秒拿不到（说明你关了客户端）
                is_monitoring = False
                gui_print("\n[系统] 检测到客户端已关闭，停止监控。")
                return
        await asyncio.sleep(1)

    if not is_monitoring:
        return

    # 2. 获取选人信息
    try:
        resp = await connection.request('get', '/lol-champ-select/v1/session')
        if resp.status == 200:
            session = await resp.json()
            my_team = session.get('myTeam', [])
            
            queue_names = {440: "灵活排位", 420: "单排/双排", 450: "极地大乱斗", 470: "海克斯大乱斗", 430: "匹配模式"}
            q_id = session.get('queueId')
            if not q_id:
                try:
                    q_resp = await connection.request('get', '/lol-gameflow/v1/session')
                    if q_resp.status == 200:
                        q_data = await q_resp.json()
                        q_id = q_data.get('gameData', {}).get('queue', {}).get('id', 0)
                except Exception:
                    pass
            gui_print(f"\n当前模式：{queue_names.get(q_id, f'未知({q_id})')}")
            
            gui_print("正在查询队友战绩...")
            tasks = []
            for member in my_team:
                puuid = member.get('puuid')
                if puuid:
                    champ_name = champion_map.get(str(member.get('championId', 0)), "?")
                    name = member.get('gameName', member.get('summonerName', '未知'))
                    tasks.append(asyncio.create_task(get_player_history(connection, puuid, name, "队友", champ_name)))
            if tasks:
                await asyncio.gather(*tasks)
            gui_print("===== 队友战绩查询完毕 =====")

            print_team_lineup(my_team, champion_map, spell_map)
            last_fingerprint = get_lineup_fingerprint(my_team)
        else:
            return
    except Exception:
        return

    # 3. 监测阵容 #新加入断路器
    error_count = 0
    while is_monitoring:
        try:
            flow_resp = await connection.request('get', '/lol-gameflow/v1/session')
            error_count = 0
            if flow_resp.status != 200:
                await asyncio.sleep(1)
                continue
                
            flow = await flow_resp.json()
            phase = flow.get('phase')
            
            if phase == 'ChampSelect':
                cs_resp = await connection.request('get', '/lol-champ-select/v1/session')
                if cs_resp.status == 200:
                    session = await cs_resp.json()
                    my_team = session.get('myTeam', [])
                    current_fingerprint = get_lineup_fingerprint(my_team)
                    if current_fingerprint != last_fingerprint:
                        gui_print("\n" + "="*40)
                        gui_print("--- 阵容已更新 ---")
                        print_team_lineup(my_team, champion_map, spell_map)
                        last_fingerprint = current_fingerprint
                await asyncio.sleep(0.5)
                
            elif phase in ['GameStart', 'InProgress']:
                gui_print("\n选人结束，游戏已开始，正在查询敌方战绩...")
                game_data = flow.get('gameData', {})
                all_players = game_data.get('teamOne', []) + game_data.get('teamTwo', [])
                my_puuids = {m.get('puuid') for m in my_team if m.get('puuid')}
                
                enemy_tasks = []
                for player in all_players:
                    puuid = player.get('puuid')
                    if puuid and puuid not in my_puuids:
                        name = player.get('summonerName', '未知')
                        champ_name = champion_map.get(str(player.get('championId', 0)), "?")
                        enemy_tasks.append(asyncio.create_task(get_player_history(connection, puuid, name, "敌方", champ_name)))
                
                if enemy_tasks:
                    await asyncio.gather(*enemy_tasks)
                    gui_print("===== 敌方战绩查询完毕 =====")
                else:
                    gui_print("未找到敌方玩家信息（可能是训练营或自定义）")
                    
                gui_print("\n正在监控对局状态，请尽情游戏...")
                
                # 4. 等待这局游戏结束 #新加入断路器
                in_game_errors = 0
                while is_monitoring:
                    try:
                        end_resp = await connection.request('get', '/lol-gameflow/v1/session')
                        in_game_errors = 0
                        if end_resp.status == 200:
                            end_flow = await end_resp.json()
                            if end_flow.get('phase') not in ['GameStart', 'InProgress', 'Reconnect']:
                                gui_print(f"\n对局已结束，准备下一场...")
                                break
                        else:
                            break
                    except Exception:
                        in_game_errors += 1
                        if in_game_errors >= 3:
                            is_monitoring = False
                            break
                    await asyncio.sleep(3)
                break
                
            elif phase in ['Lobby', 'Matchmaking', 'ReadyCheck', 'None']:
                gui_print(f"\n有人秒退或对局未开始，重新等待...")
                break
            else:
                await asyncio.sleep(1)
        except Exception:
            error_count += 1
            if error_count >= 3:
                is_monitoring = False
                gui_print("\n[系统] 检测到客户端已关闭，停止监控。")
                return
            await asyncio.sleep(2)

@connector.close
async def disconnect(_):
    global is_monitoring
    is_monitoring = False

@connector.ready
async def connect(connection):
    global is_monitoring
    is_monitoring = True
    gui_print("\n成功连接到英雄联盟客户端！")
    
    max_retries = 3          
    for attempt in range(1, max_retries + 1):
        if not is_monitoring:
            return
        try:
            resp_sum = await connection.request('get', '/lol-summoner/v1/current-summoner')
            if resp_sum.status == 200:
                data = await resp_sum.json()
                name = data.get('gameName', data.get('displayName', '未知'))
                level = data.get('summonerLevel', 0)
                tagLine = data.get('tagLine','未知')
                xpnow = data.get('xpSinceLastLevel', 0)
                xpnext = data.get('xpUntilNextLevel', 0)
                gui_print(f"你好，召唤师：{name}#{tagLine}，等级 {level}，目前经验值{xpnow}，距离下级还需{xpnext-xpnow}")
                break          
        except Exception:
            pass
        await asyncio.sleep(3)

    # 主循环调度
    while is_monitoring:
        await monitor_one_game(connection)
        
    gui_print("\n[系统] 核心监控循环已退出，等待重新连接客户端...")

def run_monitor():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    connector.start()
