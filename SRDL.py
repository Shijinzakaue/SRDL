import os
import re
import sys
import time
import ctypes
import msvcrt
import threading
import subprocess
import shutil
from datetime import datetime, timedelta, timezone

import m3u8
import requests
from bs4 import BeautifulSoup

DOWNLOAD_DIR = 'downloads'
POLL_INTERVAL_SEC = 5
RECOVERY_WINDOW_SEC = 600
NO_LINK_GRACE_SEC = 20


class Printer:
    def __init__(self):
        self._last_len = 0
        self._active = False
        self._vt = False
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                self._vt = bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
        except Exception:
            self._vt = False

    def overwrite(self, msg):
        if self._vt:
            sys.stdout.write('\r\x1b[2K' + msg)
        else:
            pad = max(self._last_len, len(msg))
            sys.stdout.write('\r' + (' ' * pad) + '\r' + msg)
        self._last_len = len(msg)
        self._active = bool(msg)
        sys.stdout.flush()

    def commit(self, msg=None):
        if msg is not None:
            self.overwrite(msg)
        if self._active or msg is not None:
            sys.stdout.write('\n')
            sys.stdout.flush()
        self._last_len = 0
        self._active = False

    def println(self, msg):
        if self._active:
            self.commit()
        print(msg)
        self._active = False


def sanitize_filename(name):
    return re.sub(r'[\\/:*?"<>|]', '_', name)


def get_room_id_from_url(url):
    html = requests.get(url).text
    soup = BeautifulSoup(html, 'html.parser')
    a_tags = soup.find_all('a', class_='st-header__link', href=True)
    for a in a_tags:
        m = re.search(r'room_id=(\d+)', a['href'])
        if m:
            return m.group(1)
    m = re.search(r'room_id=(\d+)', url)
    if m:
        return m.group(1)
    return None


def get_streaming_url(room_id):
    api_url = f'https://www.showroom-live.com/api/live/streaming_url?room_id={room_id}'
    try:
        resp = requests.get(api_url, timeout=10)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    return data.get('streaming_url_list', [])


def parse_m3u8_url(streaming_url_list):
    for item in streaming_url_list:
        if item.get('type') == 'hls' and 'ss.m3u8' in item.get('url', ''):
            return item.get('url')
    for item in streaming_url_list:
        if item.get('type') == 'hls':
            return item.get('url')
    return None


def get_live_m3u8_url(room_id):
    streaming_url_list = get_streaming_url(room_id)
    if not streaming_url_list:
        return None
    return parse_m3u8_url(streaming_url_list)


def poll_stream_in_last_minute(room_id, target_dt=None, interval_sec=POLL_INTERVAL_SEC, timeout_sec=RECOVERY_WINDOW_SEC):
    printer = Printer()
    started_at = datetime.now()
    deadline = (target_dt + timedelta(seconds=timeout_sec)) if target_dt else (started_at + timedelta(seconds=timeout_sec))

    try:
        while datetime.now() <= deadline:
            live_m3u8_url = get_live_m3u8_url(room_id)
            if live_m3u8_url:
                printer.commit('偵測到已開台，立即開始下載。')
                return live_m3u8_url

            now = datetime.now()
            remain_timeout = max(0, int((deadline - now).total_seconds()))

            if target_dt is not None:
                remain_target = int((target_dt - now).total_seconds())
                if remain_target > 0:
                    msg = f'輪詢中，預計 {remain_target} 秒後開台，{interval_sec} 秒後再檢查...'
                else:
                    msg = f'已到開台時間，{interval_sec} 秒後再檢查...（距離輪詢結束 {remain_timeout} 秒）'
            else:
                msg = f'輪詢中，{interval_sec} 秒後再檢查...（距離輪詢結束 {remain_timeout} 秒）'

            printer.overwrite(msg)
            time.sleep(interval_sec)
    except KeyboardInterrupt:
        printer.commit()
        raise

    printer.commit('10 分鐘內仍未開台。')
    return None


