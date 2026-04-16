import sys
import os
import json
import requests

# 获取 exe 所在目录（用于存放 data 缓存文件）
def get_data_dir():
    """动态获取 data 文件夹的绝对路径"""
    if getattr(sys, 'frozen', False):
        # 如果是打包后的 exe，路径以 exe 所在目录为准
        base_path = os.path.dirname(sys.executable)
    else:
        # 如果是没打包的 py 脚本，路径以当前脚本所在目录为准
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, "data")

# 缓存文件路径：exe 同目录下的 data 文件夹
DATA_DIR = os.path.join(get_data_dir(), "data")
CHAMPION_CACHE_FILE = os.path.join(DATA_DIR, "champion_cache.json")
SPELL_CACHE_FILE = os.path.join(DATA_DIR, "spell_cache.json")

# 下载与加载逻辑
def download_champion_map():
    """下载英雄映射表并保存到 exe 同目录的 data 文件夹"""
    try:
        print("正在获取最新版本号...")
        versions_url = "https://ddragon.leagueoflegends.com/api/versions.json"
        versions = requests.get(versions_url, timeout=10).json()
        latest_version = versions[0]
        print(f"最新版本: {latest_version}")

        champ_url = f"https://ddragon.leagueoflegends.com/cdn/{latest_version}/data/zh_CN/champion.json"
        print("正在下载英雄数据库...")
        data = requests.get(champ_url, timeout=10).json()

        id_to_name = {}
        for champ_name, champ_info in data["data"].items():
            # 英雄 ID 是字符串形式的数字，映射到英雄名称
            champ_id = str(champ_info["key"])
            id_to_name[champ_id] = champ_name

        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CHAMPION_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(id_to_name, f, ensure_ascii=False, indent=2)
        print(f"英雄映射表已保存到 {CHAMPION_CACHE_FILE}")
        return id_to_name
    except Exception as e:
        print(f"下载英雄映射表失败: {e}")
        return {}

def download_spell_map():
    """下载召唤师技能映射表"""
    try:
        print("正在获取最新版本号...")
        versions_url = "https://ddragon.leagueoflegends.com/api/versions.json"
        versions = requests.get(versions_url, timeout=10).json()
        latest_version = versions[0]

        spell_url = f"https://ddragon.leagueoflegends.com/cdn/{latest_version}/data/zh_CN/summoner.json"
        print("正在下载技能数据库...")
        data = requests.get(spell_url, timeout=10).json()

        id_to_name = {}
        for spell_name, spell_info in data["data"].items():
            spell_id = str(spell_info["key"])
            id_to_name[spell_id] = spell_name

        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SPELL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(id_to_name, f, ensure_ascii=False, indent=2)
        print(f"技能映射表已保存到 {SPELL_CACHE_FILE}")
        return id_to_name
    except Exception as e:
        print(f"下载技能映射表失败: {e}")
        return {}

def load_champion_map():
    # 使用绝对路径
    filepath = os.path.join(get_data_dir(), 'champion_cache.json')
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"英雄数据读取失败: {e}")
        return {}

def load_spell_map():
    # 使用绝对路径
    filepath = os.path.join(get_data_dir(), 'spell_cache.json')
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"召唤师技能读取失败: {e}")
        return {}

# 测试
if __name__ == "__main__":
    champ = load_champion_map()
    spell = load_spell_map()
    print(f"英雄数量: {len(champ)}")
    print(f"技能数量: {len(spell)}")
    print("示例英雄:", list(champ.items())[:3])
    print("示例技能:", list(spell.items())[:3])
