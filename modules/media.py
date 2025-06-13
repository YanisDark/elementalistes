# modules/media.py
import discord
from discord.ext import commands
import asyncio
import re
import os
import json
import aiofiles
from modules.rate_limiter import get_rate_limiter

class MediaModule(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.media_channel_id = int(os.getenv('MEDIA_CHANNEL_ID', 0))
        self.admin_role_id = int(os.getenv('ADMIN_ROLE_ID', 0))
        self.rate_limiter = get_rate_limiter()
        self.warning_messages_file = 'data/media_warnings.json'
        # Regex pour détecter les URLs
        self.url_pattern = re.compile(
            r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
        )

    async def load_warning_messages(self):
        """Charger les IDs des messages d'avertissement"""
        try:
            if not os.path.exists('data'):
                os.makedirs('data')
            
            if os.path.exists(self.warning_messages_file):
                async with aiofiles.open(self.warning_messages_file, 'r') as f:
                    content = await f.read()
                    return json.loads(content) if content else []
        except Exception:
            pass
        return []

    async def save_warning_messages(self, message_ids):
        """Sauvegarder les IDs des messages d'avertissement"""
        try:
            if not os.path.exists('data'):
                os.makedirs('data')
            
            async with aiofiles.open(self.warning_messages_file, 'w') as f:
                await f.write(json.dumps(message_ids))
        except Exception:
            pass

    async def cleanup_warning_messages(self):
        """Nettoyer les anciens messages d'avertissement au démarrage"""
        warning_ids = await self.load_warning_messages()
        if not warning_ids:
            return

        channel = self.bot.get_channel(self.media_channel_id)
        if not channel:
            return

        cleaned_ids = []
        for msg_id in warning_ids:
            try:
                message = await channel.fetch_message(msg_id)
                await self.rate_limiter.safe_delete(message)
            except discord.errors.NotFound:
                pass
            except Exception:
                cleaned_ids.append(msg_id)

        await self.save_warning_messages(cleaned_ids)

    @commands.Cog.listener()
    async def on_ready(self):
        """Nettoyer les messages d'avertissement au démarrage"""
        await self.cleanup_warning_messages()

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignorer les messages du bot
        if message.author.bot:
            return
            
        # Vérifier si le message est dans le canal média
        if message.channel.id != self.media_channel_id:
            return
        
        # Exception pour les administrateurs
        if hasattr(message.author, 'roles'):
            admin_role = discord.utils.get(message.author.roles, id=self.admin_role_id)
            if admin_role:
                return
        
        # Vérifier si le message contient des attachements
        has_attachment = len(message.attachments) > 0
        
        # Vérifier si le message contient des liens
        has_link = bool(self.url_pattern.search(message.content))
        
        # Si pas d'attachement ni de lien, supprimer le message
        if not has_attachment and not has_link:
            try:
                await self.rate_limiter.safe_delete(message)
                
                # Envoyer message d'avertissement
                warning_msg = await self.rate_limiter.safe_send(
                    message.channel,
                    f"{message.author.mention}, vous ne pouvez poster que des images, vidéos, liens ou autres fichiers dans ce salon."
                )
                
                if warning_msg:
                    # Sauvegarder l'ID du message d'avertissement
                    warning_ids = await self.load_warning_messages()
                    warning_ids.append(warning_msg.id)
                    await self.save_warning_messages(warning_ids)
                    
                    # Supprimer le message d'avertissement après 30 secondes
                    await asyncio.sleep(30)
                    try:
                        await self.rate_limiter.safe_delete(warning_msg)
                        # Retirer l'ID de la liste
                        warning_ids = await self.load_warning_messages()
                        if warning_msg.id in warning_ids:
                            warning_ids.remove(warning_msg.id)
                            await self.save_warning_messages(warning_ids)
                    except discord.errors.NotFound:
                        pass
                    
            except discord.errors.NotFound:
                pass
            except discord.errors.Forbidden:
                pass
                
        else:
            # Créer un thread public sous le message
            try:
                thread_name = f"Discussion - {message.author.display_name}"
                if len(thread_name) > 100:
                    thread_name = thread_name[:97] + "..."
                    
                await message.create_thread(
                    name=thread_name,
                    auto_archive_duration=1440  # 24 heures
                )
            except discord.errors.Forbidden:
                pass
            except discord.errors.HTTPException:
                pass

async def setup(bot):
    await bot.add_cog(MediaModule(bot))
