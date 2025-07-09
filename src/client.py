import requests
import sys
import time
import random
import json
import os
import threading
import queue

# 终端颜色
COLORS = {
    'R': '\033[91m',  # 红
    'Y': '\033[93m',  # 黄
    'G': '\033[92m',  # 绿
    'B': '\033[94m',  # 蓝
    'W': '\033[95m',  # 紫（万能）
    'K': '\033[90m',  # 灰（特殊）
    'END': '\033[0m',
}

SERVER = 'http://8.137.13.242:5000'
CLIENT_VERSION = '3.1.0'
BAR = r'''  ___        _ _              _   _ _   _  ___
 / _ \ _ __ | (_)_ __   ___  | | | | \ | |/ _ \
| | | | '_ \| | | '_ \ / _ \ | | | |  \| | | | |
| |_| | | | | | | | | |  __/ | |_| | |\  | |_| |
 \___/|_| |_|_|_|_| |_|\___|  \___/|_| \_|\___/
'''


def color_card(card):
    if card in ['SK', 'TL']:
        return f'{COLORS['K']}{card}{COLORS['END']}'
    result = COLORS[card[0]] + card[0] + COLORS['END']
    result += (
        (
            COLORS['W']
            if card[0] == 'W' or card[1] in ['R', 'D', 'S']
            else COLORS[card[0]]
        )
        + card[1]
        + COLORS['END']
    )
    return result


def print_hand(hand):
    print('你的手牌:', ' '.join([color_card(card) for card in hand]))


def play(card, uid, server=SERVER):
    '''出牌动作，返回服务端响应'''
    resp = requests.post(f'{server}/play', json={'uid': uid, 'card': card})
    return resp.json()


def deck(status_data):
    '''返回牌桌信息（如场上牌、玩家列表等）'''
    return {
        'top': status_data.get('top'),
        'players': status_data.get('players', []),
        'player_list': status_data.get('player_list', []),
        'game_status': status_data.get('game_status'),
        'winner': status_data.get('winner'),
    }


def player(status_data):
    '''返回当前玩家的手牌'''
    return status_data.get('hand', [])


def who(status_data):
    '''直接返回[当前玩家index, 下家index, 本玩家index]'''
    return [
        status_data.get('current_idx', -1),
        status_data.get('next_idx', -1),
        status_data.get('my_idx', -1),
    ]


def get_username():
    appdata = os.environ.get('APPDATA') or os.path.expanduser('~')
    path = os.path.join(appdata, 'uno_username.txt')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            name = f.read().strip()
            if name:
                return name
    name = input('你的昵称: ').strip()
    with open(path, 'w', encoding='utf-8') as f:
        f.write(name)
    return name


def get_uid():
    appdata = os.environ.get('APPDATA') or os.path.expanduser('~')
    path = os.path.join(appdata, 'uno_uid.txt')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            uid = f.read().strip()
            if uid:
                return uid
    return None


