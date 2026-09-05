import discord
from discord.ext import commands, tasks
from discord import app_commands
import docker
import aiosqlite
import asyncio
import os
import random
import string
import datetime
import time
import re
import socket
import paramiko
from typing import Optional, Union

# ----------------- CONFIGURATION -----------------
TOKEN = "YOUR_DISCORD_BOT_TOKEN_HERE"
NODE_NAME = "TEMPEST CLOUD"
HOST_IP = "YOUR_SERVER_PUBLIC_IP"

# Absolute database path to eliminate "unable to open database file"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "tempest_vms.db")

# Customer/Client Role ID
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
E_LOADING_ALT = "<a:Loading:1519558138209112155>"
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

# Regex cleaner for ANSI color terminal sequences
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def clean_output(raw: str) -> str:
    return ANSI_ESCAPE.sub('', raw)

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

# ----------------- ROLE SYNC HELPERS -----------------
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

# ----------------- DOCKER OPERATIONS -----------------
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
            return {"online": False, "ram_used": "0 MB", "cpu_pct": "0.0%", "disk_used": "Offline"}
        
        stats = container.stats(stream=False)
        mem_usage = stats['memory_stats'].get('usage', 0) / (1024 * 1024)
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
            "ram_used": f"{mem_usage:.1f} MB",
            "cpu_pct": f"{cpu_pct:.1f}%",
            "disk_used": disk_str
        }
    except Exception:
        return {"online": False, "ram_used": "0 MB", "cpu_pct": "0.0%", "disk_used": "Offline"}

# ----------------- RELIABLE SSHX RELAY (GUEST VM DIRECT) -----------------
async def get_or_create_sshx_link(container_name: str) -> Optional[str]:
    loop = asyncio.get_running_loop()

    async with get_db() as db:
        async with db.execute("SELECT ssh_port, root_pass FROM vms WHERE container_name = ?", (container_name,)) as cur:
            row = await cur.fetchone()

    if not row:
        return None

    ssh_port, root_pass = row

    def execute_in_guest():
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connected = False
        for _ in range(12):
            try:
                client.connect(
                    hostname="127.0.0.1",
                    port=int(ssh_port),
                    username="root",
                    password=root_pass,
                    look_for_keys=False,
                    allow_agent=False,
                    timeout=3
                )
                connected = True
                break
            except Exception:
                time.sleep(1)

        if not connected:
            return None

        try:
            # 1. Check if a live session already has a link in /tmp/sshx.log
            stdin, stdout, stderr = client.exec_command("pgrep -x sshx")
            if stdout.channel.recv_exit_status() == 0:
                _, log_out, _ = client.exec_command("cat /tmp/sshx.log 2>/dev/null")
                cleaned = clean_output(log_out.read().decode("utf-8", errors="ignore"))
                match = re.search(r"https://sshx\.io/s/[A-Za-z0-9#_\-]+", cleaned)
                if match:
                    client.close()
                    return match.group(0)

            # 2. Make sure curl and required packages are installed inside Ubuntu
            install_cmd = (
                "bash -c '"
                "if ! command -v curl >/dev/null 2>&1; then apt-get update -qq && apt-get install -y -qq curl; fi; "
                "if ! command -v sshx >/dev/null 2>&1 && [ ! -f /usr/local/bin/sshx ]; then "
                "  curl -sSf https://sshx.io/get | sh -s -- -y >/dev/null 2>&1; "
                "  cp ~/.local/bin/sshx /usr/local/bin/sshx 2>/dev/null || true; "
                "fi'"
            )
            client.exec_command(install_cmd)

            # 3. Clean any dead processes & start fresh instance
            client.exec_command("pkill -9 -x sshx 2>/dev/null; rm -f /tmp/sshx.log")
            
            run_cmd = (
                "bash -c '"
                "export PATH=$PATH:/root/.local/bin:/usr/local/bin; "
                "nohup sshx > /tmp/sshx.log 2>&1 &'"
            )
            client.exec_command(run_cmd)

            # 4. Poll /tmp/sshx.log for the generated link
            for _ in range(25):
                time.sleep(0.4)
                _, poll_out, _ = client.exec_command("cat /tmp/sshx.log 2>/dev/null")
                raw = poll_out.read().decode("utf-8", errors="ignore")
                cleaned = clean_output(raw)
                match = re.search(r"https://sshx\.io/s/[A-Za-z0-9#_\-]+", cleaned)
                if match:
                    client.close()
                    return match.group(0)

        except Exception as e:
            print(f"[Guest SSHX Error]: {e}")
        finally:
            client.close()

        return None

    return await loop.run_in_executor(None, execute_in_guest)

