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
import socket
from typing import Optional, Union

# ----------------- CONFIGURATION -----------------
TOKEN = "YOUR_DISCORD_BOT_TOKEN_HERE"
NODE_NAME = "TEMPEST CLOUD"

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
E_KING_CROWN = "<a:King_crown:1519766073560403990>"
E_BOT_TAG = "<:bot_tag:1519559016013889647>"
E_INFO = "<:Information:1545028101501747230>"

OWNER_REACTIONS = [
    "arrow:1519556677173510325",
    "1_crown:1519585072687222936",
    "leftarrow:1519585449713074226"
]

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
docker_client = docker.from_env()

# ----------------- DATABASE -----------------
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

async def grant_client_role(user_id: int):
    for guild in bot.guilds:
        member = guild.get_member(user_id)
        if member:
            role = guild.get_role(CLIENT_ROLE_ID)
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Assigned active VM")
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
                        await member.remove_roles(role, reason="All VMs removed")
                    except Exception:
                        pass

# ----------------- DOCKER ENGINE -----------------
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

# ----------------- 100% RELIABLE SSHX ENGINE -----------------
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def clean_output(raw: str) -> str:
    return ANSI_ESCAPE.sub('', raw)

async def get_or_create_sshx_link(container_name: str) -> Optional[str]:
    loop = asyncio.get_running_loop()

    def sync_sshx():
        try:
            container = docker_client.containers.get(container_name)
            if container.status != "running":
                return None

            # 1. First check if an existing session is running and valid
            ps_check = container.exec_run("pgrep -x sshx")
            if ps_check.exit_code == 0:
                cat_res = container.exec_run("cat /tmp/sshx.log 2>/dev/null")
                cleaned = clean_output(cat_res.output.decode("utf-8", errors="ignore"))
                match = re.search(r"https://sshx\.io/s/[A-Za-z0-9#_\-]+", cleaned)
                if match:
                    return match.group(0)

            # 2. Prepare container dependencies (curl, ca-certs)
            setup_cmd = (
                "sh -c '"
                "if command -v apk >/dev/null 2>&1; then apk add --no-cache curl ca-certificates util-linux >/dev/null 2>&1; "
                "elif command -v apt-get >/dev/null 2>&1; then apt-get update -qq && apt-get install -y -qq curl ca-certificates bsdmainutils >/dev/null 2>&1; fi'"
            )
            container.exec_run(setup_cmd)

            # 3. Ensure sshx binary is installed in standard path
            install_cmd = (
                "sh -c '"
                "if ! command -v sshx >/dev/null 2>&1 && [ ! -f /usr/local/bin/sshx ]; then "
                "  curl -sSf https://sshx.io/get | sh -s -- -y >/dev/null 2>&1; "
                "  cp ~/.local/bin/sshx /usr/local/bin/sshx 2>/dev/null || true; "
                "fi'"
            )
            container.exec_run(install_cmd)

            # 4. Clean old logs and run sshx cleanly detached with PTY
            container.exec_run("sh -c 'pkill -9 -x sshx 2>/dev/null; rm -f /tmp/sshx.log'")

            daemon_cmd = (
                "sh -c '"
                "export PATH=$PATH:/root/.local/bin:/usr/local/bin; "
                "nohup script -q -c \"sshx\" /tmp/sshx.log >/dev/null 2>&1 &'"
            )
            container.exec_run(daemon_cmd)

            # 5. Poll log for generated URL
            for _ in range(20):
                time.sleep(0.3)
                cat_res = container.exec_run("cat /tmp/sshx.log")
                raw_out = cat_res.output.decode("utf-8", errors="ignore")
                cleaned = clean_output(raw_out)

                match = re.search(r"https://sshx\.io/s/[A-Za-z0-9#_\-]+", cleaned)
                if match:
                    return match.group(0)

            # Fallback direct pipe attempt
            direct_run = container.exec_run("sh -c 'export PATH=$PATH:/root/.local/bin:/usr/local/bin; timeout 5 sshx 2>&1'")
            raw_out = direct_run.output.decode("utf-8", errors="ignore")
            match = re.search(r"https://sshx\.io/s/[A-Za-z0-9#_\-]+", clean_output(raw_out))
            if match:
                return match.group(0)

            return None
        except Exception as e:
            print(f"[SSHX Worker Error]: {e}")
            return None

    return await loop.run_in_executor(None, sync_sshx)

