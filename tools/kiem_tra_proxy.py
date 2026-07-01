"""Tool doc lap de tu kiem tra proxy: song hay chet, va co bi chan TikTok khong.

Muc dich: cho ban TU minh xac minh ket qua ma khong can hoi lai ai. Script nay
khong phu thuoc vao server app.py dang chay - chi can .venv da cai la dung duoc.

Cach dung (chay tu thu muc goc du an):
    .venv\\Scripts\\python.exe tools\\kiem_tra_proxy.py
    .venv\\Scripts\\python.exe tools\\kiem_tra_proxy.py duong_dan_file_proxy.txt

Neu khong truyen duong dan file, script se tu doc "data/proxy_list.txt" (file
proxy da luu tu web UI). Neu file khong ton tai, script se hoi ban dan proxy
vao thang terminal (moi dong 1 proxy, dong trong de ket thuc).

Script lam 2 lop kiem tra rieng biet de ban thay ro nguyen nhan:

  Lop 1 - "CONNECT tho" (khong dung code cua app, tu socket thuan): mo tunnel
  qua proxy toi 3 dich: Google, TikTok (ten mien), va IP goc cua TikTok/Cloudflare.
  Ban tu doc duoc HTTP status va thoi gian phan hoi cua tung dich - day la bang
  chung tho, ai doc cung kiem chung duoc, khong can tin loi giai thich.

  Lop 2 - Dung dung ham test_proxy_config() trong proxy_utils.py (chinh la ham
  nut "Test" tren web dang goi) de xac nhan ket qua Lop 1 khop voi app thuc te.

Neu ca 2 lop deu cho ra: Google OK nhanh, nhung TikTok/Cloudflare 502 cung
nhanh (duoi ~1s, khong phai timeout treo lau) -> day la dau hieu proxy/nha
mang CHU DONG chan TikTok/Cloudflare, khong phai loi code hay loi mang tinh cach.
"""

import base64
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy_utils import (  # noqa: E402
    parse_proxy_text,
    proxy_display_name,
    test_proxy_config,
)

DEFAULT_PROXY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "proxy_list.txt")

# Dich thu 3: dung IP goc cua Cloudflare (khong phai domain) de kiem tra proxy
# co chan theo dai IP hay theo ten mien (SNI). Day la 1 IP Cloudflare cong khai.
CLOUDFLARE_RAW_IP = "104.16.0.1"
RAW_TARGETS = [
    ("Google (baseline)", "www.google.com", 443),
    ("TikTok (ten mien)", "www.tiktok.com", 443),
    ("Cloudflare (IP goc, khong SNI)", CLOUDFLARE_RAW_IP, 443),
]


def load_proxy_text(path_arg):
    path = path_arg or DEFAULT_PROXY_FILE
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        print(f"[OK] Doc {path}")
        return text
    print(f"[INFO] Khong thay file '{path}'.")
    print("Dan danh sach proxy vao day (moi dong 1 proxy), roi nhan Enter tren dong trong de bat dau test:")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


def raw_connect_test(host, port, username, password, target_host, target_port, timeout=8):
    """Mo tunnel CONNECT tho qua proxy HTTP, tra ve (status_line, giay, loi)."""
    t0 = time.time()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except Exception as error:
        return None, round(time.time() - t0, 2), f"Khong ket noi duoc proxy: {error}"
    try:
        headers = f"Host: {target_host}:{target_port}\r\n"
        if username:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers += f"Proxy-Authorization: Basic {token}\r\n"
        request = f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n{headers}\r\n"
        sock.sendall(request.encode())
        sock.settimeout(timeout)
        response = sock.recv(2048)
        dt = round(time.time() - t0, 2)
        first_line = response.decode(errors="replace").splitlines()[0] if response else "(khong co phan hoi)"
        return first_line, dt, None
    except Exception as error:
        return None, round(time.time() - t0, 2), str(error)
    finally:
        sock.close()


