# modules/lovecalc/lovecalc.py
import discord
from discord.ext import commands
import aiosqlite
import hashlib
import os
import aiohttp
import io
import random
import asyncio
from PIL import Image, ImageDraw
from typing import Optional, Union, List, Tuple, Dict
from .rate_limiter import get_rate_limiter

class LoveCalc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "lovecalc.db"
        self.rate_limiter = get_rate_limiter()
        self.error_messages = {}  # Track error messages for cleanup
        
    async def setup_database(self):
        """Initialize database table if not exists"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS love_results (
                    user_pair_hash TEXT PRIMARY KEY,
                    user1_id INTEGER,
                    user2_id INTEGER,
                    love_percentage INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()
    
    def generate_user_hash(self, user1_id: int, user2_id: int) -> str:
        """Generate consistent hash for user pair"""
        sorted_ids = sorted([user1_id, user2_id])
        combined = f"{sorted_ids[0]}-{sorted_ids[1]}"
        return hashlib.md5(combined.encode()).hexdigest()
    
    async def get_or_calculate_love(self, user1_id: int, user2_id: int) -> int:
        """Get existing love percentage or calculate new one"""
        user_hash = self.generate_user_hash(user1_id, user2_id)
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT love_percentage FROM love_results WHERE user_pair_hash = ?",
                (user_hash,)
            )
            result = await cursor.fetchone()
            
            if result:
                return result[0]
            
            # Generate truly random percentage for all cases
            love_percentage = random.randint(0, 100)
            
            await db.execute("""
                INSERT INTO love_results (user_pair_hash, user1_id, user2_id, love_percentage)
                VALUES (?, ?, ?, ?)
            """, (user_hash, user1_id, user2_id, love_percentage))
            await db.commit()
            
            return love_percentage
    
    async def get_categorized_matches(self, user_id: int) -> Dict[str, List[Tuple[int, int]]]:
        """Get all matches categorized by love percentage ranges"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT user1_id, user2_id, love_percentage FROM love_results 
                WHERE (user1_id = ? OR user2_id = ?)
            """, (user_id, user_id))
            results = await cursor.fetchall()
            
            categories = {
                "perfect": [],      # 100%
                "soulmates": [],    # 90-99%
                "hatred": [],       # 1-10%
                "enemies": []       # 0%
            }
            
            for user1_id, user2_id, percentage in results:
                other_user_id = user2_id if user1_id == user_id else user1_id
                
                if percentage == 100:
                    categories["perfect"].append((other_user_id, percentage))
                elif 90 <= percentage <= 99:
                    categories["soulmates"].append((other_user_id, percentage))
                elif 1 <= percentage <= 10:
                    categories["hatred"].append((other_user_id, percentage))
                elif percentage == 0:
                    categories["enemies"].append((other_user_id, percentage))
            
            return categories
    
    def get_love_comment(self, percentage: int, is_self: bool = False) -> str:
        """Get comment based on love percentage"""
        if is_self:
            if percentage == 0:
                return "💔 Tu sembles avoir besoin de plus d'amour-propre !"
            elif 1 <= percentage <= 20:
                return "😐 Tu pourrais apprendre à t'aimer un peu plus..."
            elif 21 <= percentage <= 40:
                return "😊 Tu commences à t'apprécier, c'est bien !"
            elif 41 <= percentage <= 60:
                return "💕 Tu as une bonne estime de toi ! Continue comme ça !"
            elif 61 <= percentage <= 80:
                return "💖 Tu t'aimes beaucoup ! C'est formidable !"
            elif 81 <= percentage <= 99:
                return "💝 Tu es totalement en harmonie avec toi-même !"
            else:  # 100
                return "💍 AMOUR-PROPRE PARFAIT ! Tu es ton/ta meilleur(e) ami(e) !"
        else:
            if percentage == 0:
                return "💔 Aucune affinité... Il vaut mieux rester amis !"
            elif 1 <= percentage <= 20:
                return "😐 Il y a peut-être quelque chose, mais c'est très léger..."
            elif 21 <= percentage <= 40:
                return "😊 Une petite étincelle ! Qui sait ce que l'avenir réserve ?"
            elif 41 <= percentage <= 60:
                return "💕 Une belle complicité se dessine ! C'est prometteur !"
            elif 61 <= percentage <= 80:
                return "💖 Wow ! Il y a de la magie dans l'air ! L'amour est là !"
            elif 81 <= percentage <= 99:
                return "💝 C'est de l'amour fou ! Vous êtes faits l'un pour l'autre !"
            else:  # 100
                return "💍 AMOUR PARFAIT ! Les étoiles se sont alignées ! C'est le destin !"
    
    async def download_avatar(self, user: discord.Member) -> Image.Image:
        """Download and return user avatar as PIL Image"""
        avatar_url = user.display_avatar.with_size(512).url
        
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url) as response:
                if response.status == 200:
                    data = await response.read()
                    return Image.open(io.BytesIO(data)).convert("RGBA")
        
        # Fallback to default avatar
        return Image.new("RGBA", (512, 512), (128, 128, 128, 255))
    
    def make_circle(self, image: Image.Image, size: int = 300) -> Image.Image:
        """Convert image to circular shape"""
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        
        # Create mask for circular crop
        mask = Image.new("L", (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, size, size), fill=255)
        
        # Apply mask
        result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        result.paste(image, (0, 0))
        result.putalpha(mask)
        
        return result
    
    async def create_love_image(self, user1: discord.Member, user2: discord.Member, percentage: int) -> io.BytesIO:
        """Create love calculation image with avatars"""
        # Download avatars
        avatar1 = await self.download_avatar(user1)
        avatar2 = await self.download_avatar(user2)
        
        # Make avatars circular (larger size)
        avatar1 = self.make_circle(avatar1, 300)
        avatar2 = self.make_circle(avatar2, 300)
        
        # Create transparent background (wider to accommodate heart)
        width, height = 1000, 400
        background = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        
        # Position avatars with more space for the heart
        avatar_y = (height - 300) // 2
        background.paste(avatar1, (50, avatar_y), avatar1)
        background.paste(avatar2, (650, avatar_y), avatar2)
        
        # Load and place heart image in center (256x256)
        try:
            heart_img = Image.open("lovecalc.png").convert("RGBA")
            heart_x = (width - 256) // 2
            heart_y = (height - 256) // 2
            background.paste(heart_img, (heart_x, heart_y), heart_img)
        except FileNotFoundError:
            # Fallback: simple heart shape if file not found
            draw = ImageDraw.Draw(background)
            center_x, center_y = width // 2, height // 2
            draw.ellipse([center_x-64, center_y-32, center_x-16, center_y+32], fill=(255, 20, 147, 255))
            draw.ellipse([center_x+16, center_y-32, center_x+64, center_y+32], fill=(255, 20, 147, 255))
            draw.polygon([(center_x-64, center_y+16), (center_x+64, center_y+16), (center_x, center_y+80)], fill=(255, 20, 147, 255))
        
        # Convert to bytes
        byte_arr = io.BytesIO()
        background.save(byte_arr, format='PNG')
        byte_arr.seek(0)
        
        return byte_arr
    
    def has_bypass_role(self, user: discord.Member) -> bool:
        """Check if user has the cooldown bypass role"""
        bypass_role_id = 1345472879168323625
        return any(role.id == bypass_role_id for role in user.roles)
    
    def get_random_member(self, ctx, exclude_user: discord.Member) -> Optional[discord.Member]:
        """Get a random member from the server, excluding the specified user"""
        members = [member for member in ctx.guild.members if not member.bot and member.id != exclude_user.id]
        return random.choice(members) if members else None
    
    async def safe_delete_after_delay(self, message: discord.Message, delay: float):
        """Safely delete a message after a delay"""
        try:
            await asyncio.sleep(delay)
            if message.id in self.error_messages:
                try:
                    await message.channel.fetch_message(message.id)
                    await message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
                finally:
                    self.error_messages.pop(message.id, None)
        except Exception:
            self.error_messages.pop(message.id, None)
    
    def format_user_list(self, user_ids_and_percentages: List[Tuple[int, int]], guild: discord.Guild) -> List[str]:
        """Format a list of user IDs and percentages into display strings, filtering out missing users"""
        formatted_list = []
        for user_id, percentage in user_ids_and_percentages:
            try:
                user = guild.get_member(user_id)
                if user:  # Only add if user exists in guild
                    formatted_list.append(f"**{user.display_name}** ({percentage}%)")
            except Exception:
                continue  # Skip users that cause errors
        
        return formatted_list
    
    @commands.command(name='lovecalc', aliases=['amour', 'lc'])
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def lovecalc_prefix(self, ctx, *, args=""):
        """Calculate love percentage between users (prefix command)"""
        # Check for bypass role
        if self.has_bypass_role(ctx.author):
            self.lovecalc_prefix.reset_cooldown(ctx)
        
        # Parse arguments
        args_list = args.split() if args else []
        
        # Handle random parameter
        if args_list and args_list[0].lower() == "random":
            random_member = self.get_random_member(ctx, ctx.author)
            if not random_member:
                await self.rate_limiter.safe_send(
                    ctx.channel,
                    "❌ Aucun membre disponible pour un calcul aléatoire !"
                )
                return
            
            target_user = random_member
            author = ctx.author
        else:
            # Try to convert arguments to members
            personne = None
            avec = None
            
            if len(args_list) >= 1:
                try:
                    personne = await commands.MemberConverter().convert(ctx, args_list[0])
                except commands.BadArgument:
                    await self.rate_limiter.safe_send(
                        ctx.channel,
                        "❌ Impossible de trouver ce membre !"
                    )
                    return
            
            if len(args_list) >= 2:
                try:
                    avec = await commands.MemberConverter().convert(ctx, args_list[1])
                except commands.BadArgument:
                    await self.rate_limiter.safe_send(
                        ctx.channel,
                        "❌ Impossible de trouver le deuxième membre !"
                    )
                    return
            
            if personne is None:
                await self.rate_limiter.safe_send(
                    ctx.channel,
                    "❌ Tu dois mentionner au moins une personne ou utiliser `random` !"
                )
                return
            
            if avec is None:
                # Calculate love between author and personne
                target_user = personne
                author = ctx.author
            else:
                # Calculate love between personne and avec
                target_user = avec
                author = personne
        
        is_self = author.id == target_user.id
        percentage = await self.get_or_calculate_love(author.id, target_user.id)
        comment = self.get_love_comment(percentage, is_self)
        
        # Create love image
        image_data = await self.create_love_image(author, target_user, percentage)
        file = discord.File(image_data, filename="lovecalc_result.png")
        
        if is_self:
            message = f"__**{author.display_name}**__ 💕 __**{author.display_name}**__\n\n"
        else:
            message = f"__**{author.display_name}**__ 💕 __**{target_user.display_name}**__\n\n"
        
        message += f"🎯 **Pourcentage d'amour : {percentage}%**\n"
        message += f"{comment}"
        
        await self.rate_limiter.safe_send(ctx.channel, content=message, file=file)
    
    @commands.command(name='lovelist', aliases=['ll', 'amourlist'])
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def lovelist_prefix(self, ctx, user: Optional[discord.Member] = None):
        """Display categorized love matches for a user"""
        if self.has_bypass_role(ctx.author):
            self.lovelist_prefix.reset_cooldown(ctx)
        
        target_user = user or ctx.author
        categories = await self.get_categorized_matches(target_user.id)
        
        # Check if user has any matches at all
        total_matches = sum(len(matches) for matches in categories.values())
        if total_matches == 0:
            await self.rate_limiter.safe_send(
                ctx.channel,
                f"💔 **{target_user.display_name}** n'a encore testé sa compatibilité avec personne !"
            )
            return
        
        embed = discord.Embed(
            title=f"💘 Affinités de {target_user.display_name}",
            color=0xFF1493
        )
        embed.set_thumbnail(url=target_user.display_avatar.url)
        
        # Perfect matches (100%)
        if categories["perfect"]:
            perfect_list = self.format_user_list(categories["perfect"], ctx.guild)
            if perfect_list:  # Only add field if there are valid users
                embed.add_field(
                    name=f"💍 Amour Parfait (100%) - {len(perfect_list)}",
                    value="\n".join(perfect_list) if perfect_list else "Aucun utilisateur actuel",
                    inline=False
                )
        
        # Soulmates (90-99%)
        if categories["soulmates"]:
            soulmates_list = self.format_user_list(categories["soulmates"], ctx.guild)
            if soulmates_list:  # Only add field if there are valid users
                embed.add_field(
                    name=f"💖 Âmes-Sœurs (90-99%) - {len(soulmates_list)}",
                    value="\n".join(soulmates_list) if soulmates_list else "Aucun utilisateur actuel",
                    inline=False
                )
        
        # Absolute hatred (1-10%)
        if categories["hatred"]:
            hatred_list = self.format_user_list(categories["hatred"], ctx.guild)
            if hatred_list:  # Only add field if there are valid users
                embed.add_field(
                    name=f"💀 Haine Absolue (1-10%) - {len(hatred_list)}",
                    value="\n".join(hatred_list) if hatred_list else "Aucun utilisateur actuel",
                    inline=False
                )
        
        # Sworn enemies (0%)
        if categories["enemies"]:
            enemies_list = self.format_user_list(categories["enemies"], ctx.guild)
            if enemies_list:  # Only add field if there are valid users
                embed.add_field(
                    name=f"⚔️ Ennemis Jurés (0%) - {len(enemies_list)}",
                    value="\n".join(enemies_list) if enemies_list else "Aucun utilisateur actuel",
                    inline=False
                )
        
        # Add summary - only count valid users
        summary_parts = []
        if categories["perfect"]:
            valid_perfect = len(self.format_user_list(categories["perfect"], ctx.guild))
            if valid_perfect > 0:
                summary_parts.append(f"💍 {valid_perfect} parfait(s)")
        
        if categories["soulmates"]:
            valid_soulmates = len(self.format_user_list(categories["soulmates"], ctx.guild))
            if valid_soulmates > 0:
                summary_parts.append(f"💖 {valid_soulmates} âme(s)-sœur(s)")
        
        if categories["hatred"]:
            valid_hatred = len(self.format_user_list(categories["hatred"], ctx.guild))
            if valid_hatred > 0:
                summary_parts.append(f"💀 {valid_hatred} haine(s) absolue(s)")
        
        if categories["enemies"]:
            valid_enemies = len(self.format_user_list(categories["enemies"], ctx.guild))
            if valid_enemies > 0:
                summary_parts.append(f"⚔️ {valid_enemies} ennemi(s) juré(s)")
        
        if summary_parts:
            embed.set_footer(text=" • ".join(summary_parts))
        else:
            embed.description = "Aucune affinité avec des membres actuels du serveur."
        
        await self.rate_limiter.safe_send(ctx.channel, embed=embed)
    
    @discord.app_commands.command(name="lovecalc", description="Calcule le pourcentage d'amour entre deux utilisateurs")
    @discord.app_commands.describe(
        personne="La personne avec qui calculer l'amour",
        avec="Une deuxième personne (optionnel - sinon ce sera avec toi)"
    )
    @discord.app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
    async def lovecalc_slash(self, interaction: discord.Interaction, personne: Optional[discord.Member] = None, avec: Optional[discord.Member] = None):
        """Calculate love percentage between users (slash command)"""
        if personne is None:
            await interaction.response.send_message("❌ Tu dois mentionner au moins une personne !", ephemeral=True)
            return
        
        if avec is None:
            target_user = personne
            author = interaction.user
        else:
            target_user = avec
            author = personne
        
        await interaction.response.defer()
        
        is_self = author.id == target_user.id
        percentage = await self.get_or_calculate_love(author.id, target_user.id)
        comment = self.get_love_comment(percentage, is_self)
        
        # Create love image
        image_data = await self.create_love_image(author, target_user, percentage)
        file = discord.File(image_data, filename="lovecalc_result.png")
        
        message = f"💘 **Calcul d'amour** 💘\n"
        if is_self:
            message += f"**{author.display_name}** 💕 **{author.display_name}**\n\n"
        else:
            message += f"**{author.display_name}** 💕 **{target_user.display_name}**\n\n"
        
        message += f"🎯 **Pourcentage d'amour : {percentage}%**\n"
        message += f"{comment}"
        
        await interaction.followup.send(content=message, file=file)
    
    @discord.app_commands.command(name="lovelist", description="Affiche les affinités catégorisées d'un utilisateur")
    @discord.app_commands.describe(user="L'utilisateur dont afficher les affinités (par défaut : toi)")
    @discord.app_commands.checks.cooldown(1, 15, key=lambda i: i.user.id)
    async def lovelist_slash(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        """Display categorized love matches for a user (slash command)"""
        target_user = user or interaction.user
        
        await interaction.response.defer()
        
        categories = await self.get_categorized_matches(target_user.id)
        
        # Check if user has any matches at all
        total_matches = sum(len(matches) for matches in categories.values())
        if total_matches == 0:
            await interaction.followup.send(
                f"💔 **{target_user.display_name}** n'a encore testé sa compatibilité avec personne !"
            )
            return
        
        embed = discord.Embed(
            title=f"💘 Affinités de {target_user.display_name}",
            color=0xFF1493
        )
        embed.set_thumbnail(url=target_user.display_avatar.url)
        
        # Perfect matches (100%)
        if categories["perfect"]:
            perfect_list = self.format_user_list(categories["perfect"], interaction.guild)
            if perfect_list:  # Only add field if there are valid users
                embed.add_field(
                    name=f"💍 Amour Parfait (100%) - {len(perfect_list)}",
                    value="\n".join(perfect_list) if perfect_list else "Aucun utilisateur actuel",
                    inline=False
                )
        
        # Soulmates (90-99%)
        if categories["soulmates"]:
            soulmates_list = self.format_user_list(categories["soulmates"], interaction.guild)
            if soulmates_list:  # Only add field if there are valid users
                embed.add_field(
                    name=f"💖 Âmes-Sœurs (90-99%) - {len(soulmates_list)}",
                    value="\n".join(soulmates_list) if soulmates_list else "Aucun utilisateur actuel",
                    inline=False
                )
        
        # Absolute hatred (1-10%)
        if categories["hatred"]:
            hatred_list = self.format_user_list(categories["hatred"], interaction.guild)
            if hatred_list:  # Only add field if there are valid users
                embed.add_field(
                    name=f"💀 Haine Absolue (1-10%) - {len(hatred_list)}",
                    value="\n".join(hatred_list) if hatred_list else "Aucun utilisateur actuel",
                    inline=False
                )
        
        # Sworn enemies (0%)
        if categories["enemies"]:
            enemies_list = self.format_user_list(categories["enemies"], interaction.guild)
            if enemies_list:  # Only add field if there are valid users
                embed.add_field(
                    name=f"⚔️ Ennemis Jurés (0%) - {len(enemies_list)}",
                    value="\n".join(enemies_list) if enemies_list else "Aucun utilisateur actuel",
                    inline=False
                )
        
        # Add summary - only count valid users
        summary_parts = []
        if categories["perfect"]:
            valid_perfect = len(self.format_user_list(categories["perfect"], interaction.guild))
            if valid_perfect > 0:
                summary_parts.append(f"💍 {valid_perfect} parfait(s)")
        
        if categories["soulmates"]:
            valid_soulmates = len(self.format_user_list(categories["soulmates"], interaction.guild))
            if valid_soulmates > 0:
                summary_parts.append(f"💖 {valid_soulmates} âme(s)-sœur(s)")
        
        if categories["hatred"]:
            valid_hatred = len(self.format_user_list(categories["hatred"], interaction.guild))
            if valid_hatred > 0:
                summary_parts.append(f"💀 {valid_hatred} haine(s) absolue(s)")
        
        if categories["enemies"]:
            valid_enemies = len(self.format_user_list(categories["enemies"], interaction.guild))
            if valid_enemies > 0:
                summary_parts.append(f"⚔️ {valid_enemies} ennemi(s) juré(s)")
        
        if summary_parts:
            embed.set_footer(text=" • ".join(summary_parts))
        else:
            embed.description = "Aucune affinité avec des membres actuels du serveur."
        
        await interaction.followup.send(embed=embed)
    
    @lovecalc_prefix.error
    async def lovecalc_prefix_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            if self.has_bypass_role(ctx.author):
                await ctx.reinvoke()
                return
            
            try:
                error_msg = await self.rate_limiter.safe_send(
                    ctx.channel,
                    f"⏰ {ctx.author.mention}, tu dois attendre {error.retry_after:.1f} secondes avant d'utiliser cette commande à nouveau !"
                )
                
                if error_msg:
                    self.error_messages[error_msg.id] = error_msg
                    asyncio.create_task(self.safe_delete_after_delay(error_msg, error.retry_after))
                    
            except Exception:
                pass
        else:
            try:
                await self.rate_limiter.safe_send(
                    ctx.channel,
                    f"❌ Une erreur est survenue lors du calcul d'amour."
                )
            except Exception:
                pass
    
    @lovelist_prefix.error
    async def lovelist_prefix_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            if self.has_bypass_role(ctx.author):
                await ctx.reinvoke()
                return
            
            try:
                error_msg = await self.rate_limiter.safe_send(
                    ctx.channel,
                    f"⏰ {ctx.author.mention}, tu dois attendre {error.retry_after:.1f} secondes avant d'utiliser cette commande à nouveau !"
                )
                
                if error_msg:
                    self.error_messages[error_msg.id] = error_msg
                    asyncio.create_task(self.safe_delete_after_delay(error_msg, error.retry_after))
                    
            except Exception:
                pass
        else:
            try:
                await self.rate_limiter.safe_send(
                    ctx.channel,
                    f"❌ Une erreur est survenue lors de la récupération de la liste."
                )
            except Exception:
                pass
    
    @lovecalc_slash.error
    async def lovecalc_slash_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CommandOnCooldown):
            if self.has_bypass_role(interaction.user):
                return
            
            try:
                await interaction.response.send_message(
                    f"⏰ Tu dois attendre {error.retry_after:.1f} secondes avant d'utiliser cette commande à nouveau !",
                    ephemeral=True
                )
            except discord.InteractionResponded:
                try:
                    await interaction.followup.send(
                        f"⏰ Tu dois attendre {error.retry_after:.1f} secondes avant d'utiliser cette commande à nouveau !",
                        ephemeral=True
                    )
                except Exception:
                    pass
        else:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"❌ Une erreur est survenue lors du calcul d'amour.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        f"❌ Une erreur est survenue lors du calcul d'amour.",
                        ephemeral=True
                    )
            except Exception:
                pass
    
    @lovelist_slash.error
    async def lovelist_slash_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CommandOnCooldown):
            if self.has_bypass_role(interaction.user):
                return
            
            try:
                await interaction.response.send_message(
                    f"⏰ Tu dois attendre {error.retry_after:.1f} secondes avant d'utiliser cette commande à nouveau !",
                    ephemeral=True
                )
            except discord.InteractionResponded:
                try:
                    await interaction.followup.send(
                        f"⏰ Tu dois attendre {error.retry_after:.1f} secondes avant d'utiliser cette commande à nouveau !",
                        ephemeral=True
                    )
                except Exception:
                    pass
        else:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"❌ Une erreur est survenue lors de la récupération de la liste.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        f"❌ Une erreur est survenue lors de la récupération de la liste.",
                        ephemeral=True
                    )
            except Exception:
                pass
    
    @commands.Cog.listener()
    async def on_ready(self):
        await self.setup_database()

async def setup(bot):
    await bot.add_cog(LoveCalc(bot))
