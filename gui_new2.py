import sys
import asyncio
import threading
import queue
import os
from PySide6.QtCore import Qt, QTimer, QPoint, QThread, Signal, QUrl
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QTextEdit, QFrame,
    QLineEdit, QCheckBox, QTabWidget, QTextBrowser,
    QDialog, QFormLayout, QDialogButtonBox, QComboBox, QCompleter,
    QMessageBox, QInputDialog
)
from PySide6.QtGui import QFont, QMouseEvent, QImage

# ================= 导入原有逻辑模块 =================
from lcu_driver import Connector
from lol_map import load_champion_map, load_spell_map
from ddragon_images import get_image_helper
from resource_manager import (
    init_local_resources, get_perk, get_style,
    get_saved_runes, save_rune, delete_rune, apply_rune_to_client, DATA_DIR
)

is_monitoring_lock = threading.Lock()
log_queue = queue.Queue()

# ---- 紧凑/高级暗色调 ----
COLOR_BG_MAIN = "#1a1b26"       
COLOR_BG_CARD = "#24283b"       
COLOR_TEXT_MAIN = "#c0caf5"     
COLOR_TEXT_SUB = "#7aa2f7"      
COLOR_ACCENT = "#7aa2f7"        
COLOR_SUCCESS = "#9ece6a"       
COLOR_DANGER = "#f7768e"        
COLOR_WARN = "#e0af68"          

# 修复白格子占位符用的 1x1 透明像素 Base64
TRANSPARENT_IMG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="

def path_to_url(p):
    if not p: return ""
    return p.replace('\\', '/')

def html_text(line):
    safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<div style="color:{COLOR_TEXT_MAIN}; text-decoration:none; font-size:14px; font-weight:bold;">{safe}</div>'

champion_map = load_champion_map()
spell_map = load_spell_map()
connector = Connector()
is_monitoring = False
global_sum_name = "未知"          
global_sum_id = 0                 
last_my_champion_id = 0 

main_window = None          
monitor_loop = None         
image_helper = None         

def gui_print(target,*args, **kwargs):
    sep = kwargs.get('sep', ' ')
    end = kwargs.get('end', '\n')
    text = sep.join(str(arg) for arg in args) + end
    log_queue.put((target,text))

def print_home(*args,**kwargs): gui_print('home',*args,**kwargs)
def print_monitor(*args,**kwargs): gui_print('monitor',*args,**kwargs)
def print_search(*args,**kwargs): gui_print('search',*args,**kwargs)
def print_rune(*args,**kwargs): gui_print('rune',*args,**kwargs)

TIER_TRANSLATE = {
    "IRON": "坚韧黑铁", "BRONZE": "英勇黄铜", "SILVER": "不屈白银",
    "GOLD": "荣耀黄金", "PLATINUM": "华贵铂金", "EMERALD": "流光翡翠",     
    "DIAMOND": "璀璨钻石", "MASTER": "超凡大师", "GRANDMASTER": "傲世宗师",
    "CHALLENGER": "最强王者", "NONE": "未定级"
}

