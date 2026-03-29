import os
import re
import time
import requests
from bs4 import BeautifulSoup
import m3u8
import subprocess
from datetime import datetime, timedelta

DOWNLOAD_DIR = 'downloads'

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
    api_url = f"https://www.showroom-live.com/api/live/streaming_url?room_id={room_id}"
    resp = requests.get(api_url)
    if resp.status_code != 200:
        return None
    data = resp.json()
    return data.get('streaming_url_list', [])

def has_hls_stream(streaming_url_list):
    return bool(streaming_url_list and any('hls' in i.get('type', '') for i in streaming_url_list))

def poll_stream_in_last_minute(room_id, target_dt=None, interval_sec=5, timeout_sec=600):
    import sys
    import ctypes

    vt_ok = False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            vt_ok = bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        vt_ok = False

    started_at = datetime.now()
    deadline = (target_dt + timedelta(seconds=timeout_sec)) if target_dt else (started_at + timedelta(seconds=timeout_sec))

    def overwrite_line(msg=''):
        if vt_ok:
            sys.stdout.write('\r\x1b[2K' + msg)
        else:
            sys.stdout.write('\r' + ' ' * 160 + '\r' + msg)
        sys.stdout.flush()

    while datetime.now() <= deadline:
        streaming_url_list = get_streaming_url(room_id)
        if has_hls_stream(streaming_url_list):
            overwrite_line('')
            print('偵測到已開台，立即開始下載。')
            return streaming_url_list

        now = datetime.now()
        remain_timeout = max(0, int((deadline - now).total_seconds()))

        if target_dt is not None:
            remain_target = int((target_dt - now).total_seconds())
            if remain_target > 0:
                msg = f'輪詢中，預計 {remain_target} 秒後開台；{interval_sec} 秒後再檢查...'
            else:
                msg = f'已到開台時間，{interval_sec} 秒後再檢查...（距離輪詢結束 {remain_timeout} 秒）'
        else:
            msg = f'未開台，{interval_sec} 秒後再檢查...（剩餘輪詢 {remain_timeout} 秒）'

        overwrite_line(msg)
        time.sleep(interval_sec)

    overwrite_line('')
    print('10 分鐘內仍未開台。')
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

def wait_until(target_dt):
    import sys
    import ctypes

    vt_ok = False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            vt_ok = bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        vt_ok = False

    def format_remaining(total_seconds):
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f'距離下載還剩 {int(hours)}時{int(minutes)}分{int(seconds)}秒'

    def overwrite_line(msg=''):
        if vt_ok:
            sys.stdout.write('\r\x1b[2K' + msg)
        else:
            sys.stdout.write('\r' + ' ' * 120 + '\r' + msg)
        sys.stdout.flush()

    while True:
        now = datetime.now()
        delta = int((target_dt - now).total_seconds())

        if delta <= 0:
            overwrite_line('已到時間，立即開始')
            sys.stdout.write('\n')
            sys.stdout.flush()
            break

        overwrite_line(format_remaining(delta))

        if delta > 60:
            time.sleep(60)
        else:
            time.sleep(delta)

def parse_m3u8_url(streaming_url_list):
    for item in streaming_url_list:
        if item.get('type') == 'hls' and 'ss.m3u8' in item.get('url', ''):
            return item['url']
    for item in streaming_url_list:
        if item.get('type') == 'hls':
            return item['url']
    return None

