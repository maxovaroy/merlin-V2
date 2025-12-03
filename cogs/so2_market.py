# cogs/so2_market.py
"""
SO2 Market Cog (manual-only)
----------------------------
Fully featured manual market cog for Standoff 2.

Commands:
  Prefix:
    !price <skin>        - Show single skin
    !listskins [page]    - Paginated list of skins
    !skins [page]        - Alias for !listskins
    !findskin <term>     - Search skins
    !setprice <skin> <price> - Admin only

  Slash:
    /price
    /listskins
    /findskin
    /setprice
"""

import re
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import commands
from discord import app_commands

# ---------------------------
# Config
# ---------------------------
ALLOWED_ROLE_IDS = {1259587539212173375, 1401443966011969686, 1205431837732900904}
SKINS_PER_PAGE = 6
_IMAGE_RE = re.compile(r"^https?://.*\.(?:png|jpg|jpeg|webp|gif)$", re.IGNORECASE)

SKINS: Dict[str, Dict[str, object]] = {

    "Cosmo STREAM CRATE": {
        "price": 44.00, "image_url": "https://i.postimg.cc/7LDZv1Cf/1000240574.png",
        "rarity": "Common", "category": "Case",
    },
    "PawPaw STREAM CRATE": {
        "price": 17.92, "image_url": "https://i.postimg.cc/nMbt4ZBT/1000240575.png",
        "rarity": "Common", "category": "Case",
    },
    "Ultimate 8 YEAR GIFT CASE": {
        "price": 2479.97, "image_url": "https://i.postimg.cc/x89CKvQC/1000240567.png",
        "rarity": "Nameless", "category": "Case",
    },
    "Prime 8 YEAR GIFT CASE": {
        "price": 69.99, "image_url": "https://i.postimg.cc/rF399sdd/1000240568.png",
        "rarity": "Nameless", "category": "Case",
    },
    "Great 8 YEAR GIFT CASE": {
        "price": 36.95, "image_url": "https://i.postimg.cc/PJNmWnTN/1000240569.png",
        "rarity": "Nameless", "category": "Case",
    },
}

# ---------------------------
# Helper functions
# ---------------------------
def normalize_name(name: str) -> str:
    return name.strip().lower()

def find_skin_by_name(name: str) -> Optional[str]:
    target = normalize_name(name)
    for key in SKINS.keys():
        if normalize_name(key) == target:
            return key
    return None

def find_partial_matches(term: str, limit: int = 10) -> List[str]:
    t = normalize_name(term)
    return [k for k in SKINS.keys() if t in normalize_name(k)][:limit]

def build_price_embed(skin_name: str) -> discord.Embed:
    data = SKINS[skin_name]
    price = data.get("price", 0)
    rarity = data.get("rarity", "Unknown")
    category = data.get("category", "Misc")
    image = data.get("image_url")

    rarity_colors = {
        "common": 0x95A5A6,
        "uncommon": 0x2ECC71,
        "rare": 0x3498DB,
        "epic": 0x9B59B6,
        "legendary": 0xE67E22,
        "nameless": 0xffac21,
    }
    color = rarity_colors.get(rarity.lower(), 0x5865F2)

    embed = discord.Embed(
        title=f"{skin_name}",
        description=f"🟡 **Price:** `{price}` gold\n⭐ **Rarity:** `{rarity}`\n📦 **Category:** `{category}`",
        color=color,
    )
    if image:
        embed.set_image(url=image)
    embed.set_footer(text="Standoff 2 • Skin Prices NOTE: Skins prices gets updated everyday its not automatic and does not show real time price")
    return embed

def build_list_page_embed(page_items: List[Tuple[str, Dict]], page: int, total_pages: int) -> discord.Embed:
    embed = discord.Embed(
        title="Standoff 2 Market — Skins",
        description=f"Page {page}/{total_pages} • {len(SKINS)} total skins",
        color=discord.Color.gold()
    )
    for name, data in page_items:
        embed.add_field(
            name=name,
            value=f"💰 {data.get('price',0)} • ⭐ {data.get('rarity','Unknown')} • 📦 {data.get('category','Misc')}",
            inline=False
        )
    embed.set_footer(text="Use buttons or /listskins <page> to navigate")
    return embed

