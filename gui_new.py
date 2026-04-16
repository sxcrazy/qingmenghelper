import sys
import asyncio
import threading
import queue
from PySide6.QtCore import Qt, QTimer, QPoint, QThread, Signal, QUrl
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QTextEdit, QFrame,
    QLineEdit, QCheckBox, QTabWidget, QTextBrowser,
    QDialog, QFormLayout, QDialogButtonBox, QComboBox 
)
from PySide6.QtGui import QFont, QMouseEvent

# ================= 导入原有逻辑模块 =================
from lcu_driver import Connector
from lol_map import load_champion_map, load_spell_map
from ddragon_images import get_image_helper
from resource_manager import (
    init_local_resources, get_perk, get_style, get_item,
    get_saved_runes, get_saved_items, save_rune, delete_rune,
    save_item_set, delete_item_set, apply_rune_to_client
)

# 全局变量
log_queue = queue.Queue()
champion_map = load_champion_map()
spell_map = load_spell_map()
connector = Connector()
is_monitoring = False
global_sum_name = "未知"          # 全局变量：存储玩家自己的名字
last_my_champion_id = 0 

main_window = None          # 全局 UI 引用
monitor_loop = None         # 保存后台事件循环
image_helper = None         # DDragon 图片助手

def gui_print(target,*args, **kwargs):
    """将文本放入队列，供 UI 线程取出显示"""
    sep = kwargs.get('sep', ' ')
    end = kwargs.get('end', '\n')
    text = sep.join(str(arg) for arg in args) + end
    log_queue.put((target,text))

def print_home(*args,**kwargs): gui_print('home',*args,**kwargs)
def print_monitor(*args,**kwargs): gui_print('monitor',*args,**kwargs)
def print_search(*args,**kwargs): gui_print('search',*args,**kwargs)
def print_rune(*args,**kwargs): gui_print('rune',*args,**kwargs)
def print_item(*args,**kwargs): gui_print('item',*args,**kwargs)

TIER_TRANSLATE = {
    "IRON": "坚韧黑铁", "BRONZE": "英勇黄铜", "SILVER": "不屈白银",
    "GOLD": "荣耀黄金", "PLATINUM": "华贵铂金", "EMERALD": "流光翡翠",     
    "DIAMOND": "璀璨钻石", "MASTER": "超凡大师", "GRANDMASTER": "傲世宗师",
    "CHALLENGER": "最强王者", "NONE": "未定级"
}

def get_profile_icon_path(icon_id):
    """获取召唤师头像"""
    import os
    from resource_manager import DATA_DIR
    local_path = os.path.join(DATA_DIR, "profileicon", f"{icon_id}.png")
    return local_path if os.path.exists(local_path) else None