# ----------------- UI BUTTON VIEW -----------------
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
                description=f"{E_WARN} You do not have permission to manage this instance.",
                color=0xED4245
            ),
            ephemeral=True
        )
        return False

    @discord.ui.button(label="Web Terminal", style=discord.ButtonStyle.primary, emoji="⚡")
    async def terminal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        url = await get_or_create_sshx_link(self.container_name)
        if url:
            embed = discord.Embed(
                title=f"{E_LIGHTNING} Encrypted SSHX Terminal Ready",
                description=(
                    f"{E_STAR} **Console session for VM #{self.vm_id}**\n\n"
                    f"🔗 **[Click Here to Launch Web Console]({url})**\n\n"
                    f"```text\n{url}\n```"
                ),
                color=0x7952FF
            )
            embed.set_footer(text="Multiplayer shared sessions & mobile keyboard supported.")
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(f"{E_NO} Failed to spawn terminal relay. Ensure container is active.", ephemeral=True)

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.secondary, emoji="🔁")
    async def restart_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            container = docker_client.containers.get(self.container_name)
            container.restart(timeout=5)
            await interaction.followup.send(f"{E_CHECK} **VM #{self.vm_id}** restarted successfully.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"{E_NO} Restart failed: `{str(e)}`", ephemeral=True)

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
                description=f"{E_YES} **VM #{self.vm_id} cleanly re-imaged with Ubuntu 24!**\nUse `!terminal {self.vm_id}` to open console.",
                color=0x57F287
            )
            await interaction.edit_original_response(embed=success)
        except Exception as e:
            await interaction.edit_original_response(content=f"{E_NO} Reinstall failed: `{str(e)}`")