def run_raw_layer(config):
    rows = []
    for label, target_host, target_port in RAW_TARGETS:
        status, seconds, error = raw_connect_test(
            config["host"], config["port"], config.get("username"), config.get("password"),
            target_host, target_port,
        )
        if error:
            rows.append((label, f"LOI: {error}", seconds))
        else:
            rows.append((label, status, seconds))
    return rows


def format_raw_rows(rows):
    lines = []
    for label, status, seconds in rows:
        lines.append(f"      - {label:<32} -> {status}  ({seconds}s)")
    return "\n".join(lines)


def main():
    path_arg = sys.argv[1] if len(sys.argv) > 1 else None
    text = load_proxy_text(path_arg)
    configs = parse_proxy_text(text)
    if not configs:
        print("[ERROR] Khong doc duoc proxy nao. Kiem tra lai format: host:port:user:pass hoac socks5://user:pass@host:port")
        return

    print(f"\n[INFO] Bat dau kiem tra {len(configs)} proxy (2 lop: CONNECT tho + test_proxy_config chinh thuc)...\n")

    report_lines = []
    alive_count = 0
    tiktok_ok_count = 0

    def worker(index_config):
        index, config = index_config
        raw_rows = run_raw_layer(config)
        official = test_proxy_config(config, timeout=10)
        return index, config, raw_rows, official

    results = [None] * len(configs)
    with ThreadPoolExecutor(max_workers=min(len(configs), 12)) as pool:
        futures = [pool.submit(worker, item) for item in enumerate(configs)]
        for future in as_completed(futures):
            index, config, raw_rows, official = future.result()
            results[index] = (config, raw_rows, official)

    for i, (config, raw_rows, official) in enumerate(results, start=1):
        name = proxy_display_name(config)
        if official.get("ok"):
            alive_count += 1
        if official.get("tiktokOk"):
            tiktok_ok_count += 1

        verdict = "TIKTOK OK" if official.get("tiktokOk") else (
            "SONG NHUNG TIKTOK BI CHAN" if official.get("ok") else "CHET/KHONG KET NOI"
        )
        header = f"[{i}/{len(configs)}] {name} ({config['host']}:{config['port']}) -> {verdict}"
        print(header)
        print(format_raw_rows(raw_rows))
        if official.get("tiktokError"):
            print(f"      - test_proxy_config tiktokError: {official['tiktokError']}")
        if official.get("error"):
            print(f"      - test_proxy_config error: {official['error']}")
        print()

        report_lines.append(header)
        report_lines.append(format_raw_rows(raw_rows))
        report_lines.append("")

    summary = (
        f"KET QUA: {tiktok_ok_count}/{len(configs)} proxy quet duoc TikTok "
        f"({alive_count}/{len(configs)} proxy song)."
    )
    print("=" * 70)
    print(summary)
    print("=" * 70)
    print(
        "\nCach doc ket qua:\n"
        "  - Google OK nhanh (<1s) + TikTok/Cloudflare cung tra loi NHANH (<1s)\n"
        "    nhung la '502 Bad Gateway' hoac loi ngay lap tuc (khong phai timeout\n"
        "    treo 5-10s) => day la proxy/nha mang CHU DONG chan, khong phai do\n"
        "    mang yeu hay do code quet. Phai doi nha cung cap proxy khac ho tro\n"
        "    TikTok, hoac doi loai proxy (proxy quoc te / datacenter) thay vi\n"
        "    proxy 4G noi dia dang bi chan Cloudflare.\n"
        "  - Neu ca Google cung loi/timeout treo lau => proxy do thuc su chet,\n"
        "    khong lien quan gi TikTok.\n"
    )

    os.makedirs(os.path.dirname(DEFAULT_PROXY_FILE), exist_ok=True)
    report_path = os.path.join(os.path.dirname(DEFAULT_PROXY_FILE), f"proxy_check_report_{int(time.time())}.txt")
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(summary + "\n\n")
        handle.write("\n".join(report_lines))
    print(f"[OK] Da luu bao cao chi tiet vao: {report_path}")


if __name__ == "__main__":
    main()