async def update_opgg_data(champion_id, connection):
    global image_helper
    if champion_id == 0 or not connection: return
    cn_name = champion_map.get(str(champion_id), "未知英雄")
    eng_name = image_helper.champion_id_to_eng.get(str(champion_id), "").lower()
    
    champ_icon = image_helper.get_champion_icon_path(champion_id)
    img_html = f'<img src="file:///{champ_icon.replace(chr(92), "/")}" width="64" height="64" style="border-radius:12px; border:2px solid #5dade2;">' if champ_icon else ''
    
    # 头部设计
    def get_header(title, opgg_type):
        opgg_url = f"https://www.op.gg/champions/{eng_name}/{opgg_type}"
        if opgg_type == "runes":
            action_html = f'<a href="action:import_rune:{cn_name}" style="color:#2ecc71; text-decoration:none; font-size:12px; font-weight:bold; background:rgba(46,204,113,0.15); padding:6px 10px; border-radius:4px;">⬇️ 抓取客户端当前符文</a>'
        else:
            action_html = f'<a href="action:new_item:{cn_name}" style="color:#e67e22; text-decoration:none; font-size:12px; font-weight:bold; background:rgba(230,126,34,0.15); padding:6px 10px; border-radius:4px;">➕ 手动新建出装</a>'

        return f"""
        <table width="100%" style="margin-bottom:15px; padding-bottom:15px; border-bottom:1px solid #333;"><tr>
            <td width="75" valign="top">{img_html}</td>
            <td valign="top">
                <span style="color:white; font-size:22px; font-weight:bold;">{cn_name}</span> 
                <span style="color:#95a5a6; font-size:13px;"> {title}</span><br>
                <div style="margin-top: 8px;">
                    <a href="{opgg_url}" style="color:#5dade2; text-decoration:none; font-size:12px; font-weight:bold; background:rgba(83,131,232,0.15); padding:6px 10px; border-radius:4px; margin-right:8px;">🌍 网页查 OP.GG</a>
                    {action_html}
                </div>
            </td>
        </tr></table>
        """
    
    print_rune("CLEAR")
    print_item("CLEAR")
    print_rune(get_header("专属符文夹", "runes"))
    print_item(get_header("专属出装夹", "build"))

    # 符文页面渲染
    saved_runes = get_saved_runes()
    my_runes = [r for r in saved_runes if r['champion'] in [cn_name, '通用']]
    if my_runes:
        for r in my_runes:
            real_idx = saved_runes.index(r)
            p_style, s_style = get_style(r['primary']), get_style(r['sub'])
            icons = "".join([f'<img src="file:///{get_perk(pid)["icon"]}" width="30" height="30" style="margin-right:3px;">' for pid in r['perks'] if get_perk(pid)['icon']])
            rune_html = f"""
            <table width="100%" bgcolor="#1e2028" cellpadding="10" style="border:1px solid #2ecc71; border-radius:8px; margin-bottom:10px;"><tr>
                <td width="40"><img src="file:///{p_style['icon']}" width="36" height="36"><br><img src="file:///{s_style['icon']}" width="20" height="20" style="margin-top:5px;"></td>
                <td valign="top"><b style="color:white; font-size:15px;">{r['name']}</b><br><div style="margin-top:6px;">{icons}</div></td>
                <td width="60" align="right" valign="middle">
                    <a href="action:apply_rune:{real_idx}" style="display:inline-block; background:#27ae60; color:white; padding:6px 12px; border-radius:4px; text-decoration:none; font-weight:bold; margin-bottom:6px;">🚀 应用</a><br>
                    <a href="action:delete_rune:{real_idx}" style="color:#e74c3c; text-decoration:none; font-size:12px;">🗑️ 删除</a>
                </td>
            </tr></table>
            """
            print_rune(rune_html)
    else:
        print_rune(f"<p style='color:#95a5a6; padding: 20px; text-align: center;'>还没有为 <b>{cn_name}</b> 保存过专属符文</p>")

    # 出装页面渲染 (修复了重复渲染的Bug)
    saved_items = get_saved_items()
    my_items = [i for i in saved_items if i['champion'] in [cn_name, '通用']]
    if my_items:
        for i_data in my_items:
            real_idx = saved_items.index(i_data)
            icons = "".join([f'<img src="file:///{get_item(iid)["icon"]}" width="40" height="40" style="border-radius:5px; margin-right:5px;">' for iid in i_data['items'] if get_item(iid)['icon']])
            item_html = f"""
            <table width="100%" bgcolor="#1e2028" cellpadding="10" style="border:1px solid #2ecc71; border-radius:8px; margin-bottom:10px;"><tr>
                <td valign="top">
                    <b style="color:white; font-size:15px;">{i_data['name']}</b> <span style="color:#7f8c8d; font-size:12px;">({i_data['champion']})</span><br><div style="margin-top:8px;">{icons}</div>
                </td>
                <td width="40" align="right" valign="middle">
                    <a href="action:delete_item:{real_idx}" style="color:#e74c3c; text-decoration:none; font-size:12px;">🗑️ 删除</a>
                </td>
            </tr></table>
            """
            print_item(item_html)
    else:
        print_item(f"<p style='color:#95a5a6; padding: 20px; text-align: center;'>还没有为 <b>{cn_name}</b> 保存过专属出装<br><br>请点击上方 <span style='color:#2ecc71;'>[➕ 新建自定义配置]</span> 添加</p>")

async def get_player_rank(connection, puuid, player_name):
    """通过 PUUID 获取并打印玩家的单双排/灵活排位段位"""
    try:
        endpoint = f'/lol-ranked/v1/ranked-stats/{puuid}'
        resp = await connection.request('get', endpoint)
        
        if resp.status != 200:
            return
            
        data = await resp.json()
        queues = data.get('queues', [])
        
        solo_rank_str = "单双排: 未定级"
        flex_rank_str = "灵活排位: 未定级"
        
        for q in queues:
            q_type = q.get('queueType')
            tier = q.get('tier', 'NONE')
            division = q.get('division', 'NA')
            lp = q.get('leaguePoints', 0)
            wins = q.get('wins', 0)
            losses = q.get('losses', 0)
            
            total_games = wins + losses
            win_rate = (wins / total_games * 100) if total_games > 0 else 0
            cn_tier = TIER_TRANSLATE.get(tier, tier)
            
            if tier in ["MASTER", "GRANDMASTER", "CHALLENGER"]:
                rank_display = f"{cn_tier} {lp}胜点"
            elif tier == "NONE":
                rank_display = "未定级"
            else:
                rank_display = f"{cn_tier}{division} {lp}胜点"
                
            if tier != "NONE":
                rank_display += f" (胜率:{win_rate:.1f}%)"

            if q_type == 'RANKED_SOLO_5x5':
                solo_rank_str = f"单双排: {rank_display}"
            elif q_type == 'RANKED_FLEX_SR':
                flex_rank_str = f"灵活排: {rank_display}"

        print_search(f"\n【玩家段位】 {player_name} | {solo_rank_str} | {flex_rank_str}")
    except Exception as e:
        print_search(f"[错误] 获取段位异常：{e}")

