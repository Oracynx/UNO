from loguru import logger
import sys

# 移除 Flask 默认日志
import logging

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

logger.remove()
logger.add(
    sys.stdout, level="INFO", format="[{time:YYYY-MM-DD HH:mm:ss}] <{level}> {message}"
)
logger.add(
    "uno_server.log",
    rotation="10 MB",
    retention="10 days",
    level="DEBUG",
    encoding="utf-8",
)

import json
import flask
import flask_cors
import random
import threading
import time
import os

app = flask.Flask(__name__)
flask_cors.CORS(app)

MIN_COUNT = 2
MAX_COUNT = 12

CHARSET = '0123456789'
ROOM_ID_LEN = 6
PLAYER_ID_LEN = 12

MIN_CLIENT_VERSION = '3.0.0'

name = {}
room = {}
where = {}

lock = threading.Lock()


def getDeck():
    deck = []
    # 每种颜色0-9各1张（0只有1张，1-9各2张），S/R/D各2张
    for c in 'RYGB':
        deck.append(f'{c}0')
        for i in range(1, 10):
            deck.append(f'{c}{i}')
            deck.append(f'{c}{i}')
        deck.append(f'{c}S')
        deck.append(f'{c}S')
        deck.append(f'{c}R')
        deck.append(f'{c}R')
        deck.append(f'{c}D')
        deck.append(f'{c}D')
    # 万能牌
    for _ in range(4):
        deck.append('WW')
        deck.append('WD')
    random.shuffle(deck)
    return deck


def getUid():
    return ''.join(random.choices(CHARSET, k=PLAYER_ID_LEN))


def getId():
    return ''.join(random.choices(CHARSET, k=ROOM_ID_LEN))


def sort_hand(hand):
    color_order = {'R': 0, 'Y': 1, 'G': 2, 'B': 3}
    func_order = {'S': 1, 'R': 2, 'D': 3}

    def card_key(card):
        if card.startswith('W'):
            return (4, 0, 0, card)
        color = card[0]
        value = card[1:]
        if value.isdigit():
            return (color_order[color], 0, int(value), card)
        elif value in func_order:
            return (color_order[color], 1, func_order[value], card)
        else:
            return (color_order[color], 2, 0, card)

    return sorted(hand, key=card_key)


# [FIX] 新增：当牌堆用完时，自动洗牌
def refill_deck_if_needed(room_instance):
    """如果牌堆为空，则将历史记录洗牌作为新牌堆"""
    if not room_instance['deck']:
        logger.warning(
            f"Room {where.get(room_instance['player'][0], '')} deck is empty. Refilling from history."
        )
        room_instance['deck'] = getDeck()


def schedule_room_cleanup(room_id, timeout=300, only_if_waiting=False):
    def cleanup():
        time.sleep(timeout)
        with lock:
            if room_id in room:
                if only_if_waiting and room[room_id].get('status') != 'waiting':
                    return
                # 使用list拷贝以避免在迭代时修改字典
                players_in_room = list(room[room_id].get('player', []))
                for uid in players_in_room:
                    if uid in name:
                        del name[uid]
                    if uid in where:
                        del where[uid]
                del room[room_id]
                logger.info(f"Cleaned up room {room_id}")

    t = threading.Thread(target=cleanup, daemon=True)
    t.start()


