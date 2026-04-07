import sys
import os
import json
import requests

# 获取资源路径
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# 缓存文件路径
CHAMPION_CACHE_FILE = resource_path(os.path.join("data", "champion_cache.json"))
SPELL_CACHE_FILE = resource_path(os.path.join("data", "spell_cache.json"))

def download_champion_map():
    """下载英雄映射并保存到 data 文件夹（注意：打包后 data 只读，下载可能失败）"""
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
            champ_id = int(champ_info["key"])
            id_to_name[champ_id] = champ_name

        # 确保 data 目录存在
        os.makedirs(os.path.dirname(CHAMPION_CACHE_FILE), exist_ok=True)
        with open(CHAMPION_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(id_to_name, f, ensure_ascii=False, indent=2)
        print(f"英雄映射表已保存到 {CHAMPION_CACHE_FILE}")
        return id_to_name
    except Exception as e:
        print(f"下载英雄映射表失败: {e}")
        return {}

def download_spell_map():
    #下载召唤师技能映射表并保存到 data 文件夹
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
            spell_id = int(spell_info["key"])
            id_to_name[spell_id] = spell_name

        os.makedirs(os.path.dirname(SPELL_CACHE_FILE), exist_ok=True)
        with open(SPELL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(id_to_name, f, ensure_ascii=False, indent=2)
        print(f"技能映射表已保存到 {SPELL_CACHE_FILE}")
        return id_to_name
    except Exception as e:
        print(f"下载技能映射表失败: {e}")
        return {}

def load_champion_map():
    #加载英雄映射表
    if os.path.exists(CHAMPION_CACHE_FILE):
        print("发现英雄映射表文件已下载，直接加载...")
        with open(CHAMPION_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        print(f"未找到英雄映射表文件（期望路径: {CHAMPION_CACHE_FILE}），开始下载...")
        return download_champion_map()

def load_spell_map():
    #加载技能映射表
    if os.path.exists(SPELL_CACHE_FILE):
        print("发现技能映射表文件已下载，直接加载...")
        with open(SPELL_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        print(f"未找到技能缓存文件（期望路径: {SPELL_CACHE_FILE}），开始下载...")
        return download_spell_map()

# 测试
if __name__ == "__main__":
    champ = load_champion_map()
    spell = load_spell_map()
    print(f"英雄数量: {len(champ)}")
    print(f"技能数量: {len(spell)}")
    print("示例英雄:", list(champ.items())[:3])
    print("示例技能:", list(spell.items())[:3])