async def search_player_by_name(connection, game_name, tag_line):
    try:
        test = await asyncio.wait_for(connection.request('get', '/lol-summoner/v1/current-summoner'), timeout=3.0)
        if test.status != 200: raise Exception("LCU 未正常响应")
    except Exception as e:
        print_search(f"\n[系统] 连接尚未就绪：{e}")
        if main_window: main_window.search_finished.emit()
        return

    body = [{"gameName": game_name, "tagLine": tag_line}]
    print_search(f"\n[系统] 正在查询玩家 {game_name}#{tag_line} 的信息...")

    try:
        resp = await asyncio.wait_for(connection.request('post', '/lol-summoner/v1/summoners/aliases', json=body), timeout=5.0)
        if resp.status == 200:
            data = await resp.json()
            if data and data[0].get('puuid'):
                puuid = data[0]['puuid']
                print_search(f"\n[系统] 找到玩家了！PUUID: {puuid[:8]}...")
                await get_player_rank(connection, puuid, game_name)
                # 修复核心 2：手动搜索时，必须调用 detailed 版才有图标
                await get_match_history_detailed(connection, puuid, game_name, tag_line)
            else:
                print_search(f"\n[系统] 找不到名为 {game_name}#{tag_line} 的玩家，请检查拼写。")
        else:
            print_search(f"\n[系统] 查询失败，状态码：{resp.status}")
    except asyncio.TimeoutError:
        print_search("\n[系统] 查询超时，请稍后重试。")
    except Exception as e:
        print_search(f"\n[系统] 查询时发生异常：{e}")
    finally:
        if main_window: main_window.search_finished.emit()

async def get_match_history_detailed(connection, puuid, game_name, tagLine=""):
    try:
        endpoint = f'/lol-match-history/v1/products/lol/{puuid}/matches'
        resp = await connection.request('get', endpoint)
        if resp.status != 200:
            print_search("[提示] 无法获取战绩")
            return
            
        data = await resp.json()
        matches = data.get('games', {}).get('games', [])
        if not matches:
            print_search("[提示] 暂无对局记录")
            return
        
        full_name = f"{game_name}#{tagLine}" if tagLine and tagLine != "未知" else game_name
        print_search(f"\n{'='*60}")
        print_search(f"  {full_name} 的最近对局详情")
        print_search(f"{'='*60}\n")
        
        for idx, match in enumerate(matches[:10], 1):
            participant = match['participants'][0]
            stats = participant['stats']
            
            champion_id = participant.get('championId')
            champion_name = champion_map.get(str(champion_id), '未知英雄')
            
            champ_icon = None
            if image_helper:
                champ_icon = image_helper.get_champion_icon_path(champion_id)
            
            win = stats.get('win')
            result_color = "#2ecc71" if win else "#e74c3c"
            result_text = "胜利" if win else "失败"
            
            kills = stats.get('kills', 0)
            deaths = stats.get('deaths', 0)
            assists = stats.get('assists', 0)
            
            items_str = ""
            if image_helper:
                for i in range(7):
                    item_id = stats.get(f'item{i}', 0)
                    if item_id and item_id != 0:
                        item_path = image_helper.get_item_icon_path(item_id)
                        if item_path:
                            img_url = "file:///" + item_path.replace('\\', '/')
                            items_str += f'<img src="{img_url}" width="28" height="28" style="vertical-align:middle; margin-right:3px; border-radius:4px;">'
            
            champ_img_html = ""
            if champ_icon:
                img_url = "file:///" + champ_icon.replace('\\', '/')
                champ_img_html = f'<img src="{img_url}" width="30" height="30" style="vertical-align:middle; border-radius:15px; margin-right:5px;">'
            
            queueId = match.get('queueId', 0)
            queue_names = {440:"灵活排位", 420:"单排/双排", 450:"极地大乱斗", 480:"快速模式",
                          2400:"海克斯大乱斗", 430:"匹配模式", 3140:"训练营", 1700:"斗魂竞技场"}
            queue_name = queue_names.get(queueId, f"未知模式({queueId})")
            
            html_line = (
                f'<div style="margin-bottom: 5px; padding: 6px; background: rgba(255,255,255,0.05); border-radius: 5px; border-left: 3px solid {result_color};">'
                f'[{idx:2}] {champ_img_html} <span style="display:inline-block; width:90px; font-weight:bold;">{champion_name}</span> | '
                f'<span style="color:{result_color}; width:40px; display:inline-block;"><b>{result_text}</b></span> | '
                f'<span style="width:80px; display:inline-block; text-align:center;">{kills}/{deaths}/{assists}</span> | '
                f'<span style="width:100px; display:inline-block; color:#95a5a6;">{queue_name}</span> | '
                f'装备: {items_str}'
                f'</div>'
            )
            print_search(html_line)
    except Exception as e:
        print_search(f"[错误] 获取战绩异常: {e}")

