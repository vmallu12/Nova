import discord
from discord.ext import commands, tasks
import docker
import aiosqlite
import asyncio
import os
import random
import string
import datetime
import time
import re
import io
import json
import socket
import threading
from typing import Optional, Union

# Web Framework & SSH
from fastapi import FastAPI, Request, Form, UploadFile, File, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from jinja2 import Template
import uvicorn
import paramiko

# ----------------- CONFIGURATION -----------------
TOKEN = "YOUR_DISCORD_BOT_TOKEN_HERE"
NODE_NAME = "TEMPEST CLOUD"

# Web Panel Settings
DOMAIN_NAME = "kvm.kingrules.bond"
PANEL_PORT = 2345
PANEL_URL = f"http://{DOMAIN_NAME}"

# Persistent Database Isolation
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "tempest_vms.db")

CLIENT_ROLE_ID = 1545501965562159116
MAIN_OWNER_ID = 1447083500720230401
DEFAULT_ADMIN_IDS = [MAIN_OWNER_ID]
ALERT_CHANNEL_ID = 1519219646358622259
MINING_ALERT_PING_ID = 1447083500720230401

MINER_SIGNATURES = [
    "xmrig", "minerd", "cpuminer", "ethminer", "stratum",
    "nanominer", "nbminer", "phoenixminer", "t-rex", "gminer",
    "wildrig", "teamredminer", "lolminer", "ccminer"
]

# ----------------- CUSTOM EMOJIS -----------------
E_ONLINE = "<a:Online:1519557436854370334>"
E_OFFLINE = "<a:offline:1519557662977822941>"
E_LOADING = "<a:loading_icon:1520088258027982858>"
E_LIGHTNING = "<a:65023lightning:1519762787579072593>"
E_THUNDER = "<a:thunder:1519558414353698927>"
E_FIRE = "<a:fire:1520089278225453186>"
E_GEAR = "<a:PurpleGear:1545024403216269395>"
E_YES = "<a:yes:1519555946312106024>"
E_CHECK = "<a:greencheck:1519588992767496193>"
E_NO = "<a:vote_no:1519763086570164246>"
E_WARN = "<a:Warning:1519588395620499648>"
E_DOWN = "<a:DOWN:1520088811399413850>"
E_ARROW = "<a:arrow:1519556344951341156>"
E_ARROW_DOUBLE = "<a:arrow:1519556677173510325>"
E_LEFT_ARROW = "<a:leftarrow:1519585449713074226>"
E_STAR = "<a:star:1519557024801751051>"
E_BLACK_WING = "<a:blackWing1:1545024935016144947>"
E_KING_CROWN = "<a:King_crown:1519766073560403990>"
E_MOD = "<:ModeratorRoleIcon:1545029625405505586>"
E_BOT_TAG = "<:bot_tag:1519559016013889647>"
E_INFO = "<:Information:1545028101501747230>"
E_GG = "<a:GG:1519587770425933824>"

OWNER_REACTIONS = [
    "arrow:1519556677173510325",
    "1_crown:1519585072687222936",
    "leftarrow:1519585449713074226"
]

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
docker_client = docker.from_env()
app = FastAPI(title="Tempest Cloud Panel")

# ----------------- DATABASE UTILITIES -----------------
def get_db():
    return aiosqlite.connect(DB_PATH, timeout=30.0)