def get_title_from_url(url):
    html = requests.get(url).text
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.string if soup.title else 'Showroom'
    title = re.sub(r'[|｜].*$', '', title).strip()
    return title


def prompt_schedule():
    while True:
        sched = input('請輸入開台時間（本地時間）YYMMDDhhmm：').strip()
        if re.match(r'^\d{10}$', sched):
            return sched
        print('格式錯誤，請重新輸入')


def get_next_live_local_schedule(room_id):
    api_url = f'https://www.showroom-live.com/api/room/next_live?room_id={room_id}'
    try:
        resp = requests.get(api_url, timeout=10)
        if resp.status_code != 200:
            return None, 'next_live API 讀取失敗'
        data = resp.json()
    except Exception:
        return None, 'next_live API 讀取失敗'

    text = str(data.get('text', '')).strip()
    if not text or text == '未定':
        return None, '未定'

    epoch = data.get('epoch')
    if isinstance(epoch, int) and epoch > 0:
        try:
            local_dt = datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone()
            return local_dt.strftime('%y%m%d%H%M'), local_dt
        except Exception:
            pass

    m = re.match(r'^(\d{2})/(\d{2})\s+(\d{2}):(\d{2})$', text)
    if not m:
        return None, 'next_live 時間格式無法解析'

    now_local = datetime.now().astimezone()
    year = now_local.year
    month = int(m.group(1))
    day = int(m.group(2))
    hour = int(m.group(3))
    minute = int(m.group(4))

    jst = timezone(timedelta(hours=9))
    try:
        jst_dt = datetime(year, month, day, hour, minute, tzinfo=jst)
    except Exception:
        return None, 'next_live 時間格式無法解析'

    if jst_dt.astimezone(now_local.tzinfo) < now_local and month <= now_local.month:
        try:
            jst_dt = datetime(year + 1, month, day, hour, minute, tzinfo=jst)
        except Exception:
            return None, 'next_live 時間格式無法解析'

    local_dt = jst_dt.astimezone(now_local.tzinfo)
    return local_dt.strftime('%y%m%d%H%M'), local_dt


def wait_until(target_dt):
    printer = Printer()

    def format_remaining(total_seconds):
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f'距離程序開始還剩 {int(hours)}時{int(minutes)}分{int(seconds)}秒'

    try:
        while True:
            now = datetime.now()
            delta = int((target_dt - now).total_seconds())
            if delta <= 0:
                printer.commit('距離開台1分鐘，已開始輪詢。')
                break
            printer.overwrite(format_remaining(delta))
            time.sleep(min(delta, 1))
    except KeyboardInterrupt:
        printer.commit()
        raise