async def get_player_history(connection, player_puuid, player_name, label="玩家", champion_name="", tagLine=""):
    if not player_puuid: return
    try:
        endpoint = f'/lol-match-history/v1/products/lol/{player_puuid}/matches'
        resp = await connection.request('get', endpoint)
        if resp.status != 200:
            print_monitor(f"[提示] 无法获取 {label} {player_name}{tagLine} 的战绩 (Status: {resp.status})")
            return
        data = await resp.json()
        matches = data.get('games', {}).get('games', [])
        if not matches:
            print_monitor(f"[提示] {label} {player_name}#{tagLine} 暂无对局记录")
            return

        total_wins = total_kills = total_deaths = total_assists = valid_games = 0

        for match in matches[:20]:
            participant = match['participants'][0]
            stats = participant['stats']
            
            if stats.get('win'): total_wins += 1
            total_kills += stats.get('kills', 0)
            total_deaths += stats.get('deaths', 0)
            total_assists += stats.get('assists', 0)
            valid_games += 1
        
        full_name = f"{player_name}#{tagLine}" if tagLine and tagLine != "未知" else player_name
        display_name = f"[{champion_name}] {full_name}" if label == "敌方" and champion_name and champion_name != "?" else full_name

        if valid_games == 0:
            print_monitor(f"[战绩] {label} {display_name} | 近期无任何对局记录")
            return

        win_rate = total_wins / valid_games * 100
        avg_kda = float(total_kills + total_assists) if total_deaths == 0 else (total_kills + total_assists) / total_deaths

        score = (win_rate * 0.3) + (avg_kda * 10 * 0.7)
        if score >= 50: tag, tag_color = "通天代", "#f39c12"
        elif score >= 45: tag, tag_color = "小代", "#e67e22"
        elif score >= 35: tag, tag_color = "上等马", "#27ae60"
        elif score >= 28: tag, tag_color = "中等马", "#3498db"
        elif score >= 24: tag, tag_color = "下等马", "#95a5a6"
        else: tag, tag_color = "牛马", "#e74c3c"
        
        # 修复：使用 / 作为 URL 分隔符，避免 ^ 在 QUrl 解析时被转义破坏
        click_url = f"action:player/{player_puuid}/{player_name}/{tagLine}"
        html = f'<a href="{click_url}" style="color:#5dade2; text-decoration:underline;">[战绩] {label} {display_name} | 胜率 {win_rate:.1f}% | KDA {avg_kda:.2f} | 评分 {score:.1f}</a> | <span style="color:{tag_color}; font-weight:bold;">{tag}</span>'
        print_monitor(html)
        
    except Exception as e:
        print_monitor(f"[提示] 查询 {label} {player_name} 时发生错误：{e}")

def print_team_lineup(my_team, champion_map, spell_map):
    print_monitor("\n当前阵容：")
    for member in my_team:
        champ_id = member.get('championId', 0)
        champ_name = champion_map.get(str(champ_id), "未选") if champ_id != 0 else "未选"
        s1 = spell_map.get(str(member.get('spell1Id', 0)), "?")
        s2 = spell_map.get(str(member.get('spell2Id', 0)), "?")
        gname = member.get('gameName', member.get('summonerName', ''))
        
        champ_img = ""
        if image_helper and champ_id != 0:
            icon_path = image_helper.get_champion_icon_path(champ_id)
            if icon_path:
                img_url = "file:///" + icon_path.replace('\\', '/')
                champ_img = f'<img src="{img_url}" width="28" height="28" style="vertical-align:middle; border-radius:14px; margin-right:5px;">'
        
        line_html = f'<div style="margin:3px 0;">{champ_img}<span style="color:white; font-weight:bold;">{gname}</span> : <span style="color:#5dade2;">{champ_name}</span> <span style="color:#95a5a6;">({s1}+{s2})</span></div>'
        print_monitor(line_html)

def get_lineup_fingerprint(my_team):
    return tuple((m.get('cellId'), m.get('championId'), m.get('spell1Id'), m.get('spell2Id')) for m in my_team)

