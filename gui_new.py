import sys
import asyncio
import threading
import queue
from PySide6.QtCore import Qt, QTimer, QPoint, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QTextEdit, QFrame,
    QLineEdit, QCheckBox, QTabWidget
)
from PySide6.QtGui import QFont, QMouseEvent

# ================= 导入原有逻辑模块 =================
from lcu_driver import Connector
from lol_map import load_champion_map, load_spell_map

# 全局变量
log_queue = queue.Queue()
champion_map = load_champion_map()
spell_map = load_spell_map()
connector = Connector()
is_monitoring = False

main_window = None          # 全局 UI 引用
monitor_loop = None         # 保存后台事件循环（将在 connector.ready 中赋值）


def gui_print(target,*args, **kwargs):
    """将文本放入队列，供 UI 线程取出显示"""
    sep = kwargs.get('sep', ' ')
    end = kwargs.get('end', '\n')
    text = sep.join(str(arg) for arg in args) + end
    log_queue.put((target,text))

def print_home(*args,**kwargs):
    "输出到 主页"
    gui_print('home',*args,**kwargs)

def print_monitor(*args,**kwargs):
    "输出到 对战信息 页面"
    gui_print('monitor',*args,**kwargs)

def print_search(*args,**kwargs):
    "输出到 战绩查询 页面"
    gui_print('search',*args,**kwargs)


async def search_player_by_name(connection, game_name, tag_line):
    # 测试连接是否可用
    try:
        test = await asyncio.wait_for(
            connection.request('get', '/lol-summoner/v1/current-summoner'),
            timeout=3.0
        )
        if test.status != 200:
            raise Exception("LCU 未正常响应")
    except Exception as e:
        print_search(f"\n[系统] 连接尚未就绪：{e}")
        if main_window:
            main_window.search_finished.emit()
        return

    body = [{"gameName": game_name, "tagLine": tag_line}]
    print_search(f"\n[系统] 正在查询玩家 {game_name}#{tag_line} 的信息...")

    try:
        resp = await asyncio.wait_for(
            connection.request('post', '/lol-summoner/v1/summoners/aliases', json=body),
            timeout=5.0
        )
        if resp.status == 200:
            data = await resp.json()
            if data and data[0].get('puuid'):
                puuid = data[0]['puuid']
                print_search(f"\n[系统] 找到玩家了！PUUID: {puuid[:8]}...")
                await get_match_history(connection, puuid, game_name, label="【查询结果】")
            else:
                print_search(f"\n[系统] 找不到名为 {game_name}#{tag_line} 的玩家，请检查拼写。")
        else:
            print_search(f"\n[系统] 查询失败，状态码：{resp.status}")
    except asyncio.TimeoutError:
        print_search("\n[系统] 查询超时，请稍后重试。")
    except Exception as e:
        print_search(f"\n[系统] 查询时发生异常：{e}")
    finally:
        if main_window:
            main_window.search_finished.emit()

async def get_match_history(connection, puuid, game_name, label=""):
    try:
        endpoint = f'/lol-match-history/v1/products/lol/{puuid}/matches'
        resp = await connection.request('get', endpoint)
        if resp.status != 200:
            print_search(f"[提示] 无法获取战绩 (Status: {resp.status})")
            return
            
        data = await resp.json()
        matches = data.get('games', {}).get('games', [])
        if not matches:
            print_search(f"[提示] 该玩家暂无对局记录。")
            return
            
        print_search(f"\n{label} {game_name} 的最近战绩：")
        
        # 显示最近的 10 场
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
            queueId = match.get('queueId', 0)
            queue_names = {440: "灵活排位", 420: "单排/双排", 450: "极地大乱斗", 2400: "海克斯大乱斗", 430: "匹配模式", 3140: "训练营", 1700:"斗魂竞技场"}
            queue_name = queue_names.get(queueId, f"未知模式({queueId})")
            gameCreationDate = match.get('gameCreationDate', '未知时间')
            
            print_search(f" - 英雄: {champion_name:10} | 结果: {result} | KDA: {kills}/{deaths}/{assists} | 模式: {queue_name:10} | 时间: {gameCreationDate}")
            
    except Exception as e:
        print_search(f"[错误] 获取战绩时发生异常: {e}")