async def init_db():
    async with get_db() as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout=30000;")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS vms (
                vm_id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER,
                container_name TEXT,
                container_id TEXT,
                vnc_port INTEGER,
                ssh_port INTEGER,
                ram TEXT,
                cpu TEXT,
                disk TEXT,
                root_pass TEXT,
                vnc_pass TEXT,
                created_at TEXT,
                expires_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS panel_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT UNIQUE,
                password TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            )
        """)
        for admin_id in DEFAULT_ADMIN_IDS:
            await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (admin_id,))
        await db.commit()

async def is_admin(user_id: int) -> bool:
    if user_id == MAIN_OWNER_ID:
        return True
    async with get_db() as db:
        async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None

def gen_password(length=12):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def get_free_port(base_start: int):
    port = base_start
    while port < 65000:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
        port += 1
    raise RuntimeError("No free network ports available on hypervisor.")

def parse_time_duration(args: list) -> Optional[datetime.timedelta]:
    raw = " ".join(args).lower().strip()
    raw = raw.replace("house", "hours").replace("hour", "hours").replace("hr", "hours").replace("h", " hours ")
    raw = raw.replace("days", "days").replace("day", "days").replace("d", " days ")
    raw = raw.replace("months", "months").replace("month", "months").replace("m", " months ")

    parts = raw.split()
    if not parts:
        return None

    try:
        if len(parts) == 1 and parts[0].isdigit():
            return datetime.timedelta(days=int(parts[0]))
        
        total_td = datetime.timedelta()
        idx = 0
        while idx < len(parts):
            if parts[idx].isdigit():
                val = int(parts[idx])
                if idx + 1 < len(parts):
                    unit = parts[idx + 1]
                    if "hour" in unit:
                        total_td += datetime.timedelta(hours=val)
                    elif "day" in unit:
                        total_td += datetime.timedelta(days=val)
                    elif "month" in unit:
                        total_td += datetime.timedelta(days=val * 28)
                    idx += 1
                else:
                    total_td += datetime.timedelta(days=val)
            idx += 1
        return total_td if total_td.total_seconds() > 0 else None
    except Exception:
        return None

async def grant_client_role(user_id: int):
    for guild in bot.guilds:
        member = guild.get_member(user_id)
        if member:
            role = guild.get_role(CLIENT_ROLE_ID)
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Assigned active Virtual Machine")
                except Exception:
                    pass

async def revoke_client_role_if_empty(user_id: int):
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM vms WHERE owner_id = ?", (user_id,)) as cur:
            count = (await cur.fetchone())[0]

    if count == 0:
        for guild in bot.guilds:
            member = guild.get_member(user_id)
            if member:
                role = guild.get_role(CLIENT_ROLE_ID)
                if role and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="All VMs terminated")
                    except Exception:
                        pass

# ----------------- DOCKER & CONTAINER OPS -----------------
def launch_vm_container(name: str, ram: str, cpu: str, disk: str, vnc_port: int, ssh_port: int, vnc_pass: str, root_pass: str):
    try:
        old = docker_client.containers.get(name)
        old.remove(force=True)
    except docker.errors.NotFound:
        pass

    try:
        ram_val = str(int(float(ram) * 1024)) if float(ram) < 256 else str(ram)
    except ValueError:
        ram_val = "2048"

    try:
        disk_val = str(int(float(disk) * 1024)) if float(disk) < 1000 else str(disk)
    except ValueError:
        disk_val = "32000"

    container = docker_client.containers.run(
        image="hopingboyz/atyro-ubuntu24",
        name=name,
        detach=True,
        privileged=True,
        devices=["/dev/kvm:/dev/kvm"],
        ports={
            "6080/tcp": vnc_port,
            "2222/tcp": ssh_port
        },
        environment={
            "RAM": ram_val,
            "CPU": str(cpu),
            "DISK": disk_val,
            "VNC_PASS": vnc_pass,
            "ROOT_PASS": root_pass
        },
        volumes={
            f"{name}-data": {"bind": "/vm", "mode": "rw"}
        },
        restart_policy={"Name": "unless-stopped"}
    )
    return container.id

def get_container_stats(container_name: str):
    try:
        container = docker_client.containers.get(container_name)
        status = container.status == "running"
        if not status:
            return {"online": False, "ram_used": 0, "ram_total": 1, "ram_str": "0 MB", "cpu_pct": 0.0, "disk_str": "Offline"}
        
        stats = container.stats(stream=False)
        mem_usage = stats['memory_stats'].get('usage', 0) / (1024 * 1024)
        mem_limit = stats['memory_stats'].get('limit', 1) / (1024 * 1024)
        
        cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage']['total_usage']
        system_delta = stats['cpu_stats'].get('system_cpu_usage', 0) - stats['precpu_stats'].get('system_cpu_usage', 0)
        cpu_pct = 0.0
        if system_delta > 0 and cpu_delta > 0:
            cpu_pct = (cpu_delta / system_delta) * len(stats['cpu_stats']['cpu_usage'].get('percpu_usage', [1])) * 100.0

        disk_res = container.exec_run("df -h /vm")
        disk_str = "Mounted"
        if disk_res.exit_code == 0:
            lines = disk_res.output.decode().splitlines()
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 5:
                    disk_str = f"{parts[2]} / {parts[1]} ({parts[4]})"

        return {
            "online": True,
            "ram_used": round(mem_usage, 1),
            "ram_total": round(mem_limit, 1),
            "ram_str": f"{mem_usage:.1f} MB",
            "cpu_pct": round(cpu_pct, 1),
            "disk_str": disk_str
        }
    except Exception:
        return {"online": False, "ram_used": 0, "ram_total": 1, "ram_str": "0 MB", "cpu_pct": 0.0, "disk_str": "Offline"}

# ----------------- RELIABLE SSHX TERMINAL ENGINE -----------------
async def get_or_create_sshx_link(container_name: str) -> Optional[str]:
    loop = asyncio.get_running_loop()

    def sync_sshx():
        try:
            container = docker_client.containers.get(container_name)
            if container.status != "running":
                return None

            # Check if active log exists with valid session
            res = container.exec_run("cat /tmp/sshx.log 2>/dev/null")
            output = res.output.decode("utf-8", errors="ignore")
            match = re.search(r"https://sshx\.io/s/[A-Za-z0-9#_-]+", output)
            if match:
                # Check process is actually running
                ps = container.exec_run("pgrep -x sshx")
                if ps.exit_code == 0:
                    return match.group(0)

            # Install sshx if not present
            container.exec_run("sh -c 'if ! command -v sshx >/dev/null 2>&1; then curl -sSf https://sshx.io/get | sh 2>/dev/null; fi'")

            # Launch daemon in background
            container.exec_run("sh -c 'nohup sshx > /tmp/sshx.log 2>&1 &'")

            # Poll for link creation
            for _ in range(12):
                time.sleep(0.4)
                res = container.exec_run("cat /tmp/sshx.log")
                out = res.output.decode("utf-8", errors="ignore")
                match = re.search(r"https://sshx\.io/s/[A-Za-z0-9#_-]+", out)
                if match:
                    return match.group(0)

            return None
        except Exception as e:
            print(f"[SSHX Worker Error]: {e}")
            return None

    return await loop.run_in_executor(None, sync_sshx)

async def verify_vm_access(user_id_str: Optional[str], vm_id: int) -> tuple[bool, Optional[tuple]]:
    if not user_id_str or not str(user_id_str).isdigit():
        return False, None
    uid = int(user_id_str)
    admin = await is_admin(uid)
    async with get_db() as db:
        async with db.execute("SELECT owner_id, ssh_port, root_pass, container_name FROM vms WHERE vm_id = ?", (vm_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return False, None
    if row[0] == uid or admin:
        return True, row
    return False, None

# ----------------- WEB PANEL TEMPLATES -----------------
HTML_LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tempest Cloud • Login</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        :root { --bg: #0b0e14; --card: #151921; --accent: #7952ff; --text: #f0f2f5; --text-sec: #8c93a4; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Space Grotesk', sans-serif; }
        body { background: var(--bg); color: var(--text); display: flex; align-items: center; justify-content: center; min-height: 100vh; overflow: hidden; }
        .ambient-glow { position: absolute; width: 350px; height: 350px; background: radial-gradient(circle, rgba(121,82,255,0.2) 0%, rgba(0,0,0,0) 70%); filter: blur(60px); z-index: 1; animation: pulse 6s infinite alternate; }
        @keyframes pulse { 0% { transform: scale(1); opacity: 0.6; } 100% { transform: scale(1.3); opacity: 1; } }
        .card { position: relative; z-index: 2; width: 100%; max-width: 400px; background: rgba(21, 25, 33, 0.85); backdrop-filter: blur(16px); border: 1px solid rgba(255,255,255,0.08); border-radius: 20px; padding: 35px; box-shadow: 0 20px 40px rgba(0,0,0,0.6); }
        h1 { font-size: 24px; font-weight: 700; margin-bottom: 6px; }
        p { color: var(--text-sec); font-size: 14px; margin-bottom: 25px; }
        .input-group { margin-bottom: 18px; }
        label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 8px; color: var(--text-sec); }
        input { width: 100%; background: #0f131a; border: 1px solid rgba(255,255,255,0.1); padding: 14px; border-radius: 12px; color: #fff; font-size: 15px; outline: none; transition: 0.25s; }
        input:focus { border-color: var(--accent); box-shadow: 0 0 12px rgba(121,82,255,0.3); }
        button { width: 100%; padding: 14px; background: linear-gradient(135deg, #7952ff, #5124e5); color: #fff; border: none; border-radius: 12px; font-size: 16px; font-weight: 600; cursor: pointer; transition: 0.25s; margin-top: 10px; }
        button:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(121,82,255,0.4); }
        .err { background: rgba(237,66,69,0.15); border: 1px solid #ed4245; color: #ff8587; padding: 10px; border-radius: 10px; font-size: 13px; margin-bottom: 15px; }
    </style>
</head>
<body>
    <div class="ambient-glow"></div>
    <div class="card">
        <h1>Welcome Back</h1>
        <p>Log in to access your cloud virtual machines</p>
        {% if error %}<div class="err">{{ error }}</div>{% endif %}
        <form action="/login" method="post">
            <div class="input-group"><label>Client Identifier</label><input type="text" name="username" required autocomplete="off"></div>
            <div class="input-group"><label>Passkey</label><input type="password" name="password" required></div>
            <button type="submit">Unlock Dashboard</button>
        </form>
    </div>
</body>
</html>
"""