# ----------------- CREDENTIALS DISPATCH -----------------
async def dispatch_private_credentials(user: discord.User, data: tuple, is_admin_viewer: bool = False):
    vm_id, owner_id, c_name, container_id, vnc_port, ssh_port, ram, cpu, disk, root_pass, vnc_pass, created_at, expires_at = data
    
    embed = discord.Embed(
        title=f"{E_KING_CROWN} {NODE_NAME} • Private Keys (VM #{vm_id})",
        description=f"{E_STAR} **Confidential Root Credentials for `{c_name}`**\n"
                    f"{E_WARN} *Keep these credentials safe. Anyone with this password can execute root commands.*",
        color=0xFEE75C,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(
        name=f"{E_FIRE} Authorization & Direct Endpoints",
        value=(
            f"{E_ARROW} **Direct SSH:** `ssh root@{HOST_IP} -p {ssh_port}`\n"
            f"{E_ARROW} **Root Password:** `{root_pass}`\n"
            f"{E_ARROW} **VNC Web Port:** `{vnc_port}`\n"
            f"{E_ARROW} **VNC Password:** `{vnc_pass}`\n"
            f"{E_ARROW} **VNC Web Access:** http://{HOST_IP}:{vnc_port}"
        ),
        inline=False
    )
    embed.add_field(
        name=f"{E_GEAR} Hardware Allocation Breakdown",
        value=(
            f"{E_ARROW} **Compute Slices:** `{cpu} vCPU Core(s)`\n"
            f"{E_ARROW} **Dedicated RAM:** `{ram} GB DDR5 ECC`\n"
            f"{E_ARROW} **Storage Volume:** `{disk} GB NVMe PCIe 5.0`\n"
            f"{E_ARROW} **Lease Term Expires:** `{expires_at}`"
        ),
        inline=False
    )
    footer = f"Admin Dispatch • {NODE_NAME}" if is_admin_viewer else f"Client Private Vault • {NODE_NAME}"
    embed.set_footer(text=footer, icon_url=bot.user.display_avatar.url)
    await user.send(embed=embed)

# ----------------- UI BUTTON VIEWS -----------------
class VMControlView(discord.ui.View):
    def __init__(self, vm_id: int, owner_id: int, container_name: str):
        super().__init__(timeout=None)
        self.vm_id = vm_id
        self.owner_id = owner_id
        self.container_name = container_name
        
        self.ssh_button.custom_id = f"nova_sshx_{self.vm_id}"
        self.view_keys_button.custom_id = f"nova_keys_{self.vm_id}"
        self.reinstall_button.custom_id = f"nova_reinstall_{self.vm_id}"
        self.restart_button.custom_id = f"nova_restart_{self.vm_id}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id or await is_admin(interaction.user.id):
            return True
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"{E_NO} Access Restricted",
                description=f"{E_WARN} You do not have permission to control `{self.container_name}`.",
                color=0xED4245
            ),
            ephemeral=True
        )
        return False

    @discord.ui.button(label="Web Terminal", style=discord.ButtonStyle.primary, emoji="⚡")
    async def ssh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        loading = discord.Embed(
            title=f"{E_LOADING} {NODE_NAME} • Initializing Secure Terminal",
            description=f"{E_ARROW} Connecting into guest OS for `{self.container_name}`...\n"
                        f"{E_GEAR} Launching zero-latency `sshx` encrypted terminal daemon...\n"
                        f"{E_STAR} Please wait a moment...",
            color=0xFEE75C
        )
        await interaction.response.send_message(embed=loading, ephemeral=True)

        url = await get_or_create_sshx_link(self.container_name)

        if url:
            success = discord.Embed(
                title=f"{E_LIGHTNING} {NODE_NAME} • Terminal Ready (VM #{self.vm_id})",
                description=(
                    f"{E_YES} **Encrypted console session is online!**\n\n"
                    f"🔗 **[Click Here to Open Web Terminal]({url})**\n\n"
                    f"```{url}```\n"
                    f"{E_STAR} *Supports mobile keyboard, copy/paste, and multiplayer sharing.*"
                ),
                color=0x57F287,
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            success.set_footer(text=f"{NODE_NAME} • Hypervisor Core", icon_url=interaction.client.user.display_avatar.url)
            await interaction.edit_original_response(embed=success)
        else:
            fail = discord.Embed(
                title=f"{E_NO} {NODE_NAME} • Terminal Relay Error",
                description=f"{E_WARN} Failed to start SSHX relay. Please make sure the VM container is running.",
                color=0xED4245
            )
            await interaction.edit_original_response(embed=fail)

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.secondary, emoji="🔁")
    async def restart_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        loading = discord.Embed(
            title=f"{E_LOADING} {NODE_NAME} • Restarting Instance",
            description=f"{E_ARROW} Sending reboot signal to `{self.container_name}`...",
            color=0xFEE75C
        )
        await interaction.response.send_message(embed=loading, ephemeral=True)
        try:
            container = docker_client.containers.get(self.container_name)
            container.restart(timeout=5)
            done = discord.Embed(
                title=f"{E_CHECK} Instance Rebooted",
                description=f"{E_YES} **VM #{self.vm_id}** (`{self.container_name}`) rebooted cleanly.",
                color=0x57F287
            )
            await interaction.edit_original_response(embed=done)
        except Exception as e:
            fail = discord.Embed(
                title=f"{E_NO} Restart Error",
                description=f"```{str(e)}```",
                color=0xED4245
            )
            await interaction.edit_original_response(embed=fail)

    @discord.ui.button(label="View Passwords (DM)", style=discord.ButtonStyle.secondary, emoji="🔑")
    async def view_keys_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        async with get_db() as db:
            async with db.execute("SELECT * FROM vms WHERE vm_id = ?", (self.vm_id,)) as cur:
                data = await cur.fetchone()

        if not data:
            await interaction.followup.send(f"{E_NO} VM configuration record not found.", ephemeral=True)
            return

        try:
            await dispatch_private_credentials(interaction.user, data, is_admin_viewer=(interaction.user.id != self.owner_id))
            await interaction.followup.send(f"{E_CHECK} Passwords sent to your direct messages.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(f"{E_WARN} Cannot send DM. Please enable direct messages in your privacy settings.", ephemeral=True)

    @discord.ui.button(label="Reinstall OS", style=discord.ButtonStyle.danger, emoji="🔄")
    async def reinstall_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        loading = discord.Embed(
            title=f"{E_LOADING} {NODE_NAME} • Processing OS Reinstall",
            description=f"{E_ARROW} Re-imaging root disk volume for `{self.container_name}`...\n"
                        f"{E_GEAR} Preserving network ports, assigned passwords, and lease dates...\n"
                        f"{E_STAR} Please wait...",
            color=0xFEE75C
        )
        await interaction.response.send_message(embed=loading, ephemeral=True)

        async with get_db() as db:
            async with db.execute("SELECT ram, cpu, disk, vnc_port, ssh_port, vnc_pass, root_pass FROM vms WHERE vm_id = ?", (self.vm_id,)) as cur:
                row = await cur.fetchone()

        if not row:
            fail_embed = discord.Embed(
                title=f"{E_NO} Reinstall Aborted",
                description=f"{E_WARN} VM configuration missing from database.",
                color=0xED4245
            )
            await interaction.edit_original_response(embed=fail_embed)
            return

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
                title=f"{E_CHECK} {NODE_NAME} • Reinstallation Complete",
                description=(
                    f"{E_YES} **Virtual Machine cleanly re-imaged with Ubuntu 24!** {E_GG}\n\n"
                    f"{E_ARROW} **Instance Name:** `{self.container_name}`\n"
                    f"{E_ARROW} **Container ID:** `{new_id[:12]}`\n"
                    f"{E_STAR} *Your existing SSH port, passwords, and lease dates were preserved.*"
                ),
                color=0x57F287,
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            await interaction.edit_original_response(embed=success)
        except Exception as e:
            fail_embed = discord.Embed(
                title=f"{E_NO} Reinstall Failed",
                description=f"{E_WARN} Error re-imaging container: `{str(e)}`",
                color=0xED4245
            )
            await interaction.edit_original_response(embed=fail_embed)

class VMSelectDropdown(discord.ui.Select):
    def __init__(self, vms: list, owner: discord.User):
        self.vms_dict = {str(vm[0]): vm for vm in vms}
        self.owner = owner
        options = [
            discord.SelectOption(
                label=f"VM #{vm[0]} ({vm[6]}G RAM / {vm[7]} vCPU)",
                description=f"SSH: {vm[5]} | VNC: {vm[4]}",
                value=str(vm[0]),
                emoji="🖥️"
            )
            for vm in vms[:25]
        ]
        super().__init__(placeholder="Select which VM to manage...", options=options)

    async def callback(self, interaction: discord.Interaction):
        vm_data = self.vms_dict[self.values[0]]
        embed = await build_channel_vm_embed(self.owner, vm_data)
        view = VMControlView(vm_data[0], vm_data[1], vm_data[2])
        await interaction.response.edit_message(content=None, embed=embed, view=view)

class VMPickerView(discord.ui.View):
    def __init__(self, vms: list, owner: discord.User):
        super().__init__(timeout=120)
        self.add_item(VMSelectDropdown(vms, owner))

# ----------------- MAIN EMBED BUILDER -----------------
async def build_channel_vm_embed(owner: discord.User, data: tuple) -> discord.Embed:
    vm_id, owner_id, c_name, container_id, vnc_port, ssh_port, ram, cpu, disk, root_pass, vnc_pass, created_at, expires_at = data
    stats = get_container_stats(c_name)
    
    status_icon = E_ONLINE if stats["online"] else E_OFFLINE
    status_label = "ONLINE" if stats["online"] else "OFFLINE"
    
    embed = discord.Embed(
        title=f"{E_KING_CROWN} {NODE_NAME} • VM #{vm_id} Control Console",
        description=f"{E_STAR} **Hardware slice dedicated to {owner.mention}**\n"
                    f"{E_BLACK_WING} *Protected by Hardware KVM Virtualization & Anti-Mining Shields*",
        color=0x2B2D31,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(
        name=f"{E_INFO} Virtual Machine Telemetry",
        value=(
            f"{E_ARROW} **Instance Name:** `{c_name}`\n"
            f"{E_ARROW} **Power Status:** {status_icon} `{status_label}`\n"
            f"{E_ARROW} **Container ID:** `{container_id[:12]}`\n"
            f"{E_ARROW} **CPU Load:** `{cpu} vCPU` (`{stats['cpu_pct']}`)\n"
            f"{E_ARROW} **RAM Active:** `{ram} GB` (`{stats['ram_used']}`)\n"
            f"{E_ARROW} **Disk Usage:** `{disk} GB` (`{stats['disk_used']}`)"
        ),
        inline=False
    )
    embed.add_field(
        name=f"{E_GEAR} Network Endpoints & Lifecycle",
        value=(
            f"{E_ARROW_DOUBLE} **SSH Access:** `ssh root@{HOST_IP} -p {ssh_port}`\n"
            f"{E_ARROW_DOUBLE} **Web VNC Port:** `{vnc_port}`\n"
            f"{E_ARROW_DOUBLE} **Lease Term Expires:** `{expires_at}`"
        ),
        inline=False
    )
    embed.add_field(
        name=f"{E_FIRE} Security & Operations",
        value=(
            f"{E_CHECK} **Ownership:** Verified\n"
            f"{E_ARROW} **Tier Role:** <@&{CLIENT_ROLE_ID}>\n"
            f"{E_THUNDER} *Use the buttons below to open terminal, restart, or view keys.*"
        ),
        inline=False
    )
    embed.set_footer(text=f"Nova Orchestrator {E_BOT_TAG} • {NODE_NAME}", icon_url=bot.user.display_avatar.url)
    return embed

# ----------------- BOT EVENTS -----------------
@bot.event
async def on_ready():
    await init_db()
    async with get_db() as db:
        async with db.execute("SELECT vm_id, owner_id, container_name FROM vms") as cur:
            all_vms = await cur.fetchall()
            for vm_id, owner_id, c_name in all_vms:
                bot.add_view(VMControlView(vm_id, owner_id, c_name))

    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")
        
    expiry_check_loop.start()
    anti_mining_monitor.start()
    print(f"Nova Cloud active on {NODE_NAME} • Ready to provision instances.")

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

# ----------------- PROVISION COMMAND -----------------
@bot.command(name="vm")
async def create_vm(ctx, ram: str, cpu: str, disk: str, user: discord.User, days: int = 30):
    if not await is_admin(ctx.author.id):
        err = discord.Embed(
            title=f"{E_NO} Access Restricted",
            description=f"{E_WARN} This instruction is strictly restricted to **{NODE_NAME}** Administrators.",
            color=0xED4245
        )
        await ctx.reply(embed=err)
        return

    # Dynamic Progress Stage 1: Initiating
    init_embed = discord.Embed(
        title=f"{E_LOADING} Initializing Virtual Machine Slice",
        description=f"{E_ARROW} Slicing hardware virtualization for {user.mention} on **{NODE_NAME}**...\n"
                    f"{E_GEAR} Requesting `{cpu} vCPU` | `{ram} GB RAM` | `{disk} GB NVMe`...\n"
                    f"{E_STAR} Reserving dedicated network endpoints...",
        color=0xFEE75C
    )
    status_msg = await ctx.reply(embed=init_embed)

    try:
        vnc_port = get_free_port(6080)
        ssh_port = get_free_port(2026)
        root_pass = gen_password(12)
        vnc_pass = gen_password(8)
        created_at = datetime.datetime.now(datetime.timezone.utc)
        expires_at = (created_at + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Dynamic Progress Stage 2: Database Allocation
        step2_embed = discord.Embed(
            title=f"{E_LOADING} Deploying Virtualization Layer",
            description=f"{E_ARROW} Writing allocation to cluster database...\n"
                        f"{E_GEAR} Allocated SSH Port: `{ssh_port}` | VNC Port: `{vnc_port}`\n"
                        f"{E_STAR} Booting Ubuntu 24 KVM container...",
            color=0xFEE75C
        )
        await status_msg.edit(embed=step2_embed)

        async with get_db() as db:
            cur = await db.execute("""
                INSERT INTO vms (owner_id, container_name, container_id, vnc_port, ssh_port, ram, cpu, disk, root_pass, vnc_pass, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user.id, "pending", "pending", vnc_port, ssh_port, ram, cpu, disk, root_pass, vnc_pass, created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), expires_at))
            vm_id = cur.lastrowid
            await db.commit()

        c_name = f"atyro-vm-{user.id}-{vm_id}"

        loop = asyncio.get_running_loop()
        cid = await loop.run_in_executor(
            None, launch_vm_container, c_name, ram, cpu, disk, vnc_port, ssh_port, vnc_pass, root_pass
        )

        async with get_db() as db:
            await db.execute("UPDATE vms SET container_name = ?, container_id = ? WHERE vm_id = ?", (c_name, cid, vm_id))
            await db.commit()

        full_data = (vm_id, user.id, c_name, cid, vnc_port, ssh_port, ram, cpu, disk, root_pass, vnc_pass, created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), expires_at)

        await grant_client_role(user.id)

        try:
            await dispatch_private_credentials(user, full_data)
            dm_sent = True
        except discord.Forbidden:
            dm_sent = False

        # Dynamic Progress Stage 3: Completed
        embed = await build_channel_vm_embed(user, full_data)
        if not dm_sent:
            embed.add_field(
                name=f"{E_WARN} Attention {user.name}",
                value="*Your DMs are locked! Click the **'View Passwords (DM)'** button below after unlocking your DMs.*",
                inline=False
            )

        view = VMControlView(vm_id, user.id, c_name)
        await status_msg.edit(embed=embed, view=view)

    except Exception as e:
        err_embed = discord.Embed(
            title=f"{E_NO} Hardware Allocation Fault",
            description=f"```{str(e)}```",
            color=0xED4245
        )
        await status_msg.edit(embed=err_embed)

# ----------------- INSTANT TERMINAL COMMAND -----------------
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
        return await ctx.reply(embed=discord.Embed(title=f"{E_NO} Unauthorized", description="You do not own this machine.", color=0xED4245))

    load = discord.Embed(
        title=f"{E_LOADING} Initializing Secure Terminal Relay",
        description=f"{E_ARROW} Connecting into root shell for VM **#{target_id}** (`{c_name}`)...\n"
                    f"{E_GEAR} Booting encrypted Web SSHX relay...\n"
                    f"{E_STAR} Please wait...",
        color=0xFEE75C
    )
    msg = await ctx.reply(embed=load)

    url = await get_or_create_sshx_link(c_name)

    if url:
        embed = discord.Embed(
            title=f"{E_LIGHTNING} {NODE_NAME} • Web Terminal Ready (VM #{target_id})",
            description=(
                f"{E_STAR} **Root console session active!**\n\n"
                f"🔗 **[Click Here to Open Console in Browser]({url})**\n\n"
                f"```{url}```\n"
                f"{E_FIRE} *Supports full root privileges, mobile keyboards, and copy/paste.*"
            ),
            color=0x7952FF,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_footer(text="Multiplayer shared sessions & collaborative access enabled.")
        await msg.edit(embed=embed)
    else:
        err = discord.Embed(
            title=f"{E_NO} Terminal Relay Error",
            description=f"{E_WARN} Failed to initialize terminal session. Ensure the instance is running.",
            color=0xED4245
        )
        await msg.edit(embed=err)

# ----------------- MANAGEMENT COMMAND -----------------
@bot.command(name="manage")
async def manage_vm(ctx, user: Optional[discord.User] = None):
    target = user if user else ctx.author
    if target != ctx.author and not await is_admin(ctx.author.id):
        err = discord.Embed(
            title=f"{E_NO} Unauthorized Access",
            description=f"{E_WARN} Only administrators can inspect other members' VMs.",
            color=0xED4245
        )
        await ctx.reply(embed=err)
        return

    async with get_db() as db:
        async with db.execute("SELECT * FROM vms WHERE owner_id = ?", (target.id,)) as cur:
            records = await cur.fetchall()

    if not records:
        empty = discord.Embed(
            title=f"{E_INFO} Virtual Machine Inventory",
            description=f"{E_WARN} No virtual machines assigned to {target.mention}.",
            color=0x2B2D31
        )
        await ctx.reply(embed=empty)
        return

    if len(records) == 1:
        embed = await build_channel_vm_embed(target, records[0])
        view = VMControlView(records[0][0], target.id, records[0][2])
        await ctx.reply(embed=embed, view=view)
    else:
        picker = VMPickerView(records, target)
        select_embed = discord.Embed(
            title=f"{E_KING_CROWN} {NODE_NAME} • Select Active Machine",
            description=f"{E_ARROW} {target.mention} owns **{len(records)}** active VMs.\n"
                        f"{E_STAR} Choose a virtual machine from the dropdown below to open controls.",
            color=0x5865F2
        )
        await ctx.reply(embed=select_embed, view=picker)

# ----------------- POWER CONTROLS -----------------
@bot.command(name="restart")
async def restart_cmd(ctx, vm_id: int):
    async with get_db() as db:
        async with db.execute("SELECT owner_id, container_name FROM vms WHERE vm_id = ?", (vm_id,)) as cur:
            row = await cur.fetchone()

    if not row or (ctx.author.id != row[0] and not await is_admin(ctx.author.id)):
        return await ctx.reply(f"{E_NO} Permission denied or VM not found.")

    loading = discord.Embed(
        title=f"{E_LOADING} Power Cycling Instance",
        description=f"{E_ARROW} Rebooting `{row[1]}` (VM #{vm_id})...",
        color=0xFEE75C
    )
    msg = await ctx.reply(embed=loading)

    try:
        container = docker_client.containers.get(row[1])
        container.restart(timeout=5)
        success = discord.Embed(
            title=f"{E_CHECK} Machine Rebooted",
            description=f"{E_YES} **VM #{vm_id}** (`{row[1]}`) has been rebooted cleanly.",
            color=0x57F287
        )
        await msg.edit(embed=success)
    except Exception as e:
        await msg.edit(content=f"{E_NO} Reboot failed: `{str(e)}`")

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

    loading = discord.Embed(
        title=f"{E_LOADING} Reinstalling Operating System",
        description=f"{E_ARROW} Re-imaging root disk volume for `{c_name}`...\n"
                    f"{E_GEAR} Preserving assigned network ports and credentials...",
        color=0xFEE75C
    )
    msg = await ctx.reply(embed=loading)

    loop = asyncio.get_running_loop()
    try:
        new_id = await loop.run_in_executor(None, launch_vm_container, c_name, ram, cpu, disk, vnc_port, ssh_port, vnc_pass, root_pass)
        async with get_db() as db:
            await db.execute("UPDATE vms SET container_id = ? WHERE vm_id = ?", (new_id, vm_id))
            await db.commit()

        success = discord.Embed(
            title=f"{E_CHECK} Reinstallation Completed",
            description=f"{E_YES} **VM #{vm_id}** has been cleanly re-imaged with Ubuntu 24!\n"
                        f"Use `!terminal {vm_id}` to access your shell.",
            color=0x57F287
        )
        await msg.edit(embed=success)
    except Exception as e:
        await msg.edit(content=f"{E_NO} Reinstall failed: `{str(e)}`")

# ----------------- DELETION COMMAND -----------------
@bot.command(name="delete")
async def delete_vm(ctx, target: Union[int, discord.User]):
    if not await is_admin(ctx.author.id):
        return

    del_progress = discord.Embed(
        title=f"{E_LOADING} Processing Cluster Wipe",
        description=f"{E_ARROW} Terminating container slices and wiping storage volumes...",
        color=0xFEE75C
    )
    msg = await ctx.reply(embed=del_progress)

    async with get_db() as db:
        if isinstance(target, int):
            async with db.execute("SELECT owner_id, container_name FROM vms WHERE vm_id = ?", (target,)) as cur:
                row = await cur.fetchone()
            
            if not row:
                not_found = discord.Embed(
                    title=f"{E_NO} Wipe Error",
                    description=f"{E_WARN} Virtual machine with ID `#{target}` does not exist.",
                    color=0xED4245
                )
                await msg.edit(embed=not_found)
                return

            owner_id, c_name = row
            try:
                docker_client.containers.get(c_name).remove(force=True)
            except Exception:
                pass
            try:
                docker_client.volumes.get(f"{c_name}-data").remove(force=True)
            except Exception:
                pass

            await db.execute("DELETE FROM vms WHERE vm_id = ?", (target,))
            await db.commit()

            await revoke_client_role_if_empty(owner_id)

            success = discord.Embed(
                title=f"{E_CHECK} Machine Purged",
                description=(
                    f"{E_YES} **VM #{target} (`{c_name}`) wiped cleanly from cluster.**\n\n"
                    f"{E_ARROW} **Owner:** <@{owner_id}>\n"
                    f"{E_STAR} Storage unmounted and customer roles verified."
                ),
                color=0xED4245,
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            await msg.edit(embed=success)

        elif isinstance(target, discord.User):
            async with db.execute("SELECT vm_id, container_name FROM vms WHERE owner_id = ?", (target.id,)) as cur:
                rows = await cur.fetchall()

            if not rows:
                not_found = discord.Embed(
                    title=f"{E_NO} Wipe Error",
                    description=f"{E_WARN} No virtual machines found for user {target.mention}.",
                    color=0xED4245
                )
                await msg.edit(embed=not_found)
                return

            deleted_count = 0
            for vm_id, c_name in rows:
                try:
                    docker_client.containers.get(c_name).remove(force=True)
                except Exception:
                    pass
                try:
                    docker_client.volumes.get(f"{c_name}-data").remove(force=True)
                except Exception:
                    pass
                deleted_count += 1

            await db.execute("DELETE FROM vms WHERE owner_id = ?", (target.id,))
            await db.commit()

            await revoke_client_role_if_empty(target.id)

            success = discord.Embed(
                title=f"{E_CHECK} Member Allocation Wiped",
                description=(
                    f"{E_YES} **All {deleted_count} VM(s) owned by {target.mention} have been terminated.**\n\n"
                    f"{E_ARROW} **Storage:** Unmounted & purged\n"
                    f"{E_DOWN} Client role <@&{CLIENT_ROLE_ID}> was stripped."
                ),
                color=0xED4245,
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            await msg.edit(embed=success)

# ----------------- LEASE & EXPIRATION COMMANDS -----------------
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
    renew_embed = discord.Embed(
        title=f"{E_CHECK} Subscription Renewed (+28 Days)",
        description=f"{E_STAR} Extended **{len(records)}** instance(s) for {target.mention}!\n"
                    f"{E_ARROW} Role <@&{CLIENT_ROLE_ID}> active.",
        color=0x57F287,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    await ctx.reply(embed=renew_embed)

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

    exp_embed = discord.Embed(
        title=f"{E_GEAR} Expiration Schedule Adjusted",
        description=f"{E_CHECK} Updated expiration for {target.mention} to `{new_expiry}`.",
        color=0x5865F2,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    await ctx.reply(embed=exp_embed)

# ----------------- ADMIN COMMANDS -----------------
@bot.command(name="setadmin", aliases=["giveadmin"])
async def set_admin(ctx, target: discord.User):
    if not await is_admin(ctx.author.id):
        return
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (target.id,))
        await db.commit()

    admin_embed = discord.Embed(
        title=f"{E_KING_CROWN} Privilege Escalation",
        description=f"{E_CHECK} {E_YES} {target.mention} added to cluster administrators.",
        color=0x57F287,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    await ctx.reply(embed=admin_embed)

@bot.command(name="removeadmin")
async def remove_admin(ctx, target: discord.User):
    if not await is_admin(ctx.author.id) or target.id == MAIN_OWNER_ID:
        return
    async with get_db() as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (target.id,))
        await db.commit()

    admin_embed = discord.Embed(
        title=f"{E_WARN} Privilege Revocation",
        description=f"{E_CHECK} Administrator access revoked for {target.mention}.",
        color=0xED4245,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    await ctx.reply(embed=admin_embed)

# ----------------- REGISTRY & TELEMETRY -----------------
@bot.command(name="vinfo")
async def vinfo_prefix(ctx):
    if not await is_admin(ctx.author.id):
        return

    embed = discord.Embed(
        title=f"{E_KING_CROWN} {NODE_NAME} • Enterprise Host Cluster Telemetry",
        description=f"{E_LIGHTNING} **Node:** `tempest-tier1-master-01.dc-node.net`\n{E_STAR} **Hypervisor:** `Linux 6.8.0-40-generic x86_64` | `KVM Enabled`",
        color=0x5865F2,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(
        name=f"{E_THUNDER} Primary Processing Array",
        value=(
            f"{E_ARROW} **Processor:** `Dual AMD EPYC™ 9654 (128 Cores / 256 Threads @ 3.70 GHz)`\n"
            f"{E_ARROW} **Base Clock:** `2.40 GHz` | **Max Boost:** `3.70 GHz`\n"
            f"{E_ARROW} **Cluster Load:** `14.2%` [██░░░░░░░░░░░░░░░░░░]"
        ),
        inline=False
    )
    embed.add_field(
        name=f"{E_GEAR} DDR5 ECC Registered Memory",
        value=(
            f"{E_ARROW} **Allocated/Total:** `42.8 GB / 500.0 GB` (8.5%)\n"
            f"{E_ARROW} **Free Buffer:** `457.2 GB Available`\n"
            f"{E_ARROW} **Utilization:** [██░░░░░░░░░░░░░░░░░░]"
        ),
        inline=False
    )
    embed.add_field(
        name=f"{E_FIRE} Enterprise NVMe Storage Fabric",
        value=(
            f"{E_ARROW} **Pool Allocation:** `112.4 GB / 10,000.0 GB (10 TB)` (1.1%)\n"
            f"{E_ARROW} **Available Space:** `9,887.6 GB Free`\n"
            f"{E_ARROW} **Storage RAID:** `RAID-10 NVMe PCIe 5.0 (64 Gbps)`"
        ),
        inline=False
    )
    embed.set_footer(text=f"{NODE_NAME} • Tier-4 Datacenter Facilities", icon_url=bot.user.display_avatar.url)
    await ctx.reply(embed=embed)

@bot.command(name="vminfo")
async def vminfo_all(ctx):
    if not await is_admin(ctx.author.id):
        return

    async with get_db() as db:
        async with db.execute("SELECT vm_id, owner_id, container_name, container_id, vnc_port, ssh_port, ram, cpu, disk, expires_at FROM vms") as cur:
            records = await cur.fetchall()

    if not records:
        empty = discord.Embed(
            title=f"{E_INFO} Virtual Machine Registry",
            description=f"{E_WARN} No active virtual machines on cluster.",
            color=0x2B2D31
        )
        await ctx.reply(embed=empty)
        return

    embed = discord.Embed(
        title=f"{E_KING_CROWN} {NODE_NAME} • Global Virtual Machine Registry",
        description=f"{E_STAR} **Active Provisioned Slices:** `{len(records)}`",
        color=0x2B2D31,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )

    for r in records:
        vm_id, oid, c_name, cid, vnc, ssh, ram, cpu, disk, exp = r
        stats = get_container_stats(c_name)
        icon = E_ONLINE if stats["online"] else E_OFFLINE
        embed.add_field(
            name=f"{icon} VM #{vm_id}: `{c_name}`",
            value=(
                f"{E_ARROW} **Owner:** <@{oid}>\n"
                f"{E_ARROW} **Hardware:** `{cpu} vCPU` | `{ram}G RAM` | `{disk}G NVMe`\n"
                f"{E_ARROW_DOUBLE} **Ports:** `SSH {ssh}` | `VNC {vnc}`\n"
                f"{E_ARROW_DOUBLE} **Expires:** `{exp}`"
            ),
            inline=False
        )

    await ctx.reply(embed=embed)

# ----------------- MONITORS -----------------
@tasks.loop(seconds=20)
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
                    try:
                        docker_client.volumes.get(f"{c_name}-data").remove(force=True)
                    except Exception:
                        pass

                    async with get_db() as db:
                        await db.execute("DELETE FROM vms WHERE vm_id = ?", (vm_id,))
                        await db.commit()

                    await revoke_client_role_if_empty(owner_id)

                    channel = bot.get_channel(ALERT_CHANNEL_ID)
                    if channel:
                        alert = discord.Embed(
                            title=f"{E_WARN} CRYPTO MINING PURGE DETECTED",
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
                        try:
                            docker_client.containers.get(c_name).remove(force=True)
                        except Exception:
                            pass
                        try:
                            docker_client.volumes.get(f"{c_name}-data").remove(force=True)
                        except Exception:
                            pass
                        await db.execute("DELETE FROM vms WHERE vm_id = ?", (vm_id,))
                        await db.commit()

                        await revoke_client_role_if_empty(owner_id)

                        user = bot.get_user(owner_id)
                        if user:
                            try:
                                exp_embed = discord.Embed(
                                    title=f"{E_WARN} VM Lease Expired",
                                    description=f"Your virtual machine lease for `{c_name}` (VM #{vm_id}) on **{NODE_NAME}** has expired and was decommissioned.",
                                    color=0xED4245
                                )
                                await user.send(embed=exp_embed)
                            except Exception:
                                pass
                except Exception:
                    continue
    except Exception:
        pass

# ----------------- ENTRYPOINT -----------------
if __name__ == "__main__":
    bot.run(TOKEN)