def chunk_list(items: List[Tuple[str, Dict]], chunk_size: int) -> List[List[Tuple[str, Dict]]]:
    return [items[i:i+chunk_size] for i in range(0, len(items), chunk_size)]

def user_has_edit_role(member: discord.Member) -> bool:
    return any(role.id in ALLOWED_ROLE_IDS for role in getattr(member, "roles", []))

# ---------------------------
# Paginator
# ---------------------------
class MarketPaginator(discord.ui.View):
    def __init__(self, pages: List[discord.Embed], author_id: Optional[int] = None, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.author_id = author_id
        self.index = 0

        self.prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.primary)
        self.next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.primary)
        self.first_btn = discord.ui.Button(label="|<<", style=discord.ButtonStyle.secondary)
        self.last_btn = discord.ui.Button(label=">>|", style=discord.ButtonStyle.secondary)
        self.page_info = discord.ui.Button(label=self.page_label(), style=discord.ButtonStyle.secondary, disabled=True)

        self.prev_btn.callback = self._prev
        self.next_btn.callback = self._next
        self.first_btn.callback = self._first
        self.last_btn.callback = self._last

        self.add_item(self.first_btn)
        self.add_item(self.prev_btn)
        self.add_item(self.page_info)
        self.add_item(self.next_btn)
        self.add_item(self.last_btn)

    def page_label(self) -> str:
        return f"Page {self.index + 1}/{len(self.pages)}"

    async def _update(self, interaction: discord.Interaction):
        self.page_info.label = self.page_label()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def _prev(self, interaction: discord.Interaction):
        if self.author_id and interaction.user.id != self.author_id:
            return await interaction.response.send_message("Only author can control.", ephemeral=True)
        self.index = (self.index - 1) % len(self.pages)
        await self._update(interaction)

    async def _next(self, interaction: discord.Interaction):
        if self.author_id and interaction.user.id != self.author_id:
            return await interaction.response.send_message("Only author can control.", ephemeral=True)
        self.index = (self.index + 1) % len(self.pages)
        await self._update(interaction)

    async def _first(self, interaction: discord.Interaction):
        if self.author_id and interaction.user.id != self.author_id:
            return await interaction.response.send_message("Only author can control.", ephemeral=True)
        self.index = 0
        await self._update(interaction)

    async def _last(self, interaction: discord.Interaction):
        if self.author_id and interaction.user.id != self.author_id:
            return await interaction.response.send_message("Only author can control.", ephemeral=True)
        self.index = len(self.pages) - 1
        await self._update(interaction)