def rotate_turn(room, played_card=None, skip_count=0):
    players = room['player']
    n = len(players)
    direction = room.get('direction', 1)

    if played_card:
        if played_card.endswith('R'):
            direction *= -1
            room['direction'] = direction
        # 跳过牌和+2/+4都应跳过下家
        skip = 0
        draw_count = 0
        is_wild = False
        if played_card.endswith('S'):
            skip = 1
        if played_card.endswith('D'):
            draw_count = 2
            skip = 1
        if played_card == 'WD':
            draw_count = 4
            is_wild = True
            skip = 1
        if played_card == 'WW':
            is_wild = True
        if draw_count > 0:
            target_idx = (room['turn'] + direction) % n
            target_player_id = players[target_idx]
            refill_deck_if_needed(room)
            for _ in range(draw_count):
                if room['deck']:
                    room['hand'][target_player_id].append(room['deck'].pop())
            room['hand'][target_player_id] = sort_hand(room['hand'][target_player_id])
        # 万能牌/功能牌后，是否继续由自己出牌
        if is_wild:
            room['wait_time'] = [0] * n
            return
        # 跳过/加2/加4都应跳到下下家
        if skip > 0:
            room['turn'] = (room['turn'] + (1 + skip) * direction) % n
            room['wait_time'] = [0] * n
            return
    # 普通牌正常轮换
    room['turn'] = (room['turn'] + direction) % n
    if n > 0:
        room['wait_time'] = [0] * n


def start_auto_skip_thread(room_id):
    def auto_skip():
        while True:
            time.sleep(1)
            with lock:
                if (
                    room_id not in room
                    or room.get(room_id, {}).get('status') != 'playing'
                ):
                    break

                current_room = room[room_id]
                turn = current_room.get('turn', 0)
                players = current_room['player']

                if 'wait_time' not in current_room or len(
                    current_room['wait_time']
                ) != len(players):
                    current_room['wait_time'] = [0] * len(players)

                current_room['wait_time'][turn] += 1

                if current_room['wait_time'][turn] >= 60:
                    logger.info(
                        f"Player {name.get(players[turn])} in room {room_id} timed out."
                    )
                    uid = players[turn]

                    refill_deck_if_needed(current_room)  # [FIX] 摸牌前检查牌堆
                    if current_room['deck']:
                        current_room['hand'][uid].append(current_room['deck'].pop())
                        current_room['hand'][uid] = sort_hand(current_room['hand'][uid])
                        current_room['table_history'].append('TL')

                    rotate_turn(current_room, skip_count=0)

    t = threading.Thread(target=auto_skip, daemon=True)
    t.start()


@app.route('/status', methods=['POST'])
def status():
    logger.info(f"/status called by {flask.request.remote_addr}")
    data = flask.request.get_json()
    if data.get('uid') == 'version_check':
        return {'min_client_version': MIN_CLIENT_VERSION}
    if 'uid' not in data:
        return {'status': 'fail', 'reason': 'Missing uid'}

    uid = data['uid']
    if uid not in where:
        return {'status': 'fail', 'reason': 'Invalid uid'}

    id = where[uid]
    if id not in room:
        return {'status': 'fail', 'reason': 'Room not found'}

    current_room = room[id]
    players = current_room['player']
    player_names = [name.get(pid, 'Joining...') for pid in players]
    hand = current_room['hand'].get(uid, [])
    turn_idx = current_room.get('turn', 0)
    direction = current_room.get('direction', 1)
    next_idx = (turn_idx + direction) % len(players) if players else 0

    # [FIX] 增加状态展示，特别是 chosen_color
    return {
        'status': 'success',
        'players': player_names,
        'hand': hand,
        'current_idx': turn_idx,
        'next_idx': next_idx,
        'my_idx': players.index(uid) if uid in players else -1,
        'top': current_room.get('top', None),
        'chosen_color': current_room.get('chosen_color', None),
        'table_history': current_room.get('table_history', []),
        'game_status': current_room.get('status', 'unknown'),
        'winner': current_room.get('winner', None),
        'hand_count': [len(current_room['hand'].get(pid, [])) for pid in players],
        'direction': current_room.get('direction', 1),
    }