def download_ts_files(m3u8_url, out_dir, main_prefix, start_time):
    folder_display = os.path.basename(out_dir)
    print(f'開始下載: {folder_display}')
    os.makedirs(out_dir, exist_ok=True)
    downloaded = set()
    downloaded_count = [0]
    import threading, sys
    stop_flag = {'stop': False}
    backup_count = [0]
    error_count = [0]
    latest_seq = [None]
    first_segment = [None]
    missing_segments = set()
    last_status_len = [0]
    progress_lock = threading.Lock()
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

    def render_status(newline=False):
        status = (
            f'已下載: {downloaded_count[0]} 個片段 | '
            f'補抓: {backup_count[0]} 個片段 | '
            f'最近錯誤: {error_count[0]} 個片段'
        )
        with progress_lock:
            if newline:
                clear = ' ' * last_status_len[0]
                sys.stdout.write('\r' + clear + '\r' + status + '\n')
                last_status_len[0] = 0
            else:
                clear = ' ' * last_status_len[0]
                sys.stdout.write('\r' + clear + '\r' + status)
                last_status_len[0] = len(status)
            sys.stdout.flush()


    def main_thread():
        session = requests.Session()
        try:
            while not stop_flag['stop']:
                try:
                    m3u8_obj = m3u8.load(m3u8_url)
                    for seg in m3u8_obj.segments:
                        ts_url = seg.absolute_uri
                        ts_url_clean = ts_url.split('?')[0]
                        seq = extract_seq(ts_url_clean)
                        if seq is not None:
                            latest_seq[0] = seq
                        fname = os.path.join(out_dir, os.path.basename(ts_url_clean))

                        if os.path.exists(fname):
                            downloaded.add(ts_url)
                            downloaded_count[0] = len(downloaded)
                            continue

                        if ts_url not in downloaded:
                            try:
                                r = session.get(ts_url, timeout=5)
                                if r.status_code == 200:
                                    with open(fname, 'wb') as f:
                                        f.write(r.content)
                                    downloaded.add(ts_url)
                                    downloaded_count[0] = len(downloaded)
                                    if first_segment[0] is None:
                                        first_segment[0] = os.path.basename(ts_url_clean)
                                        write_log('FIRST', first_segment[0])
                                    if ts_url_clean in missing_segments:
                                        missing_segments.discard(ts_url_clean)
                                else:
                                    error_count[0] += 1
                                    missing_segments.add(ts_url_clean)
                                    write_log('ERROR', f'HTTP {r.status_code} | {os.path.basename(ts_url_clean)}')
                            except Exception as e:
                                error_count[0] += 1
                                missing_segments.add(ts_url_clean)
                                write_log('ERROR', f'{e} | {os.path.basename(ts_url_clean)}')
                except Exception:
                    error_count[0] += 1

                render_status(newline=False)
                time.sleep(2)
        except KeyboardInterrupt:
            stop_flag['stop'] = True
            print('\n下載中斷，開始合併...')

    def backup_thread():
        session = requests.Session()
        try:
            while not stop_flag['stop']:
                try:
                    if missing_segments:
                        for ts_url_clean in list(missing_segments)[:60]:
                            if stop_flag['stop']:
                                break
                            fname = os.path.join(out_dir, os.path.basename(ts_url_clean))
                            if os.path.exists(fname):
                                missing_segments.discard(ts_url_clean)
                                continue
                            try:
                                r = session.get(ts_url_clean, timeout=2)
                                if r.status_code == 200:
                                    with open(fname, 'wb') as f:
                                        f.write(r.content)
                                    backup_count[0] += 1
                                    missing_segments.discard(ts_url_clean)
                                    write_log('BACKUP', os.path.basename(ts_url_clean))
                                else:
                                    error_count[0] += 1
                            except Exception:
                                error_count[0] += 1

                    current_latest = latest_seq[0]
                    if current_latest is None:
                        time.sleep(1)
                        continue

                    live_obj = m3u8.load(m3u8_url)
                    if not live_obj.segments:
                        time.sleep(1)
                        continue

                    sample_clean = live_obj.segments[-1].absolute_uri.split('?')[0]
                    if not re.search(r'-(\d+)\.ts$', sample_clean):
                        time.sleep(1)
                        continue

                    window_end = max(-1, current_latest - 101)
                    for seq in range(current_latest - 1, window_end, -1):
                        if stop_flag['stop']:
                            break

                        ts_url_base_clean = re.sub(r'-(\d+)\.ts$', f'-{seq}.ts', sample_clean)
                        fname = os.path.join(out_dir, os.path.basename(ts_url_base_clean))
                        if os.path.exists(fname):
                            continue

                        try:
                            r = session.get(ts_url_base_clean, timeout=2)
                            if r.status_code == 200:
                                with open(fname, 'wb') as f:
                                    f.write(r.content)
                                backup_count[0] += 1
                                write_log('BACKUP', os.path.basename(ts_url_base_clean))
                            else:
                                error_count[0] += 1
                        except Exception:
                            error_count[0] += 1
                except Exception:
                    error_count[0] += 1

                time.sleep(1)
        except KeyboardInterrupt:
            stop_flag['stop'] = True

    t1 = threading.Thread(target=main_thread, daemon=True)
    t2 = threading.Thread(target=backup_thread, daemon=True)
    t1.start()
    t2.start()
    try:
        while t1.is_alive() or t2.is_alive():
            time.sleep(0.2)
    except KeyboardInterrupt:
        stop_flag['stop'] = True
        render_status(newline=True)
        print('\n下載中斷，開始合併...')
        return

