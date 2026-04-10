import asyncio
from lcu_driver import Connector
from lol_map import load_champion_map

champion_map = load_champion_map()

connector = Connector()

async def search_player_by_name(connection, game_name, tag_line):
    # 构造请求体
    body = [{"gameName": game_name, "tagLine": tag_line}]
    
    # 向 LCU 发送查询请求
    resp = await connection.request('post', '/lol-summoner/v1/summoners/aliases', json=body)
    
    if resp.status == 200:
        data = await resp.json()
        if data and data[0].get('puuid'):
            puuid = data[0]['puuid']
            print(f"\n[系统] 找到玩家了！PUUID: {puuid[:8]}...")
            await get_player_history(connection, puuid, game_name, label="【查询结果】")
        else:
            print(f"\n[系统] 找不到名为 {game_name}#{tag_line} 的玩家，请检查拼写。")
    else:
        print(f"\n[系统] 查询失败，接口状态码：{resp.status}")

async def get_player_history(connection, puuid, game_name, label=""):
    try:
        endpoint = f'/lol-match-history/v1/products/lol/{puuid}/matches'
        resp = await connection.request('get', endpoint)
        if resp.status != 200:
            print(f"[提示] 无法获取战绩 (Status: {resp.status})")
            return
            
        data = await resp.json()
        matches = data.get('games', {}).get('games', [])
        if not matches:
            print(f"[提示] 该玩家暂无对局记录。")
            return
            
        print(f"\n{label} {game_name} 的最近战绩：")
        
        # 只显示最近的 5 场
        for match in matches[:10]:
            participant = match['participants'][0]
            stats = participant['stats']
            
            champion_name = participant.get('championId', '未知')
            if champion_name is not None:
                champion_name = champion_map.get(str(champion_name), '未知英雄')
            else:
                champion_name = '未知英雄'
            win = stats.get('win')
            result = "胜利" if win else "失败"
            kills = stats.get('kills', 0)
            deaths = stats.get('deaths', 0)
            assists = stats.get('assists', 0)
            
            print(f" - 英雄: {champion_name:10} | 结果: {result} | KDA: {kills}/{deaths}/{assists}")
            print(matches[0])
            
    except Exception as e:
        print(f"[错误] 获取战绩时发生异常: {e}")

@connector.ready
async def connect(connection):
    print("成功连接到英雄联盟客户端！")
    
    response = await connection.request('get', '/lol-summoner/v1/current-summoner')
    if response.status == 200:
        data = await response.json()
        name = data.get('gameName', '未知') 
        print(f"你好，当前登录账号：{name}\n")
    while True:
        print("-" * 40)
        game_name = await asyncio.to_thread(input, "请输入要查询的召唤师名字 (输入 q 退出程序): ")
        
        if game_name.lower() == 'q':
            print("退出查询程序...")
            # 优雅退出
            import os
            os._exit(0)
        tag_line = await asyncio.to_thread(input, "请输入编号 (例如 0000，或你的专属编号): ")
        print("正在向服务器发送请求...")
        await search_player_by_name(connection, game_name, tag_line)

# 启动连接器
connector.start()
