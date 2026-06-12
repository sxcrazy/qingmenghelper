# ddragon_images.py
import sys
import os
import json
import requests

def get_exe_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

def _find_data_dir():
    """Find data/ directory — checks exe level first, then _internal/data/."""
    base = get_exe_dir()
    path = os.path.join(base, "data")
    if os.path.isdir(path):
        return path
    alt = os.path.join(base, "_internal", "data")
    if os.path.isdir(alt):
        return alt
    return path

DATA_DIR = _find_data_dir()
IMAGES_DIR = os.path.join(DATA_DIR, "images")
CHAMPION_IMG_DIR = os.path.join(IMAGES_DIR, "champion")
ITEM_IMG_DIR = os.path.join(IMAGES_DIR, "item")
SPELL_IMG_DIR = os.path.join(IMAGES_DIR, "spell")

# 必须有英文映射表（用于下载头像）
CHAMPION_KEY_CACHE = os.path.join(DATA_DIR, "champion_eng_keys.json")

class ImageHelper:
    def __init__(self):
        self.version = "14.7.1"
        self.base_url = f"https://ddragon.leagueoflegends.com/cdn/{self.version}"
        
        for d in [IMAGES_DIR, CHAMPION_IMG_DIR, ITEM_IMG_DIR, SPELL_IMG_DIR]:
            os.makedirs(d, exist_ok=True)
            
        self.update_version()
        self.champion_id_to_eng = self.load_champion_keys()
    
    def update_version(self):
        try:
            versions_url = "https://ddragon.leagueoflegends.com/api/versions.json"
            self.version = requests.get(versions_url, timeout=5).json()[0]
            self.base_url = f"https://ddragon.leagueoflegends.com/cdn/{self.version}"
        except Exception as e:
            print(f"[ddragon] 版本更新失败: {e}")
            
    def load_champion_keys(self):
        """生成并加载英雄ID到英文名的映射（拳头图片接口只认英文）"""
        if os.path.exists(CHAMPION_KEY_CACHE):
            with open(CHAMPION_KEY_CACHE, 'r', encoding='utf-8') as f:
                return json.load(f)
        try:
            print("[系统] 正在生成英雄英文映射表...")
            url = f"{self.base_url}/data/zh_CN/champion.json"
            data = requests.get(url, timeout=10).json()
            mapping = {}
            for eng_name, info in data["data"].items():
                champ_id = str(info["key"])
                mapping[champ_id] = eng_name
            
            with open(CHAMPION_KEY_CACHE, 'w', encoding='utf-8') as f:
                json.dump(mapping, f)
            return mapping
        except Exception as e:
            print(f"生成英文映射表失败: {e}")
            return {}

    def get_champion_icon_path(self, champion_id):
        eng_name = self.champion_id_to_eng.get(str(champion_id))
        if not eng_name: return None
        local_path = os.path.join(CHAMPION_IMG_DIR, f"{eng_name}.png")
        if not os.path.exists(local_path):
            try:
                url = f"{self.base_url}/img/champion/{eng_name}.png"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    with open(local_path, 'wb') as f: f.write(resp.content)
            except Exception:
                return None
        return local_path if os.path.exists(local_path) else None

    def get_item_icon_path(self, item_id):
        if not item_id or item_id == 0: return None
        local_path = os.path.join(ITEM_IMG_DIR, f"{item_id}.png")
        if not os.path.exists(local_path):
            try:
                url = f"{self.base_url}/img/item/{item_id}.png"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    with open(local_path, 'wb') as f: f.write(resp.content)
            except Exception:
                return None
        return local_path if os.path.exists(local_path) else None

    def _ensure_spell_map(self):
        """Build spell_id (string) → English key mapping from Data Dragon."""
        if hasattr(self, '_spell_id_to_eng'):
            return
        self._spell_id_to_eng = {}
        try:
            url = f"{self.base_url}/data/zh_CN/summoner.json"
            data = requests.get(url, timeout=10).json()
            for eng_key, info in data.get('data', {}).items():
                spell_id = str(info.get('key', ''))
                if spell_id:
                    self._spell_id_to_eng[spell_id] = eng_key
        except Exception as e:
            print(f"[ddragon] 技能映射加载失败: {e}")

    def get_spell_icon_path(self, spell_id):
        """Download and return local path for a summoner spell icon by numeric ID."""
        if not spell_id or spell_id == 0:
            return None
        self._ensure_spell_map()
        eng_key = self._spell_id_to_eng.get(str(spell_id))
        if not eng_key:
            return None
        local_path = os.path.join(SPELL_IMG_DIR, f"{eng_key}.png")
        if not os.path.exists(local_path):
            try:
                url = f"{self.base_url}/img/spell/{eng_key}.png"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    with open(local_path, 'wb') as f:
                        f.write(resp.content)
            except Exception:
                return None
        return local_path if os.path.exists(local_path) else None

_image_helper = None
def get_image_helper():
    global _image_helper
    if _image_helper is None:
        _image_helper = ImageHelper()
    return _image_helper