def download_ts_files(m3u8_url, out_dir, room_id):
    folder_display = os.path.basename(out_dir)
    print(f'開始下載: {folder_display}')
    os.makedirs(out_dir, exist_ok=True)

    printer = Printer()
    stop_flag = {'stop': False}
    stop_reason = {'reason': 'unknown'}
    latest_seq = {'value': None}

    m3u8_state = {'url': m3u8_url, 'last_check': 0.0, 'no_link_since': None}

    downloaded_set = set()
    missing_segments = set()
    backup_count = {'value': 0}
    error_count = {'value': 0}
    first_segment = {'value': None}

    data_lock = threading.Lock()
    m3u8_lock = threading.Lock()
    state_lock = threading.Lock()
    log_lock = threading.Lock()

    log_path = os.path.join(out_dir, 'log.log')

    def write_log(tag, text):
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with log_lock:
            with open(log_path, 'a', encoding='utf-8') as lf:
                lf.write(f'[{now}] [{tag}] {text}\n')

    def extract_seq(ts_url_clean):
        m = re.search(r'-(\d+)\.ts$', ts_url_clean)
        if not m:
            m = re.search(r'(\d+)\.ts$', ts_url_clean)
        return int(m.group(1)) if m else None

    def status_str():
        with data_lock:
            return (
                f'已下載: {len(downloaded_set)} 個片段 | '
                f'補抓: {backup_count["value"]} 個片段 | '
                f'錯誤: {error_count["value"]} 個片段'
            )

    def mark_first_segment(ts_url_clean):
        first_name = None
        with data_lock:
            if first_segment['value'] is None:
                first_segment['value'] = os.path.basename(ts_url_clean)
                first_name = first_segment['value']
        if first_name is not None:
            write_log('FIRST', first_name)

    def update_first_segment_if_earlier(ts_url_clean):
        new_name = os.path.basename(ts_url_clean)
        new_seq = extract_seq(new_name)
        if new_seq is None:
            return
        updated = None
        with data_lock:
            current_name = first_segment['value']
            current_seq = extract_seq(current_name) if current_name else None
            if current_seq is None or new_seq < current_seq:
                first_segment['value'] = new_name
                updated = new_name
        if updated is not None:
            write_log('FIRST', updated)

    def check_live_state():
        now_ts = time.time()
        with state_lock:
            if now_ts - m3u8_state['last_check'] < POLL_INTERVAL_SEC:
                return
            m3u8_state['last_check'] = now_ts

        latest_m3u8_url = get_live_m3u8_url(room_id)
        if latest_m3u8_url:
            with state_lock:
                m3u8_state['no_link_since'] = None
                if m3u8_state['url'] != latest_m3u8_url:
                    m3u8_state['url'] = latest_m3u8_url
                    write_log('LIVE', f'更新 m3u8 URL: {latest_m3u8_url}')
            return

        with state_lock:
            if m3u8_state['no_link_since'] is None:
                m3u8_state['no_link_since'] = now_ts
                write_log('LIVE', f'偵測到下播，最後檢查 {NO_LINK_GRACE_SEC} 秒')
                return
            if now_ts - m3u8_state['no_link_since'] >= NO_LINK_GRACE_SEC:
                stop_flag['stop'] = True
                stop_reason['reason'] = 'no_m3u8'

    def main_thread():
        session = requests.Session()
        while not stop_flag['stop']:
            check_live_state()
            if stop_flag['stop']:
                break

            try:
                with state_lock:
                    current_m3u8_url = m3u8_state['url']
                m3u8_obj = m3u8.load(current_m3u8_url)
                for seg in m3u8_obj.segments:
                    if stop_flag['stop']:
                        break

                    ts_url = seg.absolute_uri
                    ts_url_clean = ts_url.split('?')[0]
                    seq = extract_seq(ts_url_clean)
                    if seq is not None:
                        latest_seq['value'] = seq

                    fname = os.path.join(out_dir, os.path.basename(ts_url_clean))
                    if os.path.exists(fname):
                        with data_lock:
                            downloaded_set.add(ts_url_clean)
                        continue

                    try:
                        r = session.get(ts_url, timeout=5)
                        if r.status_code == 200:
                            with open(fname, 'wb') as f:
                                f.write(r.content)
                            with data_lock:
                                downloaded_set.add(ts_url_clean)
                                missing_segments.discard(ts_url_clean)
                            mark_first_segment(ts_url_clean)
                        else:
                            with data_lock:
                                error_count['value'] += 1
                                missing_segments.add(ts_url_clean)
                            write_log('ERROR', f'HTTP {r.status_code} | {os.path.basename(ts_url_clean)}')
                    except Exception as e:
                        with data_lock:
                            error_count['value'] += 1
                            missing_segments.add(ts_url_clean)
                        write_log('ERROR', f'{e} | {os.path.basename(ts_url_clean)}')
            except Exception as e:
                with data_lock:
                    error_count['value'] += 1
                write_log('ERROR', f'main_thread: {e}')

            time.sleep(2)

    def backup_thread():
        session = requests.Session()
        while not stop_flag['stop']:
            check_live_state()
            if stop_flag['stop']:
                break

            try:
                with data_lock:
                    retry_list = list(missing_segments)[:60]

                for ts_url_clean in retry_list:
                    if stop_flag['stop']:
                        break
                    fname = os.path.join(out_dir, os.path.basename(ts_url_clean))
                    if os.path.exists(fname):
                        with data_lock:
                            missing_segments.discard(ts_url_clean)
                        continue

                    try:
                        r = session.get(ts_url_clean, timeout=2)
                        if r.status_code == 200:
                            with open(fname, 'wb') as f:
                                f.write(r.content)
                            with data_lock:
                                backup_count['value'] += 1
                                downloaded_set.add(ts_url_clean)
                                missing_segments.discard(ts_url_clean)
                            mark_first_segment(ts_url_clean)
                            write_log('BACKUP', os.path.basename(ts_url_clean))
                        else:
                            with data_lock:
                                error_count['value'] += 1
                    except Exception:
                        with data_lock:
                            error_count['value'] += 1

                current_latest = latest_seq['value']
                if current_latest is None:
                    time.sleep(1)
                    continue

                with state_lock:
                    current_m3u8_url = m3u8_state['url']
                live_obj = m3u8.load(current_m3u8_url)
                if not live_obj.segments:
                    time.sleep(1)
                    continue

                sample_clean = live_obj.segments[-1].absolute_uri.split('?')[0]
                if not re.search(r'-(\d+)\.ts$', sample_clean):
                    time.sleep(1)
                    continue

                window_end = max(0, current_latest - 101)
                for seq in range(current_latest - 1, window_end, -1):
                    if stop_flag['stop']:
                        break
                    if seq <= 0:
                        continue

                    ts_url_base_clean = re.sub(r'-(\d+)\.ts$', f'-{seq}.ts', sample_clean)
                    fname = os.path.join(out_dir, os.path.basename(ts_url_base_clean))
                    if os.path.exists(fname):
                        continue

                    try:
                        r = session.get(ts_url_base_clean, timeout=2)
                        if r.status_code == 200:
                            with open(fname, 'wb') as f:
                                f.write(r.content)
                            with data_lock:
                                backup_count['value'] += 1
                                downloaded_set.add(ts_url_base_clean)
                            mark_first_segment(ts_url_base_clean)
                            write_log('BACKUP', os.path.basename(ts_url_base_clean))
                        else:
                            with data_lock:
                                error_count['value'] += 1
                    except Exception:
                        with data_lock:
                            error_count['value'] += 1

                with data_lock:
                    first_name = first_segment['value']
                first_seq = extract_seq(first_name) if first_name else None
                if first_seq is not None and first_seq > 1:
                    backward_end = max(1, first_seq - 101)
                    for seq in range(first_seq - 1, backward_end - 1, -1):
                        if stop_flag['stop']:
                            break
                        if seq <= 0:
                            continue

                        ts_url_base_clean = re.sub(r'-(\d+)\.ts$', f'-{seq}.ts', sample_clean)
                        fname = os.path.join(out_dir, os.path.basename(ts_url_base_clean))
                        if os.path.exists(fname):
                            continue

                        try:
                            r = session.get(ts_url_base_clean, timeout=2)
                            if r.status_code == 200:
                                with open(fname, 'wb') as f:
                                    f.write(r.content)
                                with data_lock:
                                    backup_count['value'] += 1
                                    downloaded_set.add(ts_url_base_clean)
                                update_first_segment_if_earlier(ts_url_base_clean)
                                write_log('BACKUP', os.path.basename(ts_url_base_clean))
                            else:
                                with data_lock:
                                    error_count['value'] += 1
                        except Exception:
                            with data_lock:
                                error_count['value'] += 1
            except Exception as e:
                with data_lock:
                    error_count['value'] += 1
                write_log('ERROR', f'backup_thread: {e}')

            time.sleep(1)

    t1 = threading.Thread(target=main_thread)
    t2 = threading.Thread(target=backup_thread)
    t1.start()
    t2.start()

    try:
        while t1.is_alive() or t2.is_alive():
            printer.overwrite(status_str())
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop_flag['stop'] = True
        stop_reason['reason'] = 'interrupted'
        t1.join(timeout=8)
        t2.join(timeout=8)
        printer.commit(status_str())
        printer.println('下載中斷，開始合併...')
        return 'interrupted'

    t1.join()
    t2.join()
    printer.commit(status_str())

    if stop_reason['reason'] == 'no_m3u8':
        printer.println('確認已關台，停止下載並進入合併。')

    if stop_reason['reason'] == 'unknown':
        stop_reason['reason'] = 'completed'
    return stop_reason['reason']