# 像素扫描精准裁切段位图标透明留白
def get_tier_icon_url(tier):
    tier_lower = tier.lower()
    if tier_lower in ["none", "unranked"]: return ""
    
    # 用全新文件名 -trim.png 强制重新生成
    cropped_path = os.path.join(DATA_DIR, "rank", f"emblem-{tier_lower}-trim.png")
    if os.path.exists(cropped_path):
        return path_to_url(cropped_path)
        
    original_path = os.path.join(DATA_DIR, "rank", f"emblem-{tier_lower}.png")
    if not os.path.exists(original_path): return ""
    
    try:
        img = QImage(original_path)
        if img.isNull(): return path_to_url(original_path)
        
        if img.format() != QImage.Format_ARGB32:
            img = img.convertToFormat(QImage.Format_ARGB32)
        
        w, h = img.width(), img.height()
        if w == 0 or h == 0: return path_to_url(original_path)
        
        ALPHA_THRESHOLD = 20
        STEP = max(1, min(w, h) // 120)  # 自适应步长，平衡精度与性能
        
        min_x, min_y = w, h
        max_x, max_y = -1, -1
        
        # 网格扫描，找出真正不透明像素的边界
        for y in range(0, h, STEP):
            for x in range(0, w, STEP):
                if ((img.pixel(x, y) >> 24) & 0xFF) > ALPHA_THRESHOLD:
                    if x < min_x: min_x = x
                    if y < min_y: min_y = y
                    if x > max_x: max_x = x
                    if y > max_y: max_y = y
        
        if max_x <= min_x or max_y <= min_y:
            return path_to_url(original_path)
        
        # 加点边距防止采样误差导致截断
        margin = STEP * 2
        x = max(0, min_x - margin)
        y = max(0, min_y - margin)
        width = min(w - x, max_x - min_x + 1 + margin * 2)
        height = min(h - y, max_y - min_y + 1 + margin * 2)
        
        cropped = img.copy(x, y, width, height)
        cropped.save(cropped_path, "PNG")
        return path_to_url(cropped_path)
    except Exception:
        return path_to_url(original_path)

# === 段位缓存系统 ===
rank_cache = {}

async def get_player_tier(connection, puuid):
    if puuid in rank_cache: return rank_cache[puuid]
    try:
        resp = await connection.request('get', f'/lol-ranked/v1/ranked-stats/{puuid}')
        if resp.status == 200:
            data = await resp.json()
            highest_tier = "NONE"
            for q in data.get('queues', []):
                t = q.get('tier', 'NONE')
                if t not in ['NONE', 'UNRANKED']:
                    highest_tier = t
                    if q.get('queueType') == 'RANKED_SOLO_5x5':
                        break
            rank_cache[puuid] = highest_tier
            return highest_tier
    except: pass
    rank_cache[puuid] = "NONE"
    return "NONE"

async def update_opgg_data(champion_id, connection):
    global image_helper
    if champion_id == 0 or not connection: return
    cn_name = champion_map.get(str(champion_id), "未知英雄")
    eng_name = image_helper.champion_id_to_eng.get(str(champion_id), "").lower() if image_helper else ""
    
    champ_icon = image_helper.get_champion_icon_path(champion_id) if image_helper else None
    img_html = f'<img src="file:///{path_to_url(champ_icon)}" width="64" height="64" style="border-radius:10px; border:1px solid {COLOR_ACCENT};">' if champ_icon else ''
    
    def get_header():
        opgg_url = f"https://www.op.gg/champions/{eng_name}/build"
        return f"""
        <table width="100%" style="margin-bottom:12px; padding-bottom:12px; border-bottom:1px solid rgba(255,255,255,0.05);"><tr>
            <td width="80" valign="top">{img_html}</td>
            <td valign="top">
                <span style="color:{COLOR_TEXT_MAIN}; font-size:22px; font-weight:bold;">{cn_name}</span> 
                <span style="color:{COLOR_WARN}; font-size:13px; font-weight:bold; margin-left:10px; background:rgba(224, 175, 104, 0.15); padding:4px 8px; border-radius:4px;">🛡️ 绝活符文夹</span><br>
                <div style="margin-top: 12px;">
                    <a href="{opgg_url}" style="color:{COLOR_ACCENT}; text-decoration:none; font-size:13px; font-weight:bold; background:rgba(122,162,247,0.15); padding:8px 14px; border-radius:4px; margin-right:10px;">🌍 网页查 OP.GG</a>
                    <a href="action:import_rune:{cn_name}" style="color:{COLOR_SUCCESS}; text-decoration:none; font-size:13px; font-weight:bold; background:rgba(158,206,106,0.15); padding:8px 14px; border-radius:4px;">⬇️ 抓取当前符文</a>
                </div>
            </td>
        </tr></table>
        """
    
    print_rune("CLEAR")
    print_rune(get_header())

    saved_runes = get_saved_runes()
    my_runes = [r for r in saved_runes if r['champion'] in [cn_name, '通用']]
    if my_runes:
        for r in my_runes:
            real_idx = saved_runes.index(r)
            p_style, s_style = get_style(r['primary']), get_style(r['sub'])
            icons = "".join([f'<img src="file:///{get_perk(pid)["icon"]}" width="36" height="36" style="margin-right:4px; border-radius:4px;">' for pid in r['perks'] if get_perk(pid)['icon']])
            print_rune(f"""
            <table width="100%" cellpadding="10" cellspacing="0" style="background:{COLOR_BG_CARD}; border:1px solid rgba(255,255,255,0.03); border-left: 4px solid {COLOR_SUCCESS}; border-radius:6px; margin-bottom:10px;"><tr>
                <td width="55" valign="middle"><img src="file:///{p_style['icon']}" width="42" height="42" style="border-radius:21px;"><br><img src="file:///{s_style['icon']}" width="22" height="22" style="margin-top:4px; border-radius:11px; margin-left:10px;"></td>
                <td valign="middle"><b style="color:{COLOR_TEXT_MAIN}; font-size:15px;">{r['name']}</b><br><div style="margin-top:8px;">{icons}</div></td>
                <td width="100" align="right" valign="middle">
                    <a href="action:apply_rune:{real_idx}" style="display:inline-block; background:rgba(158,206,106,0.2); color:{COLOR_SUCCESS}; padding:8px 14px; border-radius:4px; text-decoration:none; font-weight:bold; font-size:13px; margin-bottom:8px;">🚀 应用</a><br>
                    <a href="action:delete_rune:{real_idx}" style="color:{COLOR_DANGER}; text-decoration:none; font-size:13px; font-weight:bold;">🗑️ 删除</a>
                </td>
            </tr></table>
            """)
    else: print_rune(f"<p style='color:rgba(255,255,255,0.3); padding: 25px; text-align: center; font-size:14px; background:{COLOR_BG_CARD}; border-radius:6px;'>未保存过 <b>{cn_name}</b> 的专属符文</p>")

async def get_player_rank(connection, puuid, player_name):
    try:
        endpoint = f'/lol-ranked/v1/ranked-stats/{puuid}'
        resp = await connection.request('get', endpoint)
        if resp.status != 200: return
        data = await resp.json()
        solo_rank_str = "单双排: 未定级"
        flex_rank_str = "灵活排位: 未定级"
        for q in data.get('queues', []):
            tier = q.get('tier', 'NONE')
            win_rate = (q.get('wins', 0) / (q.get('wins', 0) + q.get('losses', 0)) * 100) if (q.get('wins', 0) + q.get('losses', 0)) > 0 else 0
            cn_tier = TIER_TRANSLATE.get(tier, tier)
            rank_display = f"{cn_tier} {q.get('leaguePoints', 0)}胜点 (胜率:{win_rate:.1f}%)" if tier in ["MASTER", "GRANDMASTER", "CHALLENGER"] else "未定级" if tier == "NONE" else f"{cn_tier}{q.get('division', 'NA')} {q.get('leaguePoints', 0)}胜点 (胜率:{win_rate:.1f}%)"
            if q.get('queueType') == 'RANKED_SOLO_5x5': solo_rank_str = f"单排/双排: {rank_display}"
            elif q.get('queueType') == 'RANKED_FLEX_SR': flex_rank_str = f"灵活排位: {rank_display}"
        
        print_search(f'<div style="font-size:14px; color:{COLOR_TEXT_MAIN}; font-weight:bold; margin-bottom:12px; background:{COLOR_BG_CARD}; padding:12px 16px; border-radius:6px; border-left:4px solid {COLOR_WARN};">'
                     f'👑 <span style="font-size:16px;">{player_name}</span> 的排位数据<br>'
                     f'<div style="margin-top:10px; font-size:13px;">'
                     f'<span style="color:{COLOR_ACCENT}; background:rgba(122,162,247,0.1); padding:6px 10px; border-radius:4px; margin-right:12px;">{solo_rank_str}</span>'
                     f'<span style="color:{COLOR_SUCCESS}; background:rgba(158,206,106,0.1); padding:6px 10px; border-radius:4px;">{flex_rank_str}</span>'
                     f'</div></div>')
    except: pass

async def search_player_by_name(connection, game_name, tag_line):
    try:
        test = await asyncio.wait_for(connection.request('get', '/lol-summoner/v1/current-summoner'), timeout=3.0)
        if test.status != 200: raise Exception("LCU 未响应")
    except Exception as e:
        print_search(f'<div style="font-size:14px; color:{COLOR_DANGER}; background:rgba(247,118,142,0.1); border-radius:4px; font-weight:bold; padding:12px;">[系统] 连接未就绪：{e}</div>')
        if main_window: main_window.search_finished.emit()
        return

    print_search(f'<div style="font-size:14px; color:{COLOR_ACCENT}; font-weight:bold; padding:12px;">[系统] 正在查询玩家 {game_name}#{tag_line} ...</div>')
    try:
        resp = await asyncio.wait_for(connection.request('post', '/lol-summoner/v1/summoners/aliases', json=[{"gameName": game_name, "tagLine": tag_line}]), timeout=5.0)
        if resp.status == 200:
            data = await resp.json()
            if data and data[0].get('puuid'):
                puuid = data[0]['puuid']
                await get_player_rank(connection, puuid, game_name)
                await get_match_history_detailed(connection, puuid, game_name, tag_line)
            else: print_search(f'<div style="font-size:14px; color:{COLOR_DANGER}; font-weight:bold; padding:12px;">[系统] 找不到名为 {game_name}#{tag_line} 的玩家</div>')
        else: print_search(f'<div style="font-size:14px; color:{COLOR_DANGER}; font-weight:bold; padding:12px;">[系统] 查询失败，状态码：{resp.status}</div>')
    except Exception as e: print_search(f'<div style="font-size:14px; color:{COLOR_DANGER}; font-weight:bold; padding:12px;">[系统] 查询异常：{e}</div>')
    finally:
        if main_window: main_window.search_finished.emit()

QUEUE_NAMES = {440:"灵活排位", 420:"单排/双排", 450:"极地大乱斗", 480:"快速模式", 2400:"海克斯大乱斗", 430:"匹配模式", 3140:"训练营", 1700:"斗魂竞技场"}

def format_k(num):
    return f"{num/1000:.1f}k" if num >= 1000 else str(num)

def _render_one_match(idx, match, show_name=False, player_name="", tagline="", detail_html=""):
    stats = match['participants'][0]['stats']
    champion_id = match['participants'][0].get('championId')
    champ_icon = image_helper.get_champion_icon_path(champion_id) if image_helper else None
    
    is_win = stats.get('win')
    result_color = COLOR_SUCCESS if is_win else COLOR_DANGER
    result_text = "胜 利" if is_win else "失 败"
    
    # 符文配置图标 (先符文, 纵向排列)
    perk0 = stats.get('perk0', 0)
    perk_sub_style = stats.get('perkSubStyle', 0)
    p0_icon = get_perk(perk0).get('icon', '') if perk0 else ''
    pSub_icon = get_style(perk_sub_style).get('icon', '') if perk_sub_style else ''
    rune_html = ""
    if p0_icon or pSub_icon:
        rune_html = '<div style="line-height:1; text-align:center;">'
        if p0_icon: 
            rune_html += f'<div style="margin-bottom:3px;"><img src="file:///{path_to_url(p0_icon)}" width="22" height="22" style="vertical-align:middle; border-radius:11px; border:1px solid {COLOR_ACCENT};"></div>'
        if pSub_icon: 
            rune_html += f'<div><img src="file:///{path_to_url(pSub_icon)}" width="18" height="18" style="vertical-align:middle; border-radius:9px;"></div>'
        rune_html += '</div>'
    
    # 召唤师技能图标 (后技能, 纵向排列)
    spell1_id = match['participants'][0].get('spell1Id', 0)
    spell2_id = match['participants'][0].get('spell2Id', 0)
    spell1_icon = path_to_url(image_helper.get_spell_icon_path(spell1_id)) if image_helper and spell1_id else ""
    spell2_icon = path_to_url(image_helper.get_spell_icon_path(spell2_id)) if image_helper and spell2_id else ""
    spells_html = ""
    if spell1_icon or spell2_icon:
        spells_html = '<div style="line-height:1; text-align:center;">'
        if spell1_icon: 
            spells_html += f'<div style="margin-bottom:3px;"><img src="file:///{spell1_icon}" width="20" height="20" style="border-radius:4px;"></div>'
        if spell2_icon: 
            spells_html += f'<div><img src="file:///{spell2_icon}" width="20" height="20" style="border-radius:4px;"></div>'
        spells_html += '</div>'

    # 物品图标与透明占位
    items_str = ""
    for i in range(7):
        item_id = stats.get(f'item{i}', 0)
        margin = "6px" if i == 5 else "3px"
        if item_id and image_helper and image_helper.get_item_icon_path(item_id):
            icon_path = path_to_url(image_helper.get_item_icon_path(item_id))
            items_str += f'<img src="file:///{icon_path}" width="32" height="32" style="vertical-align:middle; margin-right:{margin}; border-radius:4px; border:1px solid rgba(255,255,255,0.05);">'
        else:
            items_str += f'<img src="{TRANSPARENT_IMG}" width="32" height="32" style="vertical-align:middle; margin-right:{margin}; border-radius:4px; background:rgba(0,0,0,0.25); border:1px solid rgba(255,255,255,0.02);">'
            
    champ_img_html = f'<img src="file:///{path_to_url(champ_icon)}" width="52" height="52" style="border-radius:6px;">' if champ_icon else ""
    k, d, a = stats.get("kills", 0), stats.get("deaths", 0), stats.get("assists", 0)
    gold = format_k(stats.get('goldEarned', 0))
    cs = stats.get('totalMinionsKilled', 0) + stats.get('neutralMinionsKilled', 0)
    game_id = match.get("gameId", 0)
    queue_name = QUEUE_NAMES.get(match.get("queueId",0), "未知模式")
    
    row = f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="border:none;">
      <tr>
        <td width="65" align="center" valign="middle">
          {champ_img_html}
        </td>
        <td width="35" align="center" valign="middle">
          {rune_html}
        </td>
        <td width="35" align="center" valign="middle">
          {spells_html}
        </td>
        <td width="100" valign="middle" style="padding-left:6px;">
          <div style="font-size:17px; font-weight:bold; color:{result_color}; margin-bottom:4px;">{result_text}</div>
          <div style="font-size:13px; color:rgba(255,255,255,0.3); display:inline-block; padding:2px 10px; border-radius:10px;">{queue_name}</div>
        </td>
        <td width="120" align="center" valign="middle">
          <div style="font-size:15px; font-weight:bold; color:{COLOR_TEXT_MAIN}; margin-bottom:4px;">{k} <span style="color:rgba(255,255,255,0.25);">/</span> <span style="color:{COLOR_DANGER};">{d}</span> <span style="color:rgba(255,255,255,0.25);">/</span> {a}</div>
          <div style="font-size:11px; color:rgba(255,255,255,0.3); letter-spacing:2px;">KDA</div>
        </td>
        <td align="left" valign="middle" style="padding:2px 4px;">
          {items_str}
        </td>
        <td width="80" align="center" valign="middle">
          <div style="font-size:14px; font-weight:bold; color:{COLOR_WARN}; margin-bottom:4px;">{gold}</div>
          <div style="font-size:10px; color:rgba(255,255,255,0.25);">💰 金币</div>
        </td>
        <td width="60" align="center" valign="middle">
          <div style="font-size:14px; font-weight:bold; color:{COLOR_TEXT_MAIN}; margin-bottom:4px;">{cs}</div>
          <div style="font-size:10px; color:rgba(255,255,255,0.25);">📊 补兵</div>
        </td>
      </tr>
    </table>
    """
    
    if detail_html:
        return (
            f'<div style="margin-bottom:8px; border-left:4px solid {result_color}; border-radius:6px; background:{COLOR_BG_CARD};">'
            f'<a href="action:toggle_match/{game_id}" style="text-decoration:none;">'
            f'<div style="padding:8px; background:rgba(255,255,255,0.02); border-radius:6px 6px 0 0;">{row}</div>'
            f'</a>'
            f'<div style="padding:0px 8px 10px 8px; background:{COLOR_BG_CARD}; border-radius:0 0 6px 6px;">'
            f'<div style="border-top:1px solid rgba(255,255,255,0.03); padding-top:8px; margin-bottom:8px; text-align:center;">'
            f'<a href="action:toggle_match/{game_id}" style="color:{COLOR_TEXT_SUB}; font-size:12px; font-weight:bold; text-decoration:none; background:rgba(255,255,255,0.03); padding:4px 12px; border-radius:12px;">▲ 收起对局详情</a>'
            f'</div>{detail_html}</div></div>', game_id
        )
    return (
        f'<a href="action:toggle_match/{game_id}" style="text-decoration:none;">'
        f'<div style="margin-bottom:6px; padding:8px; background:{COLOR_BG_CARD}; border-radius:6px; border-left:4px solid {result_color};">{row}</div>'
        f'</a>', game_id
    )

def _build_detail_html(match_data, highlight_name=""):
    participants = match_data.get('participants', [])
    identities = match_data.get('participantIdentities', [])
    if not participants: return '<span style="color:#ffffff; font-size:13px;">无数据</span>'
    
    team_100, team_200 = [], []
    for i, ident in enumerate(identities):
        if i < len(participants):
            pid = participants[i]
            player = ident.get('player', {})
            (team_100 if pid.get('teamId') == 100 else team_200).append((pid, player))
    
    html_parts = []
    for team, label, color, bg_color in [(team_100, "蓝方队伍", COLOR_ACCENT, "rgba(122,162,247,0.08)"), (team_200, "红方队伍", COLOR_DANGER, "rgba(247,118,142,0.08)")]:
        rows = ""
        for pid, player in team:
            stats = pid.get('stats', {})
            champ_id = pid.get('championId', 0)
            ci = image_helper.get_champion_icon_path(champ_id) if image_helper else None
            cimg = f'<img src="file:///{path_to_url(ci)}" width="32" height="32" style="vertical-align:middle; border-radius:16px;">' if ci else '—'
            
            # --- 核心符文 (先符文, 纵向排列) ---
            perk0 = stats.get('perk0', 0)
            perk_sub_style = stats.get('perkSubStyle', 0)
            p0_icon = get_perk(perk0).get('icon', '') if perk0 else ''
            pSub_icon = get_style(perk_sub_style).get('icon', '') if perk_sub_style else ''
            
            rune_html = ""
            if p0_icon or pSub_icon:
                rune_html = '<div style="line-height:1; text-align:center;">'
                if p0_icon: 
                    rune_html += f'<div style="margin-bottom:2px;"><img src="file:///{path_to_url(p0_icon)}" width="20" height="20" style="vertical-align:middle; border-radius:10px; border:1px solid rgba(255,255,255,0.1); background:rgba(0,0,0,0.4);"></div>'
                else:
                    rune_html += f'<div style="margin-bottom:2px;"><img src="{TRANSPARENT_IMG}" width="20" height="20"></div>'
                if pSub_icon: 
                    rune_html += f'<div><img src="file:///{path_to_url(pSub_icon)}" width="14" height="14" style="vertical-align:middle; border-radius:7px;"></div>'
                else:
                    rune_html += f'<div><img src="{TRANSPARENT_IMG}" width="14" height="14"></div>'
                rune_html += '</div>'
            else:
                rune_html = f'<div style="text-align:center;"><img src="{TRANSPARENT_IMG}" width="20" height="20"></div>'
            
            # --- 召唤师技能 (后技能, 纵向排列) ---
            spell1_id = pid.get('spell1Id', 0)
            spell2_id = pid.get('spell2Id', 0)
            spell1_icon = path_to_url(image_helper.get_spell_icon_path(spell1_id)) if image_helper and spell1_id else ""
            spell2_icon = path_to_url(image_helper.get_spell_icon_path(spell2_id)) if image_helper and spell2_id else ""
            
            spells_html = ""
            if spell1_icon or spell2_icon:
                spells_html = '<div style="line-height:1; text-align:center;">'
                if spell1_icon: 
                    spells_html += f'<div style="margin-bottom:2px;"><img src="file:///{spell1_icon}" width="18" height="18" style="vertical-align:middle; border-radius:3px;"></div>'
                else:
                    spells_html += f'<div style="margin-bottom:2px;"><img src="{TRANSPARENT_IMG}" width="18" height="18"></div>'
                if spell2_icon: 
                    spells_html += f'<div><img src="file:///{spell2_icon}" width="18" height="18" style="vertical-align:middle; border-radius:3px;"></div>'
                else:
                    spells_html += f'<div><img src="{TRANSPARENT_IMG}" width="18" height="18"></div>'
                spells_html += '</div>'
            else:
                spells_html = f'<div style="text-align:center;"><img src="{TRANSPARENT_IMG}" width="18" height="18"></div>'
            
            # --- 段位图标 ---
            tier = rank_cache.get(player.get('puuid'), 'NONE')
            tier_cn = TIER_TRANSLATE.get(tier, tier)
            tier_url = get_tier_icon_url(tier)
            if tier_url:
                tier_img = f'<img src="file:///{tier_url}" width="40" height="32" style="vertical-align:middle;" title="{tier_cn}">'
            else:
                tier_img = (f'<span style="display:inline-block; width:40px; height:32px; vertical-align:middle; '
                           f'background:rgba(255,255,255,0.06); border-radius:4px; text-align:center; line-height:32px; '
                           f'font-size:10px; color:rgba(255,255,255,0.3);">{tier_cn[:2]}</span>')
            
            # 装备展示与透明空位修复
            ir = ""
            for i in range(7):
                item_id = stats.get(f'item{i}', 0)
                margin = "5px" if i == 5 else "2px"
                if item_id and image_helper and image_helper.get_item_icon_path(item_id):
                    ir += f'<img src="file:///{path_to_url(image_helper.get_item_icon_path(item_id))}" width="26" height="26" style="vertical-align:middle; margin-right:{margin}; border-radius:4px;">'
                else:
                    ir += f'<img src="{TRANSPARENT_IMG}" width="26" height="26" style="vertical-align:middle; margin-right:{margin}; border-radius:4px; background:rgba(0,0,0,0.25);">'
                    
            pn = player.get('gameName', player.get('summonerName', '?'))
            name_color = COLOR_WARN if highlight_name and highlight_name.lower() in pn.lower() else COLOR_TEXT_MAIN
            k_, d_, a_ = stats.get('kills',0), stats.get('deaths',0), stats.get('assists',0)
            cs = stats.get('neutralMinionsKilled',0) + stats.get('totalMinionsKilled',0)
            gold_str = format_k(stats.get('goldEarned', 0))
            dmg_str = format_k(stats.get('totalDamageDealtToChampions', 0))
            rc = COLOR_SUCCESS if stats.get("win") else COLOR_DANGER
            
            # 加了行下边线、段位/符文分列：
            rows += (f'<tr style="border-bottom: 1px solid rgba(255,255,255,0.02);">'
                    f'<td style="padding:6px 4px; text-align:center; width:45px;">{cimg}</td>'
                    f'<td style="padding:6px 4px; color:{name_color}; font-size:14px; font-weight:bold; max-width:110px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="{pn}">{pn}</td>'
                    f'<td style="padding:6px 4px; text-align:center; width:55px;">{tier_img}</td>'
                    f'<td style="padding:6px 4px; text-align:center; width:45px;">{rune_html}</td>'
                    f'<td style="padding:6px 4px; text-align:center; width:45px;">{spells_html}</td>'
                    f'<td style="padding:6px 4px; min-width:200px;">{ir}</td>'
                    f'<td style="padding:6px 4px; color:{rc}; font-size:14px; text-align:center; font-weight:bold;">{k_}/{d_}/{a_}</td>'
                    f'<td style="padding:6px 4px; color:{COLOR_WARN}; font-size:13px; text-align:right; font-weight:bold;">{gold_str}</td>'
                    f'<td style="padding:6px 4px; color:#ff9e64; font-size:13px; text-align:right; font-weight:bold;">{dmg_str}</td>'
                    f'<td style="padding:6px 12px 6px 4px; color:{COLOR_TEXT_MAIN}; font-size:13px; text-align:right;">{cs}</td>'
                    f'</tr>')
        
        # 加了表头、背景色、分队颜色区分：
        hdr = (f'<tr style="background:{bg_color};">'
              f'<th colspan="2" style="padding:8px 10px; text-align:left; color:{color}; font-size:14px; font-weight:bold; border-radius:4px 0 0 0;">■ {label}</th>'
              f'<th style="padding:8px 4px; color:rgba(255,255,255,0.4); font-size:12px; text-align:center; width:55px;">段位</th>'
              f'<th style="padding:8px 4px; color:rgba(255,255,255,0.4); font-size:12px; text-align:center; width:45px;">符文</th>'
              f'<th style="padding:8px 4px; color:rgba(255,255,255,0.4); font-size:12px; text-align:center; width:45px;">技能</th>'
              f'<th style="padding:8px 4px; color:rgba(255,255,255,0.4); font-size:12px; text-align:left;">装备</th>'
              f'<th style="padding:8px 4px; color:rgba(255,255,255,0.4); font-size:12px; text-align:center;">KDA</th>'
              f'<th style="padding:8px 4px; color:rgba(255,255,255,0.4); font-size:12px; text-align:right;">金钱</th>'
              f'<th style="padding:8px 4px; color:rgba(255,255,255,0.4); font-size:12px; text-align:right;">伤害</th>'
              f'<th style="padding:8px 12px 8px 4px; color:rgba(255,255,255,0.4); font-size:12px; text-align:right; border-radius:0 4px 0 0;">补兵</th>'
              f'</tr>')
        html_parts.append(f'<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:10px; border:1px solid rgba(255,255,255,0.03); border-radius:4px; background:rgba(0,0,0,0.15);">{hdr}{rows}</table>')
    return "".join(html_parts)

async def _fetch_match_detail_data(connection, game_id):
    try:
        resp = await connection.request('get', f'/lol-match-history/v1/games/{game_id}')
        return await resp.json() if resp.status == 200 else None
    except Exception: return None

def _rerender_search():
    if not main_window or not main_window.match_cache:
        return
    cache = main_window.match_cache
    print_search("CLEAR")
    pname = getattr(main_window, '_last_search_name', '')
    ptag = getattr(main_window, '_last_search_tag', '')
    print_search(f'<div style="padding:12px 0 16px 0; color:{COLOR_TEXT_MAIN}; font-weight:bold;">'
                 f'<span style="color:{COLOR_WARN}; font-size:22px;">{pname}</span><span style="color:{COLOR_TEXT_SUB}; font-size:16px;">#{ptag}</span> <span style="font-size:16px;">的战绩记录</span></div>')
    for idx, (match, detail_html) in enumerate(cache, 1):
        html, _ = _render_one_match(idx, match, detail_html=detail_html)
        print_search(html)

def _rerender_home():
    if not main_window or not hasattr(main_window, '_home_match_cache'): return
    for html in getattr(main_window, '_home_match_cache', []): print_home(html)
    if main_window._home_match_cache: print_home("")

async def get_match_history_detailed(connection, puuid, game_name, tagLine=""):
    try:
        resp = await connection.request('get', f'/lol-match-history/v1/products/lol/{puuid}/matches')
        if resp.status != 200: return print_search(f"<span style='color:{COLOR_DANGER}; font-size:14px; font-weight:bold;'>[提示] 无法获取战绩</span>")
        matches = (await resp.json()).get('games', {}).get('games', [])
        if not matches: return print_search(f"<span style='color:{COLOR_DANGER}; font-size:14px; font-weight:bold;'>[提示] 暂无对局记录</span>")
        
        matches = matches[:10]
        main_window.match_cache = [(m, "") for m in matches]
        main_window._last_search_name = game_name
        main_window._last_search_tag = tagLine
        main_window.expanded_game_ids.clear()
        _rerender_search()
    except Exception: pass

async def fetch_my_recent_matches(connection, puuid, player_name, tagline):
    try:
        resp = await connection.request('get', f'/lol-match-history/v1/products/lol/{puuid}/matches')
        if resp.status != 200: return
        matches = (await resp.json()).get('games', {}).get('games', [])
        if not matches: return
        
        html_parts = [f'<div style="margin-top:16px; margin-bottom:12px;">'
                      f'<span style="color:{COLOR_ACCENT}; font-size:18px; font-weight:bold;">📋 我的最近战绩</span></div>']
        for idx, match in enumerate(matches[:10], 1):
            html, _ = _render_one_match(idx, match)
            html_parts.append(html)
        main_window._home_match_cache = html_parts
        for h in html_parts:
            print_home(h)
    except Exception: pass

async def fetch_player_rating(connection, puuid):
    if not puuid: return None
    tier = await get_player_tier(connection, puuid)
    try:
        resp = await connection.request('get', f'/lol-match-history/v1/products/lol/{puuid}/matches')
        if resp.status != 200: return {"tag": "未知", "color": "#a6adc8", "win_rate": 0, "kda": 0, "score": 0, "tier": tier}
        matches = (await resp.json()).get('games', {}).get('games', [])
        if not matches: return {"tag": "未知", "color": "#a6adc8", "win_rate": 0, "kda": 0, "score": 0, "tier": tier}
        total_wins = total_kills = total_deaths = total_assists = valid_games = 0
        for match in matches[:20]:
            stats = match['participants'][0]['stats']
            if stats.get('win'): total_wins += 1
            total_kills += stats.get('kills', 0)
            total_deaths += stats.get('deaths', 0)
            total_assists += stats.get('assists', 0)
            valid_games += 1
        if valid_games == 0: return {"tag": "未知", "color": "#a6adc8", "win_rate": 0, "kda": 0, "score": 0, "tier": tier}
        win_rate = total_wins / valid_games * 100
        avg_kda = float(total_kills + total_assists) if total_deaths == 0 else (total_kills + total_assists) / total_deaths
        score = (win_rate * 0.3) + (avg_kda * 10 * 0.7)
        if score >= 50: tag, tag_color = "通天代", "#ff9e64"
        elif score >= 45: tag, tag_color = "小代", "#e0af68"
        elif score >= 35: tag, tag_color = "上等马", "#9ece6a"
        elif score >= 28: tag, tag_color = "中等马", "#7aa2f7"
        elif score >= 24: tag, tag_color = "下等马", "#a6adc8"
        else: tag, tag_color = "牛马", "#f7768e"
        return {"tag": tag, "color": tag_color, "win_rate": win_rate, "kda": avg_kda, "score": score, "tier": tier}
    except Exception: return {"tag": "未知", "color": "#a6adc8", "win_rate": 0, "kda": 0, "score": 0, "tier": tier}

def render_team_table(team, team_label, ratings, border_color, bg_tint):
    rows = ""
    for member in team:
        champ_id = member.get('championId', 0)
        puuid = member.get('puuid', '')
        name = member.get('gameName', member.get('summonerName', '?'))
        tagline = member.get('tagLine', '')
        full_name = f"{name}#{tagline}" if tagline else name
        
        champ_img = (f'<img src="file:///{path_to_url(image_helper.get_champion_icon_path(champ_id))}" width="40" height="40" style="vertical-align:middle; border-radius:20px; border:1px solid {border_color};">'
                     if image_helper and champ_id != 0 else f'<span style="color:rgba(255,255,255,0.2);">—</span>')
        champ_name = champion_map.get(str(champ_id), "?") if champ_id != 0 else "未选"
        
        spell1_id = member.get('spell1Id', 0)
        spell2_id = member.get('spell2Id', 0)
        spell1_icon = path_to_url(image_helper.get_spell_icon_path(spell1_id)) if image_helper and spell1_id else ""
        spell2_icon = path_to_url(image_helper.get_spell_icon_path(spell2_id)) if image_helper and spell2_id else ""
        spells_html = ""
        if spell1_icon: spells_html += f'<img src="file:///{spell1_icon}" width="24" height="24" style="vertical-align:middle; border-radius:4px; margin-right:4px;">'
        if spell2_icon: spells_html += f'<img src="file:///{spell2_icon}" width="24" height="24" style="vertical-align:middle; border-radius:4px;">'
        
        tier = "NONE"
        rating_html = ""
        if puuid and puuid in ratings and ratings[puuid]:
            r = ratings[puuid]
            tier = r.get("tier", "NONE")
            rating_html = (f'<span style="color:{r["color"]}; font-weight:bold; font-size:13px; background:rgba(255,255,255,0.05); padding:4px 8px; border-radius:4px; margin-right:8px;">{r["tag"]}</span> '
                          f'<span style="color:{COLOR_TEXT_MAIN}; font-size:13px; font-weight:bold;">胜率:{r["win_rate"]:.0f}%</span>')
        else:
            rating_html = f'<span style="color:rgba(255,255,255,0.3); font-size:13px;">分析中...</span>'
            
        tier_cn = TIER_TRANSLATE.get(tier, tier)
        tier_url = get_tier_icon_url(tier)
        if tier_url:
            tier_img = f'<img src="file:///{tier_url}" width="40" height="32" style="vertical-align:middle; margin-right:8px;" title="{tier_cn}">'
        else:
            tier_img = f'<span style="display:inline-block; width:40px; height:32px; vertical-align:middle; margin-right:8px; background:rgba(255,255,255,0.05); border-radius:4px; text-align:center; line-height:32px; font-size:10px; color:rgba(255,255,255,0.3);">?</span>'        
        name_display = (f'<a href="action:copy_name/{name}/{tagline}" style="color:{COLOR_WARN}; text-decoration:none; font-weight:bold; font-size:14px; vertical-align:middle;" title="点击复制名字">'
                        f'{full_name} 📋</a>'
                        if puuid else f'<span style="color:{COLOR_TEXT_MAIN}; font-size:14px; font-weight:bold; vertical-align:middle;">{full_name}</span>')
        search_link = (f'<a href="action:player/{puuid}/{name}/{tagline}" style="color:{COLOR_ACCENT}; text-decoration:none; font-size:12px; vertical-align:middle; margin-left:6px;" title="查看战绩">🔍</a>'
                       if puuid else '')
        
        rows += (f'<tr>'
                f'<td style="padding:10px 8px; width:52px; text-align:center;">{champ_img}</td>'
                f'<td style="padding:10px 8px; white-space:nowrap;">{tier_img}{name_display}{search_link}</td>'
                f'<td style="padding:10px 8px; color:{COLOR_TEXT_SUB}; font-size:14px; font-weight:bold;">{champ_name}</td>'
                f'<td style="padding:10px 8px; white-space:nowrap;">{spells_html}</td>'
                f'<td style="padding:10px 8px;">{rating_html}</td>'
                f'</tr>')
    
    header = (f'<tr style="background:{bg_tint};">'
              f'<th colspan="5" style="padding:10px 14px; text-align:left; color:{border_color}; font-size:15px; font-weight:bold; border-radius:6px 6px 0 0;">■ {team_label}</th>'
              f'</tr>')
    
    return (f'<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:14px; border:1px solid rgba(255,255,255,0.05); border-radius:6px; font-size:14px; background:{COLOR_BG_CARD};">'
            f'{header}{rows}</table>')

def get_lineup_fingerprint(my_team):
    return tuple((m.get('cellId'), m.get('championId'), m.get('spell1Id'), m.get('spell2Id')) for m in my_team)

async def monitor_one_game(connection):
    global is_monitoring, global_sum_name, last_my_champion_id
    
    error_count = 0
    while is_monitoring:
        try:
            rc_resp = await connection.request('get', '/lol-matchmaking/v1/ready-check')
            if rc_resp.status == 200:
                rc_data = await rc_resp.json()
                if rc_data.get('state') == 'InProgress' and rc_data.get('playerResponse') == 'None':
                    if main_window and main_window.auto_accept_cb.isChecked():
                        await connection.request('post', '/lol-matchmaking/v1/ready-check/accept')
                        print_monitor(f"<div style='color:{COLOR_SUCCESS}; font-weight:bold; padding:10px; font-size:14px; background:rgba(158,206,106,0.15); border-radius:4px; margin:12px 0;'>[系统] 已为您自动接受匹配！</div>")
        except Exception: pass

        try:
            resp = await connection.request('get', '/lol-gameflow/v1/session')
            if resp.status == 200 and (await resp.json()).get('phase') == 'ChampSelect': break
        except Exception:
            error_count += 1
            if error_count >= 3:
                with is_monitoring_lock: is_monitoring = False
                print_monitor(f"<div style='color:{COLOR_DANGER}; padding:12px; font-size:14px;'>[系统] 检测到客户端关闭。</div>")
                return
        await asyncio.sleep(1)

    if not is_monitoring: return

    try:
        resp = await connection.request('get', '/lol-champ-select/v1/session')
        if resp.status == 200:
            session = await resp.json()
            my_team = session.get('myTeam', [])
            their_team = session.get('theirTeam', [])
            if main_window: main_window.switch_to_monitor_tab.emit()
            print_monitor(f"<div style='color:{COLOR_TEXT_SUB}; padding:10px; font-size:14px;'>正在深度分析双方战绩与段位...</div>")
            
            rating_tasks = {}
            for member in my_team + their_team:
                puuid = member.get('puuid', '')
                if puuid: rating_tasks[puuid] = asyncio.create_task(fetch_player_rating(connection, puuid))
            ratings = {}
            for puuid, task in rating_tasks.items(): ratings[puuid] = await task
            
            print_monitor("CLEAR_TEAM")
            print_monitor(render_team_table(my_team, "我方阵容", ratings, COLOR_SUCCESS, "rgba(158,206,106,0.1)"))
            print_monitor(render_team_table(their_team, "敌方阵容", ratings, COLOR_DANGER, "rgba(247,118,142,0.1)"))
            
            last_fingerprint = get_lineup_fingerprint(my_team)
            last_their_fingerprint = get_lineup_fingerprint(their_team)
        else: return
    except Exception: return

    name_to_id = {v: int(k) for k, v in champion_map.items()}
    action_completed_ids = set()
    warned_bad_names = set()

    while is_monitoring:
        try:
            flow_resp = await connection.request('get', '/lol-gameflow/v1/session')
            if flow_resp.status != 200:
                await asyncio.sleep(1); continue

            phase = (await flow_resp.json()).get('phase')

            if phase == 'ChampSelect':
                cs_resp = await connection.request('get', '/lol-champ-select/v1/session')
                if cs_resp.status == 200:
                    session = await cs_resp.json()
                    my_team = session.get('myTeam', [])
                    local_cell_id = session.get('localPlayerCellId')
                    
                    if main_window:
                        pick_enabled = main_window.auto_pick_cb.isChecked()
                        ban_enabled = main_window.auto_ban_cb.isChecked()
                        pick_text = main_window.auto_pick_combo.currentText().strip()
                        ban_text = main_window.auto_ban_combo.currentText().strip()
                        pick_id = name_to_id.get(pick_text, 0)
                        ban_id = name_to_id.get(ban_text, 0)
                        
                        if pick_enabled and pick_id == 0 and pick_text and pick_text not in warned_bad_names:
                            warned_bad_names.add(pick_text)
                            print_monitor(f"<div style='color:{COLOR_WARN}; font-size:14px;'>[系统] 未找到英雄 '{pick_text}'，请检查名称</div>")
                        if ban_enabled and ban_id == 0 and ban_text and ban_text not in warned_bad_names:
                            warned_bad_names.add(ban_text)
                            print_monitor(f"<div style='color:{COLOR_WARN}; font-size:14px;'>[系统] 未找到英雄 '{ban_text}'，请检查名称</div>")

                        for action_row in session.get('actions', []):
                            for action in action_row:
                                if action.get('actorCellId') == local_cell_id and action.get('isInProgress'):
                                    act_id = action.get('id')
                                    if act_id not in action_completed_ids:
                                        action_completed_ids.add(act_id)
                                        if action.get('type') == 'pick' and pick_enabled and pick_id != 0:
                                            try:
                                                await connection.request('patch', f'/lol-champ-select/v1/session/actions/{act_id}', json={"championId": pick_id})
                                                await connection.request('post', f'/lol-champ-select/v1/session/actions/{act_id}/complete')
                                                print_monitor(f"<div style='color:{COLOR_SUCCESS}; padding:6px; font-size:14px;'>[系统] 已锁定秒选：{pick_text}</div>")
                                            except Exception: pass
                                        elif action.get('type') == 'ban' and ban_enabled and ban_id != 0:
                                            try:
                                                await connection.request('patch', f'/lol-champ-select/v1/session/actions/{act_id}', json={"championId": ban_id})
                                                await connection.request('post', f'/lol-champ-select/v1/session/actions/{act_id}/complete')
                                                print_monitor(f"<div style='color:{COLOR_DANGER}; padding:6px; font-size:14px;'>[系统] 已锁定秒禁：{ban_text}</div>")
                                            except Exception: pass

                    current_my_champion_id = next((m.get('championId', 0) for m in my_team if m.get('cellId') == local_cell_id), 0)
                    if current_my_champion_id != 0 and current_my_champion_id != last_my_champion_id:
                        last_my_champion_id = current_my_champion_id
                        asyncio.create_task(update_opgg_data(current_my_champion_id, connection))
                        
                    current_fingerprint = get_lineup_fingerprint(my_team)
                    their_team_cs = session.get('theirTeam', [])
                    current_their_fingerprint = get_lineup_fingerprint(their_team_cs)
                    if current_fingerprint != last_fingerprint or current_their_fingerprint != last_their_fingerprint:
                        new_ratings = {}
                        for member in my_team + their_team_cs:
                            puuid = member.get('puuid', '')
                            if puuid: new_ratings[puuid] = await fetch_player_rating(connection, puuid)
                        print_monitor("CLEAR_TEAM")
                        print_monitor(render_team_table(my_team, "我方阵容", new_ratings, COLOR_SUCCESS, "rgba(158,206,106,0.1)"))
                        print_monitor(render_team_table(their_team_cs, "敌方阵容", new_ratings, COLOR_DANGER, "rgba(247,118,142,0.1)"))
                        last_fingerprint = current_fingerprint
                        last_their_fingerprint = current_their_fingerprint
                await asyncio.sleep(0.5)

            elif phase in ['GameStart', 'InProgress']:
                action_completed_ids.clear()
                warned_bad_names.clear()
                print_monitor(f"<div style='color:{COLOR_TEXT_SUB}; padding:10px; font-size:14px;'>游戏已开始，加载最新数据...</div>")
                game_data = (await flow_resp.json()).get('gameData', {})
                my_puuids = {m.get('puuid') for m in my_team if m.get('puuid')}
                
                all_players = game_data.get('teamOne', []) + game_data.get('teamTwo', [])
                my_side = [p for p in all_players if p.get('puuid') in my_puuids]
                enemy_side = [p for p in all_players if p.get('puuid') not in my_puuids]
                
                game_ratings = {}
                for player in all_players:
                    puuid = player.get('puuid', '')
                    if puuid: game_ratings[puuid] = await fetch_player_rating(connection, puuid)
                
                print_monitor("CLEAR_TEAM")
                print_monitor(render_team_table(my_side, "我方队伍", game_ratings, COLOR_SUCCESS, "rgba(158,206,106,0.1)"))
                print_monitor(render_team_table(enemy_side, "敌方队伍", game_ratings, COLOR_DANGER, "rgba(247,118,142,0.1)"))
                print_monitor(f"<div style='color:rgba(255,255,255,0.4); text-align:center; padding:12px; font-size:13px;'>监控对局状态中...</div>")
                while is_monitoring:
                    end_resp = await connection.request('get', '/lol-gameflow/v1/session')
                    if end_resp.status == 200 and (await end_resp.json()).get('phase') not in ['GameStart', 'InProgress', 'Reconnect']: break
                    await asyncio.sleep(3)
                break

            elif phase in ['Lobby', 'Matchmaking', 'ReadyCheck', 'None']:
                action_completed_ids.clear()
                warned_bad_names.clear()
                print_monitor(f"<div style='color:{COLOR_WARN}; padding:12px; font-size:14px;'>等待下一局...</div>")
                await asyncio.sleep(2)
                break
            else: await asyncio.sleep(1)
        except Exception: await asyncio.sleep(2)

@connector.close
async def disconnect(_):
    global is_monitoring
    with is_monitoring_lock:
        is_monitoring = False

@connector.ready
async def connect(connection):
    global is_monitoring, main_window, monitor_loop, image_helper, global_sum_id
    with is_monitoring_lock:
        is_monitoring = True
    monitor_loop = asyncio.get_running_loop()
    image_helper = get_image_helper()
    init_local_resources()
    
    print_home(f"<div style='color:{COLOR_SUCCESS}; padding:12px; font-size:14px; font-weight:bold;'>[系统] 资源加载就绪，连接成功！</div>")

    if main_window: main_window.connection = connection; main_window.loop_ready.emit()

    for _ in range(3):
        if not is_monitoring: return
        try:
            resp_sum = await connection.request('get', '/lol-summoner/v1/current-summoner')
            if resp_sum.status == 200:
                data = await resp_sum.json()
                global_sum_id = data.get('summonerId')  
                xpnow = data.get('xpSinceLastLevel', 0)
                xpnext = data.get('xpUntilNextLevel', 0)
                icon_path = os.path.join(DATA_DIR, "profileicon", f"{data.get('profileIconId', 29)}.png")
                icon_html = f'<img src="file:///{path_to_url(icon_path)}" width="64" height="64" style="vertical-align:middle; border-radius:32px; margin-right:18px; border:2px solid {COLOR_ACCENT};">' if os.path.exists(icon_path) else ""
                user_puuid = data.get('puuid', '')
                user_name = data.get('gameName', '未知')
                user_tag = data.get('tagLine', '未知')
                
                print_home(f"""
                <div style='padding:18px; background: {COLOR_BG_CARD}; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.03); margin-bottom: 18px; margin-top: 12px;'>
                    <table cellpadding="0" cellspacing="0"><tr>
                        <td>{icon_html}</td>
                        <td>
                            <div style='font-size:20px; font-weight:bold; color:{COLOR_TEXT_MAIN}; margin-bottom:6px;'>{user_name}<span style='font-size:15px; color:{COLOR_TEXT_SUB};'>#{user_tag}</span></div>
                            <span style='color:{COLOR_TEXT_MAIN}; font-size:13px; font-weight:bold; margin-right:14px; background:rgba(255,255,255,0.05); padding:6px 10px; border-radius:4px;'>等级: {data.get('summonerLevel', 0)}</span>
                            <span style='color:rgba(255,255,255,0.4); font-size:13px; font-weight:bold;'>经验: {xpnow} / {xpnext}</span>
                        </td>
                    </tr></table>
                </div>
                """)
                if user_puuid:
                    asyncio.create_task(fetch_my_recent_matches(connection, user_puuid, user_name, user_tag))
                break
        except Exception: pass
        await asyncio.sleep(3)

    while is_monitoring:
        await monitor_one_game(connection)

def run_monitor(): connector.start()

# ================= PySide6 UI =================
class TitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.setFixedHeight(45)
        self.drag_pos = QPoint()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 0, 10, 0)
        
        self.title_label = QLabel(" 清梦 - 英雄联盟助手")
        self.title_label.setStyleSheet(f"color: {COLOR_TEXT_MAIN}; font-size: 30px; font-weight: bold; font-family: 'Microsoft YaHei', 'Segoe UI';")
        layout.addWidget(self.title_label)
        layout.addStretch()
        
        self.min_btn = QPushButton("—")
        self.min_btn.setFixedSize(36, 30)
        self.min_btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {COLOR_TEXT_SUB}; border: none; font-size: 14px; border-radius: 4px; }} QPushButton:hover {{ background: rgba(255,255,255,0.1); color: {COLOR_TEXT_MAIN}; }}")
        self.min_btn.clicked.connect(self.parent.showMinimized)
        layout.addWidget(self.min_btn)
        
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(36, 30)
        self.close_btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {COLOR_TEXT_SUB}; border: none; font-size: 14px; border-radius: 4px; }} QPushButton:hover {{ background: {COLOR_DANGER}; color: {COLOR_BG_MAIN}; }}")
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

# 全局极简紧凑 QSS
MODERN_QSS = f"""
QMainWindow {{
    background: transparent;
}}
#central {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {COLOR_BG_MAIN},
        stop:1 #151720);
    border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.08);
}}
QTextBrowser {{
    background: transparent;
    color: {COLOR_TEXT_MAIN};
    border: none;
    padding: 0px;
}}
QScrollBar:horizontal {{
    height: 0px; 
}}
QScrollBar:vertical {{
    border: none;
    background: transparent;
    width: 6px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: rgba(255, 255, 255, 0.12);
    min-height: 30px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(255, 255, 255, 0.30);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    border: none;
    background: transparent;
}}
QTabWidget::pane {{
    background: transparent;
    border-radius: 8px;
    margin: 0px 12px 12px 12px;
}}
QTabBar::tab {{
    background: transparent;
    color: {COLOR_TEXT_SUB};
    padding: 14px 24px;
    font-size: 15px;
    font-weight: bold;
    border-bottom: 3px solid transparent;
    margin-right: 6px;
}}
QTabBar::tab:selected {{
    color: {COLOR_ACCENT};
    border-bottom: 3px solid {COLOR_ACCENT};
    background: rgba(122,162,247,0.06);
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QTabBar::tab:hover:!selected {{
    color: {COLOR_TEXT_MAIN};
    background: rgba(255, 255, 255, 0.03);
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QLineEdit {{
    background-color: {COLOR_BG_CARD};
    color: {COLOR_TEXT_MAIN};
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 6px;
    padding: 0 14px;
    font-size: 14px;
}}
QLineEdit:focus {{
    border: 1px solid {COLOR_ACCENT};
}}
QPushButton {{
    background-color: {COLOR_ACCENT};
    color: {COLOR_BG_MAIN};
    border: none;
    border-radius: 6px;
    font-weight: bold;
    font-size: 14px;
}}
QPushButton:hover {{
    background-color: #8bb4ff;
}}
QPushButton:pressed {{
    background-color: #6a9ae8;
}}
QPushButton:hover {{
    background-color: #b4befe;
}}
QPushButton:disabled {{
    background-color: rgba(255,255,255,0.1);
    color: rgba(255,255,255,0.3);
}}
QCheckBox {{
    color: {COLOR_TEXT_MAIN};
    font-size: 14px;
    font-weight: bold;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid rgba(255,255,255,0.2);
    background: {COLOR_BG_CARD};
}}
QCheckBox::indicator:checked {{
    background: {COLOR_ACCENT};
    border: 1px solid {COLOR_ACCENT};
}}
QComboBox {{
    background: {COLOR_BG_CARD};
    color: {COLOR_TEXT_MAIN};
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 4px;
    padding-left: 10px;
    font-size: 14px;
    font-weight: bold;
}}
QComboBox:hover {{
    border: 1px solid {COLOR_ACCENT};
}}
QComboBox QAbstractItemView {{
    background: {COLOR_BG_CARD};
    color: {COLOR_TEXT_MAIN};
    selection-background-color: rgba(255,255,255,0.1);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 4px;
    outline: none;
}}
"""

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
        self.match_cache = []          
        self.expanded_game_ids = set() 

        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 将原 920x680 扩大
        self.setGeometry(100, 100, 1060, 780) 
        central_widget = QWidget(); central_widget.setObjectName("central")
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0); main_layout.setSpacing(0)
        self.title_bar = TitleBar(self); main_layout.addWidget(self.title_bar)

        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(15, 0, 15, 10); search_layout.setSpacing(10); search_layout.addStretch()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入 名字#编号 查战绩...")
        self.search_input.setFixedSize(260, 34)
        search_layout.addWidget(self.search_input)

        self.search_btn = QPushButton("搜索")
        self.search_btn.setFixedSize(90, 34)
        self.search_btn.clicked.connect(self.start_search)
        self.search_btn.setEnabled(False)
        search_layout.addWidget(self.search_btn)
        main_layout.addLayout(search_layout)

        self.tab_widget = QTabWidget()

        home_widget = QWidget()
        home_layout = QVBoxLayout(home_widget)
        home_layout.setContentsMargins(12, 12, 12, 12)
        
        auto_panel = QFrame()
        auto_panel.setStyleSheet(f"QFrame {{ background: rgba(0,0,0,0.15); border-radius: 6px; border: 1px solid rgba(255,255,255,0.03); }}")
        auto_layout = QHBoxLayout(auto_panel)
        auto_layout.setContentsMargins(15, 12, 15, 12)

        self.auto_pick_cb = QCheckBox("启用自动秒选")
        self.auto_pick_cb.setStyleSheet(f"color: {COLOR_WARN}; font-size:14px;")
        self.auto_pick_combo = QComboBox()
        self.auto_pick_combo.setEditable(True)
        self.auto_pick_combo.setFixedSize(140, 30)

        self.auto_ban_cb = QCheckBox("启用自动秒禁")
        self.auto_ban_cb.setStyleSheet(f"color: {COLOR_DANGER}; font-size:14px;")
        self.auto_ban_combo = QComboBox()
        self.auto_ban_combo.setEditable(True)
        self.auto_ban_combo.setFixedSize(140, 30)

        champ_names = sorted(list(champion_map.values()))
        self.auto_pick_combo.addItems([""] + champ_names)
        self.auto_ban_combo.addItems([""] + champ_names)
        
        pick_completer = QCompleter(champ_names); pick_completer.setFilterMode(Qt.MatchContains)
        self.auto_pick_combo.setCompleter(pick_completer)
        ban_completer = QCompleter(champ_names); ban_completer.setFilterMode(Qt.MatchContains)
        self.auto_ban_combo.setCompleter(ban_completer)
        
        auto_layout.addWidget(self.auto_pick_cb)
        auto_layout.addWidget(self.auto_pick_combo)
        auto_layout.addSpacing(30)
        auto_layout.addWidget(self.auto_ban_cb)
        auto_layout.addWidget(self.auto_ban_combo)
        auto_layout.addStretch()

        home_layout.addWidget(auto_panel)
        self.home_text = self.create_browser()
        home_layout.addWidget(self.home_text)
        self.tab_widget.addTab(home_widget, "大厅总览")

        self.monitor_text = self.create_browser(True); self.tab_widget.addTab(self.create_tab(self.monitor_text), "当前对局")
        self.search_result_text = self.create_browser(True); self.tab_widget.addTab(self.create_tab(self.search_result_text), "战绩查询")
        self.rune_browser = self.create_browser(True); self.tab_widget.addTab(self.create_tab(self.rune_browser), "符文出装")

        main_layout.addWidget(self.tab_widget)
        
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(15, 5, 15, 10)
        self.auto_accept_cb = QCheckBox("自动接受匹配队伍")
        self.auto_accept_cb.setStyleSheet(f"color: {COLOR_TEXT_SUB}; font-size:14px;")
        self.auto_accept_cb.setChecked(True)
        bottom_layout.addWidget(self.auto_accept_cb); bottom_layout.addStretch()
        self.status_label = QLabel("状态：等待游戏启动...")
        self.status_label.setStyleSheet(f"color: {COLOR_TEXT_SUB}; font-weight: bold; font-size: 13px;")
        bottom_layout.addWidget(self.status_label)
        main_layout.addLayout(bottom_layout)

        self.setStyleSheet(MODERN_QSS)
        self.connection = None
        self.timer = QTimer(); self.timer.timeout.connect(self.update_log); self.timer.start(100)
        self.monitor_thread = threading.Thread(target=run_monitor, daemon=True); self.monitor_thread.start()
        self.status_timer = QTimer(); self.status_timer.timeout.connect(self.update_status); self.status_timer.start(500)

    def create_browser(self, clickable=False):
        b = QTextBrowser()
        b.setReadOnly(True); b.setOpenLinks(False); b.setOpenExternalLinks(False)
        b.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        b.setFont(QFont("Microsoft YaHei", 10))
        b.setStyleSheet(f"""
            QTextBrowser {{ background: transparent; color: {COLOR_TEXT_MAIN}; border: none; }} 
            a {{ color: {COLOR_ACCENT}; text-decoration: none; font-weight: bold; transition: color 0.2s; }} 
            a:hover {{ color: #b4befe; text-decoration: none; }}
        """)
        if clickable: b.anchorClicked.connect(self.handle_link_clicked)
        return b

    def create_tab(self, browser):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(5,5,0,5); l.addWidget(browser); return w

    def on_loop_ready(self): self.search_btn.setEnabled(True)

    def on_search_finished(self): self.search_btn.setEnabled(True)

    def start_search(self):
        full_text = self.search_input.text().strip()
        if not full_text or '#' not in full_text: return
        game_name, tag_line = full_text.split('#', 1)
        if not self.connection or monitor_loop is None: return
        self.search_btn.setEnabled(False)
        asyncio.run_coroutine_threadsafe(search_player_by_name(self.connection, game_name.strip(), tag_line.strip()), monitor_loop)
        self.tab_widget.setCurrentIndex(2)
    
    def handle_link_clicked(self, url):
        link = url.toString()
        if link.startswith("http"):
            import webbrowser
            webbrowser.open(link)
            return

        if link.startswith("action:"):
            cmd = link.replace("action:", "")
            if cmd.startswith("player/"):
                data = cmd.replace("player/", "").split("/", 2)
                if len(data) >= 3:
                    self.search_result_text.clear()
                    self.tab_widget.setCurrentIndex(2)
                    QApplication.processEvents()
                    asyncio.run_coroutine_threadsafe(self.fetch_player_detail(data[0], data[1], data[2]), monitor_loop)
                    
            elif cmd.startswith("import_rune:"):
                name, ok = QInputDialog.getText(self, "一键保存符文", "保存为：", QLineEdit.Normal, f"{cmd.replace('import_rune:', '')} - 绝活符文")
                if ok and name: asyncio.run_coroutine_threadsafe(self._do_import_rune(name.strip(), cmd.replace("import_rune:", "")), monitor_loop)
                    
            elif cmd.startswith("apply_rune:"):
                idx = int(cmd.replace("apply_rune:", ""))
                saved = get_saved_runes()
                if idx < len(saved):
                    r = saved[idx]
                    asyncio.run_coroutine_threadsafe(self._do_apply_rune(r['name'], r['primary'], r['sub'], r['perks']), monitor_loop)
            
            elif cmd.startswith("copy_name/"):
                parts = cmd.replace("copy_name/", "").split("/", 1)
                if len(parts) >= 2:
                    name, tag = parts[0], parts[1]
                    full = f"{name}#{tag}"
                    QApplication.clipboard().setText(full)
                    self.status_label.setText(f"📋 已复制: {full}")
                    self.status_label.setStyleSheet(f"color: {COLOR_SUCCESS}; font-weight: bold; font-size:13px;")
                    QTimer.singleShot(2500, lambda: self.update_status())
                    
            elif cmd.startswith("toggle_match/"):
                game_id = int(cmd.replace("toggle_match/", ""))
                asyncio.run_coroutine_threadsafe(self._toggle_match(game_id), monitor_loop)
                
            elif cmd.startswith("delete_rune:"):
                delete_rune(int(cmd.replace("delete_rune:", "")))
                self._refresh_opgg_view()
                
    async def _toggle_match(self, game_id):
        if not self.match_cache: return
        if game_id in self.expanded_game_ids:
            self.expanded_game_ids.discard(game_id)
            for i, (match, _) in enumerate(self.match_cache):
                if match.get('gameId', 0) == game_id:
                    self.match_cache[i] = (match, "")
                    break
            _rerender_search()
        else:
            data = await _fetch_match_detail_data(self.connection, game_id)
            if data:
                # 在渲染详情前，并发预先获取所有 10 名玩家的段位缓存，避免列表渲染卡顿或缺失
                identities = data.get('participantIdentities', [])
                tasks = []
                for ident in identities:
                    puuid = ident.get('player', {}).get('puuid')
                    if puuid:
                        tasks.append(get_player_tier(self.connection, puuid))
                if tasks:
                    await asyncio.gather(*tasks)

                detail_html = _build_detail_html(data, highlight_name=getattr(self, '_last_search_name', ''))
                for i, (match, _) in enumerate(self.match_cache):
                    if match.get('gameId', 0) == game_id:
                        self.match_cache[i] = (match, detail_html)
                        break
                self.expanded_game_ids.add(game_id)
                _rerender_search()

    def _refresh_opgg_view(self):
        global last_my_champion_id
        if last_my_champion_id and self.connection and monitor_loop:
            asyncio.run_coroutine_threadsafe(update_opgg_data(last_my_champion_id, self.connection), monitor_loop)

    async def _do_apply_rune(self, name, primary, sub, perks):
        success, msg = await apply_rune_to_client(self.connection, name, primary, sub, perks)
        gui_print('alert', f"{'✅ 符文应用成功！' if success else '❌ 符文应用失败：'}\n{msg}")

    async def _do_import_rune(self, name, champion_name):
        try:
            resp = await self.connection.request('get', '/lol-perks/v1/currentpage')
            if resp.status == 200:
                page = await resp.json()
                perks = page.get('selectedPerkIds', [])
                if len(perks) == 9:
                    save_rune(name, champion_name, page.get('primaryStyleId'), page.get('subStyleId'), perks)
                    gui_print('alert', "✅ 成功抓取客户端符文并保存到本地！")
                    self._refresh_opgg_view()
                else: gui_print('alert', "❌ 失败：请确保当前符文页已经点满 9 个符文后再抓取！")
        except Exception as e: gui_print('alert', f"❌ 发生错误：{e}")

    async def fetch_player_detail(self, puuid, name, tagline):
        await get_player_rank(self.connection, puuid, name)
        await get_match_history_detailed(self.connection, puuid, name, tagline)

    def on_switch_to_monitor(self): self.tab_widget.setCurrentIndex(1)

    def update_log(self):
        while not log_queue.empty():
            try:
                target, line = log_queue.get_nowait()
                if target == 'alert':
                    QMessageBox.information(self, "提示", line)
                    continue

                clean_line = line.rstrip('\n')
                if target == 'monitor':
                    if clean_line == 'CLEAR_TEAM':
                        self.monitor_text.clear(); continue
                    if '<a href=' in clean_line or '<div' in clean_line or '<img' in clean_line or '<span' in clean_line or '<table' in clean_line or '<tr' in clean_line or '<th' in clean_line or '<td' in clean_line:
                        self.monitor_text.append(clean_line)
                    else:
                        self.monitor_text.append(html_text(clean_line))
                    self.monitor_text.verticalScrollBar().setValue(self.monitor_text.verticalScrollBar().maximum())
                    
                elif target == 'search':
                    if 'CLEAR' in clean_line and len(clean_line) <= 5: self.search_result_text.clear()
                    elif '<div' in clean_line or '<img' in clean_line or '<a href=' in clean_line or '<table' in clean_line or '<tr' in clean_line or '<th' in clean_line or '<td' in clean_line or '<span' in clean_line:
                        self.search_result_text.append(clean_line)
                    else:
                        self.search_result_text.append(html_text(clean_line))
                    self.search_result_text.verticalScrollBar().setValue(self.search_result_text.verticalScrollBar().maximum())
                        
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
        with is_monitoring_lock:
            monitoring = is_monitoring
        if monitoring:
            self.status_label.setText("🟢 状态：已连接客户端，后台运行中...")
            self.status_label.setStyleSheet(f"color: {COLOR_SUCCESS}; font-weight: bold; font-size:13px;")
        else:
            self.status_label.setText("🔴 状态：未连接客户端，请启动游戏...")
            self.status_label.setStyleSheet(f"color: {COLOR_DANGER}; font-weight: bold; font-size:13px;")

    def closeEvent(self, event):
        global is_monitoring
        with is_monitoring_lock:
            is_monitoring = False
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())