# ---------------------------
# Cog
# ---------------------------
class SO2MarketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._sorted_items = sorted(SKINS.items(), key=lambda kv: kv[0].lower())

    # -----------------------
    # Prefix commands
    # -----------------------
    @commands.command(name="price")
    async def price_cmd(self, ctx: commands.Context, *, name: str):
        key = find_skin_by_name(name)
        if not key:
            matches = find_partial_matches(name, limit=6)
            if not matches:
                return await ctx.send(f"❌ No skin found matching `{name}`")
            lines = [f"- {m} ({SKINS[m]['price']} coins)" for m in matches]
            return await ctx.send(f"❌ Did you mean:\n" + "\n".join(lines))
        await ctx.send(embed=build_price_embed(key))

    @commands.command(name="listskins", aliases=["skins"])
    async def lists_cmd(self, ctx: commands.Context, page: Optional[int] = 1):
        items = self._sorted_items
        pages = chunk_list(items, SKINS_PER_PAGE)
        total_pages = max(1, len(pages))
        page_index = min(max(page - 1, 0), total_pages - 1)
        embed_pages = [build_list_page_embed(chunk, i+1, total_pages) for i, chunk in enumerate(pages)]
        paginator = MarketPaginator(embed_pages, author_id=ctx.author.id)
        await ctx.send(embed=embed_pages[page_index], view=paginator)

    @commands.command(name="findskin")
    async def find_cmd(self, ctx: commands.Context, *, query: str):
        matches = find_partial_matches(query, limit=20)
        if not matches:
            return await ctx.send(f"❌ No skins matching `{query}`")
        lines = [f"- **{m}** — {SKINS[m]['price']} coins • {SKINS[m]['rarity']}" for m in matches]
        await ctx.send("🔎 Matches:\n" + "\n".join(lines))

    @commands.command(name="setprice")
    async def setprice_cmd(self, ctx: commands.Context, skin_name: str, price: int):
        if not user_has_edit_role(ctx.author):
            return await ctx.send("❌ You don't have permission.")
        key = find_skin_by_name(skin_name)
        if not key:
            return await ctx.send(f"❌ Skin `{skin_name}` not found.")
        old = SKINS[key]["price"]
        SKINS[key]["price"] = price
        self._sorted_items = sorted(SKINS.items(), key=lambda kv: kv[0].lower())
        embed = discord.Embed(title="✅ Price Updated", color=discord.Color.green())
        embed.add_field(name="Skin", value=key, inline=True)
        embed.add_field(name="Old Price", value=str(old), inline=True)
        embed.add_field(name="New Price", value=str(price), inline=True)
        await ctx.send(embed=embed)

    # -----------------------
    # Slash commands
    # -----------------------
    @app_commands.command(name="price", description="Show price & info for a skin")
    @app_commands.describe(name="skin name")
    async def slash_price(self, interaction: discord.Interaction, name: str):
        key = find_skin_by_name(name)
        if not key:
            matches = find_partial_matches(name, limit=6)
            if not matches:
                return await interaction.response.send_message(f"❌ No skin `{name}`", ephemeral=True)
            return await interaction.response.send_message(
                "❌ Did you mean:\n" + "\n".join(f"- {m}" for m in matches),
                ephemeral=True
            )
        await interaction.response.send_message(embed=build_price_embed(key))

    @slash_price.autocomplete("name")
    async def price_autocomplete(self, interaction: discord.Interaction, current: str):
        choices = [app_commands.Choice(name=k, value=k) for k in SKINS.keys() if current.lower() in k.lower()]
        return choices[:20]

    @app_commands.command(name="listskins", description="Paginated list of skins")
    async def slash_list(self, interaction: discord.Interaction):
        items = self._sorted_items
        pages = chunk_list(items, SKINS_PER_PAGE)
        total_pages = max(1, len(pages))
        embed_pages = [build_list_page_embed(chunk, i+1, total_pages) for i, chunk in enumerate(pages)]
        paginator = MarketPaginator(embed_pages, author_id=interaction.user.id)
        await interaction.response.send_message(embed=embed_pages[0], view=paginator)

    @app_commands.command(name="findskin", description="Search skins by keyword")
    async def slash_find(self, interaction: discord.Interaction, query: str):
        matches = find_partial_matches(query, limit=30)
        if not matches:
            return await interaction.response.send_message(f"❌ No matches for `{query}`", ephemeral=True)
        lines = [f"- **{m}** — {SKINS[m]['price']} • {SKINS[m]['rarity']}" for m in matches]
        await interaction.response.send_message("🔎 Matches:\n" + "\n".join(lines), ephemeral=True)

    @app_commands.command(name="setprice", description="(Admin) Change price for a skin")
    @app_commands.describe(skin="Skin name", price="New price")
    async def slash_setprice(self, interaction: discord.Interaction, skin: str, price: int):
        if not user_has_edit_role(interaction.user):
            return await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        key = find_skin_by_name(skin)
        if not key:
            return await interaction.response.send_message(f"❌ Skin `{skin}` not found.", ephemeral=True)
        old = SKINS[key]["price"]
        SKINS[key]["price"] = price
        self._sorted_items = sorted(SKINS.items(), key=lambda kv: kv[0].lower())
        embed = discord.Embed(title="✅ Price Updated", color=discord.Color.green())
        embed.add_field(name="Skin", value=key, inline=True)
        embed.add_field(name="Old Price", value=str(old), inline=True)
        embed.add_field(name="New Price", value=str(price), inline=True)
        await interaction.response.send_message(embed=embed)

    @slash_setprice.autocomplete("skin")
    async def setprice_autocomplete(self, interaction: discord.Interaction, current: str):
        choices = [app_commands.Choice(name=k, value=k) for k in SKINS.keys() if current.lower() in k.lower()]
        return choices[:20]

# ---------------------------
# Setup
# ---------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(SO2MarketCog(bot))