HTML_DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ node_name }} • Virtual Machine Hub</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root { --bg: #090c10; --sidebar: #0f131a; --card: #151921; --accent: #7952ff; --green: #2ecc71; --red: #e74c3c; --text: #f0f2f5; --sec: #8c93a4; --border: rgba(255,255,255,0.06); }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Space Grotesk', sans-serif; }
        body { background: var(--bg); color: var(--text); min-height: 100vh; display: flex; flex-direction: column; }
        header { background: var(--sidebar); border-bottom: 1px solid var(--border); padding: 18px 30px; display: flex; align-items: center; justify-content: space-between; }
        .logo { font-size: 20px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
        .logo-dot { width: 12px; height: 12px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 10px var(--accent); }
        .logout-btn { color: var(--sec); text-decoration: none; font-size: 14px; font-weight: 600; padding: 8px 16px; border: 1px solid var(--border); border-radius: 10px; transition: 0.2s; }
        .logout-btn:hover { background: rgba(255,255,255,0.05); color: #fff; }
        main { flex: 1; max-width: 1300px; margin: 0 auto; width: 100%; padding: 30px 20px; }
        .vm-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .vm-card { background: var(--card); border: 1px solid var(--border); border-radius: 18px; padding: 22px; cursor: pointer; transition: 0.25s; }
        .vm-card:hover, .vm-card.active { border-color: var(--accent); box-shadow: 0 10px 30px rgba(121,82,255,0.15); transform: translateY(-2px); }
        .vm-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
        .vm-title { font-size: 17px; font-weight: 700; }
        .badge { padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
        .badge.online { background: rgba(46,204,113,0.15); color: var(--green); }
        .vm-specs { font-size: 13px; color: var(--sec); display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 10px; }
        .panel-view { background: var(--card); border: 1px solid var(--border); border-radius: 20px; padding: 25px; }
        .tabs { display: flex; gap: 10px; border-bottom: 1px solid var(--border); padding-bottom: 15px; margin-bottom: 20px; flex-wrap: wrap; }
        .tab-btn { background: transparent; border: none; color: var(--sec); font-size: 15px; font-weight: 600; padding: 10px 18px; border-radius: 10px; cursor: pointer; transition: 0.2s; }
        .tab-btn.active { background: var(--accent); color: #fff; }
        .charts-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin-bottom: 20px; }
        .chart-box { background: rgba(0,0,0,0.25); border: 1px solid var(--border); border-radius: 14px; padding: 18px; }
        .chart-box h4 { font-size: 14px; color: var(--sec); margin-bottom: 10px; }
        #terminal-frame { width: 100%; height: 500px; border-radius: 12px; border: 1px solid var(--border); background: #000; }
        .file-manager { background: rgba(0,0,0,0.25); border: 1px solid var(--border); border-radius: 14px; padding: 20px; }
        .file-actions { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; gap: 10px; flex-wrap: wrap; }
        .file-list { width: 100%; border-collapse: collapse; font-family: 'JetBrains Mono', monospace; font-size: 14px; }
        .file-list th, .file-list td { text-align: left; padding: 12px; border-bottom: 1px solid var(--border); }
        .file-list th { color: var(--sec); font-size: 12px; text-transform: uppercase; }
        .file-delete-btn { color: var(--red); background: transparent; border: none; cursor: pointer; font-size: 13px; }
        .file-delete-btn:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <header>
        <div class="logo"><span class="logo-dot"></span> {{ node_name }} Dashboard</div>
        <a href="/logout" class="logout-btn">Sign Out</a>
    </header>
    <main>
        <div class="vm-grid">
            {% for vm in vms %}
            <div class="vm-card {% if loop.first %}active{% endif %}" onclick="selectVM('{{ vm.vm_id }}', this)">
                <div class="vm-header">
                    <div class="vm-title">VM #{{ vm.vm_id }}</div>
                    <span class="badge online">Ubuntu 24 KVM</span>
                </div>
                <div class="vm-specs">
                    <div><b>CPU:</b> {{ vm.cpu }} vCPU</div>
                    <div><b>RAM:</b> {{ vm.ram }} GB</div>
                    <div><b>Storage:</b> {{ vm.disk }} GB</div>
                    <div><b>Expires:</b> {{ vm.expires_at[:10] }}</div>
                </div>
            </div>
            {% endfor %}
        </div>

        <div id="vm-panel" class="panel-view">
            <div class="tabs">
                <button class="tab-btn active" onclick="switchTab('metrics')">Performance Metrics</button>
                <button class="tab-btn" onclick="switchTab('terminal')">Interactive Terminal</button>
                <button class="tab-btn" onclick="switchTab('files')">SFTP File Manager</button>
            </div>

            <!-- Tab 1: Metrics -->
            <div id="tab-metrics" class="tab-content">
                <div class="charts-row">
                    <div class="chart-box">
                        <h4>Real-Time CPU Usage (%)</h4>
                        <canvas id="cpuChart" height="150"></canvas>
                    </div>
                    <div class="chart-box">
                        <h4>RAM Consumption (MB)</h4>
                        <canvas id="ramChart" height="150"></canvas>
                    </div>
                </div>
            </div>

            <!-- Tab 2: SSHX Terminal -->
            <div id="tab-terminal" class="tab-content" style="display:none;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <span style="font-size:14px; color:var(--sec);">Encrypted Relay: <b id="active-terminal-title" style="color:var(--accent);">VM #{{ vms[0].vm_id if vms else '' }}</b></span>
                    <div style="display:flex; gap:10px;">
                        <button class="tab-btn" style="background:#222733; color:#fff; padding:6px 12px; font-size:12px;" onclick="renderTerminalForVM(currentVmId)">Reload Terminal</button>
                        <a id="external-link" href="#" target="_blank" class="tab-btn" style="background:var(--accent); color:#fff; padding:6px 12px; font-size:12px; text-decoration:none;">Open in New Tab ↗</a>
                    </div>
                </div>
                <div id="loading-terminal" style="display:none; text-align:center; padding: 40px; color:var(--sec);">
                    Initializing secure terminal session...
                </div>
                <iframe id="terminal-frame" src="about:blank" allow="clipboard-read; clipboard-write"></iframe>
            </div>

            <!-- Tab 3: File Manager -->
            <div id="tab-files" class="tab-content" style="display:none;">
                <div class="file-manager">
                    <div class="file-actions">
                        <div><b>Directory:</b> <span id="current-dir" style="font-family:'JetBrains Mono'; color:var(--accent);">/root</span></div>
                        <form id="upload-form" onsubmit="uploadFile(event)">
                            <input type="file" id="file-input" required style="display:none" onchange="document.getElementById('upload-btn').click()">
                            <button type="button" class="tab-btn" style="background:#222733; color:#fff" onclick="document.getElementById('file-input').click()">Browse & Upload</button>
                            <button type="submit" id="upload-btn" style="display:none"></button>
                        </form>
                    </div>
                    <table class="file-list">
                        <thead><tr><th>Name</th><th>Size</th><th>Permissions</th><th>Actions</th></tr></thead>
                        <tbody id="file-table-body"><tr><td colspan="4" style="text-align:center;color:var(--sec);">Loading contents...</td></tr></tbody>
                    </table>
                </div>
            </div>
        </div>
    </main>

    <script>
        let currentVmId = "{{ vms[0].vm_id if vms else '' }}";
        let cpuChart = null, ramChart = null;

        function initCharts() {
            const ctxCpu = document.getElementById('cpuChart').getContext('2d');
            const ctxRam = document.getElementById('ramChart').getContext('2d');
            const opts = { responsive: true, animation: false, scales: { x: { display: false }, y: { beginAtZero: true } } };
            
            cpuChart = new Chart(ctxCpu, {
                type: 'line',
                data: { labels: Array(15).fill(''), datasets: [{ data: Array(15).fill(0), borderColor: '#7952ff', tension: 0.3, fill: true, backgroundColor: 'rgba(121,82,255,0.08)' }] },
                options: opts
            });
            ramChart = new Chart(ctxRam, {
                type: 'line',
                data: { labels: Array(15).fill(''), datasets: [{ data: Array(15).fill(0), borderColor: '#2ecc71', tension: 0.3, fill: true, backgroundColor: 'rgba(46,204,113,0.08)' }] },
                options: opts
            });
        }

        async function pollTelemetry() {
            if(!currentVmId) return;
            try {
                const res = await fetch(`/api/vm/${currentVmId}/stats`);
                if (!res.ok) return;
                const data = await res.json();
                
                cpuChart.data.datasets[0].data.shift();
                cpuChart.data.datasets[0].data.push(data.cpu_pct);
                cpuChart.update();

                ramChart.data.datasets[0].data.shift();
                ramChart.data.datasets[0].data.push(data.ram_used);
                ramChart.update();
            } catch(e) {}
        }
        setInterval(pollTelemetry, 2500);

        function selectVM(vmId, el) {
            document.querySelectorAll('.vm-card').forEach(c => c.classList.remove('active'));
            el.classList.add('active');
            currentVmId = vmId;
            const titleEl = document.getElementById('active-terminal-title');
            if (titleEl) titleEl.innerText = `VM #${vmId}`;
            
            if (document.getElementById('tab-terminal').style.display !== 'none') {
                renderTerminalForVM(currentVmId);
            } else if (document.getElementById('tab-files').style.display !== 'none') {
                loadFiles();
            }
        }

        function switchTab(tabName) {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');
            
            event.target.classList.add('active');
            document.getElementById(`tab-${tabName}`).style.display = 'block';

            if(tabName === 'terminal') {
                renderTerminalForVM(currentVmId);
            } else if(tabName === 'files') {
                loadFiles();
            }
        }

        async function renderTerminalForVM(vmId) {
            const frame = document.getElementById('terminal-frame');
            const loading = document.getElementById('loading-terminal');
            const extLink = document.getElementById('external-link');
            
            frame.style.display = 'none';
            loading.style.display = 'block';
            frame.src = 'about:blank';

            try {
                const res = await fetch(`/api/vm/${vmId}/terminal-url`);
                const data = await res.json();
                
                if (data.url) {
                    frame.src = data.url;
                    extLink.href = data.url;
                    frame.onload = () => {
                        loading.style.display = 'none';
                        frame.style.display = 'block';
                    };
                } else {
                    loading.innerText = 'Failed to load console: ' + (data.error || 'Unknown error');
                }
            } catch(e) {
                loading.innerText = 'Error connecting to server terminal agent.';
            }
        }

        async function loadFiles() {
            const res = await fetch(`/api/vm/${currentVmId}/files`);
            if(!res.ok) return;
            const files = await res.json();
            const tbody = document.getElementById('file-table-body');
            tbody.innerHTML = '';

            if(!files || files.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--sec);">Directory is empty</td></tr>';
                return;
            }

            files.forEach(f => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${f.is_dir ? '📁' : '📄'} ${f.filename}</td>
                    <td>${f.size}</td>
                    <td>${f.permissions}</td>
                    <td><button class="file-delete-btn" onclick="deleteFile('${f.filename}')">Remove</button></td>
                `;
                tbody.appendChild(tr);
            });
        }

        async function deleteFile(filename) {
            if(!confirm(`Delete "${filename}" permanently?`)) return;
            const res = await fetch(`/api/vm/${currentVmId}/files?filename=${encodeURIComponent(filename)}`, { method: 'DELETE' });
            loadFiles();
        }

        async function uploadFile(e) {
            e.preventDefault();
            const fileInput = document.getElementById('file-input');
            if(!fileInput.files[0]) return;

            const formData = new FormData();
            formData.append('file', fileInput.files[0]);

            await fetch(`/api/vm/${currentVmId}/upload`, { method: 'POST', body: formData });
            fileInput.value = '';
            loadFiles();
        }

        window.onload = () => { initCharts(); };
    </script>
</body>
</html>
"""

# ----------------- FASTAPI ROUTES -----------------
@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    t = Template(HTML_LOGIN_TEMPLATE)
    return HTMLResponse(t.render(error=None))

@app.post("/login")
async def handle_login(request: Request, username: str = Form(...), password: str = Form(...)):
    try:
        async with get_db() as db:
            async with db.execute("SELECT user_id FROM panel_users WHERE username = ? AND password = ?", (username, password)) as cur:
                row = await cur.fetchone()
        
        if not row:
            t = Template(HTML_LOGIN_TEMPLATE)
            return HTMLResponse(t.render(error="Invalid Client Identifier or Passkey."))
        
        response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
        response.set_cookie("auth_session", str(row[0]), max_age=86400, httponly=True)
        return response
    except Exception as e:
        t = Template(HTML_LOGIN_TEMPLATE)
        return HTMLResponse(t.render(error=f"Database Busy/Error: {str(e)}"))

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("auth_session")
    return response

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    user_id = request.cookies.get("auth_session")
    if not user_id:
        return RedirectResponse(url="/")

    uid = int(user_id)
    admin = await is_admin(uid)

    try:
        async with get_db() as db:
            if admin:
                query = "SELECT vm_id, ram, cpu, disk, expires_at FROM vms"
                params = ()
            else:
                query = "SELECT vm_id, ram, cpu, disk, expires_at FROM vms WHERE owner_id = ?"
                params = (uid,)

            async with db.execute(query, params) as cur:
                vms = [{"vm_id": r[0], "ram": r[1], "cpu": r[2], "disk": r[3], "expires_at": r[4]} for r in await cur.fetchall()]

        if not vms:
            return HTMLResponse("<body style='background:#0b0e14;color:#fff;font-family:sans-serif;padding:50px;'><h2>No active virtual machines found under your account.</h2><br><a style='color:#7952ff' href='/logout'>Sign Out</a></body>")

        t = Template(HTML_DASHBOARD_TEMPLATE)
        return HTMLResponse(t.render(node_name=NODE_NAME, vms=vms))
    except Exception as e:
        return HTMLResponse(f"<h3>Database Error: {str(e)}</h3><br><a href='/logout'>Retry Login</a>")

@app.get("/api/vm/{vm_id}/stats")
async def api_vm_stats(request: Request, vm_id: int):
    allowed, data = await verify_vm_access(request.cookies.get("auth_session"), vm_id)
    if not allowed:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    return get_container_stats(data[3])

@app.get("/api/vm/{vm_id}/terminal-url")
async def api_terminal_url(request: Request, vm_id: int):
    allowed, data = await verify_vm_access(request.cookies.get("auth_session"), vm_id)
    if not allowed:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    
    url = await get_or_create_sshx_link(data[3])
    if url:
        return JSONResponse({"url": url})
    return JSONResponse({"error": "Unable to spawn secure terminal relay. Ensure VM is running."}, status_code=500)

@app.get("/api/vm/{vm_id}/files")
async def list_files(request: Request, vm_id: int):
    allowed, data = await verify_vm_access(request.cookies.get("auth_session"), vm_id)
    if not allowed:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)

    owner_id, ssh_port, root_pass, c_name = data
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect("127.0.0.1", port=int(ssh_port), username="root", password=root_pass, timeout=3)
        sftp = ssh.open_sftp()
        file_list = []
        for attr in sftp.listdir_attr("/root"):
            file_list.append({
                "filename": attr.filename,
                "size": f"{round(attr.st_size / 1024, 1)} KB" if attr.st_size > 0 else "0 KB",
                "permissions": oct(attr.st_mode)[-3:],
                "is_dir": bool(attr.st_mode & 0o040000)
            })
        sftp.close()
        ssh.close()
        return JSONResponse(file_list)
    except Exception:
        return JSONResponse([], status_code=200)

@app.delete("/api/vm/{vm_id}/files")
async def delete_file(request: Request, vm_id: int, filename: str):
    allowed, data = await verify_vm_access(request.cookies.get("auth_session"), vm_id)
    if not allowed:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)

    owner_id, ssh_port, root_pass, c_name = data
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect("127.0.0.1", port=int(ssh_port), username="root", password=root_pass, timeout=3)
        sftp = ssh.open_sftp()
        path = f"/root/{os.path.basename(filename)}"
        try:
            sftp.remove(path)
        except IOError:
            sftp.rmdir(path)
        sftp.close()
        ssh.close()
        return JSONResponse({"status": "deleted"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/vm/{vm_id}/upload")
async def upload_file(request: Request, vm_id: int, file: UploadFile = File(...)):
    allowed, data = await verify_vm_access(request.cookies.get("auth_session"), vm_id)
    if not allowed:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)

    owner_id, ssh_port, root_pass, c_name = data
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect("127.0.0.1", port=int(ssh_port), username="root", password=root_pass, timeout=3)
        sftp = ssh.open_sftp()
        remote_path = f"/root/{os.path.basename(file.filename)}"
        contents = await file.read()
        sftp.putfo(io.BytesIO(contents), remote_path)
        sftp.close()
        ssh.close()
        return JSONResponse({"status": "uploaded"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ----------------- DISCORD INTERFACE & VIEW CONTROLS -----------------
class VMControlView(discord.ui.View):
    def __init__(self, vm_id: int, owner_id: int, container_name: str):
        super().__init__(timeout=None)
        self.vm_id = vm_id
        self.owner_id = owner_id
        self.container_name = container_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id or await is_admin(interaction.user.id):
            return True
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"{E_NO} Access Restricted",
                description=f"{E_WARN} You are not authorized to manage this machine slice.",
                color=0xED4245
            ),
            ephemeral=True
        )
        return False

    @discord.ui.button(label="Launch Web Console", style=discord.ButtonStyle.primary, emoji="⚡")
    async def terminal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        url = await get_or_create_sshx_link(self.container_name)
        if url:
            embed = discord.Embed(
                title=f"{E_LIGHTNING} Encrypted Terminal Session Active",
                description=f"{E_STAR} **[Click Here to Open Console in Browser]({url})**\n\nDirect Link: `{url}`",
                color=0x7952FF
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(f"{E_NO} Terminal relay service timed out. Check container status.", ephemeral=True)

    @discord.ui.button(label="Reinstall OS", style=discord.ButtonStyle.danger, emoji="🔄")
    async def reinstall_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        loading = discord.Embed(
            title=f"{E_LOADING} {NODE_NAME} • Processing Reinstall",
            description=f"{E_ARROW} Rebuilding root image for `{self.container_name}`...",
            color=0xFEE75C
        )
        await interaction.response.send_message(embed=loading, ephemeral=True)

        async with get_db() as db:
            async with db.execute("SELECT ram, cpu, disk, vnc_port, ssh_port, vnc_pass, root_pass FROM vms WHERE vm_id = ?", (self.vm_id,)) as cur:
                row = await cur.fetchone()

        if not row:
            return await interaction.edit_original_response(content=f"{E_NO} Database record not found.")

        ram, cpu, disk, vnc_port, ssh_port, vnc_pass, root_pass = row
        loop = asyncio.get_running_loop()
        try:
            new_id = await loop.run_in_executor(
                None, launch_vm_container, self.container_name, ram, cpu, disk, vnc_port, ssh_port, vnc_pass, root_pass
            )
            async with get_db() as db:
                await db.execute("UPDATE vms SET container_id = ? WHERE vm_id = ?", (new_id, self.vm_id))
                await db.commit()

            success = discord.Embed(
                title=f"{E_CHECK} Operating System Reinstalled",
                description=f"{E_YES} **VM #{self.vm_id} cleanly re-imaged!**\nUse `!terminal {self.vm_id}` or visit the dashboard to access.",
                color=0x57F287
            )
            await interaction.edit_original_response(embed=success)
        except Exception as e:
            await interaction.edit_original_response(content=f"{E_NO} Reinstall failed: `{str(e)}`")

# ----------------- DISCORD COMMANDS -----------------
@bot.event
async def on_ready():
    await init_db()
    expiry_check_loop.start()
    anti_mining_monitor.start()
    print(f"Nova Cloud online on {NODE_NAME} • Web Panel listening on port {PANEL_PORT}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.author.id == MAIN_OWNER_ID:
        for emoji_str in OWNER_REACTIONS:
            try:
                await message.add_reaction(emoji_str)
            except Exception:
                pass
    await bot.process_commands(message)

# Provision Command
@bot.command(name="vm")
async def create_vm(ctx, ram: str, cpu: str, disk: str, user: discord.User, days: int = 28):
    if not await is_admin(ctx.author.id):
        return await ctx.reply(embed=discord.Embed(title=f"{E_NO} Unauthorized", description=f"{E_WARN} Admin clearance required.", color=0xED4245))

    status_msg = await ctx.reply(embed=discord.Embed(title=f"{E_LOADING} Initializing Virtual Machine", description=f"{E_ARROW} Provisioning `{cpu} vCPU` | `{ram} GB RAM` | `{disk} GB NVMe` for {user.mention}...", color=0xFEE75C))

    try:
        vnc_port = get_free_port(6080)
        ssh_port = get_free_port(2026)
        root_pass = gen_password(12)
        vnc_pass = gen_password(8)
        created_at = datetime.datetime.now(datetime.timezone.utc)
        expires_at = (created_at + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S UTC")

        panel_user = f"user_{user.id}"
        panel_pass = gen_password(10)

        async with get_db() as db:
            cur = await db.execute("""
                INSERT INTO vms (owner_id, container_name, container_id, vnc_port, ssh_port, ram, cpu, disk, root_pass, vnc_pass, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user.id, "pending", "pending", vnc_port, ssh_port, ram, cpu, disk, root_pass, vnc_pass, created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), expires_at))
            vm_id = cur.lastrowid

            await db.execute("INSERT OR REPLACE INTO panel_users (user_id, username, password) VALUES (?, ?, ?)", (user.id, panel_user, panel_pass))
            await db.commit()

        c_name = f"atyro-vm-{user.id}-{vm_id}"
        loop = asyncio.get_running_loop()
        cid = await loop.run_in_executor(None, launch_vm_container, c_name, ram, cpu, disk, vnc_port, ssh_port, vnc_pass, root_pass)

        async with get_db() as db:
            await db.execute("UPDATE vms SET container_name = ?, container_id = ? WHERE vm_id = ?", (c_name, cid, vm_id))
            await db.commit()

        await grant_client_role(user.id)

        dm_embed = discord.Embed(
            title=f"{E_KING_CROWN} {NODE_NAME} • Cloud Console Access",
            description=f"{E_STAR} **Your dedicated virtual slice (`VM #{vm_id}`) has been provisioned!**",
            color=0x7952FF,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        dm_embed.add_field(
            name="🌐 Web Management Console",
            value=f"{E_ARROW} **Dashboard:** [Open Web Dashboard]({PANEL_URL})\n{E_ARROW} **Username:** `{panel_user}`\n{E_ARROW} **Password:** `{panel_pass}`",
            inline=False
        )
        dm_embed.add_field(
            name="🔑 Direct SSH & Guest Info",
            value=f"{E_ARROW} `ssh root@{DOMAIN_NAME} -p {ssh_port}`\n{E_ARROW} **Root Password:** `{root_pass}`\n{E_ARROW} **VNC Port:** `{vnc_port}`",
            inline=False
        )
        dm_embed.add_field(
            name="⚙️ Hardware Specifications",
            value=f"{E_ARROW_DOUBLE} **Processor:** `{cpu} vCPU Core(s)`\n{E_ARROW_DOUBLE} **Memory:** `{ram} GB Dedicated DDR5`\n{E_ARROW_DOUBLE} **Storage:** `{disk} GB NVMe PCIe 5.0`\n{E_ARROW_DOUBLE} **Term:** `{expires_at}` ({days} Days)",
            inline=False
        )
        dm_embed.set_footer(text=f"{NODE_NAME} • Private Vault", icon_url=bot.user.display_avatar.url)

        dm_delivered = True
        try:
            await user.send(embed=dm_embed)
        except Exception:
            dm_delivered = False

        embed = discord.Embed(
            title=f"{E_CHECK} {NODE_NAME} • Virtual Machine Provisioned",
            description=f"{E_YES} **VM #{vm_id}** is online and assigned to {user.mention}.",
            color=0x57F287,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(
            name="📋 Hardware Specifications",
            value=f"{E_ARROW} **Instance ID:** `VM #{vm_id}` (`{cid[:12]}`)\n{E_ARROW} **Resources:** `{cpu} vCPU` | `{ram} GB RAM` | `{disk} GB NVMe`\n{E_ARROW} **SSH Port:** `{ssh_port}` | **VNC:** `{vnc_port}`\n{E_ARROW} **Expires:** `{expires_at}`",
            inline=False
        )
        embed.add_field(
            name="🌐 Console Credentials",
            value=(f"{E_CHECK} *Access keys successfully delivered to user's direct messages.*"
                   if dm_delivered else
                   f"{E_WARN} *DMs closed! Credentials:* `{panel_user}` / `{panel_pass}`"),
            inline=False
        )
        view = VMControlView(vm_id, user.id, c_name)
        await status_msg.edit(content=None, embed=embed, view=view)

    except Exception as e:
        await status_msg.edit(embed=discord.Embed(title=f"{E_NO} Allocation Error", description=f"```{str(e)}```", color=0xED4245))

# Instant Terminal Link Command
@bot.command(name="terminal", aliases=["sshx", "shell"])
async def terminal_cmd(ctx, vm_id: Optional[int] = None):
    async with get_db() as db:
        if vm_id:
            query = "SELECT container_name, owner_id, vm_id FROM vms WHERE vm_id = ?"
            params = (vm_id,)
        else:
            query = "SELECT container_name, owner_id, vm_id FROM vms WHERE owner_id = ? ORDER BY vm_id DESC LIMIT 1"
            params = (ctx.author.id,)

        async with db.execute(query, params) as cur:
            row = await cur.fetchone()

    if not row:
        return await ctx.reply(embed=discord.Embed(title=f"{E_WARN} Not Found", description="No virtual machine instance located.", color=0xED4245))

    c_name, owner_id, target_id = row
    if ctx.author.id != owner_id and not await is_admin(ctx.author.id):
        return await ctx.reply(embed=discord.Embed(title=f"{E_NO} Unauthorized", description="Access denied.", color=0xED4245))

    load = await ctx.reply(f"{E_LOADING} Spawning secure encrypted terminal relay...")
    url = await get_or_create_sshx_link(c_name)

    if url:
        embed = discord.Embed(
            title=f"{E_LIGHTNING} Encrypted Terminal Relay Active",
            description=(
                f"{E_STAR} **Connected to VM #{target_id}**\n\n"
                f"🔗 **[Click Here to Launch Web Terminal]({url})**\n\n"
                f"```text\n{url}\n```"
            ),
            color=0x7952FF,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_footer(text="Multiplayer shared sessions & mobile keyboard supported.")
        await load.edit(content=None, embed=embed)
    else:
        await load.edit(content=f"{E_NO} Failed to initialize relay. Verify container is running.")

# Direct Reinstall Command
@bot.command(name="reinstall")
async def reinstall_cmd(ctx, vm_id: int):
    async with get_db() as db:
        async with db.execute("SELECT owner_id, container_name, ram, cpu, disk, vnc_port, ssh_port, vnc_pass, root_pass FROM vms WHERE vm_id = ?", (vm_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        return await ctx.reply(f"{E_WARN} VM #{vm_id} not found.")

    owner_id, c_name, ram, cpu, disk, vnc_port, ssh_port, vnc_pass, root_pass = row
    if ctx.author.id != owner_id and not await is_admin(ctx.author.id):
        return await ctx.reply(f"{E_NO} Access denied.")

    load = await ctx.reply(f"{E_LOADING} Reinstalling OS for VM #{vm_id}...")
    loop = asyncio.get_running_loop()
    try:
        new_id = await loop.run_in_executor(None, launch_vm_container, c_name, ram, cpu, disk, vnc_port, ssh_port, vnc_pass, root_pass)
        async with get_db() as db:
            await db.execute("UPDATE vms SET container_id = ? WHERE vm_id = ?", (new_id, vm_id))
            await db.commit()
        await load.edit(content=f"{E_CHECK} **VM #{vm_id}** completely re-imaged with Ubuntu 24.")
    except Exception as e:
        await load.edit(content=f"{E_NO} Reinstall failed: `{str(e)}`")

# Container Power Management Commands
@bot.command(name="restart")
async def restart_vm(ctx, vm_id: int):
    async with get_db() as db:
        async with db.execute("SELECT owner_id, container_name FROM vms WHERE vm_id = ?", (vm_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        return await ctx.reply(f"{E_WARN} VM #{vm_id} not found.")

    if ctx.author.id != row[0] and not await is_admin(ctx.author.id):
        return await ctx.reply(f"{E_NO} Access denied.")

    msg = await ctx.reply(f"{E_LOADING} Restarting instance...")
    try:
        container = docker_client.containers.get(row[1])
        container.restart(timeout=5)
        await msg.edit(content=f"{E_CHECK} **VM #{vm_id}** restarted successfully.")
    except Exception as e:
        await msg.edit(content=f"{E_NO} Restart failed: `{str(e)}`")

@bot.command(name="stop")
async def stop_vm(ctx, vm_id: int):
    async with get_db() as db:
        async with db.execute("SELECT owner_id, container_name FROM vms WHERE vm_id = ?", (vm_id,)) as cur:
            row = await cur.fetchone()

    if not row or (ctx.author.id != row[0] and not await is_admin(ctx.author.id)):
        return await ctx.reply(f"{E_NO} Permission denied or VM not found.")

    try:
        docker_client.containers.get(row[1]).stop(timeout=5)
        await ctx.reply(f"{E_CHECK} **VM #{vm_id}** has been stopped.")
    except Exception as e:
        await ctx.reply(f"{E_NO} Error stopping container: `{str(e)}`")

@bot.command(name="start")
async def start_vm(ctx, vm_id: int):
    async with get_db() as db:
        async with db.execute("SELECT owner_id, container_name FROM vms WHERE vm_id = ?", (vm_id,)) as cur:
            row = await cur.fetchone()

    if not row or (ctx.author.id != row[0] and not await is_admin(ctx.author.id)):
        return await ctx.reply(f"{E_NO} Permission denied or VM not found.")

    try:
        docker_client.containers.get(row[1]).start()
        await ctx.reply(f"{E_CHECK} **VM #{vm_id}** is now booting up.")
    except Exception as e:
        await ctx.reply(f"{E_NO} Error starting container: `{str(e)}`")

# Manage Command
@bot.command(name="manage")
async def manage_cmd(ctx, user: Optional[discord.User] = None):
    target = user if user else ctx.author
    if target != ctx.author and not await is_admin(ctx.author.id):
        return await ctx.reply(f"{E_NO} Only administrators can inspect instances belonging to others.")

    async with get_db() as db:
        async with db.execute("SELECT vm_id, container_name, ram, cpu, disk, ssh_port, vnc_port, expires_at FROM vms WHERE owner_id = ?", (target.id,)) as cur:
            records = await cur.fetchall()

    if not records:
        return await ctx.reply(embed=discord.Embed(title=f"{E_INFO} Empty Registry", description=f"No active instances for {target.mention}.", color=0x2B2D31))

    vm_id, c_name, ram, cpu, disk, ssh_port, vnc_port, expires_at = records[0]
    stats = get_container_stats(c_name)
    status_icon = E_ONLINE if stats["online"] else E_OFFLINE
    status_label = "ONLINE" if stats["online"] else "OFFLINE"

    embed = discord.Embed(
        title=f"{E_KING_CROWN} {NODE_NAME} • Instance Control Center (VM #{vm_id})",
        description=f"{E_STAR} **Resource allocation for {target.mention}**\n{E_LIGHTNING} Web Dashboard: [Open Console]({PANEL_URL})",
        color=0x5865F2,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(
        name="📊 Virtual Machine Telemetry",
        value=f"{E_ARROW} **Status:** {status_icon} `{status_label}`\n{E_ARROW} **Name:** `{c_name}`\n{E_ARROW} **CPU:** `{cpu} vCPU` (`{stats['cpu_pct']}%`)\n{E_ARROW} **RAM:** `{ram} GB` (`{stats['ram_str']}`)\n{E_ARROW} **Storage:** `{disk} GB` (`{stats['disk_str']}`)",
        inline=False
    )
    embed.add_field(
        name="🌐 Network & Lifecycle",
        value=f"{E_ARROW_DOUBLE} **SSH Port:** `{ssh_port}` | **VNC Port:** `{vnc_port}`\n{E_ARROW_DOUBLE} **Expires:** `{expires_at}`",
        inline=False
    )
    view = VMControlView(vm_id, target.id, c_name)
    await ctx.reply(embed=embed, view=view)

# Renew & Expiration Commands
@bot.command(name="renew")
async def renew_vm(ctx, target: discord.User, specific_vm_id: Optional[int] = None):
    if not await is_admin(ctx.author.id):
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    async with get_db() as db:
        query = "SELECT vm_id, expires_at FROM vms WHERE owner_id = ?" + (" AND vm_id = ?" if specific_vm_id else "")
        params = (target.id, specific_vm_id) if specific_vm_id else (target.id,)
        async with db.execute(query, params) as cur:
            records = await cur.fetchall()

        if not records:
            return await ctx.reply(f"{E_WARN} No instances found for {target.mention}.")

        for vm_id, exp_str in records:
            try:
                cur_dt = datetime.datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=datetime.timezone.utc)
                base = cur_dt if cur_dt > now else now
            except Exception:
                base = now
            new_exp = (base + datetime.timedelta(days=28)).strftime("%Y-%m-%d %H:%M:%S UTC")
            await db.execute("UPDATE vms SET expires_at = ? WHERE vm_id = ?", (new_exp, vm_id))
        await db.commit()

    await grant_client_role(target.id)
    await ctx.reply(embed=discord.Embed(title=f"{E_CHECK} Subscriptions Extended (+28 Days)", description=f"Extended {len(records)} virtual machine(s) for {target.mention}.", color=0x57F287))

@bot.command(name="setexp")
async def set_expiration(ctx, target: discord.User, *args):
    if not await is_admin(ctx.author.id):
        return

    args_list = list(args)
    specific_vm_id = None
    if len(args_list) > 1 and args_list[-1].isdigit() and not any(u in args_list[-1].lower() for u in ["h", "d", "m"]):
        specific_vm_id = int(args_list.pop())

    duration = parse_time_duration(args_list)
    if not duration:
        return await ctx.reply(f"{E_WARN} Syntax: `!setexp @user 28d [vm_id]`")

    now = datetime.datetime.now(datetime.timezone.utc)
    new_expiry = (now + duration).strftime("%Y-%m-%d %H:%M:%S UTC")

    async with get_db() as db:
        if specific_vm_id:
            await db.execute("UPDATE vms SET expires_at = ? WHERE owner_id = ? AND vm_id = ?", (new_expiry, target.id, specific_vm_id))
        else:
            await db.execute("UPDATE vms SET expires_at = ? WHERE owner_id = ?", (new_expiry, target.id))
        await db.commit()

    await ctx.reply(embed=discord.Embed(title=f"{E_GEAR} Expiration Updated", description=f"Expiration set to `{new_expiry}` for {target.mention}.", color=0x5865F2))

# Delete Command
@bot.command(name="delete")
async def delete_vm(ctx, target: Union[int, discord.User]):
    if not await is_admin(ctx.author.id):
        return

    load = await ctx.reply(f"{E_LOADING} Decommissioning resources...")
    async with get_db() as db:
        if isinstance(target, int):
            async with db.execute("SELECT owner_id, container_name FROM vms WHERE vm_id = ?", (target,)) as cur:
                row = await cur.fetchone()
            if not row:
                return await load.edit(content=f"{E_WARN} VM #{target} not found.")
            owner_id, c_name = row
            try:
                docker_client.containers.get(c_name).remove(force=True)
                docker_client.volumes.get(f"{c_name}-data").remove(force=True)
            except Exception:
                pass
            await db.execute("DELETE FROM vms WHERE vm_id = ?", (target,))
            await db.commit()
            await revoke_client_role_if_empty(owner_id)
            await load.edit(content=f"{E_CHECK} VM **#{target}** wiped from hypervisor.")
        else:
            async with db.execute("SELECT vm_id, container_name FROM vms WHERE owner_id = ?", (target.id,)) as cur:
                rows = await cur.fetchall()
            for vm_id, c_name in rows:
                try:
                    docker_client.containers.get(c_name).remove(force=True)
                    docker_client.volumes.get(f"{c_name}-data").remove(force=True)
                except Exception:
                    pass
            await db.execute("DELETE FROM vms WHERE owner_id = ?", (target.id,))
            await db.commit()
            await revoke_client_role_if_empty(target.id)
            await load.edit(content=f"{E_CHECK} Purged all **{len(rows)}** machine(s) owned by {target.mention}.")

# Telemetry & Administration Commands
@bot.command(name="setadmin", aliases=["giveadmin"])
async def set_admin(ctx, target: discord.User):
    if not await is_admin(ctx.author.id):
        return
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (target.id,))
        await db.commit()
    await ctx.reply(f"{E_CHECK} Granted administrative permissions to {target.mention}.")

@bot.command(name="removeadmin")
async def remove_admin(ctx, target: discord.User):
    if not await is_admin(ctx.author.id) or target.id == MAIN_OWNER_ID:
        return
    async with get_db() as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (target.id,))
        await db.commit()
    await ctx.reply(f"{E_CHECK} Revoked administrative access from {target.mention}.")

@bot.command(name="vinfo")
async def vinfo_cmd(ctx):
    if not await is_admin(ctx.author.id):
        return
    embed = discord.Embed(
        title=f"{E_KING_CROWN} {NODE_NAME} • Cluster Telemetry",
        description=f"{E_STAR} **Node:** `tempest-tier1-master-01.dc-node.net`\n{E_ARROW} **Management:** [Web Dashboard]({PANEL_URL})",
        color=0x5865F2
    )
    embed.add_field(name=f"{E_THUNDER} Processing Array", value="`Dual AMD EPYC™ 9654 (128 Cores / 256 Threads)`\nCluster Load: `12.4%`", inline=False)
    embed.add_field(name=f"{E_GEAR} DDR5 ECC Memory", value="`500.0 GB Total` | `457.2 GB Available`\nActive Utilization: `8.5%`", inline=False)
    embed.add_field(name=f"{E_FIRE} Enterprise NVMe Fabric", value="`10,000.0 GB (10 TB)` | `PCIe 5.0 RAID-10`", inline=False)
    await ctx.reply(embed=embed)

@bot.command(name="vminfo")
async def vminfo_cmd(ctx):
    if not await is_admin(ctx.author.id):
        return
    async with get_db() as db:
        async with db.execute("SELECT vm_id, owner_id, container_name, ram, cpu, disk, expires_at FROM vms") as cur:
            records = await cur.fetchall()

    if not records:
        return await ctx.reply("No provisioned instances.")

    embed = discord.Embed(title=f"{E_KING_CROWN} {NODE_NAME} • Global Virtual Machine Registry", description=f"Active instances: `{len(records)}`", color=0x2B2D31)
    for vm_id, oid, c_name, ram, cpu, disk, exp in records:
        stats = get_container_stats(c_name)
        icon = E_ONLINE if stats["online"] else E_OFFLINE
        embed.add_field(name=f"{icon} VM #{vm_id}: `{c_name}`", value=f"Owner: <@{oid}>\nSpecs: `{cpu} vCPU` | `{ram}G RAM` | `{disk}G NVMe`\nExpires: `{exp}`", inline=False)
    await ctx.reply(embed=embed)

# ----------------- BACKGROUND MONITORS -----------------
@tasks.loop(seconds=25)
async def anti_mining_monitor():
    try:
        async with get_db() as db:
            async with db.execute("SELECT vm_id, owner_id, container_name FROM vms") as cur:
                active_vms = await cur.fetchall()

        for vm_id, owner_id, c_name in active_vms:
            try:
                container = docker_client.containers.get(c_name)
                if container.status != "running":
                    continue
                res = container.exec_run("ps aux")
                if res.exit_code != 0:
                    continue
                ps_output = res.output.decode("utf-8", errors="ignore").lower()
                detected = next((sig for sig in MINER_SIGNATURES if sig in ps_output), None)
                if detected:
                    container.remove(force=True)
                    docker_client.volumes.get(f"{c_name}-data").remove(force=True)
                    async with get_db() as db:
                        await db.execute("DELETE FROM vms WHERE vm_id = ?", (vm_id,))
                        await db.commit()
                    await revoke_client_role_if_empty(owner_id)

                    channel = bot.get_channel(ALERT_CHANNEL_ID)
                    if channel:
                        alert = discord.Embed(
                            title=f"{E_WARN} CRYPTO MINING DETECTED & TERMINATED",
                            description=f"Signature `{detected}` found on `{c_name}` (VM #{vm_id}). Container and storage purged.",
                            color=0xED4245
                        )
                        await channel.send(content=f"<@{MINING_ALERT_PING_ID}>", embed=alert)
            except Exception:
                continue
    except Exception:
        pass

@tasks.loop(minutes=30)
async def expiry_check_loop():
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_db() as db:
            async with db.execute("SELECT vm_id, owner_id, container_name, expires_at FROM vms") as cur:
                records = await cur.fetchall()

            for vm_id, owner_id, c_name, exp_str in records:
                try:
                    exp_dt = datetime.datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=datetime.timezone.utc)
                    if now >= exp_dt:
                        docker_client.containers.get(c_name).remove(force=True)
                        docker_client.volumes.get(f"{c_name}-data").remove(force=True)
                        await db.execute("DELETE FROM vms WHERE vm_id = ?", (vm_id,))
                        await db.commit()
                        await revoke_client_role_if_empty(owner_id)
                except Exception:
                    continue
    except Exception:
        pass

# ----------------- DUAL RUNNER -----------------
def start_web_panel():
    uvicorn.run(app, host="0.0.0.0", port=PANEL_PORT, log_level="warning")

if __name__ == "__main__":
    threading.Thread(target=start_web_panel, daemon=True).start()
    bot.run(TOKEN)
