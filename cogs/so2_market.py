# cogs/so2_market.py
"""
SO2 Market Cog (manual-only)
----------------------------
Place this file in your repo at: cogs/so2_market.py

- All skins are stored manually in the `SKINS` dict below.
- Metadata per skin: price, image_url, rarity, category.
- Commands:
    Prefix commands (useful if you prefer !):
      - !price <skin name>       -> show single skin (case-insensitive)
      - !listskins [page]        -> paginated list of skins
      - !findskin <term>         -> search skins by partial name
      - !setprice <skin> <price> -> admin only (role-IDs below)
    Slash commands (registered when cog is loaded):
      - /price name:<skin>
      - /listskins
      - /findskin query:<term>
      - /setprice skin:<skin> price:<int>  (admin only)

- Admins are checked by role ID (ALLOWED_ROLE_IDS).
  Replace or add IDs if needed.

- This cog intentionally does NOT write to the DB. All edits are done
  by editing this file and reloading the cog (or restarting the bot).
"""

from __future__ import annotations

import math
import re
import textwrap
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import commands
from discord import app_commands

# ---------------------------
# Configuration (edit here)
# ---------------------------
# Staff role IDs allowed to edit prices (from your message)
ALLOWED_ROLE_IDS = {
    1259587539212173375,  # @FOUNDER
    1401443966011969686,  # @CO-OWNER
    1205431837732900904,  # @Co-Founder
}

# How many skins per page in the paginated list
SKINS_PER_PAGE = 6

# Simple image URL validation (permissive)
_IMAGE_RE = re.compile(r"^https?://.*\.(?:png|jpg|jpeg|webp|gif)$", re.IGNORECASE)

# ---------------------------
# The manual SKINS dictionary
# ---------------------------
# Edit / expand this dict manually when you want to add/change skins.
# Keep keys (the skin names) unique. This is the single source-of-truth
# for the in-memory market.
#
# Fields: price (int), image_url (str), rarity (str), category (str)
SKINS: Dict[str, Dict[str, object]] = {
    "Oni Naginata": {
        "price": 950,
        "image_url": "https://i.imgur.com/abcd123.png",
        "rarity": "Legendary",
        "category": "Melee",
    },
    "AKR12 Railgun": {
        "price": 1200,
        "image_url": "https://i.imgur.com/xyzrail.png",
        "rarity": "Mythical",
        "category": "Rifle",
    },
    "Desert Eagle Thunder": {
        "price": 430,
        "image_url": "https://i.imgur.com/deagthun.png",
        "rarity": "Rare",
        "category": "Pistol",
    },
    "SM1014 Frostbite": {
        "price": 620,
        "image_url": "https://i.imgur.com/frostbite.png",
        "rarity": "Epic",
        "category": "Shotgun",
    },
    "P90 Neon Rage": {
        "price": 310,
        "image_url": "https://i.imgur.com/p90neon.png",
        "rarity": "Uncommon",
        "category": "SMG",
    },
}

# ---------------------------
# Helper utilities
# ---------------------------
def normalize_name(name: str) -> str:
    """Normalize skin names for case-insensitive lookups."""
    return name.strip().lower()


def find_skin_by_name(name: str) -> Optional[str]:
    """Return the canonical skin key matching `name` (case-insensitive) or None."""
    target = normalize_name(name)
    for key in SKINS.keys():
        if normalize_name(key) == target:
            return key
    return None


def find_partial_matches(term: str, limit: int = 10) -> List[str]:
    """Return up to `limit` skin names that contain `term` (case-insensitive)."""
    t = normalize_name(term)
    results = [k for k in SKINS.keys() if t in normalize_name(k)]
    return results[:limit]


