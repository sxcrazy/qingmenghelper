import os
import sys
import json

def get_exe_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

# 直接指向整理好的 data 目录
DATA_DIR = os.path.join(get_exe_dir(), "data")
RUNE_SAVE_FILE = os.path.join(DATA_DIR, "saved_runes.json")
ITEM_SAVE_FILE = os.path.join(DATA_DIR, "saved_items.json")

STYLE_MAP = {}  
PERK_MAP = {}   
ITEM_MAP = {}   

def init_local_resources():
    """初始化读取 data 目录下的本地资源"""
    global STYLE_MAP, PERK_MAP, ITEM_MAP
    
    # 1. 解析符文
    runes_path = os.path.join(DATA_DIR, "runesReforged.json")
    if os.path.exists(runes_path):
        with open(runes_path, 'r', encoding='utf-8') as f:
            for tree in json.load(f):
                STYLE_MAP[tree['id']] = {
                    "name": tree['name'], 
                    "icon": os.path.join(DATA_DIR, tree['icon']).replace('\\', '/')
                }
                for slot in tree['slots']:
                    for rune in slot['runes']:
                        PERK_MAP[rune['id']] = {
                            "name": rune['name'], 
                            "icon": os.path.join(DATA_DIR, rune['icon']).replace('\\', '/')
                        }
                        
    # 2. 补全 9 个属性碎片 
    stat_mods = {
        5008: ("适应之力", "StatModsAdaptiveForceIcon.png"),
        5005: ("攻击速度", "StatModsAttackSpeedIcon.png"),
        5007: ("技能急速", "StatModsCDRScalingIcon.png"),
        5011: ("固定生命值", "StatModsHealthPlusIcon.png"),
        5001: ("成长生命值", "StatModsHealthScalingIcon.png"),
        5010: ("移动速度", "StatModsMovementSpeedIcon.png"),
        5013: ("韧性及抗性", "StatModsTenacityIcon.png"),
        5002: ("护甲(旧)", "StatModsArmorIcon.png"),
        5003: ("魔抗(旧)", "StatModsMagicResIcon.png"),
    }
    for mod_id, (name, icon_name) in stat_mods.items():
        PERK_MAP[mod_id] = {
            "name": name,
            "icon": os.path.join(DATA_DIR, "perk-images", "StatMods", icon_name).replace('\\', '/')
        }

    # 3. 解析装备
    item_path = os.path.join(DATA_DIR, "item.json")
    if os.path.exists(item_path):
        with open(item_path, 'r', encoding='utf-8') as f:
            for item_id, info in json.load(f).get('data', {}).items():
                icon_path = os.path.join(DATA_DIR, "item", info['image']['full']).replace('\\', '/')
                ITEM_MAP[int(item_id)] = {"name": info['name'], "icon": icon_path}

def get_perk(id): return PERK_MAP.get(id, {"name": f"未知({id})", "icon": ""})
def get_style(id): return STYLE_MAP.get(id, {"name": f"未知({id})", "icon": ""})
def get_item(id): return ITEM_MAP.get(id, {"name": f"未知({id})", "icon": ""})

# ================= 收藏管理 =================
def load_json(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f: return json.load(f)
        except: pass
    return []

def save_json(filepath, data):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=2)

def get_saved_runes(): return load_json(RUNE_SAVE_FILE)
def get_saved_items(): return load_json(ITEM_SAVE_FILE)
def save_rune(name, champion, primary, sub, perks):
    data = get_saved_runes()
    data.append({"name": name, "champion": champion, "primary": primary, "sub": sub, "perks": perks})
    save_json(RUNE_SAVE_FILE, data)
def delete_rune(index):
    data = get_saved_runes()
    if 0 <= index < len(data):
        data.pop(index)
        save_json(RUNE_SAVE_FILE, data)
def save_item_set(name, champion, items):
    data = get_saved_items()
    data.append({"name": name, "champion": champion, "items": items})
    save_json(ITEM_SAVE_FILE, data)
def delete_item_set(index):
    data = get_saved_items()
    if 0 <= index < len(data):
        data.pop(index)
        save_json(ITEM_SAVE_FILE, data)

# ================= LCU 一键应用符文 =================
async def apply_rune_to_client(connection, name, primary, sub, perks):
    try:
        resp = await connection.request('get', '/lol-perks/v1/pages')
        pages = await resp.json()
        editable_page = next((p for p in pages if p.get('isEditable')), None)
        
        if not editable_page:
            del_page = next((p for p in pages if not p.get('isActive') and p.get('isDeletable')), None)
            if del_page: 
                await connection.request('delete', f'/lol-perks/v1/pages/{del_page["id"]}')
            else: 
                return False, "符文页已满，请手动删除一页！"
                
        rune_data = {"name": name[:15], "primaryStyleId": primary, "subStyleId": sub, "selectedPerkIds": perks, "current": True}
        if editable_page:
            res = await connection.request('put', f'/lol-perks/v1/pages/{editable_page["id"]}', json=rune_data)
        else:
            res = await connection.request('post', '/lol-perks/v1/pages', json=rune_data)
            
        return (True, "应用成功！") if res.status == 200 else (False, "客户端拒绝了请求")
    except Exception as e: return False, str(e)