async def monitor_one_game(connection):
    global is_monitoring, global_sum_name, last_my_champion_id
    try:
        resp = await connection.request('get', '/lol-summoner/v1/current-summoner')
        if resp.status == 200:
            data = await resp.json()
            global_sum_name = data.get('gameName', data.get('displayName', '未知'))
    except: pass
    
    error_count = 0
    while is_monitoring:
        try:
            rc_resp = await connection.request('get', '/lol-matchmaking/v1/ready-check')
            if rc_resp.status == 200:
                rc_data = await rc_resp.json()
                if rc_data.get('state') == 'InProgress' and rc_data.get('playerResponse') == 'None':
                    if main_window and main_window.auto_accept_cb.isChecked():
                        await connection.request('post', '/lol-matchmaking/v1/ready-check/accept')
                        print_monitor("\n[系统] 已为您自动接受对局！")
        except Exception: pass

        try:
            resp = await connection.request('get', '/lol-gameflow/v1/session')
            error_count = 0
            if resp.status == 200:
                data = await resp.json()
                if data.get('phase') == 'ChampSelect': break
        except Exception:
            error_count += 1
            if error_count >= 3:
                is_monitoring = False
                print_monitor("\n[系统] 检测到客户端已关闭，停止监控。")
                return
        await asyncio.sleep(1)

    if not is_monitoring: return

    try:
        resp = await connection.request('get', '/lol-champ-select/v1/session')
        if resp.status == 200:
            session = await resp.json()
            my_team = session.get('myTeam', [])
            if main_window: main_window.switch_to_monitor_tab.emit()
            queue_names = {440: "灵活排位", 420: "单排/双排", 450: "极地大乱斗", 2400: "海克斯大乱斗", 430: "匹配模式", 3140: "训练营", 1700:"斗魂竞技场", 480: "快速模式"}
            q_id = session.get('queueId')
            print_monitor(f"\n当前模式：{queue_names.get(q_id, f'未知({q_id})')}")

            print_monitor("正在查询队友战绩...")
            tasks = []
            for member in my_team:
                puuid = member.get('puuid')
                if puuid:
                    champ_name = champion_map.get(str(member.get('championId', 0)), "?")
                    name = member.get('gameName', member.get('summonerName', '未知'))
                    tagLine = member.get('tagLine', '未知')
                    tasks.append(asyncio.create_task(get_player_history(connection, puuid, name, "队友", champ_name,tagLine)))
            if tasks: await asyncio.gather(*tasks)
            print_monitor("===== 队友战绩查询完毕 =====")

            print_team_lineup(my_team, champion_map, spell_map)
            last_fingerprint = get_lineup_fingerprint(my_team)
        else: return
    except Exception: return

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
                    local_cell_id = session.get('localPlayerCellId')
                    current_my_champion_id = next((m.get('championId', 0) for m in my_team if m.get('cellId') == local_cell_id), 0)
                    
                    if current_my_champion_id != 0 and current_my_champion_id != last_my_champion_id:
                        last_my_champion_id = current_my_champion_id
                        asyncio.create_task(update_opgg_data(current_my_champion_id, connection))
                    if current_fingerprint != last_fingerprint:
                        print_monitor("\n" + "=" * 40)
                        print_monitor("--- 阵容已更新 ---")
                        print_team_lineup(my_team, champion_map, spell_map)
                        last_fingerprint = current_fingerprint
                await asyncio.sleep(0.5)

            elif phase in ['GameStart', 'InProgress']:
                print_monitor("\n选人结束，游戏已开始，正在查询敌方战绩...")
                game_data = flow.get('gameData', {})
                all_players = game_data.get('teamOne', []) + game_data.get('teamTwo', [])
                my_puuids = {m.get('puuid') for m in my_team if m.get('puuid')}

                enemy_tasks = []
                for player in all_players:
                    puuid = player.get('puuid')
                    if puuid and puuid not in my_puuids:
                        name = player.get('summonerName', '未知')
                        tagLine = player.get('tagLine', '未知')
                        champ_name = champion_map.get(str(player.get('championId', 0)), "?")
                        enemy_tasks.append(asyncio.create_task(get_player_history(connection, puuid, name, "敌方", champ_name,tagLine)))

                if enemy_tasks:
                    await asyncio.gather(*enemy_tasks)
                    print_monitor("===== 敌方战绩查询完毕 =====")
                else:
                    print_monitor("未找到敌方玩家信息（可能是训练营或自定义）")

                print_monitor("\n正在监控对局状态，请尽情游戏...")

                in_game_errors = 0
                while is_monitoring:
                    try:
                        end_resp = await connection.request('get', '/lol-gameflow/v1/session')
                        in_game_errors = 0
                        if end_resp.status == 200:
                            end_flow = await end_resp.json()
                            if end_flow.get('phase') not in ['GameStart', 'InProgress', 'Reconnect']:
                                print_monitor(f"\n对局已结束，准备下一场...")
                                break
                        else: break
                    except Exception:
                        in_game_errors += 1
                        if in_game_errors >= 3:
                            is_monitoring = False
                            break
                    await asyncio.sleep(3)
                break

            elif phase in ['Lobby', 'Matchmaking', 'ReadyCheck', 'None']:
                print_monitor(f"\n有人秒退或对局未开始，重新等待...")
                await asyncio.sleep(2)
                break
            else:
                await asyncio.sleep(1)
        except Exception:
            error_count += 1
            if error_count >= 3:
                is_monitoring = False
                print_monitor("\n[系统] 检测到客户端已关闭，停止监控。")
                return
            await asyncio.sleep(2)

@connector.close
async def disconnect(_):
    global is_monitoring
    is_monitoring = False

@connector.ready
async def connect(connection):
    global is_monitoring, main_window, monitor_loop, image_helper
    is_monitoring = True
    monitor_loop = asyncio.get_running_loop()
    image_helper = get_image_helper()
    init_local_resources()
    print_home("[系统] 本地资源加载就绪")

    if main_window:
        main_window.connection = connection
        main_window.loop_ready.emit()

    print_home("\n成功连接到英雄联盟客户端！")

    for _ in range(3):
        if not is_monitoring: return
        try:
            resp_sum = await connection.request('get', '/lol-summoner/v1/current-summoner')
            if resp_sum.status == 200:
                data = await resp_sum.json()
                sum_name = data.get('gameName', data.get('displayName', '未知'))
                level = data.get('summonerLevel', 0)
                tagLine = data.get('tagLine', '未知')
                xpnow = data.get('xpSinceLastLevel', 0)
                xpnext = data.get('xpUntilNextLevel', 0)
                profile_icon_id = data.get('profileIconId', 29)

                import os
                profile_dir = r"D:\work\lol\data\profileicon" 
                icon_path = os.path.join(profile_dir, f"{profile_icon_id}.png")
                icon_html = ""
                if os.path.exists(icon_path):
                    icon_url = "file:///" + icon_path.replace('\\', '/')
                    icon_html = f'<img src="{icon_url}" width="50" height="50" style="vertical-align:middle; border-radius:25px; margin-right:10px;">'

                welcome_html = f"""
                <div style='padding:10px;'>
                    {icon_html}
                    <span style='font-size:18px; font-weight:bold; color:white; vertical-align:middle;'>
                        你好，{sum_name}#{tagLine}
                    </span>
                    <br><br>
                    <span style='color:#95a5a6;'>等级: {level} | 经验值: {xpnow}/{xpnext}</span>
                </div>
                """
                print_home(welcome_html)
                break
        except Exception: pass
        await asyncio.sleep(3)

    print_monitor("\n等待进入选人阶段...")
    while is_monitoring:
        await monitor_one_game(connection)
        if is_monitoring: print_monitor("\n等待进入选人阶段...")
    print_home("\n[系统] 核心监控已退出，等待重新连接客户端...")