def build_price_embed(skin_name: str) -> discord.Embed:
    """Build a rich embed for a single skin using the SKINS metadata."""
    data = SKINS[skin_name]
    price = data.get("price", 0)
    rarity = data.get("rarity", "Unknown")
    category = data.get("category", "Misc")
    image = data.get("image_url")

    # Color by rarity (simple mapping)
    rarity_colors = {
        "common": 0x95A5A6,
        "uncommon": 0x2ECC71,
        "rare": 0x3498DB,
        "epic": 0x9B59B6,
        "legendary": 0xE67E22,
        "mythical": 0xE74C3C,
    }
    color = rarity_colors.get(rarity.lower(), 0x5865F2)

    embed = discord.Embed(
        title=f"{skin_name}",
        description=f"🟡 **Price:** `{price}` coins\n⭐ **Rarity:** `{rarity}`\n📦 **Category:** `{category}`",
        color=color,
    )
    embed.set_footer(text="Standoff 2 • Manual Market • Edit cogs/so2_market.py to change data")
    if image and isinstance(image, str):
        embed.set_image(url=image)
    return embed


def build_list_page_embed(page_items: List[Tuple[str, Dict[str, object]]], page: int, total_pages: int) -> discord.Embed:
    """Create an embed showing a page of skins (name + price + rarity)."""
    embed = discord.Embed(
        title="Standoff 2 Market — Skins",
        description=f"Page {page}/{total_pages} • {len(SKINS)} total skins",
        color=discord.Color.gold()
    )
    for name, data in page_items:
        price = data.get("price", 0)
        rarity = data.get("rarity", "Unknown")
        category = data.get("category", "Misc")
        # Short line per skin
        embed.add_field(
            name=name,
            value=f"💰 {price} • ⭐ {rarity} • 📦 {category}",
            inline=False
        )
    embed.set_footer(text="Use buttons or /listskins <page> to navigate")
    return embed


def chunk_list(items: List[Tuple[str, Dict[str, object]]], chunk_size: int) -> List[List[Tuple[str, Dict[str, object]]]]:
    """Split list into chunks of size chunk_size."""
    return [items[i:i+chunk_size] for i in range(0, len(items), chunk_size)]


def user_has_edit_role(member: discord.Member) -> bool:
    """Check if member has at least one of the allowed role IDs."""
    if not member:
        return False
    return any(role.id in ALLOWED_ROLE_IDS for role in member.roles)


# ---------------------------
# Paginator View
# ---------------------------
class MarketPaginator(discord.ui.View):
    """Button-based paginator for lists of skins."""

    def __init__(self, pages: List[discord.Embed], author_id: Optional[int] = None, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.author_id = author_id
        self.index = 0

        # Buttons
        self.prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.primary)
        self.next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.primary)
        self.jump_first = discord.ui.Button(label="|<<", style=discord.ButtonStyle.secondary)
        self.jump_last = discord.ui.Button(label=">>|", style=discord.ButtonStyle.secondary)
        self.page_info = discord.ui.Button(label=self.page_label(), style=discord.ButtonStyle.secondary, disabled=True)

        # Assign callbacks
        self.prev_btn.callback = self._prev
        self.next_btn.callback = self._next
        self.jump_first.callback = self._first
        self.jump_last.callback = self._last

        # Add to view
        self.add_item(self.jump_first)
        self.add_item(self.prev_btn)
        self.add_item(self.page_info)
        self.add_item(self.next_btn)
        self.add_item(self.jump_last)

    def page_label(self) -> str:
        return f"Page {self.index + 1}/{len(self.pages)}"

    async def _update(self, interaction: discord.Interaction):
        """Update the message embed to current page."""
        self.page_info.label = self.page_label()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def _prev(self, interaction: discord.Interaction):
        if self.author_id and interaction.user.id != self.author_id:
            return await interaction.response.send_message("Only the command author can control this.", ephemeral=True)
        self.index = (self.index - 1) % len(self.pages)
        await self._update(interaction)

    async def _next(self, interaction: discord.Interaction):
        if self.author_id and interaction.user.id != self.author_id:
            return await interaction.response.send_message("Only the command author can control this.", ephemeral=True)
        self.index = (self.index + 1) % len(self.pages)
        await self._update(interaction)

    async def _first(self, interaction: discord.Interaction):
        if self.author_id and interaction.user.id != self.author_id:
            return await interaction.response.send_message("Only the command author can control this.", ephemeral=True)
        self.index = 0
        await self._update(interaction)

    async def _last(self, interaction: discord.Interaction):
        if self.author_id and interaction.user.id != self.author_id:
            return await interaction.response.send_message("Only the command author can control this.", ephemeral=True)
        self.index = len(self.pages) - 1
        await self._update(interaction)