def save_uid(uid):
    appdata = os.environ.get('APPDATA') or os.path.expanduser('~')
    path = os.path.join(appdata, 'uno_uid.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(uid)


def clear_uid():
    appdata = os.environ.get('APPDATA') or os.path.expanduser('~')
    path = os.path.join(appdata, 'uno_uid.txt')
    if os.path.exists(path):
        os.remove(path)


def clear_screen():
    if os.name == 'nt':
        os.system('cls')
    else:
        os.system('clear')


def input_with_timeout(prompt, timeout=60):
    q = queue.Queue()

    def worker():
        try:
            q.put(input(prompt))
        except Exception:
            q.put(None)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def hash_dict(d):
    json_str = json.dumps(d, sort_keys=True)
    return hash(json_str)


def game_loop(uid, room_id=None):
    last_hash = 0
    while True:
        try:
            resp = requests.post(f'{SERVER}/status', json={'uid': uid})
        except:
            print('与服务器的连接丢失，正在重连...')
            time.sleep(0.5)
        data = resp.json()
        if data['status'] != 'success':
            print('状态获取失败:', data.get('reason'))
            break
        if hash_dict(data) == last_hash:
            continue
        last_hash = hash_dict(data)
        clear_screen()
        if room_id:
            print(f'Room ID: {room_id} User ID: {uid}')
        else:
            print(f'User ID: {uid}')
        player_list = data.get('player_list', data['players'])
        current_idx = data.get('current_idx', -1)
        next_idx = data.get('next_idx', -1)
        count = len(player_list)
        print('玩家: ', end='')
        for i in range(count):
            if i == current_idx:
                print('[当前]', end='')
            elif i == next_idx:
                print('[下家]', end='')
            print(f'{player_list[i]}(', end='')
            if data['hand_count'][i] <= 2:
                print(COLORS['R'], end='')
            print(data['hand_count'][i], end='')
            if data['hand_count'][i] <= 2:
                print(COLORS['END'], end='')
            print(')', end='')
            if i != count - 1:
                print(', ', end='')
        print('')
        print(f'当前牌桌: ', end='')
        for card in data.get('table_history', [])[::-1][:25]:
            print(color_card(card), end=' ')
        print('')
        print_hand(data['hand'])
        if data.get('game_status') == 'finished':
            if data.get('winner'):
                print(f'游戏结束，胜者：{data['winner']}')
            else:
                print('游戏结束')
            clear_uid()
            input('按 Enter 继续...')
            break
        who_idx = who(data)
        if who_idx[0] == who_idx[2] and who_idx[2] != -1:
            last_hash = 0
            print('你的回合！')
            print('输入要出的牌（如 R5），或输入 SK 跳过：')
            card = input_with_timeout('> ', timeout=65)
            if card is None:
                print('超时未操作，自动跳过或已被服务器跳过...')
                input('按 Enter 继续...')
                continue
            card = card.strip().upper()
            if card not in data['hand'] and card != 'SK':
                print('你没有这张牌！')
                input('按 Enter 继续...')
                continue
            try:
                resp = requests.post(f'{SERVER}/play', json={'uid': uid, 'card': card})
            except:
                print('与服务器的连接丢失，正在重连...')
                time.sleep(1)
                continue
            play_data = resp.json()
            if play_data['status'] != 'success':
                print('出牌失败:', play_data.get('reason'))
                continue
        else:
            print('等待其他玩家出牌...')
            time.sleep(0.2)


def main():
    try:
        print(f'已更换到自定义服务器: {sys.argv[1]}')
        SERVER = sys.argv[1]
        time.sleep(0.5)
        clear_screen()
    except:
        pass
    try:
        resp = requests.post(f'{SERVER}/status', json={'uid': 'version_check'})
        data = resp.json()
        min_version = data.get('min_client_version')
        if min_version and CLIENT_VERSION < min_version:
            print(
                f'警告：客户端版本过低，最低要求为 {min_version}，当前为 {CLIENT_VERSION}，请升级客户端！'
            )
            return
    except Exception as e:
        pass
    # 断线重连机制
    uid = get_uid()
    if uid:
        resp = requests.post(f'{SERVER}/status', json={'uid': uid})
        data = resp.json()
        # 只有游戏已开始且未结束才自动恢复
        if data.get('status') == 'success' and data.get('game_status') == 'playing':
            print('检测到上次未完成的对局。')
            choice = input('是否恢复上次对局？(Y/n): ').strip().lower()
            if choice in ('', 'y', 'yes'):  # 默认Y
                print('正在恢复对局...')
                game_loop(uid)
                return
            else:
                clear_uid()
        else:
            clear_uid()
    username = get_username()
    clear_screen()
    print(BAR)
    print(f'UNO 牌的网络联机版，版本号：{CLIENT_VERSION}')
    mode = input('1. 创建房间  2. 加入房间 选择: ')
    if mode == '1':
        count = int(input('房间人数(2-8): '))
        resp = requests.post(f'{SERVER}/create', json={'count': count})
        data = resp.json()
        if data['status'] != 'success':
            print('创建失败:', data.get('reason'))
            return
        room_id = data['id']
        print('房间ID:', room_id)
    else:
        room_id = input('输入房间ID: ').strip()
    clear_screen()
    resp = requests.post(f'{SERVER}/join', json={'id': room_id, 'username': username})
    data = resp.json()
    if data['status'] != 'success':
        print('加入失败:', data.get('reason'))
        return
    uid = data['uid']
    save_uid(uid)
    print(f'加入成功，等待其他玩家...\n你的身份ID: {uid}')
    last_hash = 0
    while True:
        try:
            resp = requests.post(f'{SERVER}/status', json={'uid': uid})
        except:
            print('与服务器的连接丢失，正在重连...')
            time.sleep(0.5)
            continue
        data = resp.json()
        if hash_dict(data) == last_hash:
            continue
        last_hash = hash_dict(data)
        clear_screen()
        print(f'Room ID: {room_id} User ID: {uid}')
        if data['status'] != 'success':
            print('状态获取失败:', data.get('reason'))
            return
        if (
            data.get('game_status') == 'playing'
            or data.get('game_status') == 'finished'
        ):
            break
        print('玩家: ', ', '.join(data.get('players', [])))
        print('当前已加入:', len(data['players']))
        time.sleep(0.2)
    game_loop(uid, room_id)


if __name__ == '__main__':
    main()