def merge_ts_to_mp4(out_dir, out_name):
    ts_files = [f for f in os.listdir(out_dir) if f.endswith('.ts')]
    if not ts_files:
        print('沒有可合併的 ts 片段，保留現有檔案並結束。')
        return False

    ts_files.sort(
        key=lambda x: int(re.search(r'-(\d+)\.ts', x).group(1)) if re.search(r'-(\d+)\.ts', x) else 0
    )

    mp4_path = os.path.join(out_dir, out_name)
    combined_ts_path = os.path.join(out_dir, '_combined.ts')
    total_ts_size = 0
    for ts in ts_files:
        total_ts_size += os.path.getsize(os.path.join(out_dir, ts))

    try:
        if os.path.exists(combined_ts_path):
            os.remove(combined_ts_path)
    except Exception:
        pass

    print('正在合併 ts 為單一串流 ...')
    try:
        with open(combined_ts_path, 'wb') as out_f:
            for ts in ts_files:
                src_path = os.path.join(out_dir, ts)
                with open(src_path, 'rb') as in_f:
                    shutil.copyfileobj(in_f, out_f, length=8 * 1024 * 1024)
    except Exception as e:
        print(f'合併 TS 失敗：{e}，已保留 ts 檔案。')
        return False

    cmd = ['ffmpeg', '-y', '-i', '_combined.ts', '-c', 'copy', '-movflags', '+faststart', out_name]
    print('正在封裝 mp4 ...')
    try:
        result = subprocess.run(
            cmd,
            cwd=out_dir,
            stdin=subprocess.DEVNULL,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        print('轉檔逾時（900 秒），已保留 ts 與 _combined.ts。')
        return False

    if result.returncode != 0:
        print(f'轉檔失敗 (ffmpeg code={result.returncode})，已保留 ts 檔案。')
        return False

    if not os.path.exists(mp4_path):
        print('轉檔失敗：未產生 mp4，已保留 ts 檔案。')
        return False

    mp4_size = os.path.getsize(mp4_path)
    min_expected_size = max(1024, int(total_ts_size * 0.5))
    if mp4_size < min_expected_size:
        print(f'轉檔失敗：mp4 大小異常 ({mp4_size} bytes)，已保留 ts 檔案。')
        return False

    print(f'完成: {mp4_path}')
    for f in os.listdir(out_dir):
        if f.endswith('.ts'):
            try:
                os.remove(os.path.join(out_dir, f))
            except Exception:
                pass
    try:
        if os.path.exists(combined_ts_path):
            os.remove(combined_ts_path)
    except Exception:
        pass
    return True


def choose_menu(title, options):
    selected = 0

    vt_ok = False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            vt_ok = bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        vt_ok = False

    def draw_menu():
        for i, opt in enumerate(options):
            mark = 'X' if i == selected else ' '
            sys.stdout.write(f'    [{mark}] {opt}\n')
        sys.stdout.flush()

    print(title)
    draw_menu()

    while True:
        ch = msvcrt.getch()
        if ch in (b' ',):
            selected = (selected + 1) % len(options)
            if vt_ok:
                sys.stdout.write('\x1b[2F')
                sys.stdout.flush()
            draw_menu()
        elif ch in (b'\r', b'\n'):
            return selected


def interactive_menu():
    options = ['立刻下載', '排程下載']
    return choose_menu('> 選擇操作 (按 空格鍵 切換, Enter 確認)', options)


def main():
    url = input('請輸入 Showroom 直播網址: ').strip()
    room_id = get_room_id_from_url(url)
    if not room_id:
        print('無法擷取 room_id')
        print('程序已結束。')
        return

    sel = interactive_menu()
    schedule = (sel == 1)

    if schedule:
        sched_str, schedule_info = get_next_live_local_schedule(room_id)
        if sched_str:
            jst_dt = schedule_info.astimezone(timezone(timedelta(hours=9)))
            print(f'偵測到平台排程：{jst_dt.strftime("%m/%d %H:%M")}（JST）')
            print(f'已自動換算本地時間並排程：{sched_str}')
        else:
            print('未偵測到平台排程。')
            sched_str = prompt_schedule()

        try:
            target_dt = datetime.strptime(sched_str, '%y%m%d%H%M')
        except Exception:
            print('時間格式錯誤')
            print('程序已結束。')
            return

        now = datetime.now()
        if (target_dt - now).total_seconds() > 60:
            wait_until(target_dt - timedelta(minutes=1))

        m3u8_url = poll_stream_in_last_minute(room_id, target_dt, interval_sec=POLL_INTERVAL_SEC, timeout_sec=RECOVERY_WINDOW_SEC)
        if not m3u8_url:
            print('程序已結束。')
            return

    if 'm3u8_url' not in locals() or not m3u8_url:
        m3u8_url = get_live_m3u8_url(room_id)
    if not m3u8_url:
        print('目前未開台，執行 10 分鐘自動輪詢...')
        m3u8_url = poll_stream_in_last_minute(room_id, target_dt=None, interval_sec=POLL_INTERVAL_SEC, timeout_sec=RECOVERY_WINDOW_SEC)

    if not m3u8_url:
        print('程序已結束。')
        return

    while m3u8_url:
        title = get_title_from_url(url)
        nowstr = datetime.now().strftime('%y%m%d%H%M')
        folder_name = f'{nowstr}_{sanitize_filename(title)}'
        out_dir = os.path.join(DOWNLOAD_DIR, folder_name)

        stop_reason = download_ts_files(m3u8_url, out_dir, room_id)

        if stop_reason != 'no_m3u8':
            merge_ok = merge_ts_to_mp4(out_dir, f'{folder_name}.mp4')
            if not merge_ok:
                print('合併未完成，ts 已保留，請稍後自行合併。')
            print('程序已結束。')
            return

        window_deadline = time.time() + RECOVERY_WINDOW_SEC
        next_m3u8 = {'url': None}

        def bg_poll(deadline=window_deadline, result=next_m3u8):
            while time.time() < deadline and result['url'] is None:
                found = get_live_m3u8_url(room_id)
                if found:
                    result['url'] = found
                    return
                time.sleep(POLL_INTERVAL_SEC)

        threading.Thread(target=bg_poll, daemon=True).start()

        merge_ok = merge_ts_to_mp4(out_dir, f'{folder_name}.mp4')
        if not merge_ok:
            print('合併未完成，ts 已保留，請稍後自行合併。')

        if next_m3u8['url']:
            m3u8_url = next_m3u8['url']
            print('偵測到已開台，立即開始下載。')
            continue

        remaining = int(window_deadline - time.time())
        if remaining > 0:
            m3u8_url = poll_stream_in_last_minute(room_id, target_dt=None, interval_sec=POLL_INTERVAL_SEC, timeout_sec=remaining)
        else:
            m3u8_url = None

        if not m3u8_url:
            print('程序已結束。')
            return

    print('找不到 m3u8 連結。')
    print('程序已結束。')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('程序已結束。')
