# modules/animation_planning.py
import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
import json
from datetime import datetime, timedelta
import pytz
import os
from typing import List, Optional
import re
import asyncio
from .rate_limiter import get_rate_limiter, safe_api_call

class AnimationPlanning(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "data/animation_planning.db"
        self.rate_limit_db = "data/sendevent_usage.db"
        self.command_usage_db = "data/command_usage.db"
        self.bot.loop.create_task(self.init_db())
        
        # Rate limiter instance
        self.rate_limiter = get_rate_limiter()
        
        # Limites de commandes par rôle (par heure)
        self.command_limits = {
            int(os.getenv('ADMIN_ROLE_ID', '0')): 100,      # SEIGNEUR - illimité pratiquement
            int(os.getenv('MODERATOR_ROLE_ID', '0')): 50,   # GARDIEN
            int(os.getenv('ANIMATOR_ROLE_ID', '0')): 20     # INVOCATEUR
        }
        
        # Rôles autorisés
        self.authorized_roles = [
            int(os.getenv('ADMIN_ROLE_ID')),      # SEIGNEUR
            int(os.getenv('MODERATOR_ROLE_ID')),  # GARDIEN
            int(os.getenv('ANIMATOR_ROLE_ID'))    # INVOCATEUR
        ]
        
        # Timezone Paris
        self.tz = pytz.timezone('Europe/Paris')
        
        # Stockage temporaire des événements en cours de création
        self.pending_events = {}
        
        # Démarrer le système de rappels
        self.bot.loop.create_task(self.reminder_system())
        
        # Nettoyage périodique des métriques
        self.bot.loop.create_task(self.cleanup_metrics())
    
    async def init_db(self):
        """Initialise la base de données"""
        os.makedirs("data", exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            # Créer la table avec toutes les colonnes
            await db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    managers TEXT NOT NULL,
                    description TEXT,
                    event_type TEXT,
                    created_at TEXT NOT NULL,
                    created_by INTEGER NOT NULL,
                    reminder_1h_sent INTEGER DEFAULT 0,
                    reminder_15m_sent INTEGER DEFAULT 0,
                    reminder_start_sent INTEGER DEFAULT 0,
                    message_id INTEGER
                )
            """)
            
            # Ajouter les colonnes manquantes si nécessaire (migration)
            columns_to_add = [
                ("description", "TEXT"),
                ("event_type", "TEXT"),
                ("reminder_1h_sent", "INTEGER DEFAULT 0"),
                ("reminder_15m_sent", "INTEGER DEFAULT 0"),
                ("reminder_start_sent", "INTEGER DEFAULT 0"),
                ("message_id", "INTEGER")
            ]
            
            for column_name, column_type in columns_to_add:
                try:
                    await db.execute(f"ALTER TABLE events ADD COLUMN {column_name} {column_type}")
                except:
                    pass  # La colonne existe déjà
            
            await db.commit()
        
        # Initialiser la base de données de rate limiting pour sendevent
        async with aiosqlite.connect(self.rate_limit_db) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS sendevent_usage (
                    user_id INTEGER PRIMARY KEY,
                    date TEXT,
                    usage_count INTEGER DEFAULT 0,
                    last_used TEXT
                )
            ''')
            await db.commit()
        
        # Initialiser la base de données générale de rate limiting
        async with aiosqlite.connect(self.command_usage_db) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS command_usage (
                    user_id INTEGER PRIMARY KEY,
                    hour_key TEXT,
                    usage_count INTEGER DEFAULT 0,
                    last_command TEXT,
                    last_used TEXT
                )
            ''')
            await db.commit()
    
    async def check_command_rate_limit(self, user: discord.Member, command_name: str) -> tuple[bool, str]:
        """Vérifie les limites de commandes générales"""
        user_limit = self.get_user_command_limit(user)
        if user_limit >= 100:  # Admins pratiquement illimités
            return True, ""
        
        current_hour = datetime.now(self.tz).strftime('%Y-%m-%d-%H')
        
        async with aiosqlite.connect(self.command_usage_db) as db:
            cursor = await db.execute(
                'SELECT hour_key, usage_count, last_used FROM command_usage WHERE user_id = ?',
                (user.id,)
            )
            row = await cursor.fetchone()
            
            if not row or row[0] != current_hour:
                # Nouvelle heure ou premier usage
                await db.execute('''
                    INSERT OR REPLACE INTO command_usage 
                    (user_id, hour_key, usage_count, last_command, last_used)
                    VALUES (?, ?, 1, ?, ?)
                ''', (user.id, current_hour, command_name, datetime.now(self.tz).isoformat()))
                await db.commit()
                return True, ""
            
            current_usage = row[1]
            if current_usage >= user_limit:
                last_used = datetime.fromisoformat(row[2]) if row[2] else datetime.now(self.tz)
                next_reset = last_used.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                remaining = next_reset - datetime.now(self.tz)
                
                if remaining.total_seconds() > 0:
                    minutes = int(remaining.total_seconds() / 60)
                    return False, f"Limite horaire atteinte ({current_usage}/{user_limit}). Réessayez dans {minutes} minutes."
            
            # Incrémenter l'usage
            await db.execute('''
                UPDATE command_usage 
                SET usage_count = usage_count + 1, last_command = ?, last_used = ?
                WHERE user_id = ?
            ''', (command_name, datetime.now(self.tz).isoformat(), user.id))
            await db.commit()
            
            return True, ""
    
    def get_user_command_limit(self, user: discord.Member) -> int:
        """Obtient la limite de commandes pour un utilisateur"""
        for role in user.roles:
            if role.id in self.command_limits:
                return self.command_limits[role.id]
        return 5  # Limite par défaut très basse pour les non-autorisés
    
    async def cleanup_metrics(self):
        """Nettoyage périodique des métriques"""
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                # Nettoyer les données anciennes (plus de 24h)
                cutoff_time = datetime.now(self.tz) - timedelta(hours=24)
                cutoff_str = cutoff_time.strftime('%Y-%m-%d')
                
                async with aiosqlite.connect(self.rate_limit_db) as db:
                    await db.execute('DELETE FROM sendevent_usage WHERE date < ?', (cutoff_str,))
                    await db.commit()
                
                # Nettoyer les métriques horaires anciennes
                cutoff_hour = cutoff_time.strftime('%Y-%m-%d-%H')
                async with aiosqlite.connect(self.command_usage_db) as db:
                    await db.execute('DELETE FROM command_usage WHERE hour_key < ?', (cutoff_hour,))
                    await db.commit()
                
                # Nettoyer les buckets du rate limiter
                await self.rate_limiter.cleanup_expired_buckets()
                
            except Exception as e:
                print(f"Erreur dans le nettoyage des métriques: {e}")
            
            await asyncio.sleep(3600)  # Nettoyer toutes les heures
    
    def has_permission(self, member: discord.Member) -> bool:
        """Vérifie si l'utilisateur a les permissions nécessaires"""
        return any(role.id in self.authorized_roles for role in member.roles)
    
    def is_invocateur_only(self, member: discord.Member) -> bool:
        """Vérifie si l'utilisateur est seulement Invocateur (pas Gardien ou Seigneur)"""
        return (any(role.id == self.authorized_roles[2] for role in member.roles) and  # INVOCATEUR
                not any(role.id in [self.authorized_roles[0], self.authorized_roles[1]] for role in member.roles))  # Pas SEIGNEUR ou GARDIEN
    
    async def get_user_usage(self, user_id: int):
        """Récupère l'usage du jour pour un utilisateur (sendevent spécifique)"""
        today = datetime.now(self.tz).strftime('%Y-%m-%d')
        async with aiosqlite.connect(self.rate_limit_db) as db:
            async with db.execute('SELECT date, usage_count, last_used FROM sendevent_usage WHERE user_id = ?', (user_id,)) as cursor:
                row = await cursor.fetchone()
                if not row or row[0] != today:
                    return 0, None
                return row[1], datetime.fromisoformat(row[2]) if row[2] else None
    
    async def update_user_usage(self, user_id: int):
        """Met à jour l'usage pour un utilisateur (sendevent spécifique)"""
        today = datetime.now(self.tz).strftime('%Y-%m-%d')
        now = datetime.now(self.tz).isoformat()
        
        async with aiosqlite.connect(self.rate_limit_db) as db:
            await db.execute('''
                INSERT OR REPLACE INTO sendevent_usage (user_id, date, usage_count, last_used)
                VALUES (?, ?, 
                    CASE 
                        WHEN (SELECT date FROM sendevent_usage WHERE user_id = ?) = ? 
                        THEN (SELECT usage_count FROM sendevent_usage WHERE user_id = ?) + 1
                        ELSE 1
                    END,
                    ?
                )
            ''', (user_id, today, user_id, today, user_id, now))
            await db.commit()
    
    def get_member_rank(self, member: discord.Member) -> int:
        """Retourne le rang du membre (0 = INVOCATEUR, 1 = GARDIEN, 2 = SEIGNEUR)"""
        if any(role.id == self.authorized_roles[2] for role in member.roles):  # INVOCATEUR
            return 0
        elif any(role.id == self.authorized_roles[1] for role in member.roles):  # GARDIEN
            return 1
        elif any(role.id == self.authorized_roles[0] for role in member.roles):  # SEIGNEUR
            return 2
        return 3  # Fallback
    
    def get_member_rank_name(self, member: discord.Member) -> str:
        """Retourne le nom du rang du membre"""
        rank = self.get_member_rank(member)
        rank_names = {0: "Invocateur", 1: "Gardien", 2: "Seigneur"}
        return rank_names.get(rank, "Membre")
    
    def get_event_mentions(self, event_type: str) -> str:
        """Retourne les mentions appropriées selon le type d'événement"""
        if event_type == "Film":
            film_role_id = os.getenv('FILM_ROLE_ID', '0')
            return f"<@&{film_role_id}>"
        elif event_type == "Soirée Jeux":
            jeu_role_id = os.getenv('JEU_ROLE_ID', '0')
            return f"<@&{jeu_role_id}>"
        elif event_type == "Animation":
            animation_role_id = os.getenv('ANIMATION_ROLE_ID', '0')
            return f"<@&{animation_role_id}>"
        return ""
    
    def get_date_choices(self) -> List[app_commands.Choice[str]]:
        """Génère les choix de dates pour les 30 prochains jours"""
        choices = []
        now = datetime.now(self.tz)
        
        for i in range(30):
            date = now + timedelta(days=i)
            # Format français pour l'affichage
            french_date = date.strftime("%A %d %B %Y")
            # Traduction des jours et mois en français
            french_date = french_date.replace("Monday", "Lundi")
            french_date = french_date.replace("Tuesday", "Mardi")
            french_date = french_date.replace("Wednesday", "Mercredi")
            french_date = french_date.replace("Thursday", "Jeudi")
            french_date = french_date.replace("Friday", "Vendredi")
            french_date = french_date.replace("Saturday", "Samedi")
            french_date = french_date.replace("Sunday", "Dimanche")
            
            french_date = french_date.replace("January", "Janvier")
            french_date = french_date.replace("February", "Février")
            french_date = french_date.replace("March", "Mars")
            french_date = french_date.replace("April", "Avril")
            french_date = french_date.replace("May", "Mai")
            french_date = french_date.replace("June", "Juin")
            french_date = french_date.replace("July", "Juillet")
            french_date = french_date.replace("August", "Août")
            french_date = french_date.replace("September", "Septembre")
            french_date = french_date.replace("October", "Octobre")
            french_date = french_date.replace("November", "Novembre")
            french_date = french_date.replace("December", "Décembre")
            
            # Valeur pour la base de données
            db_date = date.strftime("%Y-%m-%d")
            
            choices.append(app_commands.Choice(name=french_date, value=db_date))
        
        return choices[:25]  # Discord limite à 25 choix
    
    def get_time_choices(self) -> List[app_commands.Choice[str]]:
        """Génère les choix d'heures par défaut: 20:00, 21:00, 21:30, 22:00, 22:30"""
        default_times = ["20:00", "21:00", "21:30", "22:00", "22:30"]
        choices = [app_commands.Choice(name=time, value=time) for time in default_times]
        return choices
    
    def generate_hour_suggestions(self, hour_input: str) -> List[app_commands.Choice[str]]:
        """Génère des suggestions d'heures basées sur l'input de l'utilisateur"""
        choices = []
        
        # Si l'utilisateur tape juste un nombre (ex: "14", "8", "09")
        if hour_input.isdigit():
            hour = int(hour_input)
            if 0 <= hour <= 23:
                # Générer les minutes par quart d'heure
                minutes = ["00", "15", "30", "45"]
                for minute in minutes:
                    time_str = f"{hour:02d}:{minute}"
                    choices.append(app_commands.Choice(name=time_str, value=time_str))
        
        # Si l'utilisateur tape une heure avec : (ex: "14:", "14:3")
        elif ":" in hour_input:
            parts = hour_input.split(":")
            if len(parts) == 2 and parts[0].isdigit():
                hour = int(parts[0])
                minute_input = parts[1]
                
                if 0 <= hour <= 23:
                    if not minute_input:  # Juste "14:"
                        minutes = ["00", "15", "30", "45"]
                        for minute in minutes:
                            time_str = f"{hour:02d}:{minute}"
                            choices.append(app_commands.Choice(name=time_str, value=time_str))
                    else:  # "14:3" ou "14:30"
                        # Proposer les minutes qui commencent par l'input
                        all_minutes = [f"{i:02d}" for i in range(0, 60, 5)]  # Toutes les 5 minutes
                        matching_minutes = [m for m in all_minutes if m.startswith(minute_input)]
                        
                        for minute in matching_minutes[:10]:  # Limiter à 10 suggestions
                            time_str = f"{hour:02d}:{minute}"
                            choices.append(app_commands.Choice(name=time_str, value=time_str))
        
        return choices
    
    def parse_date(self, date_input: str) -> Optional[str]:
        """Parse une date entrée par l'utilisateur et retourne le format YYYY-MM-DD"""
        # Si c'est déjà au format YYYY-MM-DD
        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_input):
            try:
                datetime.strptime(date_input, '%Y-%m-%d')
                return date_input
            except ValueError:
                return None
        
        # Essayer d'autres formats courants
        formats = [
            '%d/%m/%Y',    # 25/12/2023
            '%d-%m-%Y',    # 25-12-2023
            '%d.%m.%Y',    # 25.12.2023
            '%d/%m/%y',    # 25/12/23
            '%d-%m-%y',    # 25-12-23
        ]
        
        for fmt in formats:
            try:
                parsed_date = datetime.strptime(date_input, fmt)
                return parsed_date.strftime('%Y-%m-%d')
            except ValueError:
                continue
        
        return None
    
    def format_french_date(self, date_str: str) -> str:
        """Convertit une date YYYY-MM-DD en format français"""
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            french_date = date_obj.strftime("%A %d %B %Y")
            # Traduction en français
            translations = {
                "Monday": "Lundi", "Tuesday": "Mardi", "Wednesday": "Mercredi",
                "Thursday": "Jeudi", "Friday": "Vendredi", "Saturday": "Samedi", "Sunday": "Dimanche",
                "January": "Janvier", "February": "Février", "March": "Mars", "April": "Avril",
                "May": "Mai", "June": "Juin", "July": "Juillet", "August": "Août",
                "September": "Septembre", "October": "Octobre", "November": "Novembre", "December": "Décembre"
            }
            for en, fr in translations.items():
                french_date = french_date.replace(en, fr)
            return french_date
        except:
            return date_str
    
    def get_eligible_members(self, guild: discord.Guild) -> List[discord.Member]:
        """Récupère tous les membres éligibles pour être responsables, triés par rang"""
        eligible_members = []
        for member in guild.members:
            if self.has_permission(member) and not member.bot:
                eligible_members.append(member)
        
        # Tri par rang puis par nom
        eligible_members.sort(key=lambda m: (self.get_member_rank(m), m.display_name.lower()))
        return eligible_members
    
    async def reminder_system(self):
        """Système de rappels automatiques avec rate limiting"""
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                now = datetime.now(self.tz)
                current_time = now.strftime("%Y-%m-%d %H:%M")
                
                async with aiosqlite.connect(self.db_path) as db:
                    cursor = await db.execute("""
                        SELECT id, title, event_date, event_time, managers, description, event_type,
                               created_at, created_by, reminder_1h_sent, reminder_15m_sent, reminder_start_sent, message_id
                        FROM events 
                        WHERE event_date > ? OR (event_date = ? AND event_time >= ?)
                    """, (now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"), now.strftime("%H:%M")))
                    events = await cursor.fetchall()
                    
                    for event in events:
                        event_datetime = datetime.strptime(f"{event[2]} {event[3]}", "%Y-%m-%d %H:%M")
                        event_datetime = self.tz.localize(event_datetime)
                        
                        time_diff = (event_datetime - now).total_seconds() / 60  # en minutes
                        
                        managers_data = json.loads(event[4])
                        
                        # Rappel 1h avant (55-65 minutes avant pour éviter les doublons)
                        if 55 <= time_diff <= 65 and not event[9]:  # reminder_1h_sent
                            await self.send_reminder(managers_data, event, "1 heure")
                            await db.execute("UPDATE events SET reminder_1h_sent = 1 WHERE id = ?", (event[0],))
                        
                        # Rappel 15 min avant (10-20 minutes avant)
                        elif 10 <= time_diff <= 20 and not event[10]:  # reminder_15m_sent
                            await self.send_reminder(managers_data, event, "15 minutes")
                            await db.execute("UPDATE events SET reminder_15m_sent = 1 WHERE id = ?", (event[0],))
                        
                        # Rappel au début (-5 à +5 minutes)
                        elif -5 <= time_diff <= 5 and not event[11]:  # reminder_start_sent
                            await self.send_reminder(managers_data, event, "maintenant")
                            await db.execute("UPDATE events SET reminder_start_sent = 1 WHERE id = ?", (event[0],))
                    
                    await db.commit()
                
            except Exception as e:
                print(f"Erreur dans le système de rappels: {e}")
            
            await asyncio.sleep(300)  # Vérifier toutes les 5 minutes
    
    async def send_reminder(self, managers_data: list, event: tuple, when: str):
        """Envoie un rappel aux responsables avec rate limiting"""
        event_type_emoji = {"Animation": "🎭", "Film": "🎬", "Soirée Jeux": "🎮"}.get(event[6], "🎪")
        
        message = f"📅 Rappel: Votre événement '{event[1]}' ({event_type_emoji} {event[6] or 'Événement'}) commence {when} ({event[3]} - {self.format_french_date(event[2])})."
        
        for manager in managers_data:
            try:
                user = self.bot.get_user(manager['id'])
                if user:
                    # Utiliser le rate limiter pour les DMs
                    await safe_api_call(
                        user.send(message),
                        route=f"POST /channels/@me/messages",
                        major_params={"user_id": user.id}
                    )
                    # Petit délai entre chaque DM pour éviter les rate limits
                    await asyncio.sleep(0.5)
            except Exception as e:
                print(f"Erreur envoi rappel à {manager['id']}: {e}")
    
    @app_commands.command(name="newevent", description="Créer un nouvel événement")
    @app_commands.describe(
        date="Date de l'événement (format: JJ/MM/AAAA ou sélection)",
        heure="Heure de l'événement (ex: 14 pour 14:00/14:15/14:30/14:45)",
        titre="Titre de l'événement",
        type_evenement="Type d'événement"
    )
    @app_commands.choices(type_evenement=[
        app_commands.Choice(name="🎭 Animation", value="Animation"),
        app_commands.Choice(name="🎬 Film", value="Film"),
        app_commands.Choice(name="🎮 Soirée Jeux", value="Soirée Jeux")
    ])
    async def new_event(
        self, 
        interaction: discord.Interaction,
        date: str,
        heure: str,
        titre: str,
        type_evenement: str
    ):
        # Vérifier les permissions
        if not self.has_permission(interaction.user):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        # Vérifier les limites de rate limiting
        can_proceed, error_msg = await self.check_command_rate_limit(interaction.user, "newevent")
        if not can_proceed:
            await interaction.response.send_message(f"⏳ {error_msg}", ephemeral=True)
            return
        
        # Validation de la date
        parsed_date = self.parse_date(date)
        if not parsed_date:
            await interaction.response.send_message("❌ Format de date invalide. Utilisez JJ/MM/AAAA ou sélectionnez une date proposée.", ephemeral=True)
            return
        
        # Vérification que la date n'est pas dans le passé
        try:
            event_date = datetime.strptime(parsed_date, '%Y-%m-%d').date()
            today = datetime.now(self.tz).date()
            
            if event_date < today:
                await interaction.response.send_message("❌ Impossible de créer un événement dans le passé.", ephemeral=True)
                return
            elif event_date == today:
                # Vérifier l'heure si c'est aujourd'hui
                current_time = datetime.now(self.tz).strftime('%H:%M')
                if heure <= current_time:
                    await interaction.response.send_message("❌ L'heure de l'événement doit être dans le futur.", ephemeral=True)
                    return
        except ValueError:
            await interaction.response.send_message("❌ Date invalide.", ephemeral=True)
            return
        
        # Validation de l'heure
        if not re.match(r'^([01]?[0-9]|2[0-3]):[0-5][0-9]$', heure):
            await interaction.response.send_message("❌ Format d'heure invalide. Utilisez HH:MM.", ephemeral=True)
            return
        
        # Récupérer les membres éligibles
        eligible_members = self.get_eligible_members(interaction.guild)
        
        if not eligible_members:
            await interaction.response.send_message("❌ Aucun membre autorisé trouvé pour gérer l'événement.", ephemeral=True)
            return
        
        # Créer la vue de sélection des responsables
        view = ManagerSelectionView(self, titre, parsed_date, heure, type_evenement, eligible_members, interaction.user.id)
        
        event_type_emoji = {"Animation": "🎭", "Film": "🎬", "Soirée Jeux": "🎮"}.get(type_evenement, "🎪")
        
        embed = discord.Embed(
            title="👥 Sélection des Responsables",
            description=f"**Événement:** {titre}\n**Type:** {event_type_emoji} {type_evenement}\n**Date:** {self.format_french_date(parsed_date)}\n**Heure:** {heure}\n\nSélectionnez les responsables de cet événement :",
            color=0x3498db
        )
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @new_event.autocomplete('date')
    async def date_autocomplete(self, interaction: discord.Interaction, current: str):
        choices = self.get_date_choices()
        # Filtrer en fonction de la saisie actuelle
        filtered_choices = [choice for choice in choices if current.lower() in choice.name.lower() or current in choice.value]
        return filtered_choices[:25]
    
    @new_event.autocomplete('heure')
    async def time_autocomplete(self, interaction: discord.Interaction, current: str):
        """Autocomplétion intelligente pour les heures"""
        current = current.strip()
        
        # Si rien n'est tapé, montrer les heures par défaut
        if not current:
            return self.get_time_choices()
        
        # Générer des suggestions basées sur l'input
        suggestions = self.generate_hour_suggestions(current)
        
        # Si on a des suggestions spécifiques, les utiliser
        if suggestions:
            return suggestions[:25]
        
        # Sinon, filtrer les heures par défaut
        default_choices = self.get_time_choices()
        filtered_choices = [choice for choice in default_choices if current in choice.value]
        
        # Si aucune correspondance dans les défauts, essayer de compléter partiellement
        if not filtered_choices and current:
            # Si l'utilisateur tape quelque chose qui pourrait être une heure
            if current.isdigit() or ":" in current:
                # Essayer de générer des suggestions même pour des inputs partiels
                try:
                    # Pour des inputs comme "1", "2", etc.
                    if current.isdigit() and len(current) == 1:
                        hour = int(current)
                        suggestions = []
                        # Proposer 1X:00, 1X:30 pour X de 0 à 9
                        for second_digit in range(10):
                            full_hour = hour * 10 + second_digit
                            if full_hour <= 23:
                                suggestions.append(app_commands.Choice(name=f"{full_hour:02d}:00", value=f"{full_hour:02d}:00"))
                                suggestions.append(app_commands.Choice(name=f"{full_hour:02d}:30", value=f"{full_hour:02d}:30"))
                        return suggestions[:25]
                except:
                    pass
        
        return filtered_choices[:25]
    
    @app_commands.command(name="sendevent", description="Envoyer l'annonce d'un événement")
    @app_commands.describe(event_id="ID de l'événement à annoncer")
    async def send_event(self, interaction: discord.Interaction, event_id: int):
        if not self.has_permission(interaction.user):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        # Vérifier les limites générales de commandes
        can_proceed, error_msg = await self.check_command_rate_limit(interaction.user, "sendevent")
        if not can_proceed:
            await interaction.response.send_message(f"⏳ {error_msg}", ephemeral=True)
            return
        
        # Vérifier les limites spécifiques pour les Invocateurs uniquement
        if self.is_invocateur_only(interaction.user):
            usage_count, last_used = await self.get_user_usage(interaction.user.id)
            
            # Vérifier la limite quotidienne
            if usage_count >= 2:
                temp_message = await interaction.channel.send("❌ Limite quotidienne atteinte (2/2).")
                await asyncio.sleep(60)
                try:
                    await self.rate_limiter.safe_delete(temp_message)
                except:
                    pass
                await interaction.response.send_message("Commande non exécutée.", ephemeral=True, delete_after=1)
                return
            
            # Vérifier le cooldown de 4h
            if last_used and datetime.now(self.tz) - last_used < timedelta(hours=4):
                remaining = timedelta(hours=4) - (datetime.now(self.tz) - last_used)
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes = remainder // 60
                temp_message = await self.rate_limiter.safe_send(
                    interaction.channel,
                    f"❌ Cooldown actif. Temps restant: {hours}h {minutes}m"
                )
                await asyncio.sleep(60)
                try:
                    await self.rate_limiter.safe_delete(temp_message)
                except:
                    pass
                await interaction.response.send_message("Commande non exécutée.", ephemeral=True, delete_after=1)
                return
        
        # Récupérer l'événement avec requête explicite
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT id, title, event_date, event_time, managers, description, event_type,
                       created_at, created_by, reminder_1h_sent, reminder_15m_sent, reminder_start_sent, message_id
                FROM events WHERE id = ?
            """, (event_id,))
            event = await cursor.fetchone()
        
        if not event:
            await interaction.response.send_message("❌ Événement introuvable.", ephemeral=True)
            return
        
        # Récupérer le canal d'animation
        animation_channel_id = int(os.getenv('ANIMATION_CHANNEL_ID', '0'))
        animation_channel = self.bot.get_channel(animation_channel_id)
        
        if not animation_channel:
            await interaction.response.send_message("❌ Canal d'animation introuvable.", ephemeral=True)
            return
        
        # Préparer les mentions
        mentions = self.get_event_mentions(event[6])  # event_type
        
        # Préparer le contenu du message : mentions + description
        description = event[5] if event[5] else "Aucune description disponible."
        
        if mentions:
            message_content = f"{mentions}\n\n{description}"
        else:
            message_content = description
        
        try:
            # Envoyer le message avec rate limiting
            sent_message = await self.rate_limiter.safe_send(animation_channel, message_content)
            
            if sent_message:
                # Sauvegarder l'ID du message dans la base de données
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("UPDATE events SET message_id = ? WHERE id = ?", (sent_message.id, event_id))
                    await db.commit()
                
                # Mettre à jour l'usage pour les Invocateurs
                if self.is_invocateur_only(interaction.user):
                    await self.update_user_usage(interaction.user.id)
                
                await interaction.response.send_message(f"✅ Événement annoncé avec succès ! [Voir le message]({sent_message.jump_url})", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Erreur lors de l'envoi du message (rate limit).", ephemeral=True)
                
        except Exception as e:
            await interaction.response.send_message(f"❌ Erreur lors de l'envoi: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="editeventmessage", description="Modifier le message d'annonce d'un événement")
    @app_commands.describe(identifier="ID de l'événement ou ID du message à modifier")
    async def edit_event_message(self, interaction: discord.Interaction, identifier: str):
        if not self.has_permission(interaction.user):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        # Vérifier les limites de rate limiting
        can_proceed, error_msg = await self.check_command_rate_limit(interaction.user, "editeventmessage")
        if not can_proceed:
            await interaction.response.send_message(f"⏳ {error_msg}", ephemeral=True)
            return
        
        try:
            identifier = int(identifier)
        except ValueError:
            await interaction.response.send_message("❌ ID invalide.", ephemeral=True)
            return
        
        # Chercher l'événement par ID d'événement ou ID de message
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT id, title, event_date, event_time, managers, description, event_type,
                       created_at, created_by, reminder_1h_sent, reminder_15m_sent, reminder_start_sent, message_id
                FROM events WHERE id = ? OR message_id = ?
            """, (identifier, identifier))
            event = await cursor.fetchone()
        
        if not event:
            await interaction.response.send_message("❌ Aucun événement associé à cet ID.", ephemeral=True)
            return
        
        if not event[12]:  # message_id
            await interaction.response.send_message("❌ Aucun message associé à cet événement.", ephemeral=True)
            return
        
        # Récupérer le canal d'animation
        animation_channel_id = int(os.getenv('ANIMATION_CHANNEL_ID', '0'))
        animation_channel = self.bot.get_channel(animation_channel_id)
        
        if not animation_channel:
            await interaction.response.send_message("❌ Canal d'animation introuvable.", ephemeral=True)
            return
        
        # Récupérer le message
        try:
            message = await animation_channel.fetch_message(event[12])
        except discord.NotFound:
            await interaction.response.send_message("❌ Message introuvable.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message("❌ Permissions insuffisantes pour accéder au message.", ephemeral=True)
            return
        
        await interaction.response.send_message("📝 Envoyez votre nouvelle description dans ce salon (vous avez 30 minutes). Tapez `cancel` pour annuler.", ephemeral=True)
        
        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel
        
        try:
            user_message = await self.bot.wait_for('message', check=check, timeout=1800)  # 30 minutes
            
            if user_message.content.lower() == "cancel":
                await self.rate_limiter.safe_delete(user_message)
                await interaction.followup.send("❌ Modification annulée.", ephemeral=True)
                return
            
            new_description = user_message.content
            await self.rate_limiter.safe_delete(user_message)
            
            # Mettre à jour la description dans la base de données
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("UPDATE events SET description = ? WHERE id = ?", (new_description, event[0]))
                await db.commit()
            
            # Préparer les mentions (ne pas les re-ping)
            mentions = self.get_event_mentions(event[6])  # event_type
            
            # Construire le nouveau contenu (sans re-ping)
            if mentions:
                new_content = f"{mentions}\n\n{new_description}"
            else:
                new_content = new_description
            
            # Modifier le message avec rate limiting
            await self.rate_limiter.safe_edit(message, content=new_content)
            
            await interaction.followup.send("✅ Message modifié avec succès !", ephemeral=True)
            
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Temps dépassé. Modification annulée.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erreur lors de la modification: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="deleteeventmessage", description="Supprimer le message d'annonce d'un événement")
    @app_commands.describe(identifier="ID de l'événement ou ID du message à supprimer")
    async def delete_event_message(self, interaction: discord.Interaction, identifier: str):
        if not self.has_permission(interaction.user):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        # Vérifier les limites de rate limiting
        can_proceed, error_msg = await self.check_command_rate_limit(interaction.user, "deleteeventmessage")
        if not can_proceed:
            await interaction.response.send_message(f"⏳ {error_msg}", ephemeral=True)
            return
        
        try:
            identifier = int(identifier)
        except ValueError:
            await interaction.response.send_message("❌ ID invalide.", ephemeral=True)
            return
        
        # Chercher l'événement par ID d'événement ou ID de message
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT id, title, event_date, event_time, managers, description, event_type,
                       created_at, created_by, reminder_1h_sent, reminder_15m_sent, reminder_start_sent, message_id
                FROM events WHERE id = ? OR message_id = ?
            """, (identifier, identifier))
            event = await cursor.fetchone()
        
        if not event:
            await interaction.response.send_message("❌ Aucun événement associé à cet ID.", ephemeral=True)
            return
        
        if not event[12]:  # message_id
            await interaction.response.send_message("❌ Aucun message associé à cet événement.", ephemeral=True)
            return
        
        # Récupérer le canal d'animation
        animation_channel_id = int(os.getenv('ANIMATION_CHANNEL_ID', '0'))
        animation_channel = self.bot.get_channel(animation_channel_id)
        
        if not animation_channel:
            await interaction.response.send_message("❌ Canal d'animation introuvable.", ephemeral=True)
            return
        
        # Récupérer et supprimer le message
        try:
            message = await animation_channel.fetch_message(event[12])
            await self.rate_limiter.safe_delete(message)
            
            # Retirer l'ID du message de la base de données
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("UPDATE events SET message_id = NULL WHERE id = ?", (event[0],))
                await db.commit()
            
            await interaction.response.send_message("✅ Message supprimé avec succès !", ephemeral=True)
            
        except discord.NotFound:
            await interaction.response.send_message("❌ Message introuvable (peut-être déjà supprimé).", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Permissions insuffisantes pour supprimer le message.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Erreur lors de la suppression: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="changedesc", description="Modifier la description d'un événement")
    @app_commands.describe(event_id="ID de l'événement à modifier")
    async def change_desc(self, interaction: discord.Interaction, event_id: int):
        if not self.has_permission(interaction.user):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        # Vérifier les limites de rate limiting
        can_proceed, error_msg = await self.check_command_rate_limit(interaction.user, "changedesc")
        if not can_proceed:
            await interaction.response.send_message(f"⏳ {error_msg}", ephemeral=True)
            return
        
        # Vérifier si l'événement existe avec requête explicite
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT id, title, event_date, event_time, managers, description, event_type,
                       created_at, created_by, reminder_1h_sent, reminder_15m_sent, reminder_start_sent, message_id
                FROM events WHERE id = ?
            """, (event_id,))
            event = await cursor.fetchone()
        
        if not event:
            await interaction.response.send_message("❌ Événement introuvable.", ephemeral=True)
            return
        
        await interaction.response.send_message("📝 Envoyez votre nouvelle description dans ce salon (vous avez 30 minutes). Tapez `cancel` pour annuler.", ephemeral=True)
        
        def check(message):
            return message.author == interaction.user and message.channel == interaction.channel
        
        try:
            message = await self.bot.wait_for('message', check=check, timeout=1800)  # 30 minutes
            
            if message.content.lower() == "cancel":
                await self.rate_limiter.safe_delete(message)
                await interaction.followup.send("❌ Modification annulée.", ephemeral=True)
                return
            
            description = message.content
            await self.rate_limiter.safe_delete(message)
            
            # Modifier la description
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("UPDATE events SET description = ? WHERE id = ?", (description, event_id))
                await db.commit()
            
            embed = discord.Embed(
                title="✅ Description Modifiée",
                description=f"La description de l'événement a été mise à jour avec succès.",
                color=0x00ff00
            )
            embed.add_field(name="📝 Nouvelle Description", value=description, inline=False)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Temps dépassé. Modification annulée.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erreur: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="showevent", description="Afficher les détails complets d'un événement")
    @app_commands.describe(event_id="ID de l'événement à afficher")
    async def show_event(self, interaction: discord.Interaction, event_id: int):
        if not self.has_permission(interaction.user):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        # Vérifier les limites de rate limiting
        can_proceed, error_msg = await self.check_command_rate_limit(interaction.user, "showevent")
        if not can_proceed:
            await interaction.response.send_message(f"⏳ {error_msg}", ephemeral=True)
            return
        
        # Récupérer l'événement avec requête explicite
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT id, title, event_date, event_time, managers, description, event_type,
                       created_at, created_by, reminder_1h_sent, reminder_15m_sent, reminder_start_sent, message_id
                FROM events WHERE id = ?
            """, (event_id,))
            event = await cursor.fetchone()
        
        if not event:
            await interaction.response.send_message("❌ Événement introuvable.", ephemeral=True)
            return
        
        # Créer l'embed avec les informations staff
        managers_data = json.loads(event[4])
        managers_list = ", ".join([f"<@{m['id']}>" for m in managers_data])
        
        event_type_emoji = {"Animation": "🎭", "Film": "🎬", "Soirée Jeux": "🎮"}.get(event[6], "🎪")
        
        embed = discord.Embed(
            title=f"{event_type_emoji} {event[1]} (Staff View)",
            color=0x3498db,
            timestamp=datetime.now(self.tz)
        )
        embed.add_field(name="📅 Date", value=self.format_french_date(event[2]), inline=True)
        embed.add_field(name="🕐 Heure", value=event[3], inline=True)
        embed.add_field(name="🆔 ID", value=str(event[0]), inline=True)
        embed.add_field(name="🏷️ Type", value=f"{event_type_emoji} {event[6] or 'Non spécifié'}", inline=True)
        embed.add_field(name="👥 Responsables", value=managers_list, inline=False)
        
        # Récupérer le créateur
        creator = self.bot.get_user(event[8])
        if creator:
            embed.set_footer(text=f"Créé par {creator.display_name}")
        
        # Envoyer l'embed d'abord
        await interaction.response.send_message(embed=embed)
        
        # Puis envoyer la description complète avec le markdown
        if event[5]:  # Description existe
            description_message = f"**📝 Description complète :**\n{event[5]}"
            # Si la description est trop longue, la diviser
            if len(description_message) > 2000:
                await interaction.followup.send("**📝 Description complète :**")
                # Diviser en chunks de 2000 caractères
                chunks = [event[5][i:i+2000] for i in range(0, len(event[5]), 2000)]
                for chunk in chunks:
                    await interaction.followup.send(chunk)
            else:
                await interaction.followup.send(description_message)
        else:
            await interaction.followup.send("📝 **Aucune description disponible**")
    
    @app_commands.command(name="planning", description="Afficher le planning de tous les événements futurs")
    async def planning(self, interaction: discord.Interaction):
        if not self.has_permission(interaction.user):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        # Vérifier les limites de rate limiting
        can_proceed, error_msg = await self.check_command_rate_limit(interaction.user, "planning")
        if not can_proceed:
            await interaction.response.send_message(f"⏳ {error_msg}", ephemeral=True)
            return
        
        # Récupérer les événements futurs avec requête explicite
        now = datetime.now(self.tz)
        current_date = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M")
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT id, title, event_date, event_time, managers, description, event_type,
                       created_at, created_by, reminder_1h_sent, reminder_15m_sent, reminder_start_sent, message_id
                FROM events 
                WHERE event_date > ? OR (event_date = ? AND event_time >= ?)
                ORDER BY event_date ASC, event_time ASC
            """, (current_date, current_date, current_time))
            events = await cursor.fetchall()
        
        if not events:
            await interaction.response.send_message("📅 Aucun événement planifié.", ephemeral=True)
            return
        
        # Pagination (5 événements par page)
        view = PlanningView(self, events, 0)
        embed = view.create_embed()
        
        await interaction.response.send_message(embed=embed, view=view)
    
    @app_commands.command(name="deleteevent", description="Supprimer un événement")
    @app_commands.describe(event_id="ID de l'événement à supprimer")
    async def delete_event(self, interaction: discord.Interaction, event_id: int):
        if not self.has_permission(interaction.user):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        # Vérifier les limites de rate limiting
        can_proceed, error_msg = await self.check_command_rate_limit(interaction.user, "deleteevent")
        if not can_proceed:
            await interaction.response.send_message(f"⏳ {error_msg}", ephemeral=True)
            return
        
        # Vérifier si l'événement existe avec requête explicite
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT id, title, event_date, event_time, managers, description, event_type,
                       created_at, created_by, reminder_1h_sent, reminder_15m_sent, reminder_start_sent, message_id
                FROM events WHERE id = ?
            """, (event_id,))
            event = await cursor.fetchone()
        
        if not event:
            await interaction.response.send_message("❌ Événement introuvable.", ephemeral=True)
            return
        
        # Vue de confirmation
        view = DeleteConfirmView(self, event_id, event)
        
        embed = discord.Embed(
            title="⚠️ Confirmation de Suppression",
            description=f"Êtes-vous sûr de vouloir supprimer l'événement **{event[1]}** ?\n\n⚠️ **Les responsables ne recevront plus de rappels pour cet événement.**",
            color=0xff9900
        )
        embed.add_field(name="📅 Date", value=self.format_french_date(event[2]), inline=True)
        embed.add_field(name="🕐 Heure", value=event[3], inline=True)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @app_commands.command(name="eventlist", description="Afficher la liste détaillée des événements")
    async def event_list(self, interaction: discord.Interaction):
        if not self.has_permission(interaction.user):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        # Vérifier les limites de rate limiting
        can_proceed, error_msg = await self.check_command_rate_limit(interaction.user, "eventlist")
        if not can_proceed:
            await interaction.response.send_message(f"⏳ {error_msg}", ephemeral=True)
            return
        
        # Récupérer les événements futurs avec requête explicite
        now = datetime.now(self.tz)
        current_date = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M")
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT id, title, event_date, event_time, managers, description, event_type,
                       created_at, created_by, reminder_1h_sent, reminder_15m_sent, reminder_start_sent, message_id
                FROM events 
                WHERE event_date > ? OR (event_date = ? AND event_time >= ?)
                ORDER BY event_date ASC, event_time ASC
            """, (current_date, current_date, current_time))
            events = await cursor.fetchall()
        
        if not events:
            await interaction.response.send_message("📅 Aucun événement planifié.", ephemeral=True)
            return
        
        # Pagination (un événement par page)
        view = EventListView(self, events, 0)
        embed = view.create_embed()
        
        await interaction.response.send_message(embed=embed, view=view)