# ----------------- COMMANDS -----------------
@bot.event
async def on_ready():
    await init_db()
    expiry_check_loop.start()
    anti_mining_monitor.start()
    print(f"Nova Cloud Bot Online • Engine Active on {NODE_NAME}")

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

    status_msg = await ctx.reply(embed=discord.Embed(
        title=f"{E_LOADING} Initializing Virtual Machine",
        description=f"{E_ARROW} Provisioning `{cpu} vCPU` | `{ram} GB RAM` | `{disk} GB NVMe` for {user.mention}...",
        color=0xFEE75C
    ))

    try:
        vnc_port = get_free_port(6080)
        ssh_port = get_free_port(2026)
        root_pass = gen_password(12)
        vnc_pass = gen_password(8)
        created_at = datetime.datetime.now(datetime.timezone.utc)
        expires_at = (created_at + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S UTC")

        async with get_db() as db:
            cur = await db.execute("""
                INSERT INTO vms (owner_id, container_name, container_id, vnc_port, ssh_port, ram, cpu, disk, root_pass, vnc_pass, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user.id, "pending", "pending", vnc_port, ssh_port, ram, cpu, disk, root_pass, vnc_pass, created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), expires_at))
            vm_id = cur.lastrowid
            await db.commit()

        c_name = f"atyro-vm-{user.id}-{vm_id}"
        loop = asyncio.get_running_loop()
        cid = await loop.run_in_executor(None, launch_vm_container, c_name, ram, cpu, disk, vnc_port, ssh_port, vnc_pass, root_pass)

        async with get_db() as db:
            await db.execute("UPDATE vms SET container_name = ?, container_id = ? WHERE vm_id = ?", (c_name, cid, vm_id))
            await db.commit()

        await grant_client_role(user.id)

        # Generate instant sshx link for direct DM
        sshx_url = await get_or_create_sshx_link(c_name)
        terminal_link_text = f"[Click Here to Open Shell]({sshx_url})" if sshx_url else "Use `!terminal` to generate"

        dm_embed = discord.Embed(
            title=f"{E_KING_CROWN} {NODE_NAME} • Instance Credentials",
            description=f"{E_STAR} **Your virtual machine (`VM #{vm_id}`) has been provisioned!**",
            color=0x7952FF,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        dm_embed.add_field(
            name="⚡ Instant Web Console",
            value=f"{E_ARROW} **Link:** {terminal_link_text}\n{E_LIGHTNING} *Direct zero-latency browser terminal.*",
            inline=False
        )
        dm_embed.add_field(
            name="🔑 Direct SSH / VNC Info",
            value=f"{E_ARROW} **Port:** `{ssh_port}`\n{E_ARROW} **Root Password:** `{root_pass}`\n{E_ARROW} **VNC Port:** `{vnc_port}`",
            inline=False
        )
        dm_embed.add_field(
            name="⚙️ Hardware Specifications",
            value=f"{E_ARROW_DOUBLE} **Processor:** `{cpu} vCPU Core(s)`\n{E_ARROW_DOUBLE} **Memory:** `{ram} GB Dedicated DDR5`\n{E_ARROW_DOUBLE} **Storage:** `{disk} GB NVMe PCIe 5.0`\n{E_ARROW_DOUBLE} **Expires:** `{expires_at}` ({days} Days)",
            inline=False
        )
        dm_embed.set_footer(text=f"{NODE_NAME} • Client Vault", icon_url=bot.user.display_avatar.url)

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
            value=f"{E_ARROW} **Instance ID:** `VM #{vm_id}` (`{cid[:12]}`)\n{E_ARROW} **Resources:** `{cpu} vCPU` | `{ram} GB RAM` | `{disk} GB NVMe`\n{E_ARROW} **Ports:** SSH `{ssh_port}` | VNC `{vnc_port}`\n{E_ARROW} **Expires:** `{expires_at}`",
            inline=False
        )
        embed.add_field(
            name="🌐 Console Access",
            value=(f"{E_CHECK} *Credentials & web terminal link delivered to user's DMs.*"
                   if dm_delivered else
                   f"{E_WARN} *DMs closed! Use `!terminal {vm_id}` to retrieve link.*"),
            inline=False
        )
        view = VMControlView(vm_id, user.id, c_name)
        await status_msg.edit(content=None, embed=embed, view=view)

    except Exception as e:
        await status_msg.edit(embed=discord.Embed(title=f"{E_NO} Provisioning Error", description=f"```{str(e)}```", color=0xED4245))

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
        return await ctx.reply(embed=discord.Embed(title=f"{E_NO} Unauthorized", description="You do not own this machine.", color=0xED4245))

    load = await ctx.reply(f"{E_LOADING} Initializing secure encrypted terminal relay...")
    url = await get_or_create_sshx_link(c_name)

    if url:
        embed = discord.Embed(
            title=f"{E_LIGHTNING} Encrypted Web Console Ready",
            description=(
                f"{E_STAR} **Active session for VM #{target_id}**\n\n"
                f"🔗 **[Click Here to Open Console in Browser]({url})**\n\n"
                f"```text\n{url}\n```"
            ),
            color=0x7952FF,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_footer(text="Multiplayer shared sessions & mobile keyboard supported.")
        await load.edit(content=None, embed=embed)
    else:
        await load.edit(content=f"{E_NO} Failed to spawn SSHX relay. Ensure container is active.")

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
        description=f"{E_STAR} **Resource allocation for {target.mention}**",
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

# Lifecycle Controls
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
        await load.edit(content=f"{E_CHECK} **VM #{vm_id}** cleanly re-imaged with Ubuntu 24.")
    except Exception as e:
        await load.edit(content=f"{E_NO} Reinstall failed: `{str(e)}`")

@bot.command(name="restart")
async def restart_vm(ctx, vm_id: int):
    async with get_db() as db:
        async with db.execute("SELECT owner_id, container_name FROM vms WHERE vm_id = ?", (vm_id,)) as cur:
            row = await cur.fetchone()

    if not row or (ctx.author.id != row[0] and not await is_admin(ctx.author.id)):
        return await ctx.reply(f"{E_NO} Permission denied or VM not found.")

    msg = await ctx.reply(f"{E_LOADING} Restarting instance...")
    try:
        docker_client.containers.get(row[1]).restart(timeout=5)
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
        await ctx.reply(f"{E_CHECK} **VM #{vm_id}** is now online.")
    except Exception as e:
        await ctx.reply(f"{E_NO} Error starting container: `{str(e)}`")

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
    await ctx.reply(embed=discord.Embed(title=f"{E_CHECK} Subscriptions Extended (+28 Days)", description=f"Extended {len(records)} instance(s) for {target.mention}.", color=0x57F287))

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
            await load.edit(content=f"{E_CHECK} VM **#{target}** removed from hypervisor.")
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
            await load.edit(content=f"{E_CHECK} Purged all **{len(rows)}** instance(s) owned by {target.mention}.")

# Admin Permissions
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

# Telemetry Commands
@bot.command(name="vinfo")
async def vinfo_cmd(ctx):
    if not await is_admin(ctx.author.id):
        return
    embed = discord.Embed(
        title=f"{E_KING_CROWN} {NODE_NAME} • Cluster Telemetry",
        description=f"{E_STAR} **Node:** `tempest-tier1-master-01.dc-node.net`",
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
                            description=f"Signature `{detected}` found on `{c_name}` (VM #{vm_id}). Container purged.",
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

# ----------------- RUN -----------------
if __name__ == "__main__":
    bot.run(TOKEN)
