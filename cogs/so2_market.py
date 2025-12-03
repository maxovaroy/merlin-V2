import discord
from discord.ext import commands
from discord import app_commands

"""
============================================================
 Standoff 2 Market Cog — Manual Skin Management System
 Author: Max Ovaroy (Merlin Bot Owner)

 Purpose:
 --------
 A fully manual Standoff 2 skin market management module.    
 NO API is used — all skins are added manually inside this file.

 Features:
 ---------
 ✓ Manual skin list stored in memory (editable in code)
 ✓ Each skin contains:
      - name
      - price
      - image_url
      - rarity
      - category
 ✓ /skinlist      → show all skins
 ✓ /skininfo name → show 1 specific skin
 ✓ /setprice      → select skin & update its price (ADMIN ONLY)
 ✓ Clean embed UI with images
 ✓ Slash commands
 ✓ Role-restricted editing:
      @FOUNDER     = 1259587539212173375
      @CO-OWNER    = 1401443966011969686
      @Co-Founder  = 1205431837732900904

============================================================
"""


# ------------------------------------------------------------
# Role IDs allowed to modify skin prices
# ------------------------------------------------------------
ALLOWED_ROLES = {
    1259587539212173375,  # FOUNDER
    1401443966011969686,  # CO-OWNER
    1205431837732900904,  # Co-Founder
}


class SO2Market(commands.Cog):
    """A complete Standoff 2 market system with manual skins."""

    def __init__(self, bot):
        self.bot = bot

        # ------------------------------------------------------------
        # MANUAL SKIN DATABASE
        # Add more skins here manually.
        # ------------------------------------------------------------
        self.skins = {
            "Oni Naginata": {
                "price": 950,
                "image_url": "https://i.imgur.com/abcd123.png",
                "rarity": "Legendary",
                "category": "Melee"
            },
            "AKR12 Railgun": {
                "price": 1200,
                "image_url": "https://i.imgur.com/xyzrail.png",
                "rarity": "Mythical",
                "category": "Rifle"
            },
            "Desert Eagle Thunder": {
                "price": 430,
                "image_url": "https://i.imgur.com/deagthun.png",
                "rarity": "Rare",
                "category": "Pistol"
            },
            "SM1014 Frostbite": {
                "price": 620,
                "image_url": "https://i.imgur.com/frostbite.png",
                "rarity": "Epic",
                "category": "Shotgun"
            },
            "P90 Neon Rage": {
                "price": 310,
                "image_url": "https://i.imgur.com/p90neon.png",
                "rarity": "Uncommon",
                "category": "SMG"
            },
        }

    # ============================================================
    # Helper function — check if user has permission
    # ============================================================
    def has_permission(self, interaction: discord.Interaction):
        """Check if a user has an allowed role."""
        roles = {role.id for role in interaction.user.roles}
        return any(r in roles for r in ALLOWED_ROLES)

    # ============================================================
    # Slash Command: Show all skins
    # ============================================================
    @app_commands.command(name="skinlist", description="Show all Standoff 2 skins.")
    async def skinlist(self, interaction: discord.Interaction):

        embed = discord.Embed(
            title="🟨 Standoff 2 Market — Available Skins",
            color=discord.Color.gold()
        )

        for name, data in self.skins.items():
            embed.add_field(
                name=f"**{name}**",
                value=(
                    f"💰 **Price:** {data['price']} gold\n"
                    f"⭐ **Rarity:** {data['rarity']}\n"
                    f"📦 **Category:** {data['category']}"
                ),
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    # ============================================================
    # Slash Command: Show info for 1 skin
    # ============================================================
    @app_commands.command(name="skininfo", description="Show information about a specific skin.")
    @app_commands.describe(name="Enter the skin name")
    async def skininfo(self, interaction: discord.Interaction, name: str):

        name = name.strip()

        if name not in self.skins:
            await interaction.response.send_message(
                f"❌ Skin `{name}` not found.", ephemeral=True
            )
            return

        skin = self.skins[name]

        embed = discord.Embed(
            title=f"🎯 {name}",
            description=f"Information about `{name}`",
            color=discord.Color.blue(),
        )
        embed.add_field(name="💰 Price", value=f"{skin['price']} gold", inline=True)
        embed.add_field(name="⭐ Rarity", value=skin["rarity"], inline=True)
        embed.add_field(name="📦 Category", value=skin["category"], inline=True)
        embed.set_thumbnail(url=skin["image_url"])

        await interaction.response.send_message(embed=embed)

    # ============================================================
    # Slash Command: Set Price (Admin Only)
    # ============================================================
    @app_commands.command(name="setprice", description="Change price of a Standoff 2 skin (Admin only).")
    @app_commands.describe(
        skin="Choose the skin",
        price="Enter new price (number only)"
    )
    async def setprice(self, interaction: discord.Interaction, skin: str, price: int):

        if not self.has_permission(interaction):
            await interaction.response.send_message(
                "❌ You are **not allowed** to change skin prices.",
                ephemeral=True,
            )
            return

        skin = skin.strip()

        if skin not in self.skins:
            await interaction.response.send_message(
                f"❌ Skin `{skin}` does not exist.", ephemeral=True
            )
            return

        # Update price
        old_price = self.skins[skin]["price"]
        self.skins[skin]["price"] = price

        embed = discord.Embed(
            title="✅ Price Updated",
            color=discord.Color.green()
        )
        embed.add_field(name="Skin", value=skin, inline=True)
        embed.add_field(name="Old Price", value=old_price, inline=True)
        embed.add_field(name="New Price", value=price, inline=True)

        await interaction.response.send_message(embed=embed)

    # ============================================================
    # Autocomplete for skin names
    # ============================================================
    @setprice.autocomplete("skin")
    async def skin_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=k, value=k)
            for k in self.skins.keys()
            if current.lower() in k.lower()
        ][:20]


# ------------------------------------------------------------
# Setup
# ------------------------------------------------------------
async def setup(bot):
    await bot.add_cog(SO2Market(bot))
