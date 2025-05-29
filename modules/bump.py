import discord
from discord.ext import commands, tasks
import os
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta
import logging
import asyncio
import pytz
import random
from typing import Optional, Dict, Set
import time

# Import du rate limiter
from .rate_limiter import get_rate_limiter, DiscordRateLimiter

load_dotenv(dotenv_path='../.env')

class BumpReminder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bump_message = None
        self.personal_reminder_message = None
        self.last_bump_time = None
        self.last_general_reminder_time = None
        self.last_personal_reminder_time = None
        self.data_file = "bump_data.json"
        self.initialized = False
        self.reminder_active = False
        self.france_tz = pytz.timezone('Europe/Paris')
        
        # Cache renforcé pour les utilisateurs qui ont utilisé /bump récemment
        self.recent_bump_users = {}  # {timestamp: user}
        self.pending_bumps = {}  # {user_id: {'timestamp': time, 'user': user_obj}}
        self.bump_interactions = {}  # {interaction_id: {'user': user, 'timestamp': time}}
        
        # Cache pour les dernières interactions Disboard
        self.last_disboard_interactions = []  # Liste des dernières interactions pour correlation
        
        # Utilisation du rate limiter avancé
        self.rate_limiter = get_rate_limiter()
        
        # Cache pour optimiser les performances
        self._cached_guild = None
        self._cached_channels = {}
        self._recent_active_members = set()
        self._last_member_scan = None
        
        # Configuration depuis .env
        self._load_config()
        self.load_data()
        
    def _load_config(self):
        """Charge la configuration depuis les variables d'environnement"""
        try:
            self.guild_id = int(os.getenv('GUILD_ID', 0)) or None
            self.bump_reminder_channel_id = int(os.getenv('GENERAL_CHANNEL_ID', 0)) or None
            self.incantations_channel_id = int(os.getenv('INCANTATIONS_CHANNEL_ID', 0)) or None
            self.disboard_id = int(os.getenv('DISBOARD_ID', '302050872383242240'))
            self.bump_role_id = int(os.getenv('BUMP_ROLE_ID', '1347588223777640531'))
            self.bump_command_id = 947088344167366698
        except (ValueError, TypeError) as e:
            logging.error(f"Erreur configuration: {e}")
            
    def get_france_time(self):
        """Retourne l'heure actuelle en timezone France"""
        return datetime.now(self.france_tz)
    
    def convert_to_france_time(self, dt):
        """Convertit une datetime UTC en timezone France"""
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        return dt.astimezone(self.france_tz)
        
    def load_data(self):
        """Charge les données de bump depuis le fichier"""
        try:
            with open(self.data_file, 'r') as f:
                data = json.load(f)
                if data.get('last_bump_time'):
                    self.last_bump_time = datetime.fromisoformat(data['last_bump_time'])
                if data.get('last_general_reminder_time'):
                    self.last_general_reminder_time = datetime.fromisoformat(data['last_general_reminder_time'])
                if data.get('last_personal_reminder_time'):
                    self.last_personal_reminder_time = datetime.fromisoformat(data['last_personal_reminder_time'])
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            logging.info("Aucune donnée de bump trouvée")
            
    def save_data(self):
        """Sauvegarde les données de bump"""
        data = {}
        if self.last_bump_time:
            data['last_bump_time'] = self.last_bump_time.isoformat()
        if self.last_general_reminder_time:
            data['last_general_reminder_time'] = self.last_general_reminder_time.isoformat()
        if self.last_personal_reminder_time:
            data['last_personal_reminder_time'] = self.last_personal_reminder_time.isoformat()
        try:
            with open(self.data_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logging.error(f"Erreur sauvegarde: {e}")
            
    def clean_old_caches(self):
        """Nettoie tous les caches (garde 20 minutes)"""
        cutoff_time = datetime.utcnow() - timedelta(minutes=20)
        cutoff_timestamp = time.time() - 1200  # 20 minutes
        
        # Nettoie recent_bump_users
        self.recent_bump_users = {
            timestamp: user for timestamp, user in self.recent_bump_users.items()
            if timestamp > cutoff_time
        }
        
        # Nettoie pending_bumps
        self.pending_bumps = {
            user_id: data for user_id, data in self.pending_bumps.items()
            if data['timestamp'] > cutoff_timestamp
        }
        
        # Nettoie bump_interactions
        self.bump_interactions = {
            interaction_id: data for interaction_id, data in self.bump_interactions.items()
            if data['timestamp'] > cutoff_timestamp
        }
        
        # Nettoie last_disboard_interactions (garde seulement les 10 dernières)
        self.last_disboard_interactions = self.last_disboard_interactions[-10:]
            
    async def get_guild_safe(self) -> Optional[discord.Guild]:
        """Récupère le serveur avec cache et gestion d'erreurs"""
        if self._cached_guild and self._cached_guild.id == self.guild_id:
            return self._cached_guild
            
        if not self.guild_id:
            return None
            
        try:
            self._cached_guild = self.bot.get_guild(self.guild_id)
            return self._cached_guild
        except Exception as e:
            logging.error(f"Erreur récupération serveur: {e}")
            return None
    
    async def get_channel_safe(self, channel_id: int) -> Optional[discord.TextChannel]:
        """Récupère un canal avec cache et vérification des permissions"""
        if not channel_id:
            return None
            
        # Vérification cache
        if channel_id in self._cached_channels:
            channel = self._cached_channels[channel_id]
            if channel and hasattr(channel, 'guild'):
                return channel
        
        guild = await self.get_guild_safe()
        if not guild:
            return None
            
        try:
            channel = guild.get_channel(channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                # Vérification permissions avant mise en cache
                perms = channel.permissions_for(guild.me)
                if perms.read_messages and perms.send_messages:
                    self._cached_channels[channel_id] = channel
                    return channel
        except Exception as e:
            logging.error(f"Erreur récupération canal {channel_id}: {e}")
            
        return None
    
    def time_since_last_bump(self):
        """Retourne le temps écoulé depuis le dernier bump"""
        if not self.last_bump_time:
            return timedelta(hours=999)
        now = self.get_france_time()
        if self.last_bump_time.tzinfo is None:
            last_bump_france = pytz.utc.localize(self.last_bump_time).astimezone(self.france_tz)
        else:
            last_bump_france = self.last_bump_time.astimezone(self.france_tz)
        return now - last_bump_france
    
    def time_since_last_general_reminder(self):
        """Retourne le temps écoulé depuis le dernier rappel général"""
        if not self.last_general_reminder_time:
            return timedelta(hours=999)
        now = self.get_france_time()
        if self.last_general_reminder_time.tzinfo is None:
            last_reminder_france = pytz.utc.localize(self.last_general_reminder_time).astimezone(self.france_tz)
        else:
            last_reminder_france = self.last_general_reminder_time.astimezone(self.france_tz)
        return now - last_reminder_france
    
    def time_since_last_personal_reminder(self):
        """Retourne le temps écoulé depuis le dernier rappel personnel"""
        if not self.last_personal_reminder_time:
            return timedelta(hours=999)
        now = self.get_france_time()
        if self.last_personal_reminder_time.tzinfo is None:
            last_personal_france = pytz.utc.localize(self.last_personal_reminder_time).astimezone(self.france_tz)
        else:
            last_personal_france = self.last_personal_reminder_time.astimezone(self.france_tz)
        return now - last_personal_france
        
    def can_send_reminder(self):
        """Vérifie si on peut envoyer un rappel (2 heures écoulées)"""
        return self.time_since_last_bump() >= timedelta(hours=2)
    
    def should_send_personal_reminder(self):
        """Vérifie si on doit envoyer un rappel personnel"""
        time_since_general = self.time_since_last_general_reminder()
        time_since_personal = self.time_since_last_personal_reminder()
        
        if time_since_general >= timedelta(minutes=15):
            if time_since_personal >= timedelta(minutes=30):
                return True
        return False

    async def clear_old_bump_messages(self):
        """Nettoie les anciens messages de bump au démarrage"""
        try:
            discussion_channel = await self.get_channel_safe(self.bump_reminder_channel_id)
            if not discussion_channel:
                logging.warning("Canal de discussion non trouvé pour le nettoyage")
                return
                
            logging.info("🧹 Nettoyage des anciens messages de bump...")
            
            guild = await self.get_guild_safe()
            if not guild:
                return
                
            # Mots-clés pour identifier les messages de bump
            bump_keywords = [
                "bump le serveur",
                "serveur bumpé avec succès",
                "pourrais-tu bump",
                "bump afin de",
                "<:konatacry:",
                "<a:anyayay:",
                "incantations",
                "utiliser la commande bump"
            ]
            
            messages_to_delete = []
            
            # Recherche les messages du bot contenant des mots-clés de bump (limite à 50)
            async for message in discussion_channel.history(limit=50):
                # Vérifie si c'est un message du bot
                if message.author == guild.me:
                    message_content = message.content.lower()
                    
                    # Vérifie si le message contient des mots-clés de bump
                    if any(keyword.lower() in message_content for keyword in bump_keywords):
                        messages_to_delete.append(message)
                        
                    # Limite pour éviter trop de suppressions
                    if len(messages_to_delete) >= 20:
                        break
            
            # Supprime les messages trouvés avec rate limiting
            deleted_count = 0
            for message in messages_to_delete:
                try:
                    await self.rate_limiter.safe_delete(message)
                    deleted_count += 1
                    await asyncio.sleep(0.5)  # Pause pour éviter rate limit
                except Exception as e:
                    logging.error(f"Erreur suppression message {message.id}: {e}")
                    
            if deleted_count > 0:
                logging.info(f"✅ {deleted_count} anciens messages de bump supprimés")
            else:
                logging.info("Aucun ancien message de bump trouvé")
                
        except Exception as e:
            logging.error(f"Erreur nettoyage messages de bump: {e}")
            
    async def detect_last_bump_from_history(self):
        """Détecte le dernier bump avec optimisations rate limit"""
        try:
            incantations_channel = await self.get_channel_safe(self.incantations_channel_id)
            if not incantations_channel:
                return False
                
            logging.info("Recherche du dernier bump dans l'historique...")
            
            # Limite stricte pour éviter trop de requêtes
            async for message in incantations_channel.history(limit=20):
                if (message.author.id == self.disboard_id and 
                    message.embeds and 
                    any("bump effectué" in str(embed.description).lower() for embed in message.embeds if embed.description)):
                    
                    self.last_bump_time = message.created_at.replace(tzinfo=None)
                    self.save_data()
                    
                    time_since = self.time_since_last_bump()
                    france_time = self.convert_to_france_time(self.last_bump_time)
                    logging.info(f"Dernier bump trouvé: {france_time} (il y a {time_since})")
                    return True
                    
            logging.info("Aucun bump récent trouvé")
            return False
            
        except Exception as e:
            logging.error(f"Erreur détection dernier bump: {e}")
            return False
    
    async def send_pretty_bump_command_message(self):
        """Envoie un message simple de commande bump dans INCANTATIONS"""
        try:
            incantations_channel = await self.get_channel_safe(self.incantations_channel_id)
            if not incantations_channel:
                return
            
            bump_command_mention = f"</bump:{self.bump_command_id}>"
            
            message_content = f"🚀 **Bump le serveur !**\n\n" \
                            f"Utilise la commande {bump_command_mention} pour aider notre serveur à grandir !\n\n" \
                            f"💜 **Pourquoi bumper ?** Cela permet à de nouvelles personnes de découvrir notre communauté !\n\n" \
                            f"*Merci de soutenir Les Élémentalistes ! ✨*"
            
            await self.rate_limiter.safe_send(incantations_channel, message_content)
            logging.info("Message de commande bump envoyé")
                
        except Exception as e:
            logging.error(f"Erreur envoi message bump: {e}")
    
    async def update_active_members_cache(self):
        """Met à jour le cache des membres actifs avec optimisations"""
        try:
            now = datetime.utcnow()
            
            # Ne scan qu'une fois toutes les 5 minutes
            if (self._last_member_scan and 
                now - self._last_member_scan < timedelta(minutes=5)):
                return
            
            guild = await self.get_guild_safe()
            if not guild:
                return
            
            cutoff_time = now - timedelta(minutes=10)
            new_active_members = set()
            
            # Limite à quelques canaux principaux pour éviter trop de requêtes
            priority_channels = [
                self.bump_reminder_channel_id,
                self.incantations_channel_id
            ]
            
            for channel_id in priority_channels:
                if not channel_id:
                    continue
                    
                channel = await self.get_channel_safe(channel_id)
                if not channel:
                    continue
                    
                try:
                    # Limite stricte des messages vérifiés
                    async for message in channel.history(limit=15, after=cutoff_time):
                        if not message.author.bot and message.author != guild.me:
                            new_active_members.add(message.author)
                                
                except Exception as e:
                    logging.error(f"Erreur scan canal {channel.name}: {e}")
                    continue
                    
            self._recent_active_members = new_active_members
            self._last_member_scan = now
            logging.info(f"Cache mis à jour: {len(new_active_members)} membres actifs")
            
        except Exception as e:
            logging.error(f"Erreur mise à jour cache membres: {e}")
    
    async def get_recent_active_member(self):
        """Trouve un membre actif depuis le cache"""
        await self.update_active_members_cache()
        
        if self._recent_active_members:
            return random.choice(list(self._recent_active_members))
        return None
    
    async def send_personal_bump_reminder(self, member):
        """Envoie un rappel personnel de bump à un membre"""
        try:
            if not member:
                return False
                
            reminder_channel = await self.get_channel_safe(self.bump_reminder_channel_id)
            if not reminder_channel:
                return False
            
            incantations_mention = f"<#{self.incantations_channel_id}>" if self.incantations_channel_id else "#incantations"
            
            message_content = f"<:konatacry:1377089246766174308> {member.mention}, pourrais-tu bump le serveur afin de nous soutenir ?\n" \
                            f"Rendez-vous dans {incantations_mention} pour utiliser la commande bump !\n\n" \
                            f"*Si quelqu'un d'autre bump, ça me va aussi :)*"
            
            sent_message = await self.rate_limiter.safe_send(reminder_channel, message_content)
            
            if sent_message:
                # Stocke le message personnel pour pouvoir le transformer plus tard
                self.personal_reminder_message = sent_message
                self.last_personal_reminder_time = datetime.utcnow()
                self.save_data()
                
                france_time = self.convert_to_france_time(self.last_personal_reminder_time)
                logging.info(f"Rappel personnel envoyé à {member.display_name} à {france_time}")
                return True
            return False
                
        except Exception as e:
            logging.error(f"Erreur envoi rappel personnel: {e}")
            return False
    
    def find_bump_user_from_interaction(self, message):
        """NOUVELLE MÉTHODE: Trouve l'utilisateur via message.interaction"""
        try:
            # Méthode principale: Utilise message.interaction
            if hasattr(message, 'interaction') and message.interaction:
                user = message.interaction.user
                logging.info(f"✅ Utilisateur trouvé via message.interaction: {user} (ID: {user.id})")
                return user
            
            # Méthode fallback: Utilise message.reference si c'est une réponse
            if hasattr(message, 'reference') and message.reference:
                logging.info(f"🔍 Message de réponse détecté, référence: {message.reference}")
                # Si on peut résoudre la référence, on pourrait obtenir l'auteur original
                if hasattr(message.reference, 'resolved') and message.reference.resolved:
                    original_author = message.reference.resolved.author
                    if not original_author.bot:
                        logging.info(f"✅ Utilisateur trouvé via message.reference: {original_author}")
                        return original_author
            
            logging.warning("❌ Aucune interaction ou référence trouvée sur le message")
            return None
            
        except Exception as e:
            logging.error(f"Erreur lors de la recherche via interaction: {e}")
            return None
        
    def find_most_recent_bump_user(self, disboard_message_time):
        """Trouve l'utilisateur qui a fait le bump avec méthodes améliorées (backup)"""
        self.clean_old_caches()
        
        # Convertit le temps du message Disboard en UTC pour comparaison
        if disboard_message_time.tzinfo is not None:
            disboard_utc = disboard_message_time.astimezone(pytz.UTC).replace(tzinfo=None)
        else:
            disboard_utc = disboard_message_time
        
        logging.info(f"🔍 Recherche utilisateur bump (fallback) pour message Disboard à {disboard_utc}")
        
        # Méthode 1: Cache des interactions bump récentes (fenêtre plus large)
        best_candidate = None
        best_time_diff = None
        
        for timestamp, user in self.recent_bump_users.items():
            time_diff = disboard_utc - timestamp
            
            # Fenêtre élargie: de -60 secondes à +15 minutes
            if timedelta(seconds=-60) <= time_diff <= timedelta(minutes=15):
                if best_time_diff is None or abs(time_diff.total_seconds()) < abs(best_time_diff.total_seconds()):
                    best_time_diff = time_diff
                    best_candidate = user
        
        if best_candidate:
            logging.info(f"✅ Utilisateur trouvé via cache fallback: {best_candidate} (diff: {best_time_diff})")
            return best_candidate
        
        logging.warning(f"❌ Aucun utilisateur bump trouvé via fallback pour le message à {disboard_utc}")
        return None
            
    async def handle_successful_bump(self, bump_user):
        """Gère un bump réussi"""
        try:
            # Met à jour le temps du dernier bump et reset les timers
            self.last_bump_time = datetime.utcnow()
            self.last_general_reminder_time = None
            self.last_personal_reminder_time = None
            self.save_data()
            self.reminder_active = False
            
            # Détermine quel message transformer (priorité au personnel)
            message_to_update = None
            if self.personal_reminder_message:
                message_to_update = self.personal_reminder_message
                self.personal_reminder_message = None
            elif self.bump_message:
                message_to_update = self.bump_message
                self.bump_message = None
            
            # Transforme le message approprié
            if message_to_update:
                if bump_user:
                    message_content = f"<a:anyayay:1377087649403109498> Serveur bumpé avec succès ! Merci à {bump_user.mention} pour avoir soutenu le serveur !"
                else:
                    message_content = f"<a:anyayay:1377087649403109498> Serveur bumpé avec succès ! Merci !"
                
                await self.rate_limiter.safe_edit(message_to_update, content=message_content)
                
                france_time = self.convert_to_france_time(self.last_bump_time)
                logging.info(f"Message de bump mis à jour pour {bump_user or 'utilisateur inconnu'} à {france_time}")
                
                # Supprime le message après 5 minutes
                await asyncio.sleep(300)
                await self.rate_limiter.safe_delete(message_to_update)
            
            logging.info(f"✅ Bump traité pour {bump_user or 'utilisateur inconnu'}")
                
        except Exception as e:
            logging.error(f"Erreur handle successful bump: {e}")
            
    async def initialize_system(self):
        """Initialise le système de bump"""
        if self.initialized:
            return
            
        logging.info("Initialisation du système de bump...")
        
        guild = await self.get_guild_safe()
        if not guild:
            logging.error("Serveur non trouvé")
            return
            
        logging.info(f"✅ Serveur trouvé: {guild.name}")
        
        # NETTOYAGE EN PREMIER - Supprime les anciens messages de bump
        await self.clear_old_bump_messages()
        
        # Détecte le dernier bump depuis l'historique
        await self.detect_last_bump_from_history()
        
        # Démarre les systèmes de surveillance
        if not self.bump_monitor_task.is_running():
            self.bump_monitor_task.start()
        if not self.personal_reminder_task.is_running():
            self.personal_reminder_task.start()
            
        # Si assez de temps s'est écoulé, envoie un rappel
        if self.can_send_reminder():
            await self.send_bump_reminder()
            
        self.initialized = True
        logging.info("🚀 Système de bump initialisé")
        
    @commands.Cog.listener()
    async def on_ready(self):
        """Se déclenche quand le bot est prêt"""
        if not self.initialized:
            await self.initialize_system()
            
    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        """Capture les interactions bump pour backup (garde la version existante)"""
        try:
            if interaction.guild and interaction.guild.id == self.guild_id:
                
                if interaction.type == discord.InteractionType.application_command:
                    user = interaction.user
                    current_time = time.time()
                    
                    command_name = interaction.data.get('name', 'Unknown') if hasattr(interaction, 'data') and interaction.data else 'Unknown'
                    app_id = getattr(interaction, 'application_id', 'Unknown')
                    
                    if (command_name == 'bump' and 
                        interaction.channel_id == self.incantations_channel_id):
                        
                        timestamp = datetime.utcnow()
                        
                        logging.info(f"💾 Stockage interaction bump (backup): {user}")
                        
                        # Stockage backup pour correlation
                        self.recent_bump_users[timestamp] = user
                        
                        self.pending_bumps[user.id] = {
                            'timestamp': current_time,
                            'user': user,
                            'verified_disboard': app_id == self.disboard_id
                        }
                        
                        if hasattr(interaction, 'id'):
                            self.bump_interactions[str(interaction.id)] = {
                                'user': user,
                                'timestamp': current_time,
                                'command': command_name,
                                'app_id': app_id
                            }
                        
                        self.last_disboard_interactions.append({
                            'user': user,
                            'timestamp': current_time,
                            'interaction_id': getattr(interaction, 'id', None),
                            'channel_id': interaction.channel_id,
                            'command': command_name,
                            'app_id': app_id,
                            'verified_disboard': app_id == self.disboard_id
                        })
                        
                        if len(self.last_disboard_interactions) > 20:
                            self.last_disboard_interactions = self.last_disboard_interactions[-15:]
                        
                        self.clean_old_caches()
                        
                        logging.info(f"✅ Interaction /bump stockée (backup): {user} (ID: {user.id}) à {timestamp}")
                        
        except Exception as e:
            logging.error(f"Erreur capture interaction: {e}")
        
    async def cog_load(self):
        """Chargement du module"""
        logging.info("Module bump chargé")
            
    async def cog_unload(self):
        """Déchargement du module"""
        if self.bump_monitor_task.is_running():
            self.bump_monitor_task.cancel()
        if self.personal_reminder_task.is_running():
            self.personal_reminder_task.cancel()
            
    @tasks.loop(minutes=2)
    async def bump_monitor_task(self):
        """Surveille si un rappel doit être envoyé"""
        try:
            if not self.initialized:
                return
                
            # Si aucun rappel actif et que 2h se sont écoulées, envoie un rappel
            if not self.reminder_active and self.can_send_reminder():
                await self.send_bump_reminder()
                
        except Exception as e:
            logging.error(f"Erreur bump monitor task: {e}")
    
    @tasks.loop(minutes=10)
    async def personal_reminder_task(self):
        """Vérifie si un rappel personnel doit être envoyé"""
        try:
            if not self.initialized or not self.reminder_active:
                return
            
            if self.should_send_personal_reminder():
                # D'ABORD trouve un membre actif
                active_member = await self.get_recent_active_member()
                
                if active_member:
                    # SEULEMENT SI un membre est trouvé, supprime l'ancien message général
                    if self.bump_message:
                        await self.rate_limiter.safe_delete(self.bump_message)
                        self.bump_message = None
                    
                    await self.send_personal_bump_reminder(active_member)
                else:
                    logging.info("Aucun membre actif trouvé, rappel général maintenu")
                
        except Exception as e:
            logging.error(f"Erreur personal reminder task: {e}")
            
    async def send_bump_reminder(self):
        """Envoie un rappel de bump"""
        try:
            if not self.can_send_reminder():
                return False
                
            guild = await self.get_guild_safe()
            if not guild:
                return False
                
            reminder_channel = await self.get_channel_safe(self.bump_reminder_channel_id)
            if not reminder_channel:
                logging.error("Channel de rappel non trouvé")
                return False
                
            # Supprime l'ancien message s'il existe
            if self.bump_message:
                await self.rate_limiter.safe_delete(self.bump_message)
                    
            bump_role = guild.get_role(self.bump_role_id)
            role_mention = bump_role.mention if bump_role else f"<@&{self.bump_role_id}>"
            
            incantations_mention = f"<#{self.incantations_channel_id}>" if self.incantations_channel_id else "#incantations"
            
            message_content = f"<:konatacry:1377089246766174308> Quelqu'un pourrait-il bump le serveur afin de nous soutenir ? {role_mention}\n" \
                            f"Rendez-vous dans {incantations_mention} pour utiliser la commande bump !"
            
            self.bump_message = await self.rate_limiter.safe_send(reminder_channel, message_content)
            
            if self.bump_message:
                self.reminder_active = True
                self.last_general_reminder_time = datetime.utcnow()
                self.save_data()
                
                # Envoie le message de commande bump juste après
                await self.send_pretty_bump_command_message()
                
                france_time = self.convert_to_france_time(self.last_general_reminder_time)
                logging.info(f"✅ Rappel de bump envoyé à {france_time}")
                return True
            return False
            
        except Exception as e:
            logging.error(f"Erreur envoi bump reminder: {e}")
            return False
        
    @commands.Cog.listener()
    async def on_message(self, message):
        """Détecte les bumps réussis via Disboard avec detection d'interaction améliorée"""
        try:
            if not self.initialized:
                return
                
            # Vérifie que c'est Disboard dans le bon serveur
            if (message.author.id != self.disboard_id or 
                not message.guild or 
                message.guild.id != self.guild_id or
                not message.embeds):
                return
                
            # Vérifie si c'est un message de bump réussi
            embed = message.embeds[0]
            description = embed.description or ""
            
            if "bump effectué" in description.lower():
                # Si ce n'est pas dans INCANTATIONS, supprimer le message
                if message.channel.id != self.incantations_channel_id:
                    await self.rate_limiter.safe_delete(message)
                    return
                
                logging.info(f"💥 Bump détecté dans {message.channel.name} à {message.created_at}")
                
                # NOUVELLE MÉTHODE PRINCIPALE: Utilise message.interaction
                bump_user = self.find_bump_user_from_interaction(message)
                
                # Si la méthode principale échoue, utilise les méthodes de fallback
                if not bump_user:
                    logging.warning("⚠️ message.interaction non disponible, utilisation du fallback")
                    bump_user = self.find_most_recent_bump_user(message.created_at)
                
                if bump_user:
                    logging.info(f"✅ Utilisateur qui a bumpé identifié: {bump_user}")
                    await self.handle_successful_bump(bump_user)
                else:
                    logging.warning("⚠️ Impossible de trouver l'utilisateur qui a bumpé")
                    # Met à jour quand même le temps
                    self.last_bump_time = datetime.utcnow()
                    self.last_general_reminder_time = None
                    self.last_personal_reminder_time = None
                    self.save_data()
                    self.reminder_active = False
                    
                    # Supprime les messages de rappel s'ils existent
                    if self.bump_message:
                        await self.rate_limiter.safe_delete(self.bump_message)
                        self.bump_message = None
                    if self.personal_reminder_message:
                        await self.rate_limiter.safe_delete(self.personal_reminder_message)
                        self.personal_reminder_message = None
                    
        except Exception as e:
            logging.error(f"Erreur détection bump: {e}")
            
    @commands.command(name='bump_debug')
    @commands.has_permissions(administrator=True)
    async def debug_bump(self, ctx):
        """Affiche les informations de debug améliorées"""
        time_since = self.time_since_last_bump()
        time_since_general = self.time_since_last_general_reminder()
        time_since_personal = self.time_since_last_personal_reminder()
        
        last_bump_france = self.convert_to_france_time(self.last_bump_time) if self.last_bump_time else None
        last_general_france = self.convert_to_france_time(self.last_general_reminder_time) if self.last_general_reminder_time else None
        last_personal_france = self.convert_to_france_time(self.last_personal_reminder_time) if self.last_personal_reminder_time else None
        
        # Récupère les métriques du rate limiter
        metrics = self.rate_limiter.get_metrics()
        
        # Nettoie et affiche les caches
        self.clean_old_caches()
        
        # Formate les caches pour l'affichage
        recent_users_str = ", ".join([f"{user.display_name} ({timestamp.strftime('%H:%M:%S')})" 
                                     for timestamp, user in sorted(self.recent_bump_users.items(), reverse=True)])
        
        pending_bumps_str = ", ".join([f"{data['user'].display_name} ({datetime.utcfromtimestamp(data['timestamp']).strftime('%H:%M:%S')})" 
                                      for user_id, data in self.pending_bumps.items()])
        
        debug_info = f"""**🔧 Debug Bump System (Avec message.interaction)**

**Méthodes de Détection:**
• **1. message.interaction** ✅ (Méthode principale)
• **2. Fallback caches** (Si interaction non disponible)

**État:**
• Système initialisé: `{self.initialized}`
• Rappel actif: `{self.reminder_active}`
• Message de rappel général: `{bool(self.bump_message)}`
• Message de rappel personnel: `{bool(self.personal_reminder_message)}`
• Membres actifs en cache: `{len(self._recent_active_members)}`

**Caches Backup:**
• **recent_bump_users:** `{len(self.recent_bump_users)}`
  `{recent_users_str[:200]}{'...' if len(recent_users_str) > 200 else ''}`

• **pending_bumps:** `{len(self.pending_bumps)}`
  `{pending_bumps_str[:200]}{'...' if len(pending_bumps_str) > 200 else ''}`

**Rate Limiter:**
• Requêtes totales: `{metrics['total_requests']}`
• Rate limited: `{metrics['rate_limited_requests']} ({metrics['rate_limit_percentage']}%)`
• Req/min: `{metrics['requests_per_minute']}`
• Buckets actifs: `{metrics['active_buckets']}`

**Timing:**
• Dernier bump: `{last_bump_france or 'Jamais'}`
• Temps écoulé: `{time_since if self.last_bump_time else 'N/A'}`
• Peut envoyer rappel: `{self.can_send_reminder()}`

**Rappels:**
• Dernier rappel général: `{last_general_france or 'Jamais'}`
• Dernier rappel personnel: `{last_personal_france or 'Jamais'}`
• Doit envoyer personnel: `{self.should_send_personal_reminder()}`

**Configuration:**
• Guild ID: `{self.guild_id}`
• Channel rappel: `{self.bump_reminder_channel_id}`
• Channel incantations: `{self.incantations_channel_id}`
• Disboard ID: `{self.disboard_id}`
"""
        
        # Divise le message si trop long
        if len(debug_info) > 2000:
            parts = [debug_info[i:i+1900] for i in range(0, len(debug_info), 1900)]
            for i, part in enumerate(parts):
                if i == 0:
                    await ctx.send(part)
                else:
                    await ctx.send(f"**Debug Bump (suite {i+1})**\n" + part)
        else:
            await ctx.send(debug_info)

    @commands.command(name='bump_test_interaction')
    @commands.has_permissions(administrator=True)
    async def test_interaction_detection(self, ctx):
        """Teste la détection d'interactions pour debug"""
        
        embed = discord.Embed(
            title="🔧 Test Détection avec message.interaction", 
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="Nouvelle Méthode Principale",
            value="✅ `message.interaction.user` - Utilisateur exact de l'interaction",
            inline=False
        )
        
        embed.add_field(
            name="Méthodes Backup",
            value="🔄 Cache des interactions récentes (si interaction non disponible)",
            inline=False
        )
        
        embed.add_field(
            name="Avantages",
            value="• Précision maximale\n• Pas de corrélation temporelle\n• Fonctionne toujours si message.interaction existe",
            inline=False
        )
        
        embed.add_field(
            name="État Actuel du Cache Backup",
            value=f"• Recent bump users: {len(self.recent_bump_users)}\n"
                  f"• Pending bumps: {len(self.pending_bumps)}\n"
                  f"• Bump interactions: {len(self.bump_interactions)}",
            inline=False
        )
        
        await ctx.send(embed=embed)
            
    @commands.command(name='bump_init')
    @commands.has_permissions(administrator=True)
    async def manual_init(self, ctx):
        """Initialise manuellement le système"""
        self.initialized = False
        await self.initialize_system()
        await ctx.send("✅ Système de bump initialisé !")
        
    @commands.command(name='bump_send_command')
    @commands.has_permissions(administrator=True)
    async def send_bump_command_message(self, ctx):
        """Force l'envoi du message de commande bump"""
        await self.send_pretty_bump_command_message()
        await ctx.send("✅ Message de commande bump envoyé !")
            
    @commands.command(name='bump_force')
    @commands.has_permissions(administrator=True)
    async def force_bump_reminder(self, ctx):
        """Force l'envoi d'un rappel"""
        success = await self.send_bump_reminder()
        if success:
            await ctx.send("✅ Rappel de bump forcé envoyé !")
        else:
            await ctx.send("❌ Erreur lors de l'envoi du rappel")
    
    @commands.command(name='bump_personal')
    @commands.has_permissions(administrator=True)
    async def force_personal_reminder(self, ctx):
        """Force l'envoi d'un rappel personnel"""
        active_member = await self.get_recent_active_member()
        if active_member:
            success = await self.send_personal_bump_reminder(active_member)
            if success:
                await ctx.send(f"✅ Rappel personnel envoyé à {active_member.mention} !")
            else:
                await ctx.send("❌ Erreur lors de l'envoi du rappel personnel")
        else:
            await ctx.send("❌ Aucun membre actif trouvé")
        
    @commands.command(name='bump_reset')
    @commands.has_permissions(administrator=True)
    async def reset_bump_timer(self, ctx):
        """Reset le timer de bump"""
        self.last_bump_time = None
        self.last_general_reminder_time = None
        self.last_personal_reminder_time = None
        self.reminder_active = False
        self.recent_bump_users.clear()
        self.pending_bumps.clear()
        self.bump_interactions.clear()
        self.last_disboard_interactions.clear()
        self.save_data()
        if self.bump_message:
            await self.rate_limiter.safe_delete(self.bump_message)
            self.bump_message = None
        if self.personal_reminder_message:
            await self.rate_limiter.safe_delete(self.personal_reminder_message)
            self.personal_reminder_message = None
        await ctx.send("✅ Timer de bump reseté !")

    @commands.command(name='bump_clean')
    @commands.has_permissions(administrator=True)
    async def clean_bump_messages(self, ctx):
        """Force le nettoyage des messages de bump"""
        await self.clear_old_bump_messages()
        await ctx.send("✅ Nettoyage des messages de bump effectué !")
        
    @commands.command(name='bump_status')
    @commands.has_permissions(administrator=True)
    async def bump_status(self, ctx):
        """Affiche le statut du système"""
        if self.last_bump_time:
            time_since = self.time_since_last_bump()
            last_bump_france = self.convert_to_france_time(self.last_bump_time)
            
            if self.can_send_reminder():
                status = "🚀 **Bump disponible maintenant !**"
            else:
                time_remaining = timedelta(hours=2) - time_since
                status = f"⏰ Prochain bump dans: **{time_remaining}**"
            status += f"\n📅 Dernier bump: {last_bump_france} (il y a {time_since})"
        else:
            status = "❓ Aucun bump enregistré"
            
        await ctx.send(f"📊 **Statut du système de bump**\n{status}")

def setup(bot):
    return bot.add_cog(BumpReminder(bot))