def run_monitor(): connector.start()

# ================= PySide6 UI =================
class TitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.setFixedHeight(40)
        self.drag_pos = QPoint()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        
        self.title_label = QLabel("清梦 - 英雄联盟助手")
        self.title_label.setStyleSheet("color: #cccccc; font-size: 20px;")
        layout.addWidget(self.title_label)
        layout.addStretch()

        self.min_btn = QPushButton("—")
        self.min_btn.setFixedSize(30, 30)
        self.min_btn.setStyleSheet("QPushButton { background: transparent; color: #cccccc; border: none; font-size: 16px; font-weight: bold; } QPushButton:hover { background: #3a3a3a; color: white; }")
        self.min_btn.clicked.connect(self.parent.showMinimized)
        layout.addWidget(self.min_btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setStyleSheet("QPushButton { background: transparent; color: #cccccc; border: none; font-size: 14px; font-weight: bold; } QPushButton:hover { background: #e81123; color: white; }")
        self.close_btn.clicked.connect(self.parent.close)
        layout.addWidget(self.close_btn)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.parent.frameGeometry().topLeft()
            event.accept()
    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.LeftButton:
            self.parent.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()

class MainWindow(QMainWindow):
    search_finished = Signal()
    loop_ready = Signal()
    switch_to_monitor_tab = Signal()

    def __init__(self):
        super().__init__()
        global main_window
        main_window = self
        self.search_finished.connect(self.on_search_finished)
        self.loop_ready.connect(self.on_loop_ready)
        self.switch_to_monitor_tab.connect(self.on_switch_to_monitor)

        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(100, 100, 900, 650)
        central_widget = QWidget(); central_widget.setObjectName("central")
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0); main_layout.setSpacing(0)
        self.title_bar = TitleBar(self); main_layout.addWidget(self.title_bar)

        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(15, 10, 15, 10); search_layout.setSpacing(10); search_layout.addStretch()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入 名字#编号 查战绩...")
        self.search_input.setFixedSize(200,32)
        self.search_input.setStyleSheet("QLineEdit { background-color: rgba(40, 40, 40, 200); color: white; border: 1px solid #555; border-radius: 5px; padding: 0 10px; } QLineEdit:focus { border: 1px solid #66b3ff; }")
        search_layout.addWidget(self.search_input)

        self.search_btn = QPushButton("搜索")
        self.search_btn.setFixedSize(60, 32)
        self.search_btn.setStyleSheet("QPushButton { background-color:#1e90ff; color: white; border: none; border-radius: 5px; font-weight: bold; } QPushButton:hover { background-color: #c0392b; }")
        self.search_btn.clicked.connect(self.start_search)
        self.search_btn.setEnabled(False)
        search_layout.addWidget(self.search_btn)

        self.result_label = QLabel("")
        self.result_label.setStyleSheet("color: #88ff88; padding-left: 15px;")
        search_layout.addWidget(self.result_label)
        main_layout.addLayout(search_layout)

        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane { background: rgba(25, 25, 25, 220); border: none; border-radius: 8px; margin: 0 15px; } 
            QTabBar::tab { background: #2a2a2a; color: #cccccc; padding: 14px 30px; font-size: 14px; font-weight: bold; margin-right: 2px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #3a3a3a; color: white; }
            QTabBar::tab:hover { background: #4a4a4a; }
        """)

        # Tab pages
        self.home_text = self.create_browser(); self.tab_widget.addTab(self.create_tab(self.home_text), " 主页 ")
        self.monitor_text = self.create_browser(True); self.tab_widget.addTab(self.create_tab(self.monitor_text), " 对战信息 ")
        self.search_result_text = self.create_browser(True); self.tab_widget.addTab(self.create_tab(self.search_result_text), " 战绩查询 ")
        self.rune_browser = self.create_browser(True); self.tab_widget.addTab(self.create_tab(self.rune_browser), " 符文建议 ")
        self.item_browser = self.create_browser(True); self.tab_widget.addTab(self.create_tab(self.item_browser), " 出装推荐 ")

        main_layout.addWidget(self.tab_widget)
        
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(15, 10, 15, 10)
        self.auto_accept_cb = QCheckBox("自动接受对局")
        self.auto_accept_cb.setStyleSheet("QCheckBox { color: #cccccc; font-size: 13px; } QCheckBox::indicator { width: 18px; height: 18px; }")
        self.auto_accept_cb.setChecked(True)
        bottom_layout.addWidget(self.auto_accept_cb); bottom_layout.addStretch()
        self.status_label = QLabel("状态：等待启动...")
        self.status_label.setStyleSheet("color: #aaaaaa; font-weight: bold;")
        bottom_layout.addWidget(self.status_label)
        main_layout.addLayout(bottom_layout)

        self.setStyleSheet("QMainWindow { background: transparent; } #central { background-color: rgba(30, 32, 40, 240); border-radius: 12px; border: 1px solid rgba(255, 255, 255, 30); }")
        self.connection = None
        self.timer = QTimer(); self.timer.timeout.connect(self.update_log); self.timer.start(100)
        self.monitor_thread = threading.Thread(target=run_monitor, daemon=True); self.monitor_thread.start()
        self.status_timer = QTimer(); self.status_timer.timeout.connect(self.update_status); self.status_timer.start(500)

    def create_browser(self, clickable=False):
        b = QTextBrowser()
        b.setReadOnly(True); b.setOpenLinks(False); b.setOpenExternalLinks(False)
        b.setFont(QFont("Consolas" if clickable else "Microsoft YaHei", 10))
        b.setStyleSheet("QTextBrowser { background: transparent; color: #d4d4d4; border: none; } a { color: #5dade2; text-decoration: underline; } a:hover { color: #85c1e9; }")
        if clickable: b.anchorClicked.connect(self.handle_link_clicked)
        return b

    def create_tab(self, browser):
        w = QWidget(); l = QVBoxLayout(w); l.addWidget(browser); return w

    def on_loop_ready(self):
        self.search_btn.setEnabled(True)
        print_search("[系统] 后台通信已准备就绪，您可以开始搜索了！")

    def on_search_finished(self):
        self.search_btn.setEnabled(True)
        self.result_label.setText("✓")

    def start_search(self):
        full_text = self.search_input.text().strip()
        if not full_text: return print_search("[系统] 输入不能为空")
        if '#' not in full_text: return print_search("[系统] 输入格式错误，请使用 名字#编号 的格式")
        game_name, tag_line = full_text.split('#', 1)
        if not self.connection or monitor_loop is None or monitor_loop.is_closed(): return print_search("[系统] 尚未连接或后台未准备好")

        self.search_btn.setEnabled(False)
        self.result_label.setText("正在提交查询...")
        print_search(f"\n[系统] 准备投递搜索任务: {game_name}#{tag_line}")

        try:
            asyncio.run_coroutine_threadsafe(search_player_by_name(self.connection, game_name.strip(), tag_line.strip()), monitor_loop)
            self.tab_widget.setCurrentIndex(2)
        except Exception as e:
            print_search(f"[错误] 提交搜索任务失败：{e}")
            self.search_btn.setEnabled(True)
    
    def handle_link_clicked(self, url):
        # 使用 FullyDecoded，防范 QUrl 吞噬或者转义特殊字符
        link = url.toString(QUrl.FullyDecoded) 
        
        if link.startswith("http"):
            import webbrowser
            webbrowser.open(link)
            return

        if link.startswith("action:"):
            cmd = link.replace("action:", "")
            
            # 使用更安全的斜杠 / 作为分隔符解析战绩查询跳转
            if cmd.startswith("player/"):
                data = cmd.replace("player/", "").split("/", 2)
                if len(data) >= 3:
                    puuid, name, tagline = data[0], data[1], data[2]
                    self.show_player_detail(puuid, name, tagline)
                    
            elif cmd.startswith("import_rune:"):
                cn_name = cmd.replace("import_rune:", "")
                from PySide6.QtWidgets import QInputDialog
                name, ok = QInputDialog.getText(self, "一键保存符文", f"请先在LOL客户端配好符文。\n将当前正在使用的符文保存为：", QLineEdit.Normal, f"{cn_name} - 绝活符文")
                if ok and name: asyncio.run_coroutine_threadsafe(self._do_import_rune(name.strip(), cn_name), monitor_loop)
                    
            elif cmd.startswith("new_item:"):
                self.show_new_item_dialog(cmd.replace("new_item:", ""))
                
            elif cmd.startswith("apply_rune:"):
                idx = int(cmd.replace("apply_rune:", ""))
                saved = get_saved_runes()
                if idx < len(saved):
                    r = saved[idx]
                    asyncio.run_coroutine_threadsafe(self._do_apply_rune(r['name'], r['primary'], r['sub'], r['perks']), monitor_loop)
                    
            elif cmd.startswith("delete_rune:"):
                delete_rune(int(cmd.replace("delete_rune:", "")))
                self._refresh_opgg_view()
                
            elif cmd.startswith("delete_item:"):
                delete_item_set(int(cmd.replace("delete_item:", "")))
                self._refresh_opgg_view()

    def _refresh_opgg_view(self):
        global last_my_champion_id
        if last_my_champion_id and self.connection and monitor_loop:
            asyncio.run_coroutine_threadsafe(update_opgg_data(last_my_champion_id, self.connection), monitor_loop)

    async def _do_apply_rune(self, name, primary, sub, perks):
        success, msg = await apply_rune_to_client(self.connection, name, primary, sub, perks)
        if success: print_rune(f"<p align='center' style='color:#2ecc71; font-weight:bold; background:#1e2028; padding:8px; border-radius:5px;'>✅ {msg}</p>")
        else: print_rune(f"<p align='center' style='color:#e74c3c; font-weight:bold;'>❌ {msg}</p>")

    async def _do_import_rune(self, name, champion_name):
        try:
            resp = await self.connection.request('get', '/lol-perks/v1/currentpage')
            if resp.status == 200:
                page = await resp.json()
                perks = page.get('selectedPerkIds', [])
                if len(perks) == 9:
                    save_rune(name, champion_name, page.get('primaryStyleId'), page.get('subStyleId'), perks)
                    print_rune(f"<p align='center' style='color:#2ecc71; font-weight:bold;'>✅ 成功抓取客户端符文并保存！</p>")
                    self._refresh_opgg_view()
                else: print_rune("<p align='center' style='color:#e74c3c; font-weight:bold;'>❌ 失败：当前符文页未点满 9 个符文！</p>")
        except Exception as e: print_rune(f"<p align='center' style='color:#e74c3c;'>❌ 发生错误：{e}</p>")

    def show_new_item_dialog(self, champion_name):
        from PySide6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox
        dialog = QDialog(self)
        dialog.setWindowTitle(f"新建 {champion_name} 自定义出装")
        dialog.setFixedSize(450, 200)
        dialog.setStyleSheet("background: #1e2028; color: white; font-size:13px;")
        layout = QFormLayout(dialog)
        
        name_input = QLineEdit(f"{champion_name} - 绝活出装")
        name_input.setStyleSheet("background: #2a2a2a; border: 1px solid #444; padding: 5px;")
        layout.addRow("方案名称:", name_input)
        
        items_input = QLineEdit()
        items_input.setPlaceholderText("填入装备数字 ID，用逗号分隔 (例: 3153,3006,6630...)")
        items_input.setStyleSheet("background: #2a2a2a; border: 1px solid #444; padding: 5px;")
        layout.addRow("装备 ID:", items_input)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.setStyleSheet("QPushButton { background: #5383e8; color: white; padding: 5px 15px; border-radius: 4px; font-weight: bold; border: none; }")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        
        if dialog.exec() == QDialog.Accepted:
            try:
                raw_text = items_input.text().replace('，', ',')
                items = [int(x.strip()) for x in raw_text.split(',') if x.strip()]
                if not items: raise ValueError("装备不能为空")
                save_item_set(name_input.text().strip(), champion_name, items)
                self._refresh_opgg_view()
            except Exception as e:
                print_item("<p style='color:#e74c3c;'>❌ 保存失败，请确保填写的是正确的数字 ID</p>")

    def show_player_detail(self, puuid, name, tagline):
        if not self.connection or not monitor_loop or monitor_loop.is_closed(): return
        self.search_result_text.clear()
        self.search_result_text.append(f"<p style='color:#95a5a6;'>正在加载 {name}#{tagline} 的详细战绩...</p>")
        self.tab_widget.setCurrentIndex(2)
        QApplication.processEvents()
        try: asyncio.run_coroutine_threadsafe(self.fetch_player_detail(puuid, name, tagline), monitor_loop)
        except Exception as e: print_search(f"[错误] 提交查询任务失败：{e}")

    async def fetch_player_detail(self, puuid, name, tagline):
        try:
            await get_player_rank(self.connection, puuid, name)
            await get_match_history_detailed(self.connection, puuid, name, tagline)
        except Exception as e: print_search(f"[错误] 获取详细信息失败: {e}")

    def on_switch_to_monitor(self): self.tab_widget.setCurrentIndex(1)

    def update_log(self):
        while not log_queue.empty():
            try:
                target, line = log_queue.get_nowait()
                clean_line = line.rstrip('\n')
                
                if target == 'monitor':
                    if clean_line == 'CLEAR_AND_UPDATE': self.monitor_text.clear(); continue
                    if '<a href=' in clean_line or '<div' in clean_line or '<img' in clean_line:
                        self.monitor_text.append(clean_line)
                    else:
                        safe_text = clean_line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace(' ', '&nbsp;')
                        self.monitor_text.append(f'<div style="color:#d4d4d4; text-decoration:none;">{safe_text}</div>')
                    self.monitor_text.verticalScrollBar().setValue(self.monitor_text.verticalScrollBar().maximum())
                    
                elif target == 'search':
                    if 'CLEAR' in clean_line and len(clean_line) <= 5: self.search_result_text.clear()
                    elif '<div' in clean_line or '<img' in clean_line: self.search_result_text.append(clean_line)
                    else:
                        safe_text = clean_line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace(' ', '&nbsp;')
                        self.search_result_text.append(f'<div style="color:#d4d4d4; text-decoration:none;">{safe_text}</div>')
                    self.search_result_text.verticalScrollBar().setValue(self.search_result_text.verticalScrollBar().maximum())
                        
                elif target == 'item':
                    if 'CLEAR' in clean_line and len(clean_line) <= 5: self.item_browser.clear()
                    else:
                        self.item_browser.append(clean_line)
                        self.item_browser.verticalScrollBar().setValue(self.item_browser.verticalScrollBar().maximum())
                        
                elif target == 'rune':
                    if 'CLEAR' in clean_line and len(clean_line) <= 5: self.rune_browser.clear()
                    else:
                        self.rune_browser.append(clean_line)
                        self.rune_browser.verticalScrollBar().setValue(self.rune_browser.verticalScrollBar().maximum())

                elif target == 'home':
                    self.home_text.append(clean_line)
                    self.home_text.verticalScrollBar().setValue(self.home_text.verticalScrollBar().maximum())
            except queue.Empty: break

    def update_status(self):
        global is_monitoring
        if is_monitoring:
            self.status_label.setText("● 状态：已连接客户端，后台运行中...")
            self.status_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        else:
            self.status_label.setText("○ 状态：未连接客户端，等待游戏启动...")
            self.status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")

    def closeEvent(self, event):
        global is_monitoring
        is_monitoring = False
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
