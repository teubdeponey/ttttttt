import json
import pytz  
import asyncio
import string
import random
from datetime import datetime, timedelta 
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest as TelegramBadRequest


class AdminFeatures:
    paris_tz = pytz.timezone('Europe/Paris')
    STATES = {
        'CHOOSING': 'CHOOSING',
        'WAITING_CODE_NUMBER': 'WAITING_CODE_NUMBER'
    }
    def __init__(self, users_file: str = 'data/users.json', access_codes_file: str = 'data/access_codes.json', broadcasts_file: str = 'data/broadcasts.json', config_file: str = 'config/config.json'):  # Ajout du paramètre config_file
        self.users_file = users_file
        self.access_codes_file = access_codes_file
        self.broadcasts_file = broadcasts_file
        self.config_file = config_file  
        self._users = self._load_users()
        self._access_codes = self._load_access_codes()
        self.broadcasts = self._load_broadcasts()
        self.admin_ids = self._load_admin_ids()
        self.cleanup_expired_codes() 

    def _load_admin_ids(self) -> list:
        """Charge les IDs admin depuis le fichier de configuration"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return config.get('admin_ids', [])
        except Exception as e:
            print(f"Erreur lors du chargement des admin IDs : {e}")
            return []

    def _load_access_codes(self):
        """Charge les codes d'accès depuis le fichier"""
        try:
            with open(self.access_codes_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data
        except FileNotFoundError:
            print(f"Access codes file not found: {self.access_codes_file}")
            return {"authorized_users": []}
        except json.JSONDecodeError as e:
            print(f"Error decoding access codes file: {e}")
            return {"authorized_users": []}
        except Exception as e:
            print(f"Unexpected error loading access codes: {e}")
            return {"authorized_users": []}

    def is_user_authorized(self, user_id: int) -> bool:
        """Vérifie si l'utilisateur est autorisé"""
        # Recharger les codes d'accès à chaque vérification
        self._access_codes = self._load_access_codes()
        
        # Convertir l'ID en nombre et vérifier sa présence
        return int(user_id) in self._access_codes.get("authorized_users", [])

    def is_user_banned(self, user_id: int) -> bool:
        """Vérifie si l'utilisateur est banni"""
        self._access_codes = self._load_access_codes()
        return int(user_id) in self._access_codes.get("banned_users", [])

    def reload_access_codes(self):
        """Recharge les codes d'accès depuis le fichier"""
        self._access_codes = self._load_access_codes()
        return self._access_codes.get("authorized_users", [])

    def _load_users(self):
        """Charge les utilisateurs depuis le fichier"""
        try:
            with open(self.users_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _save_users(self):
        """Sauvegarde les utilisateurs"""
        try:
            with open(self.users_file, 'w', encoding='utf-8') as f:
                json.dump(self._users, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Erreur lors de la sauvegarde des utilisateurs : {e}")

    def _create_message_keyboard(self):
        """Crée le clavier standard pour les messages"""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Menu Principal", callback_data="start_cmd")
        ]])

    def _load_broadcasts(self):
        """Charge les broadcasts depuis le fichier"""
        try:
            with open(self.broadcasts_file, 'r', encoding='utf-8') as f:
                broadcasts = json.load(f)
                # Vérifier et corriger la structure de chaque broadcast
                for broadcast_id, broadcast in broadcasts.items():
                    if 'message_ids' not in broadcast:
                        broadcast['message_ids'] = {}
                    # Assurer que les user_ids sont des strings
                    if 'message_ids' in broadcast:
                        broadcast['message_ids'] = {
                            str(user_id): msg_id 
                            for user_id, msg_id in broadcast['message_ids'].items()
                        }
                return broadcasts
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            print("Erreur de décodage JSON, création d'un nouveau fichier broadcasts")
            return {}

    def _save_broadcasts(self):
        """Sauvegarde les broadcasts"""
        try:
            with open(self.broadcasts_file, 'w', encoding='utf-8') as f:
                json.dump(self.broadcasts, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Erreur lors de la sauvegarde des broadcasts : {e}")

    def _save_access_codes(self):
        """Sauvegarde les codes d'accès"""
        try:
            with open(self.access_codes_file, 'w', encoding='utf-8') as f:
                json.dump(self._access_codes, f, indent=4)
        except Exception as e:
            print(f"Erreur lors de la sauvegarde des codes d'accès : {e}")

    def authorize_user(self, user_id: int) -> bool:
        """Ajoute un utilisateur à la liste des utilisateurs autorisés"""
        try:
            if "authorized_users" not in self._access_codes:
                self._access_codes["authorized_users"] = []
        
            user_id = int(user_id)
            if user_id not in self._access_codes["authorized_users"]:
                self._access_codes["authorized_users"].append(user_id)
                self._save_access_codes()
                return True
            return False
        except Exception as e:
            print(f"Erreur lors de l'autorisation de l'utilisateur : {e}")
            return False

    def mark_code_as_used(self, code: str, user_id: int) -> bool:
        """Marque un code comme utilisé et autorise l'utilisateur"""
        try:
            if "codes" not in self._access_codes:
                return False
        
            for code_entry in self._access_codes["codes"]:
                if code_entry["code"] == code and not code_entry["used"]:
                    code_entry["used"] = True
                    code_entry["used_by"] = user_id
                    self.authorize_user(user_id)
                    self._save_access_codes()
                    return True
            return False
        except Exception as e:
            print(f"Erreur lors du marquage du code comme utilisé : {e}")
            return False

    def generate_temp_code(self, generator_id: int, generator_username: str = None) -> tuple:
        """Génère un code d'accès temporaire"""
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        expiration = (datetime.utcnow() + timedelta(days=2)).isoformat()  # 48h

        if "codes" not in self._access_codes:
            self._access_codes["codes"] = []

        # Ajouter le code dans la section "codes"
        self._access_codes["codes"].append({
            'code': code,
            'expiration': expiration,
            'created_by': generator_id,  # Utiliser le même format que les autres codes
            'used': False
        })

        self._save_access_codes()
        return code, expiration

    def list_temp_codes(self, show_used: bool = False) -> list:
        """Liste les codes temporaires"""
        current_time = datetime.utcnow().isoformat()
        codes = self._access_codes.get("codes", [])

        if show_used:
            # Retourner uniquement les codes marqués comme utilisés
            return [code for code in codes if code.get("used") is True]
        else:
            # Retourner les codes non utilisés et non expirés
            return [code for code in codes 
                    if not code.get("used") and code.get("expiration", "") > current_time]

    def cleanup_expired_codes(self):
        """Supprime complètement les codes expirés"""
        current_time = datetime.utcnow().isoformat()
    
        if "codes" not in self._access_codes:
            return
    
        # Garder uniquement les codes non expirés
        self._access_codes["codes"] = [
            code for code in self._access_codes["codes"]
            if code["expiration"] > current_time
        ]
    
        # Sauvegarder les modifications
        self._save_access_codes()

    def mark_code_as_used(self, code: str, user_id: int, username: str = None) -> bool:
        """Marque un code comme utilisé et autorise l'utilisateur"""
        try:
            if "codes" not in self._access_codes:
                return False
        
            for code_entry in self._access_codes["codes"]:
                if code_entry["code"] == code and not code_entry["used"]:
                    code_entry["used"] = True
                    code_entry["used_by"] = {
                        "id": user_id,
                        "username": username
                    }
                    # Ajouter l'utilisateur à la liste des autorisés
                    if "authorized_users" not in self._access_codes:
                        self._access_codes["authorized_users"] = []
                    if user_id not in self._access_codes["authorized_users"]:
                        self._access_codes["authorized_users"].append(user_id)
                    self._save_access_codes()
                    return True
            return False
        except Exception as e:
            print(f"Erreur lors du marquage du code comme utilisé : {e}")
            return False

    async def handle_generate_multiple_codes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gère la génération de plusieurs codes d'accès"""
        if str(update.effective_user.id) not in self.admin_ids:
            await update.callback_query.answer("❌ Vous n'êtes pas autorisé à utiliser cette fonction.")
            return self.STATES['CHOOSING']

        keyboard = [
            [InlineKeyboardButton("1️⃣ Un code", callback_data="gen_code_1")],
            [InlineKeyboardButton("5️⃣ Cinq codes", callback_data="gen_code_5")],
            [InlineKeyboardButton("🔢 Nombre personnalisé", callback_data="gen_code_custom")],
            [InlineKeyboardButton("🔙 Retour", callback_data="back_to_home")]
        ]
    
        await update.callback_query.edit_message_text(
            "🎫 Génération de codes d'accès\n\n"
            "Choisissez le nombre de codes à générer :",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return self.STATES['CHOOSING']

    async def handle_custom_code_number(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gère la demande de nombre personnalisé de codes"""
        if str(update.effective_user.id) not in self.admin_ids:
            await update.callback_query.answer("❌ Vous n'êtes pas autorisé à utiliser cette fonction.")
            return self.STATES['CHOOSING']
    
        keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data="generate_multiple_codes")]]
    
        await update.callback_query.edit_message_text(
            "🔢 Génération personnalisée\n\n"
            "Envoyez le nombre de codes que vous souhaitez générer (maximum 20) :",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return self.STATES['WAITING_CODE_NUMBER']

    async def handle_code_number_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Traite le nombre de codes demandé"""
        if str(update.effective_user.id) not in self.admin_ids:
            await update.message.reply_text("❌ Vous n'êtes pas autorisé à utiliser cette fonction.")
            return self.STATES['CHOOSING']

        try:
            num = int(update.message.text)
            if num <= 0 or num > 20:
                raise ValueError()
            
            # Supprimer le message de l'utilisateur
            await update.message.delete()
        
            codes_text = "🎫 *Codes générés :*\n\n"
            for _ in range(num):
                code, expiration = self.generate_temp_code(
                    update.effective_user.id,
                    update.effective_user.username
                )
                exp_date = datetime.fromisoformat(expiration)
                exp_str = exp_date.strftime("%d/%m/%Y à %H:%M")
                codes_text += f"📎 *Code:* `{code}`\n"
                codes_text += f"⚠️ _Code à usage unique, expire le {exp_str}_\n"
                codes_text += f"👤 _Généré par:_ @{update.effective_user.username or 'Unknown'}\n\n"
        
            keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data="generate_multiple_codes")]]
        
            await update.message.reply_text(
                codes_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return self.STATES['CHOOSING']
        
        except ValueError:
            keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data="generate_multiple_codes")]]
            await update.message.reply_text(
                "❌ Erreur : Veuillez entrer un nombre valide entre 1 et 20.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return self.STATES['WAITING_CODE_NUMBER']

    async def generate_codes(self, update: Update, context: ContextTypes.DEFAULT_TYPE, num_codes: int = 1):
        """Génère un nombre spécifié de codes"""
        if str(update.effective_user.id) not in self.admin_ids:
            await update.callback_query.answer("❌ Vous n'êtes pas autorisé à utiliser cette fonction.")
            return self.STATES['CHOOSING']

        codes_text = "🎫 *Codes générés :*\n\n"
        for _ in range(num_codes):
            code, expiration = self.generate_temp_code(
                update.effective_user.id,
                update.effective_user.username
            )
            exp_date = datetime.fromisoformat(expiration)
            exp_str = exp_date.strftime("%d/%m/%Y à %H:%M")
        
            # Format modifié avec uniquement le code copiable
            codes_text += "*Code d'accès temporaire :*\n"
            codes_text += f"`{code}`\n"  # Seul le code est dans un bloc copiable
            codes_text += "⚠️ Code à usage unique\n"
            codes_text += f"⏰ Expire le {exp_str}\n\n"

        keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data="generate_multiple_codes")]]

        await update.callback_query.edit_message_text(
            codes_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return self.STATES['CHOOSING']

    async def back_to_generate_codes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):  # Ajout de self
        """Retourne au menu de génération de codes"""
        if str(update.effective_user.id) not in self.admin_ids:
            await update.callback_query.answer("❌ Vous n'êtes pas autorisé à utiliser cette fonction.")
            return self.STATES['CHOOSING']

        query = update.callback_query
        await query.answer()
    
        keyboard = [
            [InlineKeyboardButton("1️⃣ Un code", callback_data="gen_code_1")],
            [InlineKeyboardButton("5️⃣ Cinq codes", callback_data="gen_code_5")],
            [InlineKeyboardButton("🔢 Nombre personnalisé (20 maximum)", callback_data="gen_code_custom")],
            [InlineKeyboardButton("🔙 Retour", callback_data="back_to_home")]
        ]
    
        await query.edit_message_text(
            "🎫 Génération de codes d'accès\n\n"
            "Choisissez le nombre de codes à générer :",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return self.STATES['CHOOSING']

    async def show_codes_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Affiche l'historique des codes"""
        try:
            if str(update.effective_user.id) not in self.admin_ids:
                await update.callback_query.answer("❌ Vous n'êtes pas autorisé à utiliser cette fonction.")
                return self.STATES['CHOOSING']

            showing_used = context.user_data.get('showing_used_codes', False)
            all_codes = self.list_temp_codes(showing_used)

            # Paginer les résultats
            if len(all_codes) > 10:
                current_page = context.user_data.get('codes_page', 0)
                total_pages = (len(all_codes) + 9) // 10
                start_idx = current_page * 10
                end_idx = min(start_idx + 10, len(all_codes))
                codes = all_codes[start_idx:end_idx]
            else:
                codes = all_codes
                current_page = 0
                total_pages = 1

            if not codes:
                text = "📜 *Aucun code à afficher*"
            else:
                text = "📜 *Codes " + ("utilisés" if showing_used else "actifs") + " :*\n\n"
                for code in codes:
                    text += "*Code d'accès temporaire :*\n"
                    if showing_used and "used_by" in code:
                        used_by = code["used_by"]
                        user_id = used_by.get("id", "N/A")

                        # Récupérer les informations de l'utilisateur
                        user_data = self._users.get(str(user_id), {})
                        username = user_data.get('username', '')
                        first_name = user_data.get('first_name', '')
                        last_name = user_data.get('last_name', '')

                        # Échapper les caractères spéciaux
                        if username:
                            username = username.replace('_', r'\_').replace('*', r'\*').replace('`', r'\`')
                        if first_name:
                            first_name = first_name.replace('_', r'\_').replace('*', r'\*').replace('`', r'\`')
                        if last_name:
                            last_name = last_name.replace('_', r'\_').replace('*', r'\*').replace('`', r'\`')

                        # Construire le nom d'affichage
                        display_parts = []
                        if first_name:
                            display_parts.append(first_name)
                        if last_name:
                            display_parts.append(last_name)

                        if username:
                            display_name = f"@{username}"
                        elif display_parts:
                            display_name = " ".join(display_parts)
                        else:
                            display_name = str(user_id)

                        text += f"`{code['code']}`\n"  # Seul le code est copiable
                        text += f"✅ Utilisé par : {display_name} (`{user_id}`)\n\n"
                    else:
                        exp_date = datetime.fromisoformat(code["expiration"])
                        exp_str = exp_date.strftime("%d/%m/%Y à %H:%M")
                        text += f"`{code['code']}`\n"  # Seul le code est copiable
                        text += f"⚠️ Code à usage unique\n"
                        text += f"⏰ Expire le {exp_str}\n\n"

            active_btn_text = "📍 Codes actifs" if not showing_used else "Codes actifs"
            used_btn_text = "📍 Codes utilisés" if showing_used else "Codes utilisés"

            keyboard = [
                [
                    InlineKeyboardButton(active_btn_text, callback_data="show_active_codes"),
                    InlineKeyboardButton(used_btn_text, callback_data="show_used_codes")
                ],
                [InlineKeyboardButton("🔙 Retour", callback_data="back_to_home")]
            ]

            # Ajouter les boutons de pagination si nécessaire
            if len(all_codes) > 10:
                nav_buttons = []
                if current_page > 0:
                    nav_buttons.append(InlineKeyboardButton("◀️", callback_data="prev_codes_page"))
                nav_buttons.append(InlineKeyboardButton(f"{current_page + 1}/{total_pages}", callback_data="current_page"))
                if current_page < total_pages - 1:
                    nav_buttons.append(InlineKeyboardButton("▶️", callback_data="next_codes_page"))
            
                if nav_buttons:
                    keyboard.insert(-2, nav_buttons)

            await update.callback_query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return self.STATES['CHOOSING']

        except TelegramBadRequest as e:
            if str(e) == "Message is not modified":
                await update.callback_query.answer("Liste déjà à jour!")
            else:
                raise
            return self.STATES['CHOOSING']


    async def show_user_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_type: str = None):
        """Affiche une liste paginée d'utilisateurs selon leur type (validés/en attente/bannis)"""
        try:
            query = update.callback_query
            
            # Si user_type n'est pas fourni, l'extraire du callback_data
            if user_type is None:
                callback_data = query.data
                user_type = callback_data.split('_')[2]

            # Récupérer la page depuis le callback_data
            page_data = query.data.split('_')
            try:
                current_page = int(page_data[-1])
            except (ValueError, IndexError):
                current_page = 0
            
            # Nombre d'utilisateurs par page
            users_per_page = 30
            
            # Récupérer et mettre à jour les listes d'utilisateurs
            self._access_codes = self._load_access_codes()
            authorized_users = set(self._access_codes.get("authorized_users", []))
            banned_users = set(self._access_codes.get("banned_users", []))
            
            # Filtrer les utilisateurs selon le type demandé
            filtered_users = []
            for user_id, user_data in self._users.items():
                user_id_int = int(user_id)
                if user_type == 'validated' and user_id_int in authorized_users:
                    filtered_users.append((user_id, user_data))
                elif user_type == 'pending' and user_id_int not in authorized_users and user_id_int not in banned_users:
                    filtered_users.append((user_id, user_data))
                elif user_type == 'banned' and user_id_int in banned_users:
                    filtered_users.append((user_id, user_data))
            
            # Calculer la pagination
            total_users = len(filtered_users)
            total_pages = max(1, (total_users + users_per_page - 1) // users_per_page)
            current_page = min(current_page, total_pages - 1)  # S'assurer que la page est valide
            start_idx = current_page * users_per_page
            end_idx = min(start_idx + users_per_page, total_users)
            
            # Préparer le titre selon le type
            titles = {
                'validated': "✅ Utilisateurs validés",
                'pending': "⏳ Utilisateurs en attente",
                'banned': "🚫 Utilisateurs bannis"
            }
            
            text = f"{titles.get(user_type, 'Liste des utilisateurs')}\n\n"
            if total_pages > 1:
                text += f"Page {current_page + 1}/{total_pages}\n\n"
            
            # Afficher les utilisateurs
            if filtered_users:
                for user_id, user_data in filtered_users[start_idx:end_idx]:
                    username = user_data.get('username', '')
                    first_name = user_data.get('first_name', '')
                    last_name = user_data.get('last_name', '')
                    
                    # Construire le nom d'affichage
                    if username:
                        display_name = f"@{username}"
                    elif first_name and last_name:
                        display_name = f"{first_name} {last_name}"
                    elif first_name:
                        display_name = first_name
                    else:
                        display_name = "Sans nom"
                    
                    # Afficher l'utilisateur avec l'ID
                    text += f"• {display_name} ({user_id})\n"
            else:
                text += "Aucun utilisateur dans cette catégorie."
            
            # Construire le clavier avec pagination
            keyboard = []
            
            # Boutons de pagination si nécessaire
            if total_pages > 1:
                nav_row = []
                if current_page > 0:
                    nav_row.append(InlineKeyboardButton("◀️", callback_data=f"user_list_{user_type}_{current_page - 1}"))
                nav_row.append(InlineKeyboardButton(f"{current_page + 1}/{total_pages}", callback_data="current_page"))
                if current_page < total_pages - 1:
                    nav_row.append(InlineKeyboardButton("▶️", callback_data=f"user_list_{user_type}_{current_page + 1}"))
                if nav_row:
                    keyboard.append(nav_row)
            
            # Bouton de retour
            keyboard.append([InlineKeyboardButton("🔙 Retour à la gestion", callback_data="manage_users")])
            
            try:
                await query.edit_message_text(
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except TelegramBadRequest as e:
                if "Message is not modified" not in str(e):
                    raise
            
            return "CHOOSING"
        
        except Exception as e:
            print(f"Erreur dans show_user_list : {e}")
            try:
                await update.callback_query.answer("Une erreur est survenue.")
            except:
                pass
            return "CHOOSING" 
            
    async def show_ban_user_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Affiche le menu pour bannir un utilisateur"""
        keyboard = [
            [InlineKeyboardButton("🔙 Retour", callback_data="manage_users")]
        ]
        
        await update.callback_query.edit_message_text(
            "*🚫 Bannir un utilisateur*\n\n"
            "Pour bannir un utilisateur, envoyez soit :\n"
            "• Son nom d'utilisateur (ex: @username)\n"
            "• Son ID (ex: 123456789)\n\n"
            "_Note : Les caractères spéciaux sont automatiquement gérés._",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        
        return "WAITING_BAN_INPUT"

    async def handle_ban_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gère l'entrée de l'utilisateur pour le bannissement"""
        try:
            input_text = update.message.text.strip()
            
            # Supprimer le message de l'utilisateur
            await update.message.delete()
            
            success_message = None
            user_found = False
            
            # Identifier si c'est un username ou un ID
            if input_text.startswith('@'):
                username = input_text[1:]
                for user_id, user_data in self._users.items():
                    if str(user_data.get('username', '')).lower() == username.lower():
                        await self.ban_user(int(user_id), context)
                        user_found = True
                        success_message = f"✅ Utilisateur {input_text} banni avec succès."
                        break
                if not user_found:
                    success_message = "❌ Utilisateur non trouvé."
            else:
                try:
                    user_id = int(input_text)
                    await self.ban_user(user_id, context)
                    user_found = True
                    success_message = f"✅ Utilisateur {user_id} banni avec succès."
                except ValueError:
                    success_message = "❌ Format d'ID invalide."
            
            if success_message:
                # Envoyer un message de confirmation temporaire
                message = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=success_message,
                    parse_mode=None
                )
                
                # Supprimer le message après 3 secondes
                async def delete_message():
                    await asyncio.sleep(3)
                    try:
                        await message.delete()
                    except Exception as e:
                        print(f"Error deleting message: {e}")
                
                asyncio.create_task(delete_message())
            
            # Retourner au menu des utilisateurs seulement si l'utilisateur a été trouvé et banni
            if user_found:
                keyboard = [
                    [InlineKeyboardButton("🔙 Retour", callback_data="manage_users")]
                ]
                
                await update.message.reply_text(
                    "Retour au menu de gestion des utilisateurs...",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            
            return "CHOOSING"
            
        except Exception as e:
            print(f"Erreur dans handle_ban_input : {e}")
            return "CHOOSING"
        
    async def show_unban_user_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Affiche le menu pour débannir un utilisateur"""
        try:
            # Récupérer la liste des utilisateurs bannis
            banned_users = self._access_codes.get("banned_users", [])
            
            text = "🔓 *Débannir un utilisateur*\n\n"
            
            if not banned_users:
                text += "Aucun utilisateur banni actuellement."
                keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data="manage_users")]]
            else:
                text += "Sélectionnez un utilisateur à débannir :\n\n"
                keyboard = []
                
                # Afficher chaque utilisateur banni avec un bouton pour le débannir
                for user_id in banned_users:
                    user_data = self._users.get(str(user_id), {})
                    username = user_data.get('username', '')
                    first_name = user_data.get('first_name', '')
                    last_name = user_data.get('last_name', '')
                    
                    # Échapper les caractères spéciaux pour le Markdown
                    if username:
                        username = username.replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
                    if first_name:
                        first_name = first_name.replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
                    if last_name:
                        last_name = last_name.replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
                    
                    # Construire le nom d'affichage
                    if username:
                        display_name = f"@{username}"
                    elif first_name and last_name:
                        display_name = f"{first_name} {last_name}"
                    elif first_name:
                        display_name = first_name
                    else:
                        display_name = f"Utilisateur {user_id}"
                    
                    # Ajouter les informations de l'utilisateur au texte
                    text += f"• {display_name} `{user_id}`\n"
                    
                    # Pour le bouton, utiliser le nom non échappé
                    raw_display_name = f"@{user_data.get('username', '')}" if user_data.get('username') else display_name
                    
                    # Ajouter un bouton pour débannir cet utilisateur
                    keyboard.append([
                        InlineKeyboardButton(f"🔓 Débannir {raw_display_name}", callback_data=f"unban_{user_id}")
                    ])
                
                keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="manage_users")])
            
            await update.callback_query.edit_message_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            
            return "CHOOSING"
            
        except Exception as e:
            print(f"Erreur dans show_unban_user_menu : {e}")
            await update.callback_query.answer("Une erreur est survenue.")
            return "CHOOSING"
        
    async def handle_unban_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gère l'entrée de l'utilisateur pour le débannissement"""
        try:
            input_text = update.message.text.strip()
            
            # Supprimer le message de l'utilisateur
            await update.message.delete()
            
            # Identifier si c'est un username ou un ID
            if input_text.startswith('@'):
                username = input_text[1:]
                user_found = False
                for user_id, user_data in self._users.items():
                    if user_data.get('username') == username:
                        await self.unban_user(int(user_id))
                        user_found = True
                        success_message = f"✅ Utilisateur @{username} débanni avec succès."
                        break
                if not user_found:
                    success_message = "❌ Utilisateur non trouvé."
            else:
                try:
                    user_id = int(input_text)
                    await self.unban_user(user_id)
                    success_message = f"✅ Utilisateur {user_id} débanni avec succès."
                except ValueError:
                    success_message = "❌ Format d'ID invalide."
            
            # Envoyer un message de confirmation temporaire
            message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=success_message,
                parse_mode='Markdown'
            )
            
            # Supprimer le message après 3 secondes
            async def delete_message():
                await asyncio.sleep(3)
                try:
                    await message.delete()
                except Exception as e:
                    print(f"Error deleting message: {e}")
            
            asyncio.create_task(delete_message())
            
            # Retourner au menu de gestion des utilisateurs
            await self.handle_user_management(update, context)
            return "CHOOSING"
        
        except Exception as e:
            print(f"Erreur dans handle_unban_input : {e}")
            return "CHOOSING"

    async def toggle_codes_view(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Bascule entre codes actifs et utilisés"""
        if str(update.effective_user.id) not in self.admin_ids:
            await update.callback_query.answer("❌ Vous n'êtes pas autorisé à utiliser cette fonction.")
            return self.STATES['CHOOSING']
    
        context.user_data['showing_used_codes'] = update.callback_query.data == "show_used_codes"
        return await self.show_codes_history(update, context)

    async def handle_codes_pagination(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gère la pagination des codes"""
        query = update.callback_query.data
        current_page = context.user_data.get('codes_page', 0)
    
        codes = self.list_temp_codes(context.user_data.get('showing_used_codes', False))
        total_pages = (len(codes) + 9) // 10

        if query == "prev_codes_page" and current_page > 0:
            context.user_data['codes_page'] = current_page - 1
        elif query == "next_codes_page" and current_page < total_pages - 1:
            context.user_data['codes_page'] = current_page + 1
    
        return await self.show_codes_history(update, context)

    async def ban_user(self, user_id: int, context: ContextTypes.DEFAULT_TYPE = None) -> bool:
        """Banni un utilisateur et réinitialise sa conversation"""
        try:
            # Convertir en int si c'est un string
            user_id = int(user_id)
            
            # Retirer l'utilisateur des codes d'accès s'il y est
            if user_id in self._access_codes.get("authorized_users", []):
                self._access_codes["authorized_users"].remove(user_id)
                self._save_access_codes()

            # Ajouter l'utilisateur à la liste des bannis
            if "banned_users" not in self._access_codes:
                self._access_codes["banned_users"] = []
            
            if user_id not in self._access_codes["banned_users"]:
                self._access_codes["banned_users"].append(user_id)
                self._save_access_codes()
                
                # Si le contexte est fourni, nettoyer l'historique et envoyer le message de bienvenue
                if context and context.bot:
                    async def clean_and_reset():
                        try:
                            # Supprimer les messages par lots de 10
                            for start_id in range(1, 1000, 10):
                                tasks = []
                                for msg_id in range(start_id, start_id + 10):
                                    tasks.append(
                                        asyncio.create_task(
                                            context.bot.delete_message(
                                                chat_id=user_id, 
                                                message_id=msg_id
                                            )
                                        )
                                    )
                                # Attendre que tous les messages du lot soient traités
                                await asyncio.gather(*tasks, return_exceptions=True)
                                # Petite pause entre les lots
                                await asyncio.sleep(0.1)
                            
                            # Envoyer le message de bienvenue
                            await context.bot.send_message(
                                chat_id=user_id,
                                text="👋 *Bienvenue !*\n\nPour accéder au bot, veuillez entrer votre code d'accès :",
                                parse_mode='Markdown'
                            )
                        except Exception as e:
                            print(f"Erreur lors du nettoyage de l'historique ou de l'envoi du message : {e}")
                    
                    # Lancer le nettoyage en arrière-plan
                    asyncio.create_task(clean_and_reset())

            return True
        except Exception as e:
            print(f"Erreur lors du bannissement de l'utilisateur : {e}")
            return False
        
    async def unban_user(self, user_id: int) -> bool:
        """Débanni un utilisateur"""
        try:
            user_id = int(user_id)
            if "banned_users" in self._access_codes and user_id in self._access_codes["banned_users"]:
                self._access_codes["banned_users"].remove(user_id)
                self._save_access_codes()
            return True
        except Exception as e:
            print(f"Erreur lors du débannissement de l'utilisateur : {e}")
            return False

    async def show_banned_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Affiche la liste des utilisateurs bannis"""
        try:
            banned_users = self._access_codes.get("banned_users", [])
        
            text = "🚫 *Utilisateurs bannis*\n\n"
        
            if not banned_users:
                text += "Aucun utilisateur banni."
                keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data="manage_users")]]
            else:
                text += "Sélectionnez un utilisateur pour le débannir :\n\n"
                keyboard = []
            
                for user_id in banned_users:
                    user_data = self._users.get(str(user_id), {})
                    username = user_data.get('username')
                    first_name = user_data.get('first_name')
                    last_name = user_data.get('last_name')
                
                    if username:
                        display_name = f"@{username}"
                    elif first_name and last_name:
                        display_name = f"{first_name} {last_name}"
                    elif first_name:
                        display_name = first_name
                    elif last_name:
                        display_name = last_name
                    else:
                        display_name = f"Utilisateur {user_id}"
                
                    text += f"• {display_name} (`{user_id}`)\n"
                    keyboard.append([InlineKeyboardButton(
                        f"🔓 Débannir {display_name}",
                        callback_data=f"unban_{user_id}"
                    )])
            
                keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="manage_users")])
        
            await update.callback_query.edit_message_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
            return "CHOOSING"
        
        except Exception as e:
            print(f"Erreur dans show_banned_users : {e}")
            return "CHOOSING"

    async def handle_ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gère la commande /ban"""
        try:
            # Supprimer la commande /ban
            try:
                await update.message.delete()
            except Exception as e:
                print(f"Erreur lors de la suppression de la commande ban: {e}")

            # Vérifier si l'utilisateur est admin
            if not self.is_user_authorized(update.effective_user.id):
                return

            # Vérifier les arguments
            if not context.args:
                message = await update.message.reply_text(
                    "❌ Usage : /ban <user_id> ou /ban @username"
                )
                # Supprimer le message après 3 secondes
                async def delete_message():
                    await asyncio.sleep(3)
                    try:
                        await message.delete()
                    except Exception as e:
                        print(f"Error deleting message: {e}")
                asyncio.create_task(delete_message())
                return

            target = context.args[0]
        
            # Si c'est un username
            if target.startswith('@'):
                username = target[1:]
                user_found = False
                for user_id, user_data in self._users.items():
                    if user_data.get('username') == username:
                        target = user_id
                        user_found = True
                        break
                if not user_found:
                    message = await update.message.reply_text("❌ Utilisateur non trouvé.")
                    # Supprimer le message après 3 secondes
                    async def delete_message():
                        await asyncio.sleep(3)
                        try:
                            await message.delete()
                        except Exception as e:
                            print(f"Error deleting message: {e}")
                    asyncio.create_task(delete_message())
                    return

            # Bannir l'utilisateur
            if await self.ban_user(target, context):  # Ajout du contexte ici
                message = await update.message.reply_text(f"✅ Utilisateur {target} banni avec succès.")
            else:
                message = await update.message.reply_text("❌ Erreur lors du bannissement.")

            # Supprimer le message de confirmation après 3 secondes
            async def delete_message():
                await asyncio.sleep(3)
                try:
                    await message.delete()
                except Exception as e:
                    print(f"Error deleting message: {e}")
        
            asyncio.create_task(delete_message())

        except Exception as e:
            print(f"Erreur dans handle_ban_command : {e}")
            message = await update.message.reply_text("❌ Une erreur est survenue.")
        
            # Supprimer le message d'erreur après 3 secondes
            async def delete_message():
                await asyncio.sleep(3)
                try:
                    await message.delete()
                except Exception as e:
                    print(f"Error deleting message: {e}")
        
            asyncio.create_task(delete_message())
        
    async def handle_unban_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gère le débannissement depuis le callback"""
        try:
            query = update.callback_query
            
            # Vérifier si c'est un callback de débannissement valide
            if not query.data.startswith("unban_") or query.data == "unban_user_menu":
                return "CHOOSING"
            
            try:
                user_id = int(query.data.replace("unban_", ""))
            except ValueError:
                await query.answer("❌ ID utilisateur invalide")
                return "CHOOSING"
            
            if await self.unban_user(user_id):
                # Message de confirmation avec bouton de retour
                await query.edit_message_text(
                    f"✅ Utilisateur {user_id} débanni avec succès.",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Retour", callback_data="unban_user_menu")]
                    ])
                )
            else:
                await query.answer("❌ Erreur lors du débannissement.")
                
        except Exception as e:
            print(f"Erreur dans handle_unban_callback : {e}")
            await query.answer("❌ Une erreur est survenue.")
        
        return "CHOOSING"
    
    async def register_user(self, user):
        """Enregistre ou met à jour un utilisateur"""
        user_id = str(user.id)
        paris_tz = pytz.timezone('Europe/Paris')
        paris_time = datetime.utcnow().replace(tzinfo=pytz.UTC).astimezone(paris_tz)
        
        self._users[user_id] = {
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'last_seen': paris_time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self._save_users()

    async def handle_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Démarre le processus de diffusion"""
        try:
            context.user_data.clear()
            context.user_data['broadcast_chat_id'] = update.effective_chat.id
            
            keyboard = [
                [InlineKeyboardButton("❌ Annuler", callback_data="admin")]
            ]
            
            message = await update.callback_query.edit_message_text(
                "📢 *Nouveau message de diffusion*\n\n"
                "Envoyez le message que vous souhaitez diffuser aux utilisateurs autorisés.\n"
                "Vous pouvez envoyer du texte, des photos, des vidéos ou des stickers.",  # Modifié ici
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            context.user_data['instruction_message_id'] = message.message_id
            return "WAITING_BROADCAST_MESSAGE"
        except Exception as e:
            print(f"Erreur dans handle_broadcast : {e}")
            return "CHOOSING"

    async def manage_broadcasts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gère les annonces existantes"""
        keyboard = []
        if self.broadcasts:
            for broadcast_id, broadcast in self.broadcasts.items():
                keyboard.append([InlineKeyboardButton(
                    f"📢 {broadcast['content'][:30]}...",
                    callback_data=f"edit_broadcast_{broadcast_id}"
                )])
        
        keyboard.append([InlineKeyboardButton("➕ Nouvelle annonce", callback_data="start_broadcast")])
        keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="admin")])
        
        await update.callback_query.edit_message_text(
            "📢 *Gestion des annonces*\n\n"
            "Sélectionnez une annonce à modifier ou créez-en une nouvelle.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return "CHOOSING"

    async def edit_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Permet de modifier une annonce existante"""
        query = update.callback_query
        broadcast_id = query.data.replace("edit_broadcast_", "")
    
        if broadcast_id in self.broadcasts:
            broadcast = self.broadcasts[broadcast_id]
            keyboard = [
                [InlineKeyboardButton("✏️ Modifier l'annonce", callback_data=f"edit_broadcast_content_{broadcast_id}")],
                [InlineKeyboardButton("❌ Supprimer", callback_data=f"delete_broadcast_{broadcast_id}")],
                [InlineKeyboardButton("🔙 Retour", callback_data="manage_broadcasts")]
            ]
        
            await query.edit_message_text(
                f"📢 *Gestion de l'annonce*\n\n"
                f"Message actuel :\n{broadcast['content'][:200]}...",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.edit_message_text(
                "❌ Cette annonce n'existe plus.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour", callback_data="manage_broadcasts")
                ]])
            )
    
        return "CHOOSING"

    async def edit_broadcast_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Démarre l'édition d'une annonce"""
        query = update.callback_query
        broadcast_id = query.data.replace("edit_broadcast_content_", "")

        context.user_data['editing_broadcast_id'] = broadcast_id

        # Envoyer le message d'instruction et stocker son ID
        message = await query.edit_message_text(
            "✏️ *Modification de l'annonce*\n\n"
            "Envoyez un nouveau message (texte et/ou média) pour remplacer cette annonce.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Annuler", callback_data=f"edit_broadcast_{broadcast_id}")
            ]])
        )
    
        # Stocker l'ID du message d'instruction
        context.user_data['instruction_message_id'] = message.message_id

        return "WAITING_BROADCAST_EDIT"

    async def handle_broadcast_edit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Traite la modification d'une annonce"""
        try:
            broadcast_id = context.user_data.get('editing_broadcast_id')
            if not broadcast_id or broadcast_id not in self.broadcasts:
                return "CHOOSING"

            # Supprimer les messages intermédiaires
            try:
                await update.message.delete()
                if 'instruction_message_id' in context.user_data:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=context.user_data['instruction_message_id']
                    )
            except Exception as e:
                print(f"Error deleting messages: {e}")

            admin_id = update.effective_user.id
            new_content = update.message.text if update.message.text else update.message.caption if update.message.caption else "Media sans texte"
        
            # Convertir les nouvelles entités
            new_entities = None
            if update.message.entities:
                new_entities = [{'type': entity.type, 
                               'offset': entity.offset,
                               'length': entity.length} 
                              for entity in update.message.entities]
            elif update.message.caption_entities:
                new_entities = [{'type': entity.type, 
                               'offset': entity.offset,
                               'length': entity.length} 
                              for entity in update.message.caption_entities]

            broadcast = self.broadcasts[broadcast_id]
            broadcast['content'] = new_content
            broadcast['entities'] = new_entities

            success = 0
            failed = 0
            messages_updated = []
        
            # Tenter de modifier les messages existants
            for user_id, msg_id in broadcast['message_ids'].items():
                if int(user_id) == admin_id:  # Skip l'admin
                    continue
                try:
                    await context.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=msg_id,
                        text=new_content,
                        entities=update.message.entities,
                        reply_markup=self._create_message_keyboard()
                    )
                    success += 1
                    messages_updated.append(user_id)
                except Exception as e:
                    print(f"Error updating message for user {user_id}: {e}")
                    failed += 1

            # Pour les utilisateurs qui n'ont pas reçu le message
            for user_id in self._users.keys():
                if (str(user_id) not in messages_updated and 
                    self.is_user_authorized(int(user_id)) and 
                    int(user_id) != admin_id):  # Skip l'admin
                    try:
                        sent_msg = await context.bot.send_message(
                            chat_id=user_id,
                            text=new_content,
                            entities=update.message.entities,
                            reply_markup=self._create_message_keyboard()
                        )
                        broadcast['message_ids'][str(user_id)] = sent_msg.message_id
                        success += 1
                    except Exception as e:
                        print(f"Error sending new message to user {user_id}: {e}")
                        failed += 1

            self._save_broadcasts()

            # Créer la bannière de gestion des annonces
            keyboard = []
            if self.broadcasts:
                for b_id, broadcast in self.broadcasts.items():
                    keyboard.append([InlineKeyboardButton(
                        f"📢 {broadcast['content'][:30]}...",
                        callback_data=f"edit_broadcast_{b_id}"
                    )])
        
            keyboard.append([InlineKeyboardButton("➕ Nouvelle annonce", callback_data="start_broadcast")])
            keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="admin")])
        
            # Envoyer la nouvelle bannière avec le contenu de l'annonce
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="📢 *Gestion des annonces*\n\n"
                     "Sélectionnez une annonce à modifier ou créez-en une nouvelle.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            # Message de confirmation avec le contenu
            confirmation_message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ Message modifié ({success} succès, {failed} échecs)\n\n"
                     f"📝 *Contenu de l'annonce :*\n{new_content}",
                parse_mode='Markdown'
            )

            # Programmer la suppression du message après 3 secondes
            async def delete_message():
                await asyncio.sleep(3)
                try:
                    await confirmation_message.delete()
                except Exception as e:
                    print(f"Error deleting confirmation message: {e}")

            asyncio.create_task(delete_message())

            return "CHOOSING"

        except Exception as e:
            print(f"Error in handle_broadcast_edit: {e}")
            return "CHOOSING"

    async def resend_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Renvoie une annonce existante"""
        query = update.callback_query
        broadcast_id = query.data.replace("resend_broadcast_", "")

        if broadcast_id not in self.broadcasts:
            await query.edit_message_text(
                "❌ Cette annonce n'existe plus.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour", callback_data="manage_broadcasts")
                ]])
            )
            return "CHOOSING"

        broadcast = self.broadcasts[broadcast_id]
        success = 0
        failed = 0

        progress_message = await query.edit_message_text(
            "📤 *Renvoi de l'annonce en cours...*",
            parse_mode='Markdown'
        )

        for user_id in self._users.keys():
            user_id_int = int(user_id)
            if not self.is_user_authorized(user_id_int):
                print(f"User {user_id_int} not authorized")
                continue
        
            try:
                if broadcast['type'] == 'photo' and broadcast['file_id']:
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=broadcast['file_id'],
                        caption=broadcast['caption'] if broadcast['caption'] else '',
                        parse_mode='Markdown',
                        reply_markup=self._create_message_keyboard()
                    )
                elif broadcast['type'] == 'sticker' and broadcast['file_id']:  # Ajout du support des stickers
                    await context.bot.send_sticker(
                        chat_id=user_id,
                        sticker=broadcast['file_id'],
                        reply_markup=self._create_message_keyboard()
                    )
                else:
                    message_text = broadcast.get('content', '')
                    if not message_text:
                        print(f"No content found for broadcast {broadcast_id}")
                        continue
            
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=message_text,
                        parse_mode='Markdown',
                        reply_markup=self._create_message_keyboard()
                    )
                success += 1
                print(f"Successfully sent to user {user_id}")
            except Exception as e:
                print(f"Error sending to user {user_id}: {e}")
                failed += 1

        keyboard = [
            [InlineKeyboardButton("📢 Retour aux annonces", callback_data="manage_broadcasts")],
            [InlineKeyboardButton("🔙 Menu admin", callback_data="admin")]
        ]

        await progress_message.edit_text(
            f"✅ *Annonce renvoyée !*\n\n"
            f"📊 *Rapport d'envoi :*\n"
            f"• Envois réussis : {success}\n"
            f"• Échecs : {failed}\n"
            f"• Total : {success + failed}",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return "CHOOSING"

    async def delete_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Supprime une annonce"""
        query = update.callback_query
        broadcast_id = query.data.replace("delete_broadcast_", "")
        
        if broadcast_id in self.broadcasts:
            del self.broadcasts[broadcast_id]
            self._save_broadcasts()  # Sauvegarder après suppression
        await query.edit_message_text(
            "✅ *L'annonce a été supprimée avec succès !*",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Retour aux annonces", callback_data="manage_broadcasts")
            ]])
        )
        
        return "CHOOSING"

    async def send_broadcast_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Envoie le message aux utilisateurs autorisés"""
        success = 0
        failed = 0
        chat_id = update.effective_chat.id
        message_ids = {}  # Pour stocker les IDs des messages envoyés

        try:
            # Supprimer les messages précédents
            try:
                await update.message.delete()
                if 'instruction_message_id' in context.user_data:
                    await context.bot.delete_message(
                        chat_id=chat_id,
                        message_id=context.user_data['instruction_message_id']
                    )
            except Exception as e:
                print(f"Erreur lors de la suppression du message: {e}")

            # Enregistrer le broadcast
            broadcast_id = str(datetime.now().timestamp())
            message_content = update.message.text if update.message.text else update.message.caption if update.message.caption else "Media sans texte"
        
            # Convertir les entités en format sérialisable
            entities = None
            if update.message.entities:
                entities = [{'type': entity.type, 
                            'offset': entity.offset,
                            'length': entity.length} 
                           for entity in update.message.entities]
            elif update.message.caption_entities:
                entities = [{'type': entity.type, 
                            'offset': entity.offset,
                            'length': entity.length} 
                           for entity in update.message.caption_entities]

            # Déterminer le type de message
            message_type = 'text'
            file_id = None
            if update.message.photo:
                message_type = 'photo'
                file_id = update.message.photo[-1].file_id
            elif update.message.sticker:  # Ajout du support des stickers
                message_type = 'sticker'
                file_id = update.message.sticker.file_id
                message_content = "Sticker"  # Les stickers n'ont pas de texte

            self.broadcasts[broadcast_id] = {
                'content': message_content,
                'type': message_type,
                'file_id': file_id,
                'caption': update.message.caption if update.message.photo else None,
                'entities': entities,
                'message_ids': {},
                'parse_mode': None
            }

            # Message de progression
            progress_message = await context.bot.send_message(
                chat_id=chat_id,
                text="📤 <b>Envoi du message en cours...</b>",
                parse_mode='HTML'
            )

            # Envoi aux utilisateurs autorisés
            for user_id in self._users.keys():
                user_id_int = int(user_id)
                if not self.is_user_authorized(user_id_int) or user_id_int == update.effective_user.id:
                    print(f"User {user_id_int} skipped")
                    continue
            
                try:
                    if message_type == 'photo':
                        sent_msg = await context.bot.send_photo(
                            chat_id=user_id,
                            photo=file_id,
                            caption=update.message.caption if update.message.caption else '',
                            caption_entities=update.message.caption_entities,
                            reply_markup=self._create_message_keyboard()
                        )
                    elif message_type == 'sticker':  # Ajout de l'envoi de sticker
                        sent_msg = await context.bot.send_sticker(
                            chat_id=user_id,
                            sticker=file_id,
                            reply_markup=self._create_message_keyboard()
                        )
                    else:
                        sent_msg = await context.bot.send_message(
                            chat_id=user_id,
                            text=message_content,
                            entities=update.message.entities,
                            reply_markup=self._create_message_keyboard()
                        )
                    self.broadcasts[broadcast_id]['message_ids'][str(user_id)] = sent_msg.message_id
                    success += 1
                except Exception as e:
                    print(f"Error sending to user {user_id}: {e}")
                    failed += 1

            # Sauvegarder les broadcasts
            self._save_broadcasts()

            # Rapport final
            keyboard = [
                [InlineKeyboardButton("📢 Gérer les annonces", callback_data="manage_broadcasts")],
                [InlineKeyboardButton("🔙 Menu admin", callback_data="admin")]
            ]

            await progress_message.edit_text(
                f"✅ *Message envoyé avec succès !*\n\n"
                f"📊 *Rapport d'envoi :*\n"
                f"• Envois réussis : {success}\n"
                f"• Échecs : {failed}\n"
                f"• Total : {success + failed}",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            return "CHOOSING"

        except Exception as e:
            print(f"Erreur lors de l'envoi du broadcast : {e}")
            return "CHOOSING"

    async def handle_user_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gère l'affichage du menu de gestion des utilisateurs"""
        try:
            # Récupérer les listes d'utilisateurs
            authorized_users = set(self._access_codes.get("authorized_users", []))
            banned_users = set(self._access_codes.get("banned_users", []))
            
            # Compter les utilisateurs de chaque catégorie
            pending_count = 0
            validated_count = 0
            banned_count = 0
            
            for user_id in self._users.keys():
                user_id_int = int(user_id)
                if user_id_int in authorized_users:
                    validated_count += 1
                elif user_id_int in banned_users:
                    banned_count += 1
                else:
                    pending_count += 1
            
            text = "👥 *Gestion des utilisateurs*\n\n"
            text += f"✅ Utilisateurs validés : {validated_count}\n"
            text += f"⏳ Utilisateurs en attente : {pending_count}\n"
            text += f"🚫 Utilisateurs bannis : {banned_count}\n"
            
            # Construire le clavier avec les nouveaux boutons
            keyboard = [
                [InlineKeyboardButton("✅ Voir utilisateurs validés", callback_data="user_list_validated_0")],
                [InlineKeyboardButton("⏳ Voir utilisateurs en attente", callback_data="user_list_pending_0")],
                [InlineKeyboardButton("🚫 Voir utilisateurs bannis", callback_data="user_list_banned_0")],
                [InlineKeyboardButton("⛔️ Bannir un utilisateur", callback_data="ban_user_menu")],
                [InlineKeyboardButton("🔓 Débannir un utilisateur", callback_data="unban_user_menu")],
                [InlineKeyboardButton("📊 Statistiques avancées", callback_data="advanced_stats")],
                [InlineKeyboardButton("🔙 Retour", callback_data="admin")]
            ]
            
            await update.callback_query.edit_message_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            
            return "CHOOSING"
        
        except Exception as e:
            print(f"Erreur dans handle_user_management : {e}")
            await update.callback_query.answer("Une erreur est survenue.")
            return "CHOOSING"
        
    async def show_advanced_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Affiche les statistiques avancées des utilisateurs"""
        try:
            # Calculer les statistiques
            total_users = len(self._users)
            authorized_users = len(self._access_codes.get("authorized_users", []))
            banned_users = len(self._access_codes.get("banned_users", []))
            pending_users = total_users - authorized_users - banned_users

            # Calculer les statistiques d'activité
            active_today = 0
            active_week = 0
            active_month = 0
            total_connections = 0
            total_products_viewed = 0
            most_viewed_products = {}
            most_visited_categories = {}

            current_time = datetime.now(self.paris_tz)
        
            for user_id, user_data in self._users.items():
                # Vérifier la dernière activité
                last_seen_str = user_data.get('last_seen')
                if last_seen_str:
                    try:
                        last_seen = datetime.strptime(last_seen_str, "%Y-%m-%d %H:%M:%S")
                        if (current_time - last_seen).days == 0:
                            active_today += 1
                        if (current_time - last_seen).days <= 7:
                            active_week += 1
                        if (current_time - last_seen).days <= 30:
                            active_month += 1
                    except:
                        pass

                # Compter les connexions
                total_connections += user_data.get('connections', 0)

                # Compter les produits vus
                products_viewed = user_data.get('products_viewed', [])
                total_products_viewed += len(products_viewed)
            
                # Compter les catégories visitées
                for category in user_data.get('categories_visited', []):
                    most_visited_categories[category] = most_visited_categories.get(category, 0) + 1

                # Compter les produits vus
                for product_view in products_viewed:
                    product_name = product_view.get('product')
                    if product_name:
                        most_viewed_products[product_name] = most_viewed_products.get(product_name, 0) + 1

            # Préparer le texte des statistiques
            text = "📊 *Statistiques avancées des utilisateurs*\n\n"
        
            # Statistiques générales
            text += "*Utilisateurs :*\n"
            text += f"• Total : {total_users}\n"
            text += f"• Validés : {authorized_users}\n"
            text += f"• En attente : {pending_users}\n"
            text += f"• Bannis : {banned_users}\n\n"
        
            # Statistiques d'activité
            text += "*Activité :*\n"
            text += f"• Actifs aujourd'hui : {active_today}\n"
            text += f"• Actifs cette semaine : {active_week}\n"
            text += f"• Actifs ce mois : {active_month}\n"
            text += f"• Total connexions : {total_connections}\n"
            text += f"• Total produits consultés : {total_products_viewed}\n\n"
        
            # Top 5 des catégories les plus visitées
            text += "*Top 5 catégories visitées :*\n"
            sorted_categories = sorted(most_visited_categories.items(), key=lambda x: x[1], reverse=True)[:5]
            for category, count in sorted_categories:
                text += f"• {category}: {count} visites\n"
            text += "\n"
        
            # Top 5 des produits les plus vus
            text += "*Top 5 produits consultés :*\n"
            sorted_products = sorted(most_viewed_products.items(), key=lambda x: x[1], reverse=True)[:5]
            for product, count in sorted_products:
                text += f"• {product}: {count} vues\n"

            keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data="manage_users")]]
        
            await update.callback_query.edit_message_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
            return "CHOOSING"

        except Exception as e:
            print(f"Erreur dans show_advanced_stats : {e}")
            await update.callback_query.answer("Une erreur est survenue.")
            return "CHOOSING"

    async def add_user_buttons(self, keyboard: list) -> list:
        """Ajoute les boutons de gestion utilisateurs au clavier admin existant"""
        try:
            keyboard.insert(-1, [InlineKeyboardButton("👥 Gérer utilisateurs", callback_data="manage_users")])
        except Exception as e:
            print(f"Erreur lors de l'ajout des boutons admin : {e}")
        return keyboard

    async def update_user_activity(self, user_id: int, activity_type: str, data: str = None):
        """Met à jour l'activité d'un utilisateur
        Args:
            user_id (int): ID de l'utilisateur
            activity_type (str): Type d'activité ('connection', 'view_category', 'view_product')
            data (str, optional): Données supplémentaires (nom du produit/catégorie). Defaults to None.
        """
        try:
            str_user_id = str(user_id)
            if str_user_id not in self._users:
                self._users[str_user_id] = {}

            user = self._users[str_user_id]
            current_time = datetime.now(self.paris_tz).strftime("%Y-%m-%d %H:%M:%S")
        
            if activity_type == 'connection':
                user['connections'] = user.get('connections', 0) + 1
            
            elif activity_type == 'view_product':
                if 'products_viewed' not in user:
                    user['products_viewed'] = []
            
                # Ajouter le produit consulté avec timestamp
                user['products_viewed'].append({
                    'product': data,
                    'timestamp': current_time
                })
            
                # Garder seulement les 20 derniers produits vus
                user['products_viewed'] = user['products_viewed'][-20:]
            
            elif activity_type == 'view_category':
                if 'categories_visited' not in user:
                    user['categories_visited'] = []
                
                # Ajouter la catégorie si pas déjà présente
                if data not in user['categories_visited']:
                    user['categories_visited'].append(data)

            # Mettre à jour last_seen
            user['last_seen'] = current_time
        
            # Sauvegarder les modifications
            self._save_users()
        
        except Exception as e:
            print(f"Erreur dans update_user_activity : {e}")