async def get_player_history(connection, player_puuid, player_name, label="玩家", champion_name=""):
    if not player_puuid:
        return
    try:
        endpoint = f'/lol-match-history/v1/products/lol/{player_puuid}/matches'
        resp = await connection.request('get', endpoint)
        if resp.status != 200:
            print_monitor(f"[提示] 无法获取 {label} {player_name} 的战绩 (Status: {resp.status})")
            return
        data = await resp.json()
        matches = data.get('games', {}).get('games', [])
        game_count = min(20, len(matches))
        if game_count == 0:
            print_monitor(f"[提示] {label} {player_name} 暂无对局记录")
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

        display_name = f"[{champion_name}] {player_name}" if (label == "敌方" and champion_name and champion_name != "?") else f"{player_name}"
        print_monitor(f"[战绩] {label} {display_name} | 胜率 {win_rate:.1f}% | KDA {avg_kda:.2f} | 评分 {score:.1f} | {tag}")
    except Exception as e:
        print_monitor(f"[提示] 查询 {label} {player_name} 时发生错误：{e}")


def print_team_lineup(my_team, champion_map, spell_map):
    print_monitor("\n当前阵容：")
    for member in my_team:
        champ_id = member.get('championId', 0)
        champ_name = champion_map.get(str(champ_id), "未选") if champ_id != 0 else "未选"
        s1 = spell_map.get(str(member.get('spell1Id', 0)), "?")
        s2 = spell_map.get(str(member.get('spell2Id', 0)), "?")
        pos = member.get('assignedPosition', '')
        gname = member.get('gameName', member.get('summonerName', ''))
        print_monitor(f"{pos:6} {gname[:12]:12} : {champ_name:10} ({s1}+{s2})")


def get_lineup_fingerprint(my_team):
    fingerprint = []
    for m in my_team:
        fingerprint.append((m.get('cellId'), m.get('championId'), m.get('spell1Id'), m.get('spell2Id')))
    return tuple(fingerprint)


async def monitor_one_game(connection):
    global is_monitoring

    error_count = 0
    while is_monitoring:
        try:
            rc_resp = await connection.request('get', '/lol-matchmaking/v1/ready-check')
            if rc_resp.status == 200:
                rc_data = await rc_resp.json()
                if rc_data.get('state') == 'InProgress' and rc_data.get('playerResponse') == 'None':
                    if main_window and main_window.auto_accept_cb.isChecked():
                        await asyncio.sleep(1)
                        await connection.request('post', '/lol-matchmaking/v1/ready-check/accept')
                        print_monitor("\n[系统] 已为您自动接受对局！")
        except Exception:
            pass

        try:
            resp = await connection.request('get', '/lol-gameflow/v1/session')
            error_count = 0
            if resp.status == 200:
                data = await resp.json()
                if data.get('phase') == 'ChampSelect':
                    break
        except Exception:
            error_count += 1
            if error_count >= 3:
                is_monitoring = False
                print_monitor("\n[系统] 检测到客户端已关闭，停止监控。")
                return
        await asyncio.sleep(1)

    if not is_monitoring:
        return

    # 获取选人信息
    try:
        resp = await connection.request('get', '/lol-champ-select/v1/session')
        if resp.status == 200:
            session = await resp.json()
            my_team = session.get('myTeam', [])
            if main_window:
                main_window.switch_to_monitor_tab.emit()
            queue_names = {440: "灵活排位", 420: "单排/双排", 450: "极地大乱斗", 2400: "海克斯大乱斗", 430: "匹配模式", 3140: "训练营", 1700:"斗魂竞技场"}
            q_id = session.get('queueId')
            if not q_id:
                try:
                    q_resp = await connection.request('get', '/lol-gameflow/v1/session')
                    if q_resp.status == 200:
                        q_data = await q_resp.json()
                        q_id = q_data.get('gameData', {}).get('queue', {}).get('id', 0)
                except Exception:
                    pass
            print_monitor(f"\n当前模式：{queue_names.get(q_id, f'未知({q_id})')}")

            print_monitor("正在查询队友战绩...")
            tasks = []
            for member in my_team:
                puuid = member.get('puuid')
                if puuid:
                    champ_name = champion_map.get(str(member.get('championId', 0)), "?")
                    name = member.get('gameName', member.get('summonerName', '未知'))
                    tasks.append(asyncio.create_task(get_player_history(connection, puuid, name, "队友", champ_name)))
            if tasks:
                await asyncio.gather(*tasks)
            print_monitor("===== 队友战绩查询完毕 =====")

            print_team_lineup(my_team, champion_map, spell_map)
            last_fingerprint = get_lineup_fingerprint(my_team)
        else:
            return
    except Exception:
        return

    # 监测阵容变化
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
                        champ_name = champion_map.get(str(player.get('championId', 0)), "?")
                        enemy_tasks.append(asyncio.create_task(get_player_history(connection, puuid, name, "敌方", champ_name)))

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
    global is_monitoring, main_window, monitor_loop
    is_monitoring = True

    # 保存后台事件循环
    monitor_loop = asyncio.get_running_loop()

    if main_window:
        main_window.connection = connection
        main_window.loop_ready.emit()

    print_home("\n成功连接到英雄联盟客户端！")

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
                tagLine = data.get('tagLine', '未知')
                xpnow = data.get('xpSinceLastLevel', 0)
                xpnext = data.get('xpUntilNextLevel', 0)
                print_home(f"你好，召唤师：{name}#{tagLine}，等级 {level}，目前经验值{xpnow}，距离下级还需{xpnext - xpnow}")
                break
        except Exception:
            pass
        await asyncio.sleep(3)

    print_monitor("\n等待进入选人阶段...")

    while is_monitoring:
        await monitor_one_game(connection)
        if is_monitoring:
            print_monitor("\n等待进入选人阶段...")

    print_home("\n[系统] 核心监控已退出，等待重新连接客户端...")