@app.route('/join', methods=['POST'])
def join():
    logger.info(f"/join called: {flask.request.get_json()}")
    data = flask.request.get_json()
    id = data.get('id')
    username = data.get('username')

    if not id or not username:
        return {'status': 'fail', 'reason': 'Missing id or username'}
    if id not in room:
        return {'status': 'fail', 'reason': 'Room not found'}
    if room[id]['status'] != 'waiting':
        return {'status': 'fail', 'reason': 'Game has already started'}

    with lock:
        if len(room[id]['player']) >= room[id]['count']:
            return {'status': 'fail', 'reason': 'Room is full'}

        uid = getUid()
        name[uid] = username
        where[uid] = id
        room[id]['player'].append(uid)

        # 检查是否全部加入
        if len(room[id]['player']) == room[id]['count']:
            current_room = room[id]
            random.shuffle(current_room['player'])

            deck = getDeck()
            current_room['deck'] = deck

            for pid in current_room['player']:
                hand = [deck.pop() for _ in range(7)]
                current_room['hand'][pid] = sort_hand(hand)

            # [FIX] 确保开局第一张是数字牌
            while True:
                refill_deck_if_needed(current_room)
                top_card = deck.pop()
                if top_card[1].isdigit():
                    current_room['top'] = top_card
                    break
                else:
                    deck.append(top_card)
                    random.shuffle(deck)

            current_room['table_history'] = [current_room['top']]
            current_room['status'] = 'playing'
            start_auto_skip_thread(id)

    return {'status': 'success', 'uid': uid}


@app.route('/create', methods=['POST'])
def create():
    logger.info(f"/create called: {flask.request.get_json()}")
    data = flask.request.get_json()
    count = data.get('count')
    if not isinstance(count, int) or not (MIN_COUNT <= count <= MAX_COUNT):
        return {'status': 'fail', 'reason': 'Invalid player count'}

    id = getId()
    with lock:
        # [FIX] 完整初始化房间状态
        room[id] = {
            'count': count,
            'status': 'waiting',
            'player': [],
            'hand': {},
            'deck': [],
            'top': None,
            'turn': 0,
            'direction': 1,
            'table_history': [],
            'chosen_color': None,
            'winner': None,
        }
    schedule_room_cleanup(id, timeout=300, only_if_waiting=True)
    logger.info(f"Room {id} created for {count} players.")
    return {'status': 'success', 'id': id}


@app.route('/play', methods=['POST'])
def play():
    logger.info(f"/play called: {flask.request.get_json()}")
    data = flask.request.get_json()
    uid = data.get('uid')
    card = data.get('card')

    if not uid or not card:
        return {'status': 'fail', 'reason': 'Missing uid or card'}
    if uid not in where:
        return {'status': 'fail', 'reason': 'Invalid uid'}

    id = where[uid]
    if id not in room:
        return {'status': 'fail', 'reason': 'Room not found'}

    with lock:
        current_room = room[id]
        players = current_room['player']
        turn = current_room['turn']

        if players[turn] != uid:
            return {'status': 'fail', 'reason': 'Not your turn'}

        hand = current_room['hand'][uid]

        if card == 'SK':
            refill_deck_if_needed(current_room)  # [FIX] 摸牌前检查牌堆
            if current_room['deck']:
                hand.append(current_room['deck'].pop())
                current_room['table_history'].append('SK')
                current_room['hand'][uid] = sort_hand(hand)
            rotate_turn(current_room, skip_count=0)
            return {'status': 'success'}

        if card not in hand:
            return {'status': 'fail', 'reason': 'Card not in hand'}

        top = current_room['top']
        chosen_color = current_room.get('chosen_color')

        # [FIX] 重构 can_play 逻辑
        def can_play(p_card, p_top, p_chosen_color):
            if p_card[0] == p_top[0] or p_top[0] == 'W':
                return True
            if p_card.startswith('W'):
                return True
            if p_top and (p_card[0] == p_top[0] or p_card[1:] == p_top[1:]):
                return True
            return False

        if not can_play(card, top, chosen_color):
            return {'status': 'fail', 'reason': 'Card does not match'}

        hand.remove(card)
        current_room['top'] = card
        current_room['table_history'].append(card)

        # [FIX] 万能牌处理
        if card.startswith('W'):
            color = data.get('color')
            if color not in ['R', 'Y', 'G', 'B']:
                # 如果客户端没传颜色，服务器随便选一个，增加容错
                color = random.choice(['R', 'Y', 'G', 'B'])
            current_room['chosen_color'] = color
        else:
            # 打出普通牌后，清除万能牌颜色状态
            current_room['chosen_color'] = None

        rotate_turn(current_room, played_card=card)

        if not hand:
            current_room['status'] = 'finished'
            current_room['winner'] = name[uid]
            schedule_room_cleanup(id, timeout=600, only_if_waiting=False)

        return {'status': 'success'}