# Conserver les classes View existantes sans modification majeure
# mais ajouter le rate limiting où nécessaire

class ManagerSelectionView(discord.ui.View):
    def __init__(self, cog, title, date, time, event_type, eligible_members, creator_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.title = title
        self.date = date
        self.time = time
        self.event_type = event_type
        self.eligible_members = eligible_members
        self.creator_id = creator_id
        
        # Créer les options pour le dropdown
        options = []
        for member in eligible_members[:25]:  # Discord limite à 25 options
            rank_name = cog.get_member_rank_name(member)
            
            options.append(discord.SelectOption(
                label=member.display_name,
                value=str(member.id),
                description=rank_name,
                emoji="👤"
            ))
        
        if options:
            self.manager_select = discord.ui.Select(
                placeholder="Sélectionnez les responsables (maximum 10)...",
                min_values=1,
                max_values=min(10, len(options)),
                options=options
            )
            self.manager_select.callback = self.manager_select_callback
            self.add_item(self.manager_select)
    
    async def manager_select_callback(self, interaction: discord.Interaction):
        selected_ids = [int(value) for value in self.manager_select.values]
        selected_members = [interaction.guild.get_member(user_id) for user_id in selected_ids]
        selected_members = [member for member in selected_members if member]  # Filtrer les None
        
        if not selected_members:
            await interaction.response.send_message("❌ Aucun responsable sélectionné.", ephemeral=True)
            return
        
        # Stocker temporairement les données de l'événement
        temp_id = f"{interaction.user.id}_{int(datetime.now().timestamp())}"
        self.cog.pending_events[temp_id] = {
            'title': self.title,
            'date': self.date,
            'time': self.time,
            'event_type': self.event_type,
            'managers': selected_members,
            'creator_id': self.creator_id,
            'channel_id': interaction.channel.id
        }
        
        # Demander la description via message
        managers_list = ", ".join([f"<@{member.id}>" for member in selected_members])
        
        embed = discord.Embed(
            title="📝 Description de l'Événement",
            description=f"**Responsables sélectionnés:** {managers_list}\n\nMaintenant, envoyez un message avec la description de votre événement.\nVous avez **30 minutes**. Tapez `cancel` pour annuler.",
            color=0xffa500
        )
        
        await interaction.response.edit_message(embed=embed, view=None)
        
        # Attendre le message de description
        def check(message):
            return message.author.id == self.creator_id and message.channel.id == interaction.channel.id
        
        try:
            message = await self.cog.bot.wait_for('message', check=check, timeout=1800)  # 30 minutes
            
            if message.content.lower() == "cancel":
                await self.cog.rate_limiter.safe_delete(message)
                if temp_id in self.cog.pending_events:
                    del self.cog.pending_events[temp_id]
                
                embed = discord.Embed(
                    title="❌ Création Annulée",
                    description="La création de l'événement a été annulée.",
                    color=0x999999
                )
                await interaction.edit_original_response(embed=embed)
                return
            
            description = message.content
            await self.cog.rate_limiter.safe_delete(message)
            
            # Créer la vue de confirmation
            view = EventConfirmationView(self.cog, self.cog.pending_events[temp_id], description, temp_id)
            
            # Créer l'embed de prévisualisation
            event_type_emoji = {"Animation": "🎭", "Film": "🎬", "Soirée Jeux": "🎮"}.get(self.event_type, "🎪")
            
            embed = discord.Embed(
                title="📋 Prévisualisation de l'Événement",
                color=0xffa500
            )
            embed.add_field(name="📝 Titre", value=self.title, inline=False)
            embed.add_field(name="🏷️ Type", value=f"{event_type_emoji} {self.event_type}", inline=True)
            embed.add_field(name="📅 Date", value=self.cog.format_french_date(self.date), inline=True)
            embed.add_field(name="🕐 Heure", value=self.time, inline=True)
            embed.add_field(name="👥 Responsables", value=managers_list, inline=False)
            embed.add_field(name="📝 Description", value=description, inline=False)
            embed.set_footer(text="Vérifiez les informations et confirmez la création")
            
            await interaction.edit_original_response(embed=embed, view=view)
            
        except asyncio.TimeoutError:
            if temp_id in self.cog.pending_events:
                del self.cog.pending_events[temp_id]
            
            embed = discord.Embed(
                title="⏰ Temps Dépassé",
                description="La création de l'événement a été annulée (temps dépassé).",
                color=0x999999
            )
            await interaction.edit_original_response(embed=embed)

class EventConfirmationView(discord.ui.View):
    def __init__(self, cog, event_data, description, temp_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.event_data = event_data
        self.description = description
        self.temp_id = temp_id
    
    @discord.ui.button(label="✅ Créer l'Événement", style=discord.ButtonStyle.success)
    async def confirm_creation(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Préparer les données des responsables
        managers_data = [{"id": member.id, "name": member.display_name} for member in self.event_data['managers']]
        
        # Insertion en base de données
        async with aiosqlite.connect(self.cog.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO events (title, event_date, event_time, managers, description, event_type, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.event_data['title'],
                self.event_data['date'],
                self.event_data['time'],
                json.dumps(managers_data),
                self.description,
                self.event_data['event_type'],
                datetime.now(self.cog.tz).isoformat(),
                self.event_data['creator_id']
            ))
            event_id = cursor.lastrowid
            await db.commit()
        
        # Nettoyer les données temporaires
        if self.temp_id in self.cog.pending_events:
            del self.cog.pending_events[self.temp_id]
        
        # Message de confirmation
        managers_list = ", ".join([f"<@{member.id}>" for member in self.event_data['managers']])
        event_type_emoji = {"Animation": "🎭", "Film": "🎬", "Soirée Jeux": "🎮"}.get(self.event_data['event_type'], "🎪")
        
        embed = discord.Embed(
            title="🎉 Événement Créé avec Succès",
            color=0x00ff00,
            timestamp=datetime.now(self.cog.tz)
        )
        embed.add_field(name="🆔 ID", value=str(event_id), inline=True)
        embed.add_field(name="📝 Titre", value=self.event_data['title'], inline=True)
        embed.add_field(name="🏷️ Type", value=f"{event_type_emoji} {self.event_data['event_type']}", inline=True)
        embed.add_field(name="📅 Date", value=self.cog.format_french_date(self.event_data['date']), inline=True)
        embed.add_field(name="🕐 Heure", value=self.event_data['time'], inline=True)
        embed.add_field(name="👥 Responsables", value=managers_list, inline=False)
        embed.set_footer(text=f"Créé par {interaction.user.display_name}")
        
        await interaction.response.edit_message(embed=embed, view=None)
    
    @discord.ui.button(label="❌ Annuler", style=discord.ButtonStyle.danger)
    async def cancel_creation(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Nettoyer les données temporaires
        if self.temp_id in self.cog.pending_events:
            del self.cog.pending_events[self.temp_id]
        
        embed = discord.Embed(
            title="❌ Création Annulée",
            description="La création de l'événement a été annulée.",
            color=0x999999
        )
        
        await interaction.response.edit_message(embed=embed, view=None)

class DeleteConfirmView(discord.ui.View):
    def __init__(self, cog, event_id, event_data):
        super().__init__(timeout=30)
        self.cog = cog
        self.event_id = event_id
        self.event_data = event_data
    
    @discord.ui.button(label="✅ Confirmer", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Supprimer l'événement de la base de données
        # Cela empêchera automatiquement l'envoi de rappels futurs
        async with aiosqlite.connect(self.cog.db_path) as db:
            await db.execute("DELETE FROM events WHERE id = ?", (self.event_id,))
            await db.commit()
        
        embed = discord.Embed(
            title="✅ Événement Supprimé",
            description=f"L'événement **{self.event_data[1]}** a été supprimé avec succès.\n\n📧 Les rappels automatiques ont été annulés.",
            color=0x00ff00
        )
        
        await interaction.response.edit_message(embed=embed, view=None)
    
    @discord.ui.button(label="❌ Annuler", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="❌ Suppression Annulée",
            description="L'événement n'a pas été supprimé.",
            color=0x999999
        )
        
        await interaction.response.edit_message(embed=embed, view=None)

class PlanningView(discord.ui.View):
    def __init__(self, cog, events, page=0):
        super().__init__(timeout=300)
        self.cog = cog
        self.events = events
        self.page = page
        self.per_page = 5  # 5 événements par page
        self.max_pages = (len(events) - 1) // self.per_page + 1
        
        # Mise à jour des boutons
        self.update_buttons()
    
    def update_buttons(self):
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.max_pages - 1
    
    def create_embed(self):
        embed = discord.Embed(
            title="📅 Planning des Événements",
            color=0x3498db,
            timestamp=datetime.now(pytz.timezone('Europe/Paris'))
        )
        
        start_idx = self.page * self.per_page
        end_idx = start_idx + self.per_page
        page_events = self.events[start_idx:end_idx]
        
        if not page_events:
            embed.description = "Aucun événement sur cette page."
            return embed
        
        for event in page_events:
            managers_data = json.loads(event[4])
            managers_list = ", ".join([f"<@{m['id']}>" for m in managers_data])
            
            event_type_emoji = {"Animation": "🎭", "Film": "🎬", "Soirée Jeux": "🎮"}.get(event[6], "🎪")
            
            embed.add_field(
                name=f"{event_type_emoji} {event[1]} (ID: {event[0]})",
                value=f"📅 **Date:** {self.cog.format_french_date(event[2])}\n🕐 **Heure:** {event[3]}\n👥 **Responsables:** {managers_list}",
                inline=False
            )
        
        embed.set_footer(text=f"Page {self.page + 1}/{self.max_pages} • {len(self.events)} événement(s) total")
        
        return embed
    
    @discord.ui.button(label="⬅️ Précédent", style=discord.ButtonStyle.primary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.update_buttons()
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="➡️ Suivant", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.update_buttons()
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

class EventListView(discord.ui.View):
    def __init__(self, cog, events, page=0):
        super().__init__(timeout=300)
        self.cog = cog
        self.events = events
        self.page = page
        self.per_page = 1  # Un événement par page
        self.max_pages = len(events)
        
        # Mise à jour des boutons
        self.update_buttons()
    
    def update_buttons(self):
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.max_pages - 1
    
    def create_embed(self):
        if not self.events:
            embed = discord.Embed(
                title="📅 Liste des Événements",
                description="Aucun événement disponible.",
                color=0x3498db
            )
            return embed
        
        event = self.events[self.page]
        managers_data = json.loads(event[4])
        managers_list = ", ".join([f"<@{m['id']}>" for m in managers_data])
        
        event_type_emoji = {"Animation": "🎭", "Film": "🎬", "Soirée Jeux": "🎮"}.get(event[6], "🎪")
        
        embed = discord.Embed(
            title=f"{event_type_emoji} {event[1]}",
            color=0x3498db,
            timestamp=datetime.now(pytz.timezone('Europe/Paris'))
        )
        
        embed.add_field(name="🆔 ID", value=str(event[0]), inline=True)
        embed.add_field(name="🏷️ Type", value=f"{event_type_emoji} {event[6] or 'Non spécifié'}", inline=True)
        embed.add_field(name="📅 Date", value=self.cog.format_french_date(event[2]), inline=True)
        embed.add_field(name="🕐 Heure", value=event[3], inline=True)
        embed.add_field(name="👥 Responsables", value=managers_list, inline=False)
        
        # Ajouter la description complète avec formatage markdown
        if event[5]:  # Description existe
            # Limiter la description à 1024 caractères pour l'embed
            description = event[5]
            if len(description) > 1020:
                description = description[:1020] + "..."
            embed.add_field(name="📝 Description", value=description, inline=False)
        else:
            embed.add_field(name="📝 Description", value="*Aucune description*", inline=False)
        
        # Récupérer le créateur
        creator = self.cog.bot.get_user(event[8])
        if creator:
            footer_text = f"Créé par {creator.display_name} • Page {self.page + 1}/{self.max_pages}"
        else:
            footer_text = f"Page {self.page + 1}/{self.max_pages}"
        
        embed.set_footer(text=footer_text)
        
        return embed
    
    @discord.ui.button(label="⬅️ Précédent", style=discord.ButtonStyle.primary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.update_buttons()
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="➡️ Suivant", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.update_buttons()
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

async def setup(bot):
    await bot.add_cog(AnimationPlanning(bot))