def merge_ts_to_mp4(out_dir, out_name):
    ts_files = [f for f in os.listdir(out_dir) if f.endswith('.ts')]
    ts_files.sort(key=lambda x: int(re.search(r'-(\d+)\.ts', x).group(1)) if re.search(r'-(\d+)\.ts', x) else 0)
    list_path = os.path.join(out_dir, 'tslist.txt')
    with open(list_path, 'w', encoding='utf-8') as f:
        for ts in ts_files:
            f.write(f"file '{ts}'\n")
    mp4_path = os.path.join(out_dir, out_name)
    cmd = [
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c', 'copy', mp4_path
    ]
    print('正在轉檔... (合併 ts 為 mp4...)')
    subprocess.run(cmd)
    print(f'完成: {mp4_path}')
    for f in os.listdir(out_dir):
        if f.endswith('.ts'):
            try:
                os.remove(os.path.join(out_dir, f))
            except Exception:
                pass

def main():
    url = input('請輸入 Showroom 直播網址: ').strip()
    room_id = get_room_id_from_url(url)
    if not room_id:
        print('無法擷取 room_id')
        return
    import sys
    import msvcrt

    def choose_menu(title, options):
        selected = 0

        vt_ok = False
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
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
                else:
                    draw_menu()
            elif ch in (b'\r', b'\n'):
                return selected

    def interactive_menu():
        options = ['立刻下載', '排程下載']
        return choose_menu('> 選擇操作 (按 空格鍵 切換, Enter 確認)', options)

    sel = interactive_menu()
    schedule = (sel == 1)

    if schedule:
        sched_str = prompt_schedule()
        try:
            target_dt = datetime.strptime(sched_str, '%y%m%d%H%M')
        except Exception:
            print('時間格式錯誤')
            return
        now = datetime.now()
        if (target_dt - now).total_seconds() > 60:
            wait_until(target_dt - timedelta(minutes=1))
        else:
            print('鄰近開台時間，已開始抓取。')

        pre_live = poll_stream_in_last_minute(room_id, target_dt, interval_sec=5, timeout_sec=600)
        if pre_live:
            streaming_url_list = pre_live
        else:
            streaming_url_list = None

    title = get_title_from_url(url)
    nowstr = datetime.now().strftime('%y%m%d%H%M')
    folder_name = f'{nowstr}_{sanitize_filename(title)}'
    out_dir = os.path.join(DOWNLOAD_DIR, folder_name)

    if 'streaming_url_list' not in locals() or not has_hls_stream(streaming_url_list):
        streaming_url_list = get_streaming_url(room_id)
    if not has_hls_stream(streaming_url_list):
        print('目前未開台，執行 10 分鐘自動輪詢...')
        streaming_url_list = poll_stream_in_last_minute(room_id, target_dt=None, interval_sec=5, timeout_sec=600)

    if not has_hls_stream(streaming_url_list):
        sel_sched = choose_menu('未開台，要開始排程嗎？', ['開始排程', '結束'])
        if sel_sched == 1:
            print('結束進程')
            return
        sched_str = prompt_schedule()
        try:
            target_dt = datetime.strptime(sched_str, '%y%m%d%H%M')
        except Exception:
            print('時間格式錯誤')
            return
        now = datetime.now()
        if (target_dt - now).total_seconds() > 60:
            wait_until(target_dt - timedelta(minutes=1))
        print('進入輪詢...')
        streaming_url_list = poll_stream_in_last_minute(room_id, target_dt, interval_sec=5, timeout_sec=600)
        if not has_hls_stream(streaming_url_list):
            print('仍未開台，結束')
            return

    m3u8_url = parse_m3u8_url(streaming_url_list)
    if not m3u8_url:
        print('找不到 m3u8 連結')
        return
    m = re.match(r'(.+)_ss\.m3u8', m3u8_url)
    main_prefix = m.group(1) if m else m3u8_url.rsplit('.',1)[0]

    start_time = time.time()
    download_ts_files(m3u8_url, out_dir, main_prefix, start_time)
    merge_ts_to_mp4(out_dir, f'{folder_name}.mp4')
    return

if __name__ == '__main__':
    main()