@app.route('/product', methods=['GET'])
def product():
    # 绝对路径，便于调试
    dist_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), 'dist', 'client.exe')
    )
    logger.info(f"/product 请求，查找路径: {dist_path}")
    if not os.path.exists(dist_path):
        logger.warning(f"client.exe not found at {dist_path}")
        return f'client.exe not found at {dist_path}', 404
    return flask.send_file(dist_path, as_attachment=True, download_name='client.exe')


# IP 封禁等管理功能保持不变
BANNED_IPS = set()
BAN_IP_SECRET = os.environ.get('BAN_IP_SECRET', 'default_secret')


@app.before_request
def block_banned_ip():
    ip = flask.request.remote_addr
    if ip in BANNED_IPS:
        logger.warning(f"Blocked banned IP: {ip}")
        return flask.jsonify({'status': 'fail', 'reason': 'IP banned'}), 403


@app.route('/ban_ip', methods=['POST'])
def ban_ip():
    data = flask.request.get_json()
    ip = data.get('ip')
    secret = data.get('secret')
    if not ip or not secret:
        return {'status': 'fail', 'reason': 'Missing ip or secret'}
    if secret != BAN_IP_SECRET:
        logger.warning(
            f"Ban IP attempt failed: wrong secret from {flask.request.remote_addr}"
        )
        return {'status': 'fail', 'reason': 'Unauthorized'}, 401
    BANNED_IPS.add(ip)
    logger.info(f"IP banned: {ip} by {flask.request.remote_addr}")
    return {'status': 'success', 'ip': ip}


@app.route('/unban_ip', methods=['POST'])
def unban_ip():
    data = flask.request.get_json()
    ip = data.get('ip')
    secret = data.get('secret')
    if not ip or not secret:
        return {'status': 'fail', 'reason': 'Missing ip or secret'}
    if secret != BAN_IP_SECRET:
        logger.warning(
            f"Unban IP attempt failed: wrong secret from {flask.request.remote_addr}"
        )
        return {'status': 'fail', 'reason': 'Unauthorized'}, 401
    BANNED_IPS.discard(ip)
    logger.info(f"IP unbanned: {ip} by {flask.request.remote_addr}")
    return {'status': 'success', 'ip': ip}


@app.route('/')
def index():
    # 确保index.html存在于脚本同级目录
    if os.path.exists('./index.html'):
        return open('./index.html', 'r', encoding='utf-8').read()
    return "<h1>UNO Server is running</h1><p>index.html not found.</p>"


# 持久化功能保持不变
DATA_FILE = 'data.json'
SAVE_INTERVAL = 5


def save_data_periodically():
    while True:
        time.sleep(SAVE_INTERVAL)
        try:
            with lock:
                with open(DATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump(
                        {'name': name, 'room': room, 'where': where},
                        f,
                        ensure_ascii=False,
                        indent=2,
                    )
        except Exception as e:
            logger.error(f"Failed to save data: {e}")


def load_data_on_start():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        with lock:
            name.update(data.get('name', {}))
            room.update(data.get('room', {}))
            where.update(data.get('where', {}))
        logger.info('Data loaded from data.json')
    except Exception as e:
        logger.warning(f"No previous data loaded: {e}")


if __name__ == '__main__':
    load_data_on_start()
    threading.Thread(target=save_data_periodically, daemon=True).start()
    for room_id, current_room in room.items():
        if current_room.get('status') == 'playing':
            logger.info(f"Restarting auto-skip thread for active room {room_id}")
            start_auto_skip_thread(room_id)
    app.run(host='0.0.0.0', port=5000)