def run_monitor():
    connector.start()


# ================= PySide6 UI =================
class TitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.setFixedHeight(40)
        self.drag_pos = QPoint()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(0)

        self.title_label = QLabel("清梦 - 英雄联盟助手")
        self.title_label.setStyleSheet("color: #cccccc; font-size: 20px;")
        layout.addWidget(self.title_label)

        layout.addStretch()

        self.min_btn = QPushButton("—")
        self.min_btn.setFixedSize(30, 30)
        self.min_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #cccccc;
                border: none;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #3a3a3a;
                color: white;
            }
        """)
        self.min_btn.clicked.connect(self.parent.showMinimized)
        layout.addWidget(self.min_btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #cccccc;
                border: none;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #e81123;
                color: white;
            }
        """)
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

        central_widget = QWidget()
        central_widget.setObjectName("central")
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.title_bar = TitleBar(self)
        main_layout.addWidget(self.title_bar)

        # 搜索栏
        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(15, 10, 15, 10)
        search_layout.setSpacing(10)
        search_layout.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入 名字#编号 查战绩...")
        self.search_input.setFixedSize(200,32)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: rgba(40, 40, 40, 200);
                color: white;
                border: 1px solid #555;
                border-radius: 5px;
                padding: 0 10px;
            }
            QLineEdit:focus { border: 1px solid #66b3ff; }
        """)
        search_layout.addWidget(self.search_input)

        self.search_btn = QPushButton("搜索")
        self.search_btn.setFixedSize(60, 32)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background-color:#1e90ff;
                color: white;
                border: none;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #c0392b; }
        """)
        self.search_btn.clicked.connect(self.start_search)
        self.search_btn.setEnabled(False)   # 初始禁用，等待连接就绪
        search_layout.addWidget(self.search_btn)

        self.result_label = QLabel("")
        self.result_label.setStyleSheet("color: #88ff88; padding-left: 15px;")
        search_layout.addWidget(self.result_label)

        main_layout.addLayout(search_layout)

        # 日志区域
        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane {
                background: rgba(25, 25, 25, 220);
                border: none;
                border-radius: 8px;
                margin: 0 15px;
            }
            QTabBar::tab {
                background: #2a2a2a;
                color: #cccccc;
                padding: 8px 20px;
                margin-right: 2px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected {
                background: #3a3a3a;
                color: white;
            }
            QTabBar::tab:hover {
                background: #4a4a4a;
            }
        """)

        # --- 主页 ---
        self.home_page = QWidget()
        home_layout = QVBoxLayout(self.home_page)
        self.home_text = QTextEdit()
        self.home_text.setReadOnly(True)
        self.home_text.setFont(QFont("Consolas", 10))
        self.home_text.setStyleSheet("background: transparent; color: #d4d4d4; border: none;")
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane {
                background: rgba(25, 25, 25, 220);
                border: none;
                border-radius: 8px;
                margin: 0 15px;
            } 
            QTabBar::tab {
                background: #2a2a2a;
                color: #cccccc;
                padding: 14px 30px;          
                font-size: 14px;             
                font-weight: bold;           
                margin-right: 2px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected {
                background: #3a3a3a;
                color: white;
            }
            QTabBar::tab:hover {
                background: #4a4a4a;
            }
        """)
        home_layout.addWidget(self.home_text)
        self.tab_widget.addTab(self.home_page, " 主页 ")

        # --- 对战信息页 ---
        self.monitor_page = QWidget()
        monitor_layout = QVBoxLayout(self.monitor_page)
        self.monitor_text = QTextEdit()
        self.monitor_text.setReadOnly(True)
        self.monitor_text.setFont(QFont("Consolas", 10))
        self.monitor_text.setStyleSheet("background: transparent; color: #d4d4d4; border: none;")
        monitor_layout.addWidget(self.monitor_text)
        self.tab_widget.addTab(self.monitor_page, " 对战信息 ")

        # --- 战绩查询页 ---
        self.search_page = QWidget()
        search_layout = QVBoxLayout(self.search_page)
        self.search_result_text = QTextEdit()
        self.search_result_text.setReadOnly(True)
        self.search_result_text.setFont(QFont("Consolas", 10))
        self.search_result_text.setStyleSheet("background: transparent; color: #d4d4d4; border: none;")
        search_layout.addWidget(self.search_result_text)
        self.tab_widget.addTab(self.search_page, " 战绩查询 ")

        # 把标签页添加到主布局
        main_layout.addWidget(self.tab_widget)
        # 底部控制栏
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(15, 10, 15, 10)
        self.auto_accept_cb = QCheckBox("自动接受对局")
        self.auto_accept_cb.setStyleSheet("""
            QCheckBox { color: #cccccc; font-size: 13px; }
            QCheckBox::indicator { width: 18px; height: 18px; }
        """)
        self.auto_accept_cb.setChecked(True)
        bottom_layout.addWidget(self.auto_accept_cb)
        bottom_layout.addStretch()
        self.status_label = QLabel("状态：等待启动...")
        self.status_label.setStyleSheet("color: #aaaaaa; font-weight: bold;")
        bottom_layout.addWidget(self.status_label)
        main_layout.addLayout(bottom_layout)

        self.setStyleSheet("""
            QMainWindow { background: transparent; }
            #central {
                background-color: rgba(30, 32, 40, 240);
                border-radius: 12px;
                border: 1px solid rgba(255, 255, 255, 30);
            }
        """)

        self.connection = None

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_log)
        self.timer.start(100)

        # 启动监控线程
        self.monitor_thread = threading.Thread(target=run_monitor, daemon=True)
        self.monitor_thread.start()

        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(500)

    def on_loop_ready(self):
        """后台事件循环就绪后启用搜索按钮"""
        self.search_btn.setEnabled(True)
        print_search("[系统] 后台通信已准备就绪，您可以开始搜索了！")

    def on_search_finished(self):
        """搜索完成，恢复按钮状态"""
        self.search_btn.setEnabled(True)
        self.result_label.setText("✓")

    def start_search(self):
        full_text = self.search_input.text().strip()
        if not full_text:
            print_search("[系统] 输入不能为空")
            return
        if '#' not in full_text:
            print_search("[系统] 输入格式错误，请使用 名字#编号 的格式")
            return

        game_name, tag_line = full_text.split('#', 1)

        if not self.connection:
            print_search("[系统] 尚未连接到客户端，无法搜索")
            return

        global monitor_loop
        if monitor_loop is None or monitor_loop.is_closed():
            print_search("[系统] 后台通信循环未准备好，请稍后再试！")
            return

        self.search_btn.setEnabled(False)
        self.result_label.setText("正在提交查询...")
        print_search(f"\n[系统] 准备投递搜索任务: {game_name}#{tag_line}")

        try:
            asyncio.run_coroutine_threadsafe(
                search_player_by_name(self.connection, game_name.strip(), tag_line.strip()),
                monitor_loop
            )
            self.tab_widget.setCurrentIndex(2)
        except Exception as e:
            print_search(f"[错误] 提交搜索任务失败：{e}")
            self.search_btn.setEnabled(True)
            self.result_label.setText("")
    def on_switch_to_monitor(self):
        self.tab_widget.setCurrentIndex(1)

    def update_log(self):
        while not log_queue.empty():
            try:
                target, line = log_queue.get_nowait()
                clean_line = line.rstrip('\n')
                if target == 'monitor':
                    self.monitor_text.append(clean_line)
                    # 自动滚动到底部
                    self.monitor_text.verticalScrollBar().setValue(
                        self.monitor_text.verticalScrollBar().maximum()
                    )
                elif target == 'search':
                    self.search_result_text.append(clean_line)
                    self.search_result_text.verticalScrollBar().setValue(
                        self.search_result_text.verticalScrollBar().maximum()
                    )
                elif target == 'home':
                    self.home_text.append(clean_line)
                    self.home_text.verticalScrollBar().setValue(
                        self.home_text.verticalScrollBar().maximum()
                    )
            except queue.Empty:
                break

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