# ---------------------------
# Cog
# ---------------------------
class SO2MarketCog(commands.Cog):
    """Manual-only Standoff 2 market cog with polished commands and embeds."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # sort keys once for consistent ordering
        self._sorted_items = sorted(SKINS.items(), key=lambda kv: kv[0].lower())

    # -----------------------
    # Prefix command: !price
    # -----------------------
    @commands.command(name="price")
    async def price_cmd(self, ctx: commands.Context, *, name: str):
        """Show a skin's price. Usage: !price <skin name>"""
        key = find_skin_by_name(name)
        if not key:
            # try partial matches
            matches = find_partial_matches(name, limit=6)
            if not matches:
                return await ctx.send(f"❌ No skin found matching `{name}`")
            # show quick list of suggestions
            lines = [f"- {m}  ({SKINS[m]['price']} coins)" for m in matches]
            return await ctx.send(f"❌ Exact not found. Did you mean:\n" + "\n".join(lines))

        embed = build_price_embed(key)
        await ctx.send(embed=embed)

    # --------------------------
    # Prefix: !listskins [page]
    # --------------------------
    @commands.command(name="listskins")
    async def lists_cmd(self, ctx: commands.Context, page: Optional[int] = 1):
        """List all skins in paginated embeds. Usage: !listskins [page]"""
        items = sorted(SKINS.items(), key=lambda kv: kv[0].lower())
        pages = chunk_list(items, SKINS_PER_PAGE)
        total_pages = max(1, len(pages))
        page_index = max(1, page) - 1
        page_index = min(page_index, total_pages - 1)

        embed_pages = []
        for i, chunk in enumerate(pages, start=1):
            embed_pages.append(build_list_page_embed(chunk, i, total_pages))

        paginator = MarketPaginator(embed_pages, author_id=ctx.author.id)
        await ctx.send(embed=embed_pages[page_index], view=paginator)

    # ------------------------
    # Prefix: !findskin <term>
    # ------------------------
    @commands.command(name="findskin")
    async def find_cmd(self, ctx: commands.Context, *, query: str):
        """Find skins by partial match. Usage: !findskin <term>"""
        matches = find_partial_matches(query, limit=20)
        if not matches:
            return await ctx.send(f"❌ No skins matching `{query}`")
        lines = [f"- **{m}** — {SKINS[m]['price']} coins • {SKINS[m]['rarity']}" for m in matches]
        await ctx.send("🔎 Matches:\n" + "\n".join(lines))

    # ------------------------
    # Admin prefix: !setprice <skin> <price>
    # ------------------------
    @commands.command(name="setprice")
    async def setprice_cmd(self, ctx: commands.Context, skin_name: str, price: int):
        """
        Admin-only command to update a skin price in the file (in-memory).
        Note: editing this way changes runtime memory until next reload.
        For permanent changes edit this file and reload the cog.
        """
        if not user_has_edit_role(ctx.author):
            return await ctx.send("❌ You don't have permission to change prices.")

        key = find_skin_by_name(skin_name)
        if not key:
            return await ctx.send(f"❌ Skin `{skin_name}` not found.")

        if price < 0:
            return await ctx.send("❌ Price must be non-negative.")

        old = SKINS[key]["price"]
        SKINS[key]["price"] = price
        # refresh sorted view
        self._sorted_items = sorted(SKINS.items(), key=lambda kv: kv[0].lower())

        embed = discord.Embed(title="✅ Price Updated", color=discord.Color.green())
        embed.add_field(name="Skin", value=key, inline=True)
        embed.add_field(name="Old Price", value=str(old), inline=True)
        embed.add_field(name="New Price", value=str(price), inline=True)
        await ctx.send(embed=embed)

    # -------------------------
    # Slash: /price (autocomplete)
    # -------------------------
    @app_commands.command(name="price", description="Show price & info for a skin")
    @app_commands.describe(name="skin name")
    async def slash_price(self, interaction: discord.Interaction, name: str):
        key = find_skin_by_name(name)
        if not key:
            matches = find_partial_matches(name, limit=6)
            if not matches:
                return await interaction.response.send_message(f"❌ No skin named `{name}` found.", ephemeral=True)
            # show suggestions ephemeral
            return await interaction.response.send_message(
                "❌ Exact not found. Suggestions:\n" + "\n".join(f"- {m}" for m in matches),
                ephemeral=True
            )
        embed = build_price_embed(key)
        await interaction.response.send_message(embed=embed)

    @slash_price.autocomplete("name")
    async def price_autocomplete(self, interaction: discord.Interaction, current: str):
        choices = [app_commands.Choice(name=k, value=k) for k in SKINS.keys() if current.lower() in k.lower()]
        return choices[:20]

    # -------------------------
    # Slash: /listskins
    # -------------------------
    @app_commands.command(name="listskins", description="Show paginated list of skins")
    async def slash_list(self, interaction: discord.Interaction):
        items = sorted(SKINS.items(), key=lambda kv: kv[0].lower())
        pages = chunk_list(items, SKINS_PER_PAGE)
        total_pages = max(1, len(pages))
        embed_pages = [build_list_page_embed(chunk, i + 1, total_pages) for i, chunk in enumerate(pages)]

        paginator = MarketPaginator(embed_pages, author_id=interaction.user.id)
        await interaction.response.send_message(embed=embed_pages[0], view=paginator)

    # -------------------------
    # Slash: /findskin
    # -------------------------
    @app_commands.command(name="findskin", description="Search skins by keyword")
    async def slash_find(self, interaction: discord.Interaction, query: str):
        matches = find_partial_matches(query, limit=30)
        if not matches:
            return await interaction.response.send_message(f"❌ No matches for `{query}`", ephemeral=True)
        lines = [f"- **{m}** — {SKINS[m]['price']} • {SKINS[m]['rarity']}" for m in matches]
        # send ephemeral search result
        await interaction.response.send_message("🔎 Matches:\n" + "\n".join(lines), ephemeral=True)

    # -------------------------
    # Slash: /setprice (admin)
    # -------------------------
    @app_commands.command(name="setprice", description="(Admin) Change price for a skin")
    @app_commands.describe(skin="Skin name", price="New price (int)")
    async def slash_setprice(self, interaction: discord.Interaction, skin: str, price: int):
        if not user_has_edit_role(interaction.user):
            return await interaction.response.send_message("❌ You are not allowed to change prices.", ephemeral=True)

        key = find_skin_by_name(skin)
        if not key:
            return await interaction.response.send_message(f"❌ Skin `{skin}` not found.", ephemeral=True)
        if price < 0:
            return await interaction.response.send_message("❌ Price must be non-negative.", ephemeral=True)

        old = SKINS[key]["price"]
        SKINS[key]["price"] = price
        # refresh sorted items
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

    # -------------------------
    # Cog error handler
    # -------------------------
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception):
        # Let bot-wide handler manage most errors; we only handle cog-specific here if needed
        pass


# ---------------------------
# Cog setup
# ---------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(SO2MarketCog(bot))
