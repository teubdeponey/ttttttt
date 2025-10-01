from handlers.admin_features import AdminFeatures
from modules.access_manager import AccessManager
import json
import logging
import asyncio
import shutil
import hashlib
import os
import re
import string
import random
from datetime import datetime, time
import pytz
import base64
from urllib.parse import quote, unquote
from telegram.error import NetworkError, TimedOut, RetryAfter
from telegram.error import BadRequest as TelegramBadRequest
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    filters, 
    ContextTypes, 
    ConversationHandler
)
paris_tz = pytz.timezone('Europe/Paris')

STATS_CACHE = None
LAST_CACHE_UPDATE = None
admin_features = None
ADMIN_CREATIONS = {} 
LAST_CLEANUP = None 

logging.getLogger("httpx").setLevel(logging.WARNING)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

try:
    with open('config/config.json', 'r', encoding='utf-8') as f:
        CONFIG = json.load(f)
        TOKEN = CONFIG['token']
        ADMIN_IDS = CONFIG['admin_ids']
except FileNotFoundError:
    print("Erreur: Le fichier config.json n'a pas été trouvé!")
    exit(1)
except KeyError as e:
    print(f"Erreur: La clé {e} est manquante dans le fichier config.json!")
    exit(1)

def load_catalog():
    try:
        with open(CONFIG['catalog_file'], 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_catalog(catalog):
    with open(CONFIG['catalog_file'], 'w', encoding='utf-8') as f:
        json.dump(catalog, f, indent=4, ensure_ascii=False)

def clean_stats():
    """Nettoie les statistiques des produits et catégories qui n'existent plus"""
    if 'stats' not in CATALOG:
        return
    
    stats = CATALOG['stats']
    
    if 'category_views' in stats:
        categories_to_remove = []
        for category in stats['category_views']:
            if category not in CATALOG or category == 'stats':
                categories_to_remove.append(category)
        
        for category in categories_to_remove:
            del stats['category_views'][category]
            print(f"🧹 Suppression des stats de la catégorie: {category}")

    if 'product_views' in stats:
        categories_to_remove = []
        for category in stats['product_views']:
            if category not in CATALOG or category == 'stats':
                categories_to_remove.append(category)
                continue
            
            products_to_remove = []
            existing_products = [p['name'] for p in CATALOG[category]]
            
            for product_name in stats['product_views'][category]:
                if product_name not in existing_products:
                    products_to_remove.append(product_name)
            
            for product in products_to_remove:
                del stats['product_views'][category][product]
                print(f"🧹 Suppression des stats du produit: {product} dans {category}")
            
            if not stats['product_views'][category]:
                categories_to_remove.append(category)
        
        for category in categories_to_remove:
            if category in stats['product_views']:
                del stats['product_views'][category]

    stats['last_updated'] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    save_catalog(CATALOG)

def get_stats():
    global STATS_CACHE, LAST_CACHE_UPDATE
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    
    if STATS_CACHE and LAST_CACHE_UPDATE and (current_time - LAST_CACHE_UPDATE).seconds < 30:
        return STATS_CACHE
        
    STATS_CACHE = load_catalog()['stats']
    LAST_CACHE_UPDATE = current_time
    return STATS_CACHE

def backup_data():
    """Crée une sauvegarde des fichiers de données"""
    backup_dir = "backups"
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if os.path.exists("config/config.json"):
        shutil.copy2("config/config.json", f"{backup_dir}/config_{timestamp}.json")
    
    if os.path.exists("config/catalog.json"):
        shutil.copy2("config/catalog.json", f"{backup_dir}/catalog_{timestamp}.json")

def print_catalog_debug():
    """Fonction de debug pour afficher le contenu du catalogue"""
    for category, products in CATALOG.items():
        if category != 'stats':
            print(f"\nCatégorie: {category}")
            for product in products:
                print(f"  Produit: {product['name']}")
                if 'media' in product:
                    print(f"    Médias ({len(product['media'])}): {product['media']}")

def sanitize_callback_data(text):
    """Sanitize text for use in callback_data by removing special characters and encoding"""
    text = re.sub(r'[^\w\s\-_]', '', text)
    text = text.strip().replace(' ', '_')[:20]
    return text

def encode_callback_data(category, product=None):
    """Encode category and optionally product name for callback_data"""
    if product:
        data = f"{category}|||{product}"
    else:
        data = category
    return base64.b64encode(data.encode()).decode()

def decode_callback_data(data):
    """Decode callback_data back to category and optionally product"""
    try:
        decoded = base64.b64decode(data.encode()).decode()
        if '|||' in decoded:
            category, product = decoded.split('|||')
            return category, product
        return decoded, None
    except:
        return None, None
        
def create_safe_callback_data(prefix, data, max_length=64):
    """
    Crée un callback_data sécurisé et dans les limites de taille de Telegram
    """
    hash_object = hashlib.md5(data.encode())
    short_hash = hash_object.hexdigest()[:8]
    
    safe_data = re.sub(r'[^\w\-]', '_', data)
    safe_data = safe_data[:10]  
    
    callback_data = f"{prefix}_{safe_data}_{short_hash}"
    
    if len(callback_data) > max_length:
        callback_data = f"{prefix}_{short_hash}"
    
    return callback_data

def get_sibling_products(category, product_name, user_id=None):
    products = CATALOG[category]
    visible_products = []
    
    for product in products:
        show_product = True
        for group_name in admin_features._access_codes.get("groups", {}).keys():
            if product['name'].startswith(f"{group_name}_"):
                if user_id not in admin_features._access_codes["groups"][group_name]:
                    show_product = False
                break
        if show_product:
            visible_products.append(product)
    
    current_index = next((i for i, p in enumerate(visible_products) if p['name'] == product_name), -1)
    
    prev_product = visible_products[current_index - 1] if current_index > 0 else None
    next_product = visible_products[current_index + 1] if current_index < len(visible_products) - 1 else None
    
    return prev_product, next_product
CALLBACK_DATA_MAPPING = {}

def store_callback_mapping(callback_data, original_data):
    """Stocke la correspondance entre le callback_data et les données originales"""
    CALLBACK_DATA_MAPPING[callback_data] = original_data

def get_original_data(callback_data):
    """Récupère les données originales à partir du callback_data"""
    return CALLBACK_DATA_MAPPING.get(callback_data)

def is_product_in_promo(product):
    """Vérifie si un produit est en promotion"""
    if not product.get('promo'):
        return False
    
    promo = product['promo']
    if not promo.get('is_promo'):
        return False
    
    # Vérifier la date de fin si elle existe
    if promo.get('promo_end_date'):
        from datetime import datetime
        try:
            end_date = datetime.fromisoformat(promo['promo_end_date'])
            if datetime.now() > end_date:
                return False
        except:
            pass
    
    return True

def get_product_display_price(product):
    """Retourne le prix à afficher pour un produit"""
    if is_product_in_promo(product):
        promo = product['promo']
        original = promo.get('original_price', product['price'])
        promo_price = promo.get('promo_price', product['price'])
        return f"<s>{original}</s> ➡️ <b>{promo_price}</b>"
    return product['price']

def get_promo_badge(product):
    """Retourne le badge promo à afficher"""
    if is_product_in_promo(product):
        promo = product['promo']
        badge = promo.get('promo_badge', '🔥 PROMO')
        return f"{badge} "
    return ""

WAITING_FOR_ACCESS_CODE = "WAITING_FOR_ACCESS_CODE"
CHOOSING = "CHOOSING"
WAITING_CATEGORY_NAME = "WAITING_CATEGORY_NAME"
WAITING_PRODUCT_NAME = "WAITING_PRODUCT_NAME"
WAITING_PRODUCT_PRICE = "WAITING_PRODUCT_PRICE"
WAITING_PRODUCT_DESCRIPTION = "WAITING_PRODUCT_DESCRIPTION"
WAITING_PRODUCT_MEDIA = "WAITING_PRODUCT_MEDIA"
SELECTING_CATEGORY = "SELECTING_CATEGORY"
SELECTING_CATEGORY_TO_DELETE = "SELECTING_CATEGORY_TO_DELETE"
SELECTING_PRODUCT_TO_DELETE = "SELECTING_PRODUCT_TO_DELETE"
WAITING_CONTACT_USERNAME = "WAITING_CONTACT_USERNAME"
SELECTING_PRODUCT_TO_EDIT = "SELECTING_PRODUCT_TO_EDIT"
EDITING_PRODUCT_FIELD = "EDITING_PRODUCT_FIELD"
WAITING_NEW_VALUE = "WAITING_NEW_VALUE"
WAITING_BANNER_IMAGE = "WAITING_BANNER_IMAGE"
WAITING_BROADCAST_MESSAGE = "WAITING_BROADCAST_MESSAGE"
WAITING_ORDER_BUTTON_CONFIG = "WAITING_ORDER_BUTTON_CONFIG"
WAITING_WELCOME_MESSAGE = "WAITING_WELCOME_MESSAGE" 
EDITING_CATEGORY = "EDITING_CATEGORY"
WAITING_NEW_CATEGORY_NAME = "WAITING_NEW_CATEGORY_NAME"
WAITING_BUTTON_NAME = "WAITING_BUTTON_NAME"
WAITING_BUTTON_VALUE = "WAITING_BUTTON_VALUE"
WAITING_BROADCAST_EDIT = "WAITING_BROADCAST_EDIT"
WAITING_CODE_NUMBER = "WAITING_CODE_NUMBER"
WAITING_BAN_INPUT = "WAITING_BAN_INPUT"
WAITING_UNBAN_INPUT = "WAITING_UNBAN_INPUT"
WAITING_REVIEW_TEXT = "WAITING_REVIEW_TEXT"
REVIEW_PAGES = {}
CATALOG = load_catalog()


async def handle_access_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la vérification du code d'accès"""
    user_id = update.effective_user.id
    code = update.message.text.strip()
    chat_id = update.effective_chat.id
    
    try:
        await update.message.delete()
    except Exception as e:
        pass

    if admin_features.mark_code_as_used(code, user_id, update.effective_user.username):
        try:
            current_message_id = update.message.message_id
            
            for i in range(current_message_id - 15, current_message_id + 1):
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=i)
                except Exception as e:
                    pass  
                    
            if 'initial_welcome_message_id' in context.user_data:
                try:
                    await context.bot.delete_message(
                        chat_id=chat_id,
                        message_id=context.user_data['initial_welcome_message_id']
                    )
                except Exception as e:
                    pass
                
            context.user_data.clear() 
            
        except Exception as e:
            pass  
        
        return await start(update, context)
    else:
        try:
            await update.message.reply_text(
                text="❌ Code invalide ou expiré",
                reply_markup=None
            )
        except Exception as e:
            pass
            
        return WAITING_FOR_ACCESS_CODE

async def admin_generate_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Génère un nouveau code d'accès (commande admin)"""
    if str(update.effective_user.id) not in ADMIN_IDS:
        await update.message.reply_text("❌ Cette commande est réservée aux administrateurs.")
        return

    code, expiration = access_manager.generate_code(update.effective_user.id)
    
    exp_date = datetime.fromisoformat(expiration)
    exp_str = exp_date.strftime("%d/%m/%Y %H:%M")
    
    await update.message.reply_text(
        f"✅ Nouveau code généré :\n\n"
        f"Code: `{code}`\n"
        f"Expire le: {exp_str}",
        parse_mode='Markdown'
    )

async def admin_list_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liste tous les codes actifs (commande admin)"""
    if str(update.effective_user.id) not in ADMIN_IDS:
        await update.message.reply_text("❌ Cette commande est réservée aux administrateurs.")
        return

    active_codes = access_manager.list_active_codes()
    
    if not active_codes:
        await update.message.reply_text("Aucun code actif.")
        return

    message = "📝 Codes actifs :\n\n"
    for code in active_codes:
        exp_date = datetime.fromisoformat(code["expiration"])
        exp_str = exp_date.strftime("%d/%m/%Y %H:%M")
        message += f"Code: `{code['code']}`\n"
        message += f"Expire le: {exp_str}\n\n"

    await update.message.reply_text(message, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    if hasattr(update, 'message') and update.message:
        try:
            await update.message.delete()
        except Exception:
            pass
    
    await admin_features.register_user(user)
    
    if not access_manager.is_authorized(user.id):
        if 'initial_welcome_message_id' in context.user_data:
            try:
                await context.bot.delete_message(
                    chat_id=chat_id,
                    message_id=context.user_data['initial_welcome_message_id']
                )
            except Exception:
                pass
        
        welcome_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="🔒 Bienvenue ! Pour accéder au bot, veuillez entrer votre code d'accès."
        )
        context.user_data['initial_welcome_message_id'] = welcome_msg.message_id
        return WAITING_FOR_ACCESS_CODE
    
    if 'menu_message_id' in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=context.user_data['menu_message_id']
            )
        except:
            pass
    
    if 'banner_message_id' in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=context.user_data['banner_message_id']
            )
            del context.user_data['banner_message_id']
        except:
            pass
    
    keyboard = [
        [InlineKeyboardButton("📋 MENU", callback_data="show_categories")]
]

    with open('config/config.json', 'r') as f:
        config = json.load(f)
    
    for button in config.get('custom_buttons', []):
        if button['type'] == 'url':
            keyboard.append([InlineKeyboardButton(button['name'], url=button['value'])])
        elif button['type'] == 'text':
            keyboard.append([InlineKeyboardButton(button['name'], callback_data=f"custom_text_{button['id']}")])

    welcome_text = CONFIG.get('welcome_message', 
        "🌿 <b>Bienvenue sur votre bot !</b> 🌿\n\n"
        "<b>Pour changer ce message d accueil, rendez vous dans l onglet admin.</b>\n"
        "📋 Cliquez sur MENU pour voir les catégories"
    )

    if config.get('networks_enabled', True):
        keyboard.append([InlineKeyboardButton("📱 Réseaux", callback_data="show_networks")])
        
    if config.get('reviews_enabled', True):
        keyboard.append([InlineKeyboardButton("📝 Avis", callback_data="show_reviews")])

    if str(update.effective_user.id) in ADMIN_IDS and access_manager.is_access_code_enabled():
        keyboard.extend([
            [InlineKeyboardButton("🎫 Générer des codes d'accès", callback_data="generate_multiple_codes")],
            [InlineKeyboardButton("📜 Historique codes", callback_data="show_codes_history")]
        ])

    if str(update.effective_user.id) in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("🔧 Menu Admin", callback_data="admin")])

    try:
        if CONFIG.get('banner_image'):
            banner_message = await context.bot.send_photo(
                chat_id=chat_id,
                photo=CONFIG['banner_image']
            )
            context.user_data['banner_message_id'] = banner_message.message_id

        menu_message = await context.bot.send_message(
            chat_id=chat_id,
            text=welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'  
        )
        context.user_data['menu_message_id'] = menu_message.message_id
        
    except Exception as e:
        print(f"Erreur lors du démarrage: {e}")
        menu_message = await context.bot.send_message(
            chat_id=chat_id,
            text=welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        context.user_data['menu_message_id'] = menu_message.message_id
    
    return CHOOSING

async def show_reviews_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le menu des avis"""
    query = update.callback_query
    await query.answer()

    try:
        # Charger les avis depuis le fichier reviews.json
        with open('config/reviews.json', 'r') as f:
            reviews = json.load(f)
    except FileNotFoundError:
        # Créer le fichier s'il n'existe pas
        reviews = {
            "approved": [],
            "pending": [],
            "total": 0,
            "settings": {
                "reviews_per_page": 5,
                "max_reviews_per_user": 100,
                "require_approval": True
            }
        }
        with open('config/reviews.json', 'w') as f:
            json.dump(reviews, f, indent=2)

    keyboard = [
        [InlineKeyboardButton("✍️ Poster un avis", callback_data="post_review")],
        [InlineKeyboardButton("👀 Voir les avis", callback_data="view_reviews")]
    ]
    
    # Ajouter le bouton de gestion des avis en attente pour les admins
    if str(update.effective_user.id) in ADMIN_IDS:
        pending_count = len(reviews.get('pending', []))
        if pending_count > 0:
            keyboard.insert(1, [InlineKeyboardButton(f"👨‍💼 Avis en attente ({pending_count})", callback_data="manage_pending_reviews")])
    
    keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="back_to_home")])
    
    await query.edit_message_text(
        "📝 *Menu des Avis*\n\n"
        "• Consultez les avis des utilisateurs\n"
        "• Partagez votre expérience\n",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    
    return CHOOSING

async def handle_review_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la navigation entre les pages d'avis"""
    query = update.callback_query
    await query.answer()

    direction = "next" if query.data == "next_review_page" else "prev"
    current_page = context.user_data.get('review_page', 0)
    
    if direction == "next":
        context.user_data['review_page'] = current_page + 1
    else:
        context.user_data['review_page'] = max(0, current_page - 1)

    # Rediriger vers la fonction d'affichage des avis
    await show_reviews(update, context)
    return CHOOSING

async def manage_pending_reviews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère les avis en attente de validation"""
    query = update.callback_query
    await query.answer()
    
    if str(update.effective_user.id) not in ADMIN_IDS:
        keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data="show_reviews")]]
        await query.edit_message_text(
            "❌ Vous n'êtes pas autorisé à accéder à cette fonction.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CHOOSING

    # Charger les avis en attente 
    with open('config/reviews.json', 'r') as f:
        reviews = json.load(f)

    pending_reviews = reviews.get('pending', [])
    if not pending_reviews:
        keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data="show_reviews")]]
        await query.edit_message_text(
            "✅ Aucun avis en attente de validation.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CHOOSING

    review = pending_reviews[0]
    # Afficher le @ Telegram seulement pour les admins
    admin_info = f"📱 Telegram: @{review.get('username')}\n" if str(update.effective_user.id) in ADMIN_IDS else ""
    
    text = (
        "📝 *Avis en attente de validation*\n\n"
        f"👤 ID: {review['user_id']}\n"
        f"{admin_info}"
        f"📅 Date: {review['date']}\n"
        f"💬 Message:\n{review['text']}\n\n"
        "Que souhaitez-vous faire ?"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Approuver", callback_data=f"approve_review_{review['id']}"),
            InlineKeyboardButton("❌ Refuser", callback_data=f"reject_review_{review['id']}")
        ],
        [InlineKeyboardButton("🔙 Retour", callback_data="show_reviews")]
    ]

    try:
        # D'abord essayer d'éditer le message existant
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        # Si l'édition échoue (par exemple pour les notifications), envoyer un nouveau message
        try:
            await query.message.delete()
        except:
            pass
            
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    return CHOOSING

async def handle_review_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la décision d'approuver ou refuser un avis"""
    query = update.callback_query
    await query.answer()
    
    if str(update.effective_user.id) not in ADMIN_IDS:
        return CHOOSING

    # Extraire l'action et l'ID de l'avis
    action, review_id = query.data.split("_review_")
    review_id = int(review_id)

    with open('config/reviews.json', 'r+') as f:
        reviews = json.load(f)
        
        # Trouver l'avis dans la liste des avis en attente
        review = None
        for r in reviews['pending']:
            if r['id'] == review_id:
                review = r
                reviews['pending'].remove(r)
                break

        if review:
            if action == "approve":
                reviews['approved'].append(review)
                success_text = "✅ Avis approuvé avec succès !"
            else:
                success_text = "❌ Avis refusé !"          

            # Sauvegarder les changements
            f.seek(0)
            json.dump(reviews, f, indent=2)
            f.truncate()

            # S'il reste des avis en attente, afficher le suivant
            if reviews['pending']:
                return await manage_pending_reviews(update, context)
            else:
                keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data="show_reviews")]]
                await query.edit_message_text(
                    f"{success_text}\n\nTous les avis ont été traités.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return CHOOSING

    return CHOOSING

async def post_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Démarre le processus d'ajout d'un avis"""
    query = update.callback_query
    await query.answer()
    
    # Vérifier si l'utilisateur a déjà posté un avis
    with open('config/reviews.json', 'r') as f:
        reviews = json.load(f)

    user_id = str(update.effective_user.id)
    has_review = any(str(r.get('user_id')) == user_id for r in reviews.get('approved', []))
    has_pending = any(str(r.get('user_id')) == user_id for r in reviews.get('pending', []))

    keyboard = [[InlineKeyboardButton("❌ Annuler", callback_data="show_reviews")]]
    await query.edit_message_text(
        "✍️ *Postez votre avis*\n\n"
        "Envoyez votre message. Il sera examiné par notre équipe avant d'être publié.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return WAITING_REVIEW_TEXT

async def handle_review_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la réception d'un nouvel avis"""
    try:
        await update.message.delete()
    except:
        pass

    user = update.effective_user
    review_text = update.message.text

    # Enregistrer l'avis avec l'username au lieu de l'ID
    with open('config/reviews.json', 'r+') as f:
        reviews = json.load(f)
        reviews['pending'].append({
            'id': len(reviews.get('pending', [])) + len(reviews.get('approved', [])) + 1,
            'user_id': user.id,
            'username': user.username or str(user.id), 
            'text': review_text,
            'date': datetime.now(paris_tz).strftime('%d/%m/%Y'),
            'status': 'pending'
        })
        f.seek(0)
        json.dump(reviews, f, indent=2)
        f.truncate()

    # Supprime le message précédent (la demande d'avis)
    if 'review_prompt_message_id' in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['review_prompt_message_id']
            )
        except:
            pass

    # Envoie uniquement le message de confirmation avec le bouton retour
    keyboard = [[InlineKeyboardButton("🔙 Retour aux avis", callback_data="show_reviews")]]
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✅ Votre avis a été enregistré et sera examiné par notre équipe. Merci !",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    # Notifier les admins
    for admin_id in ADMIN_IDS:
        try:
            # Créer un nouveau message interactif pour l'admin
            await context.bot.send_message(
                chat_id=admin_id,
                text="📢 *Nouvel avis en attente de validation ! Relancez le bot avec /start ! *\n\n",
                parse_mode='Markdown'
            )
        except Exception as e:
            print(f"Erreur lors de la notification admin: {e}")
            continue

    return CHOOSING

async def post_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commence le processus de création d'un avis"""
    query = update.callback_query
    await query.answer()
    
    # Vérifier si l'utilisateur a déjà posté un avis
    with open('config/reviews.json', 'r') as f:
        reviews = json.load(f)

    user_id = str(update.effective_user.id)
    has_review = any(str(r.get('user_id')) == user_id for r in reviews.get('approved', []))
    has_pending = any(str(r.get('user_id')) == user_id for r in reviews.get('pending', []))


    keyboard = [[InlineKeyboardButton("❌ Annuler", callback_data="show_reviews")]]
    message = await query.edit_message_text(
        "✍️ *Postez votre avis*\n\n"
        "Envoyez votre message. Il sera examiné par notre équipe avant d'être publié.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    
    # Stocker l'ID du message pour pouvoir le supprimer plus tard
    context.user_data['review_prompt_message_id'] = message.message_id
    
    return WAITING_REVIEW_TEXT

async def handle_review_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la navigation entre les pages d'avis"""
    query = update.callback_query
    await query.answer()
    
    direction = "next" if query.data == "next_review_page" else "prev"
    current_page = context.user_data.get('review_page', 0)
    
    if direction == "next":
        context.user_data['review_page'] = current_page + 1
    else:
        context.user_data['review_page'] = max(0, current_page - 1)
    
    # Afficher la nouvelle page
    return await view_reviews(update, context)

async def view_reviews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les avis approuvés avec pagination"""
    query = update.callback_query
    await query.answer()
    
    with open('config/reviews.json', 'r') as f:
        reviews = json.load(f)
    
    approved_reviews = reviews.get('approved', [])
    page = context.user_data.get('review_page', 0)
    items_per_page = 5
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    current_reviews = approved_reviews[start_idx:end_idx]
    
    if not current_reviews:
        if page > 0:
            context.user_data['review_page'] = 0
            return await view_reviews(update, context)
            
        keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data="show_reviews")]]
        await query.edit_message_text(
            "Aucun avis n'a encore été publié.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CHOOSING
    
    text = f"📝 Avis {start_idx + 1} à {min(end_idx, len(approved_reviews))} sur {len(approved_reviews)} :\n\n"
    
    for review in current_reviews:
        text += f"👤 {review['username']}\n"
        text += f"📅 {review['date']}\n"
        text += f"💬 {review['text']}\n"
        text += "----------------------------------------\n\n"
    
    keyboard = []
    nav_row = []
    
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Précédent", callback_data="prev_review_page"))
    if end_idx < len(approved_reviews):
        nav_row.append(InlineKeyboardButton("➡️ Suivant", callback_data="next_review_page"))
    
    if nav_row:
        keyboard.append(nav_row)
    
    keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="show_reviews")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING

async def show_reviews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le menu des avis"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("✍️ Poster un avis", callback_data="post_review")],
        [InlineKeyboardButton("👀 Voir les avis", callback_data="view_reviews")]
    ]
    
    if str(update.effective_user.id) in ADMIN_IDS:
        # Charger les avis en attente
        with open('config/reviews.json', 'r') as f:
            reviews = json.load(f)
            pending_count = len(reviews.get('pending', []))
            if pending_count > 0:
                keyboard.insert(1, [InlineKeyboardButton(f"👨‍💼 Avis en attente ({pending_count})", callback_data="manage_pending_reviews")])
    
    keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="back_to_home")])
    
    await query.edit_message_text(
        "📝 *Menu des Avis*\n\n"
        "• Consultez les avis des utilisateurs\n"
        "• Partagez votre expérience\n",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    
    return CHOOSING

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande pour accéder au menu d'administration"""
    if str(update.effective_user.id) in ADMIN_IDS:
        await update.message.delete()
        
        messages_to_delete = ['menu_message_id', 'banner_message_id', 'category_message_id', 
                            'last_product_message_id', 'instruction_message_id']
        
        for message_key in messages_to_delete:
            if message_key in context.user_data:
                try:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=context.user_data[message_key]
                    )
                    del context.user_data[message_key]
                except Exception as e:
                    print(f"Erreur lors de la suppression du message {message_key}: {e}")
        
        if CONFIG.get('banner_image'):
            try:
                banner_message = await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=CONFIG['banner_image']
                )
                context.user_data['banner_message_id'] = banner_message.message_id
            except Exception as e:
                print(f"Erreur lors de l'envoi de la bannière: {e}")
        
        return await show_admin_menu(update, context)
    else:
        await update.message.reply_text("❌ Vous n'êtes pas autorisé à accéder au menu d'administration.")
        return ConversationHandler.END

async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le menu d'administration principal simplifié"""
    with open('config/config.json', 'r') as f:
        config = json.load(f)
    
    reviews_enabled = config.get('reviews_enabled', True)
    reviews_status = "✅ Activé" if reviews_enabled else "❌ Désactivé"
    
    keyboard = [
        [InlineKeyboardButton("📦 Gestion du catalogue", callback_data="menu_catalog")],
        [InlineKeyboardButton("🎨 Configuration du bot", callback_data="menu_config")],
        [InlineKeyboardButton("👥 Utilisateurs & Accès", callback_data="menu_users")],
        [
            InlineKeyboardButton("📊 Statistiques", callback_data="show_stats"),
            InlineKeyboardButton("📢 Annonces", callback_data="manage_broadcasts")
        ],
        [InlineKeyboardButton("🔙 Retour à l'accueil", callback_data="back_to_home")]
    ]

    admin_text = (
        "🔧 *Menu d'administration*\n\n"
        "Sélectionnez une catégorie :"
    )

    try:
        if update.callback_query:
            message = await update.callback_query.edit_message_text(
                admin_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            message = await update.message.reply_text(
                admin_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        context.user_data['menu_message_id'] = message.message_id
    except Exception as e:
        print(f"Erreur dans show_admin_menu: {e}")

    return CHOOSING

async def show_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le message d'information"""
    query = update.callback_query
    await query.answer()

    info_text = CONFIG.get('info_message', 
        "ℹ️ Aucune information n'a été configurée.\n"
        "Les administrateurs peuvent ajouter des informations depuis le menu admin."
    )

    keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data="back_to_home")]]

    await query.edit_message_text(
        text=info_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return CHOOSING

async def edit_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Démarre l'édition du message d'information"""
    query = update.callback_query
    await query.answer()

    current_info = CONFIG.get('info_message', "Aucune information configurée.")
    
    await query.edit_message_text(
        "📝 Envoyez le nouveau message d'information :\n"
        "Vous pouvez utiliser le formatage HTML pour mettre en forme votre texte.\n\n"
        "Message actuel :\n"
        f"{current_info}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Annuler", callback_data="admin")
        ]]),
        parse_mode='HTML'
    )
    return WAITING_INFO_MESSAGE

async def handle_info_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la réception du nouveau message d'information"""
    new_info = update.message.text_html if hasattr(update.message, 'text_html') else update.message.text

    CONFIG['info_message'] = new_info
    with open('config/config.json', 'w', encoding='utf-8') as f:
        json.dump(CONFIG, f, indent=4)

    try:
        await update.message.delete()
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id - 1
        )
    except Exception as e:
        print(f"Erreur lors de la suppression des messages : {e}")

    success_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✅ Message d'information mis à jour avec succès !",
        parse_mode='HTML'
    )

    await asyncio.sleep(3)
    await success_msg.delete()

    return await show_admin_menu(update, context)

async def handle_new_category_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la modification du nom d'une catégorie"""
    if str(update.message.from_user.id) not in ADMIN_IDS:
        return

    new_name = update.message.text
    old_name = context.user_data.get('category_to_edit')

    if old_name and old_name in CATALOG:
        if new_name in CATALOG:
            await update.message.reply_text(
                "❌ Une catégorie avec ce nom existe déjà.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour", callback_data=f"edit_cat_{old_name}")
                ]])
            )
            return EDITING_CATEGORY

        products = CATALOG[old_name]
        del CATALOG[old_name]
        CATALOG[new_name] = products
        save_catalog(CATALOG)

        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id - 1
            )
            await update.message.delete()
        except:
            pass

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ Nom de la catégorie modifié avec succès!\n\n"
                 f"*{old_name}* ➡️ *{new_name}*",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Retour au menu admin", callback_data="admin")
            ]]),
            parse_mode='Markdown'
        )
        return CHOOSING

    return EDITING_CATEGORY
    
async def show_custom_buttons_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le menu de gestion des boutons personnalisés"""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("➕ Ajouter un bouton", callback_data="add_custom_button")],
        [InlineKeyboardButton("❌ Supprimer un bouton", callback_data="list_buttons_delete")],
        [InlineKeyboardButton("✏️ Modifier un bouton", callback_data="list_buttons_edit")],
        [InlineKeyboardButton("🔙 Retour", callback_data="admin")]
    ]

    await query.edit_message_text(
        "🔧 Gestion des boutons personnalisés\n\n"
        "Choisissez une action :",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING

async def start_add_custom_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commence le processus d'ajout d'un bouton personnalisé"""
    query = update.callback_query
    await query.answer()
    
    message = await query.edit_message_text(
        "➕ Ajout d'un nouveau bouton\n\n"
        "Envoyez le nom du bouton (exemple: '🌟 Mon Bouton') :",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Annuler", callback_data="show_custom_buttons")
        ]])
    )
    
    context.user_data['messages_to_delete'] = [message.message_id]
    
    return WAITING_BUTTON_NAME

async def handle_order_button_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gère la configuration du bouton Commander"""
        new_config = update.message.text_html if hasattr(update.message, 'text_html') else update.message.text.strip()
    
        try:
            await update.message.delete()
        
            if new_config.startswith(('http://', 'https://')):
                CONFIG['order_url'] = new_config
                CONFIG['order_text'] = None
                CONFIG['order_telegram'] = None
                button_type = "URL"
            elif new_config.startswith('@') or not any(c in new_config for c in ' /?=&'):
                username = new_config[1:] if new_config.startswith('@') else new_config
                CONFIG['order_telegram'] = username
                CONFIG['order_url'] = f"https://t.me/{username}"
                CONFIG['order_text'] = None
                button_type = "Telegram"
            else:
                CONFIG['order_text'] = new_config
                CONFIG['order_url'] = None
                CONFIG['order_telegram'] = None
                button_type = "texte"
            
            with open('config/config.json', 'w', encoding='utf-8') as f:
                json.dump(CONFIG, f, indent=4)
        
            if 'edit_order_button_message_id' in context.user_data:
                try:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=context.user_data['edit_order_button_message_id']
                    )
                except:
                    pass
        
            display_value = new_config
            if button_type == "Telegram" and not new_config.startswith('@'):
                display_value = f"@{new_config}"
            
            success_message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ Configuration du bouton Commander mise à jour avec succès!\n\n"
                     f"Type: {button_type}\n"
                     f"Valeur: {display_value}",
                parse_mode='HTML'
            )
        
            await asyncio.sleep(3)
            try:
                await success_message.delete()
            except:
                pass
        
            return await show_admin_menu(update, context)
        
        except Exception as e:
            print(f"Erreur dans handle_order_button_config: {e}")
            return WAITING_ORDER_BUTTON_CONFIG

async def handle_network_button_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la modification du nom d'un bouton réseau"""
    query = update.callback_query
    await query.answer()
    
    network_id = query.data.replace("edit_network_name_", "")
    context.user_data['editing_network_id'] = network_id
    context.user_data['is_network_button'] = True
    
    with open('config/config.json', 'r') as f:
        config = json.load(f)
    
    network = next((n for n in config.get('networks_buttons', []) if n['id'] == network_id), None)
    
    message = await query.edit_message_text(
        f"✏️ Modification du nom du réseau\n\n"
        f"Nom actuel : {network['name']}\n\n"
        "Envoyez le nouveau nom :",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Annuler", callback_data=f"edit_network_{network_id}")
        ]])
    )
    
    context.user_data['messages_to_delete'] = [message.message_id]
    return WAITING_BUTTON_NAME

async def handle_network_button_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la modification de la valeur d'un bouton réseau"""
    query = update.callback_query
    await query.answer()
    
    network_id = query.data.replace("edit_network_value_", "")
    context.user_data['editing_network_id'] = network_id
    context.user_data['is_network_button'] = True
    
    with open('config/config.json', 'r') as f:
        config = json.load(f)
    
    network = next((n for n in config.get('networks_buttons', []) if n['id'] == network_id), None)
    
    message = await query.edit_message_text(
        f"✏️ Modification de la valeur du réseau\n\n"
        f"Valeur actuelle : {network['value']}\n\n"
        "Envoyez la nouvelle valeur :\n"
        "• Pour un lien : envoyez une URL commençant par http:// ou https://\n"
        "• Pour du texte : envoyez le texte à afficher",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Annuler", callback_data=f"edit_network_{network_id}")
        ]])
    )
    
    context.user_data['messages_to_delete'] = [message.message_id]
    return WAITING_BUTTON_VALUE

async def handle_button_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la réception du nom du bouton"""
    button_name = update.message.text
    chat_id = update.effective_chat.id
    
    # Supprimer le message de l'utilisateur
    await update.message.delete()
    
    # Supprimer tous les messages précédents stockés
    messages_to_delete = context.user_data.get('messages_to_delete', [])
    for msg_id in messages_to_delete:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            print(f"Erreur lors de la suppression du message {msg_id}: {e}")
    
    # Vérifier si on est en mode édition
    if 'editing_network_id' in context.user_data:  # Pour les boutons réseaux
        button_id = context.user_data['editing_network_id']
        
        with open('config/config.json', 'r') as f:
            config = json.load(f)
        
        # Chercher dans les boutons réseaux
        for button in config.get('networks_buttons', []):
            if button['id'] == button_id:
                button['name'] = button_name
                break
        
        with open('config/config.json', 'w') as f:
            json.dump(config, f, indent=4)
        
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="✅ Nom du bouton modifié avec succès !",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Retour", callback_data="manage_networks")
            ]])
        )
        
        context.user_data.clear()
        return CHOOSING
        
    elif 'editing_button_id' in context.user_data:  # Pour les boutons normaux
        button_id = context.user_data['editing_button_id']
        
        # Charger la configuration
        with open('config/config.json', 'r') as f:
            config = json.load(f)
        
        # Mettre à jour le nom du bouton
        for button in config.get('custom_buttons', []):
            if button['id'] == button_id:
                button['name'] = button_name
                break
        
        # Sauvegarder la configuration
        with open('config/config.json', 'w') as f:
            json.dump(config, f, indent=4)
        
        # Message de confirmation
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="✅ Nom du bouton modifié avec succès !",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Retour", callback_data="show_custom_buttons")
            ]])
        )
        
        # Nettoyer les données utilisateur
        context.user_data.clear()
        return CHOOSING
    
    # Mode création
    context.user_data['temp_button'] = {'name': button_name}
    
    message = await context.bot.send_message(
        chat_id=chat_id,
        text="Maintenant, envoyez :\n\n"
             "- Une URL (commençant par http:// ou https://) pour créer un bouton de lien\n"
             "- Ou du texte pour créer un bouton qui affichera ce texte",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Annuler", callback_data=context.user_data.get('is_network_button', False) and "manage_networks" or "show_custom_buttons")
        ]])
    )
    
    # Mettre à jour la liste des messages à supprimer
    context.user_data['messages_to_delete'] = [message.message_id]
    
    return WAITING_BUTTON_VALUE

async def start_edit_button_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commence l'édition du nom d'un bouton"""
    query = update.callback_query
    await query.answer()
    
    button_id = query.data.replace("edit_button_name_", "")
    context.user_data['editing_button_id'] = button_id
    
    with open('config/config.json', 'r') as f:
        config = json.load(f)
    
    button = next((b for b in config.get('custom_buttons', []) if b['id'] == button_id), None)
    
    message = await query.edit_message_text(
        f"✏️ Modification du nom du bouton\n\n"
        f"Nom actuel : {button['name']}\n\n"
        "Envoyez le nouveau nom :",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Annuler", callback_data=f"edit_button_{button_id}")
        ]])
    )
    
    # Initialiser ou réinitialiser la liste des messages à supprimer
    context.user_data['messages_to_delete'] = [message.message_id]
    
    return WAITING_BUTTON_NAME

async def start_edit_button_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commence l'édition de la valeur d'un bouton"""
    query = update.callback_query
    await query.answer()
    
    button_id = query.data.replace("edit_button_value_", "")
    context.user_data['editing_button_id'] = button_id
    
    with open('config/config.json', 'r') as f:
        config = json.load(f)
    
    button = next((b for b in config.get('custom_buttons', []) if b['id'] == button_id), None)
    
    message = await query.edit_message_text(
        f"✏️ Modification de la valeur du bouton\n\n"
        f"Valeur actuelle : {button['value']}\n\n"
        "Envoyez la nouvelle valeur :\n"
        "• Pour un bouton URL : envoyez un lien commençant par http:// ou https://\n"
        "• Pour un bouton texte : envoyez le texte à afficher",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Annuler", callback_data=f"edit_button_{button_id}")
        ]])
    )
    
    # Initialiser ou réinitialiser la liste des messages à supprimer
    context.user_data['messages_to_delete'] = [message.message_id]
    
    return WAITING_BUTTON_VALUE

async def handle_button_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la réception de la valeur du bouton"""
    value = update.message.text_html if hasattr(update.message, 'text_html') else update.message.text
    chat_id = update.effective_chat.id
    
    # Supprimer le message de l'utilisateur
    await update.message.delete()
    
    # Supprimer tous les messages précédents stockés
    messages_to_delete = context.user_data.get('messages_to_delete', [])
    for msg_id in messages_to_delete:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            print(f"Erreur lors de la suppression du message {msg_id}: {e}")
    
    is_url = value.startswith(('http://', 'https://'))
    is_network = context.user_data.get('is_network_button', False)
    
    with open('config/config.json', 'r') as f:
        config = json.load(f)
    
    if 'editing_button_id' in context.user_data:
        # Mode édition
        button_id = context.user_data['editing_button_id']
        button_list = 'networks_buttons' if is_network else 'custom_buttons'
        success_text = "✅ Valeur du bouton modifiée avec succès !"  # Ajout du texte pour l'édition
        
        if button_list not in config:
            config[button_list] = []
        
        for button in config[button_list]:
            if button['id'] == button_id:
                button['value'] = value
                button['type'] = 'url' if is_url else 'text'
                button['parse_mode'] = 'HTML' if not is_url else None
                break
        
        return_callback = "manage_networks" if is_network else "show_custom_buttons"
        
    else:
        # Mode création
        temp_button = context.user_data.get('temp_button', {})
        button_list = 'networks_buttons' if is_network else 'custom_buttons'
        success_text = "✅ Bouton ajouté avec succès !"  # Ajout du texte pour la création
        
        if button_list not in config:
            config[button_list] = []
        
        button_id = f"button_{len(config[button_list]) + 1}"
        new_button = {
            'id': button_id,
            'name': temp_button.get('name', 'Bouton'),
            'type': 'url' if is_url else 'text',
            'value': value,
            'parse_mode': 'HTML' if not is_url else None
        }
        
        config[button_list].append(new_button)
        return_callback = "manage_networks" if is_network else "show_custom_buttons"
    
    with open('config/config.json', 'w') as f:
        json.dump(config, f, indent=4)
    
    # Nettoyer les données utilisateur
    context.user_data.clear()
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=success_text,  # Utilisation du texte approprié selon le mode
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Retour", callback_data=return_callback)
        ]])
    )
    return CHOOSING

async def list_buttons_for_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liste les boutons pour suppression"""
    query = update.callback_query
    await query.answer()
    
    with open('config/config.json', 'r') as f:
        config = json.load(f)
    
    buttons = config.get('custom_buttons', [])
    if not buttons:
        await query.edit_message_text(
            "Aucun bouton personnalisé n'existe.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Retour", callback_data="show_custom_buttons")
            ]])
        )
        return CHOOSING
    
    keyboard = []
    for button in buttons:
        keyboard.append([InlineKeyboardButton(
            f"❌ {button['name']}", 
            callback_data=f"delete_button_{button['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="show_custom_buttons")])
    
    await query.edit_message_text(
        "Sélectionnez le bouton à supprimer :",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING

async def handle_button_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la suppression d'un bouton"""
    query = update.callback_query
    await query.answer()
    
    button_id = query.data.replace("delete_button_", "")
    
    with open('config/config.json', 'r') as f:
        config = json.load(f)
    
    config['custom_buttons'] = [b for b in config.get('custom_buttons', []) if b['id'] != button_id]
    
    with open('config/config.json', 'w') as f:
        json.dump(config, f, indent=4)
    
    await query.edit_message_text(
        "✅ Bouton supprimé avec succès !",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Retour", callback_data="show_custom_buttons")
        ]])
    )
    return CHOOSING

async def list_buttons_for_editing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liste les boutons pour modification"""
    query = update.callback_query
    await query.answer()
    
    with open('config/config.json', 'r') as f:
        config = json.load(f)
    
    buttons = config.get('custom_buttons', [])
    if not buttons:
        await query.edit_message_text(
            "Aucun bouton personnalisé n'existe.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Retour", callback_data="show_custom_buttons")
            ]])
        )
        return CHOOSING
    
    keyboard = []
    for button in buttons:
        keyboard.append([InlineKeyboardButton(
            f"✏️ {button['name']}", 
            callback_data=f"edit_button_{button['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="show_custom_buttons")])
    
    await query.edit_message_text(
        "Sélectionnez le bouton à modifier :",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING

async def handle_button_editing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la modification d'un bouton"""
    query = update.callback_query
    await query.answer()
    
    button_id = query.data.replace("edit_button_", "")
    context.user_data['editing_button_id'] = button_id
    
    with open('config/config.json', 'r') as f:
        config = json.load(f)
    
    button = next((b for b in config.get('custom_buttons', []) if b['id'] == button_id), None)
    if button:
        keyboard = [
            [InlineKeyboardButton("✏️ Modifier le nom", callback_data=f"edit_button_name_{button_id}")],
            [InlineKeyboardButton("🔗 Modifier la valeur", callback_data=f"edit_button_value_{button_id}")],
            [InlineKeyboardButton("🔙 Retour", callback_data="list_buttons_edit")]
        ]
        
        await query.edit_message_text(
            f"Modification du bouton : {button['name']}\n"
            f"Type actuel : {button['type']}\n"
            f"Valeur actuelle : {button['value']}\n\n"
            "Que souhaitez-vous modifier ?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CHOOSING

async def handle_banner_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère l'ajout de l'image bannière"""
    if not update.message.photo:
        await update.message.reply_text("Veuillez envoyer une photo.")
        return WAITING_BANNER_IMAGE

    # Supprimer le message précédent
    if 'banner_msg' in context.user_data:
        await context.bot.delete_message(
            chat_id=context.user_data['banner_msg'].chat_id,
            message_id=context.user_data['banner_msg'].message_id
        )
        del context.user_data['banner_msg']

    # Obtenir l'ID du fichier de la photo
    file_id = update.message.photo[-1].file_id
    
    # IMPORTANT : Recharger la configuration depuis le fichier pour ne pas écraser les modifications récentes
    with open('config/config.json', 'r', encoding='utf-8') as f:
        current_config = json.load(f)
    
    # Mettre à jour l'image bannière
    current_config['banner_image'] = file_id
    
    # Sauvegarder la configuration mise à jour
    with open('config/config.json', 'w', encoding='utf-8') as f:
        json.dump(current_config, f, indent=4)
    
    # Mettre aussi à jour la variable globale CONFIG pour cohérence
    CONFIG['banner_image'] = file_id

    # Supprimer le message contenant l'image
    await update.message.delete()

    thread_id = update.message.message_thread_id if update.message.is_topic_message else None

    # Envoyer le message de confirmation
    success_msg = await update.message.reply_text(
        "✅ Image bannière mise à jour avec succès !",
        message_thread_id=thread_id
    )

    # Attendre 3 secondes et supprimer le message
    await asyncio.sleep(3)
    await success_msg.delete()

    # Supprimer l'ancienne bannière si elle existe
    if 'banner_message_id' in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['banner_message_id']
            )
        except:
            pass

    # Envoyer la nouvelle bannière (utiliser current_config au lieu de CONFIG)
    if current_config.get('banner_image'):
        try:
            banner_message = await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=current_config['banner_image']
            )
            context.user_data['banner_message_id'] = banner_message.message_id
        except Exception as e:
            print(f"Erreur lors de l'envoi de la bannière: {e}")

    return await show_admin_menu(update, context)

async def handle_category_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère l'ajout d'une nouvelle catégorie"""
    category_name = update.message.text.strip()
    
    def count_emojis(text):
        emoji_pattern = re.compile("["
            u"\U0001F600-\U0001F64F"
            u"\U0001F300-\U0001F5FF"  
            u"\U0001F680-\U0001F6FF"  
            u"\U0001F1E0-\U0001F1FF"  
            u"\U00002702-\U000027B0"
            u"\U000024C2-\U0001F251"
            "]+", flags=re.UNICODE)
        return len(emoji_pattern.findall(text))
    
    MAX_LENGTH = 32 
    MAX_EMOJIS = 3   
    MAX_WORDS = 5    
    
    word_count = len(category_name.split())
    emoji_count = count_emojis(category_name)
    
    error_message = None
    if len(category_name) > MAX_LENGTH:
        error_message = f"❌ Le nom de la catégorie ne doit pas dépasser {MAX_LENGTH} caractères."
    elif word_count > MAX_WORDS:
        error_message = f"❌ Le nom de la catégorie ne doit pas dépasser {MAX_WORDS} mots."
    elif emoji_count > MAX_EMOJIS:
        error_message = f"❌ Le nom de la catégorie ne doit pas contenir plus de {MAX_EMOJIS} emojis."
    elif category_name in CATALOG:
        error_message = "❌ Cette catégorie existe déjà."
    
    if error_message:
        await update.message.reply_text(
            error_message + "\nVeuillez choisir un autre nom:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Annuler", callback_data="cancel_add_category")
            ]])
        )
        return WAITING_CATEGORY_NAME
    
    CATALOG[category_name] = []
    save_catalog(CATALOG)
    
    await context.bot.delete_message(
        chat_id=update.effective_chat.id,
        message_id=update.message.message_id - 1
    )
    
    await update.message.delete()
    
    return await show_admin_menu(update, context)

async def handle_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère l'entrée du nom du produit"""
    try:
        product_name = update.message.text
        category = context.user_data.get('temp_product_category')
        admin_id = str(update.effective_user.id)
        
        if category and CATALOG.get(category):
            if len(CATALOG[category]) == 1 and CATALOG[category][0].get('name') == 'SOLD OUT ! ❌':
                CATALOG[category] = []
                save_catalog(CATALOG)

        if category and any(p.get('name') == product_name for p in CATALOG.get(category, [])):
            await update.message.reply_text(
                "❌ Ce produit existe déjà dans cette catégorie. Veuillez choisir un autre nom:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Annuler", callback_data="cancel_add_product")
                ]])
            )
            return WAITING_PRODUCT_NAME

        creation_key = f"admin_{admin_id}"
        ADMIN_CREATIONS[creation_key] = {
            'category': category,
            'name': product_name,
            'status': 'name_added',
            'timestamp': datetime.now().isoformat()
        }
        
        context.user_data['temp_product_name'] = product_name
        context.user_data['creation_key'] = creation_key
        
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id - 1
            )
        except Exception as e:
            print(f"Erreur suppression message précédent: {e}")
            
        try:
            await update.message.delete()
        except Exception as e:
            print(f"Erreur suppression message utilisateur: {e}")

        await update.message.reply_text(
            "💰 Veuillez entrer le prix du produit:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Annuler", callback_data="cancel_add_product")
            ]])
        )
        
        return WAITING_PRODUCT_PRICE
        
    except Exception as e:
        print(f"Erreur dans handle_product_name: {e}")
        return WAITING_PRODUCT_NAME

async def handle_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère l'entrée du prix du produit"""
    try:
        price = update.message.text_html if hasattr(update.message, 'text_html') else update.message.text
        admin_id = str(update.effective_user.id)
        creation_key = f"admin_{admin_id}"
        
        if creation_key in ADMIN_CREATIONS:
            ADMIN_CREATIONS[creation_key]['price'] = price
            ADMIN_CREATIONS[creation_key]['status'] = 'price_added'
            
        context.user_data['temp_product_price'] = price
        
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id - 1
            )
        except Exception as e:
            print(f"Erreur suppression message précédent: {e}")
            
        try:
            await update.message.delete()
        except Exception as e:
            print(f"Erreur suppression message utilisateur: {e}")
        
        await update.message.reply_text(
            "📝 Veuillez entrer la description du produit:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Annuler", callback_data="cancel_add_product")
            ]])
        )
        
        return WAITING_PRODUCT_DESCRIPTION
        
    except Exception as e:
        print(f"Erreur dans handle_product_price: {e}")
        return WAITING_PRODUCT_PRICE

async def handle_product_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère l'entrée de la description du produit"""
    try:
        description = update.message.text_html if hasattr(update.message, 'text_html') else update.message.text
        admin_id = str(update.effective_user.id)
        creation_key = f"admin_{admin_id}"
        
        if creation_key in ADMIN_CREATIONS:
            ADMIN_CREATIONS[creation_key]['description'] = description
            ADMIN_CREATIONS[creation_key]['status'] = 'description_added'
            
        context.user_data['temp_product_description'] = description
        context.user_data['temp_product_media'] = []
        
        # NOUVEAU: Initialiser les données de promo
        context.user_data['temp_product_promo'] = {
            'is_promo': False,
            'original_price': None,
            'promo_price': None,
            'promo_badge': None,
            'promo_end_date': None
        }
        
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id - 1
            )
        except Exception as e:
            print(f"Erreur suppression message précédent: {e}")
            
        try:
            await update.message.delete()
        except Exception as e:
            print(f"Erreur suppression message utilisateur: {e}")
        
        invitation_message = await update.message.reply_text(
            "📸 Envoyez les photos ou vidéos du produit (plusieurs possibles)\n"
            "Si vous ne voulez pas en envoyer, cliquez sur ignorer :",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏩ Ignorer", callback_data="skip_media")],
                [InlineKeyboardButton("🔙 Annuler", callback_data="cancel_add_product")]
            ])
        )
        context.user_data['media_invitation_message_id'] = invitation_message.message_id
        
        return WAITING_PRODUCT_MEDIA
        
    except Exception as e:
        print(f"Erreur dans handle_product_description: {e}")
        return WAITING_PRODUCT_DESCRIPTION

async def show_promo_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, product):
    """Affiche le menu de gestion des promotions pour un produit"""
    query = update.callback_query
    
    promo = product.get('promo', {})
    is_promo = promo.get('is_promo', False)
    
    if is_promo:
        status_text = "✅ EN PROMOTION"
        promo_info = f"\n🏷️ Prix original: {promo.get('original_price', 'Non défini')}"
        promo_info += f"\n💰 Prix promo: {promo.get('promo_price', 'Non défini')}"
        promo_info += f"\n🔥 Badge: {promo.get('promo_badge', 'Non défini')}"
        if promo.get('promo_end_date'):
            promo_info += f"\n⏰ Fin: {promo.get('promo_end_date')}"
    else:
        status_text = "❌ PAS DE PROMOTION"
        promo_info = ""
    
    keyboard = [
        [InlineKeyboardButton("🔥 Activer la promotion", callback_data="activate_promo")],
        [InlineKeyboardButton("❌ Désactiver la promotion", callback_data="deactivate_promo")],
        [InlineKeyboardButton("⚡ Promotions rapides", callback_data="quick_promos")],
        [InlineKeyboardButton("🔙 Retour", callback_data="cancel_edit")]
    ]
    
    await query.edit_message_text(
        f"🎯 <b>Gestion des promotions</b>\n\n"
        f"Produit: <b>{product['name']}</b>\n"
        f"Statut: {status_text}{promo_info}\n\n"
        f"Que souhaitez-vous faire ?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return CHOOSING

# 8. PROMOTIONS RAPIDES
async def show_quick_promos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les promotions rapides prédéfinies"""
    query = update.callback_query
    
    keyboard = [
        [InlineKeyboardButton("🔥 -10% - PROMO", callback_data="quick_promo_10")],
        [InlineKeyboardButton("⚡ -20% - FLASH", callback_data="quick_promo_20")],
        [InlineKeyboardButton("💥 -30% - MEGA PROMO", callback_data="quick_promo_30")],
        [InlineKeyboardButton("🎯 -50% - LIQUIDATION", callback_data="quick_promo_50")],
        [InlineKeyboardButton("🔙 Retour", callback_data="edit_promo")]
    ]
    
    await query.edit_message_text(
        "⚡ <b>Promotions rapides</b>\n\n"
        "Sélectionnez une promotion prédéfinie:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return CHOOSING

# 9. APPLICATION DES PROMOTIONS RAPIDES
async def apply_quick_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Applique une promotion rapide"""
    query = update.callback_query
    
    # Mapping des promotions
    promo_mapping = {
        "quick_promo_10": {"discount": 10, "badge": "🔥 -10%"},
        "quick_promo_20": {"discount": 20, "badge": "⚡ -20%"},
        "quick_promo_30": {"discount": 30, "badge": "💥 -30%"},
        "quick_promo_50": {"discount": 50, "badge": "🎯 -50%"}
    }
    
    promo_data = promo_mapping.get(query.data)
    if not promo_data:
        return CHOOSING
    
    category = context.user_data.get('editing_category')
    product_name = context.user_data.get('editing_product')
    
    if category and product_name:
        for product in CATALOG[category]:
            if product['name'] == product_name:
                # Calculer le nouveau prix
                try:
                    # Extraire le prix numérique (enlever symboles)
                    import re
                    original_price_str = product['price']
                    price_numbers = re.findall(r'\d+(?:[.,]\d+)?', original_price_str)
                    if price_numbers:
                        original_price_num = float(price_numbers[0].replace(',', '.'))
                        promo_price_num = original_price_num * (1 - promo_data['discount'] / 100)
                        promo_price_str = original_price_str.replace(
                            price_numbers[0], 
                            f"{promo_price_num:.2f}".replace('.', ',')
                        )
                    else:
                        promo_price_str = product['price']
                except:
                    promo_price_str = product['price']
                
                # Appliquer la promotion
                product['promo'] = {
                    'is_promo': True,
                    'original_price': product['price'],
                    'promo_price': promo_price_str,
                    'promo_badge': promo_data['badge'],
                    'promo_end_date': None
                }
                
                save_catalog(CATALOG)
                
                await query.edit_message_text(
                    f"✅ <b>Promotion appliquée!</b>\n\n"
                    f"Produit: {product['name']}\n"
                    f"Prix original: {product['price']}\n"
                    f"Prix promo: {promo_price_str}\n"
                    f"Badge: {promo_data['badge']}",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Retour au menu", callback_data="admin")
                    ]]),
                    parse_mode='HTML'
                )
                break
    
    return CHOOSING

async def handle_product_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère l'ajout des médias (photos ou vidéos) du produit"""
    if not (update.message.photo or update.message.video):
        await update.message.reply_text("Veuillez envoyer une photo ou une vidéo.")
        return WAITING_PRODUCT_MEDIA

    if 'temp_product_media' not in context.user_data:
        context.user_data['temp_product_media'] = []

    if 'media_count' not in context.user_data:
        context.user_data['media_count'] = 0

    if context.user_data.get('media_invitation_message_id'):
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['media_invitation_message_id']
            )
            del context.user_data['media_invitation_message_id']
        except Exception as e:
            print(f"Erreur lors de la suppression du message d'invitation: {e}")

    if context.user_data.get('last_confirmation_message_id'):
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['last_confirmation_message_id']
            )
        except Exception as e:
            print(f"Erreur lors de la suppression du message de confirmation: {e}")

    context.user_data['media_count'] += 1

    if update.message.photo:
        media_id = update.message.photo[-1].file_id
        media_type = 'photo'
    else:
        media_id = update.message.video.file_id
        media_type = 'video'

    new_media = {
        'media_id': media_id,
        'media_type': media_type,
        'order_index': context.user_data['media_count']
    }

    context.user_data['temp_product_media'].append(new_media)

    await update.message.delete()

    message = await update.message.reply_text(
        f"Photo/Vidéo {context.user_data['media_count']} ajoutée ! Cliquez sur Terminé pour valider :",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Terminé", callback_data="finish_media")],
            [InlineKeyboardButton("🔙 Annuler", callback_data="cancel_add_product")]
        ])
    )
    context.user_data['last_confirmation_message_id'] = message.message_id

    return WAITING_PRODUCT_MEDIA

async def finish_product_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    admin_id = str(update.effective_user.id)
    creation_key = f"admin_{admin_id}"
    
    try:
        if creation_key in ADMIN_CREATIONS:
            category = ADMIN_CREATIONS[creation_key]['category']
            new_product = {
                'name': ADMIN_CREATIONS[creation_key]['name'],
                'price': ADMIN_CREATIONS[creation_key]['price'],
                'description': ADMIN_CREATIONS[creation_key]['description'],
                'media': context.user_data.get('temp_product_media', []),
                # NOUVEAU: Ajouter les données de promo
                'promo': context.user_data.get('temp_product_promo', {
                    'is_promo': False,
                    'original_price': None,
                    'promo_price': None,
                    'promo_badge': None,
                    'promo_end_date': None
                })
            }
            
            if creation_key in ADMIN_CREATIONS:
                category = ADMIN_CREATIONS[creation_key]['category']
                new_product = {
                    'name': ADMIN_CREATIONS[creation_key]['name'],
                    'price': ADMIN_CREATIONS[creation_key]['price'],
                    'description': ADMIN_CREATIONS[creation_key]['description'],
                    'media': context.user_data.get('temp_product_media', [])
                }
            
                if category not in CATALOG:
                    CATALOG[category] = []
                CATALOG[category].append(new_product)
            
                if creation_key in ADMIN_CREATIONS:
                    del ADMIN_CREATIONS[creation_key]
                
        elif 'editing_category' in context.user_data and 'editing_product' in context.user_data:
            category = context.user_data['editing_category']
            product_name = context.user_data['editing_product']
            
            for product in CATALOG[category]:
                if product['name'] == product_name:
                    product['media'] = context.user_data.get('temp_product_media', [])
                    break
        
        save_catalog(CATALOG)
        context.user_data.clear()

        try:
            await query.message.delete()
        except Exception as e:
            print(f"Erreur lors de la suppression du message: {e}")

        keyboard = [
            [InlineKeyboardButton("📦 Gestion du catalogue", callback_data="menu_catalog")],
            [InlineKeyboardButton("🎨 Configuration du bot", callback_data="menu_config")],
            [InlineKeyboardButton("👥 Utilisateurs & Accès", callback_data="menu_users")],
            [
                InlineKeyboardButton("📊 Statistiques", callback_data="show_stats"),
                InlineKeyboardButton("📢 Annonces", callback_data="manage_broadcasts")
            ],
            [InlineKeyboardButton("🔙 Retour à l'accueil", callback_data="back_to_home")]
        ]

        message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="✅ Modifications enregistrées avec succès !\n\n"
                 "🔧 *Menu d'administration*\n\n"
                 "Sélectionnez une catégorie :",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        
        context.user_data['menu_message_id'] = message.message_id
        return CHOOSING
            
    except Exception as e:
        print(f"Erreur dans finish_product_media: {e}")

        keyboard = [
            [InlineKeyboardButton("📦 Gestion du catalogue", callback_data="menu_catalog")],
            [InlineKeyboardButton("🎨 Configuration du bot", callback_data="menu_config")],
            [InlineKeyboardButton("👥 Utilisateurs & Accès", callback_data="menu_users")],
            [
                InlineKeyboardButton("📊 Statistiques", callback_data="show_stats"),
                InlineKeyboardButton("📢 Annonces", callback_data="manage_broadcasts")
            ],
            [InlineKeyboardButton("🔙 Retour à l'accueil", callback_data="back_to_home")]
        ]
        
        message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="❌ Une erreur est survenue mais les modifications ont été sauvegardées.\n\n"
                 "🔧 *Menu d'administration*\n\n"
                 "Sélectionnez une catégorie :",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        
        context.user_data['menu_message_id'] = message.message_id
        return CHOOSING

async def handle_new_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la nouvelle valeur pour le champ en cours de modification"""
    category = context.user_data.get('editing_category')
    product_name = context.user_data.get('editing_product')
    field = context.user_data.get('editing_field')
    
    # Utiliser text_html pour capturer le formatage
    new_value = update.message.text_html if hasattr(update.message, 'text_html') else update.message.text

    if not all([category, product_name, field]):
        await update.message.reply_text("❌ Une erreur est survenue. Veuillez réessayer.")
        return await show_admin_menu(update, context)

    for product in CATALOG.get(category, []):
        if product['name'] == product_name:
            old_value = product.get(field, "Non défini")
            product[field] = new_value
            save_catalog(CATALOG)

            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id - 1
            )
            await update.message.delete()

            keyboard = [[InlineKeyboardButton("🔙 Retour au menu", callback_data="admin")]]
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ Modification effectuée avec succès !\n\n"
                     f"Ancien {field}: {old_value}\n"
                     f"Nouveau {field}: {new_value}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'  # Ajout du parse_mode HTML
            )
            break

    return CHOOSING

async def handle_contact_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la modification du contact"""
    new_value = update.message.text.strip()
    
    try:
        # Supprimer le message de l'utilisateur
        await update.message.delete()
        
        if new_value.startswith(('http://', 'https://')):
            # C'est une URL
            CONFIG['contact_url'] = new_value
            CONFIG['contact_username'] = None
            config_type = "URL"
        else:
            # C'est un pseudo Telegram
            username = new_value.replace("@", "")
            # Vérifier le format basique d'un username Telegram
            if not bool(re.match(r'^[a-zA-Z0-9_]{5,32}$', username)):
                if 'edit_contact_message_id' in context.user_data:
                    await context.bot.edit_message_text(
                        chat_id=update.effective_chat.id,
                        message_id=context.user_data['edit_contact_message_id'],
                        text="❌ Format d'username Telegram invalide.\n"
                             "L'username doit contenir entre 5 et 32 caractères,\n"
                             "uniquement des lettres, chiffres et underscores (_).\n\n"
                             "Veuillez réessayer:",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔙 Annuler", callback_data="cancel_edit_contact")
                        ]])
                    )
                return WAITING_CONTACT_USERNAME
                
            CONFIG['contact_username'] = username
            CONFIG['contact_url'] = None
            config_type = "Pseudo Telegram"
        
        # Sauvegarder dans config.json
        with open('config/config.json', 'w', encoding='utf-8') as f:
            json.dump(CONFIG, f, indent=4)
        
        # Supprimer l'ancien message de configuration
        if 'edit_contact_message_id' in context.user_data:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=context.user_data['edit_contact_message_id']
                )
            except:
                pass
        
        # Message de confirmation avec le @ ajouté si c'est un pseudo Telegram sans @
        display_value = new_value
        if config_type == "Pseudo Telegram" and not new_value.startswith('@'):
            display_value = f"@{new_value}"
        
        success_message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ Configuration du contact mise à jour avec succès!\n\n"
                 f"Type: {config_type}\n"
                 f"Valeur: {display_value}",
            parse_mode='HTML'
        )
        
        # Attendre 3 secondes puis supprimer le message de confirmation
        await asyncio.sleep(3)
        try:
            await success_message.delete()
        except:
            pass
        
        return await show_admin_menu(update, context)
        
    except Exception as e:
        print(f"Erreur dans handle_contact_username: {e}")
        return WAITING_CONTACT_USERNAME

async def handle_welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la modification du message d'accueil"""
    # Utiliser text_html pour capturer le formatage
    new_message = update.message.text_html if hasattr(update.message, 'text_html') else update.message.text
    
    try:
        # Supprimer le message de l'utilisateur
        await update.message.delete()
        
        # Mettre à jour la config
        CONFIG['welcome_message'] = new_message
        
        # Sauvegarder dans config.json
        with open('config/config.json', 'w', encoding='utf-8') as f:
            json.dump(CONFIG, f, indent=4)
        
        # Supprimer l'ancien message si possible
        if 'edit_welcome_message_id' in context.user_data:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=context.user_data['edit_welcome_message_id']
                )
            except:
                pass
        
        # Message de confirmation
        success_message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ Message d'accueil mis à jour avec succès!\n\n"
                 f"Nouveau message :\n{new_message}",
            parse_mode='HTML'
        )
        
        # Attendre 3 secondes puis supprimer le message de confirmation
        await asyncio.sleep(3)
        try:
            await success_message.delete()
        except:
            pass
        
        return await show_admin_menu(update, context)
        
    except Exception as e:
        print(f"Erreur dans handle_welcome_message: {e}")
        return WAITING_WELCOME_MESSAGE

async def show_networks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche tous les réseaux sociaux"""
    query = update.callback_query
    await query.answer()

    with open('config/config.json', 'r') as f:
        config = json.load(f)

    keyboard = []
    
    for network in config.get('networks_buttons', []):
        if network['type'] == 'url':
            keyboard.append([InlineKeyboardButton(network['name'], url=network['value'])])
        elif network['type'] == 'text':
            keyboard.append([InlineKeyboardButton(network['name'], 
                           callback_data=f"network_text_{network['id']}")])

    keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="back_to_home")])

    await query.edit_message_text(
        "🌐 Voici nos réseaux :",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING

async def manage_reviews_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère le toggle des avis"""
    query = update.callback_query
    await query.answer()

    with open('config/config.json', 'r') as f:
        config = json.load(f)

    # Inverser l'état actuel des avis 
    config['reviews_enabled'] = not config.get('reviews_enabled', True)
    
    # Sauvegarder la configuration
    with open('config/config.json', 'w') as f:
        json.dump(config, f, indent=4)

    # Mettre à jour le menu actuel au lieu de retourner au menu admin
    reviews_status = "✅ Activé" if config['reviews_enabled'] else "❌ Désactivé"
    keyboard = [
        [InlineKeyboardButton("🏠 Modifier message d'accueil", callback_data="edit_welcome")],
        [InlineKeyboardButton("🖼️ Modifier image bannière", callback_data="edit_banner_image")],
        [InlineKeyboardButton("🛒 Modifier bouton Commander", callback_data="edit_order_button")],
        [InlineKeyboardButton("🎯 Gérer boutons personnalisés", callback_data="show_custom_buttons")],
        [InlineKeyboardButton("🌐 Gérer catégorie Réseaux", callback_data="manage_networks")],
        [InlineKeyboardButton(f"📝 Avis: {reviews_status}", callback_data="manage_reviews_config")],
        [InlineKeyboardButton("🔙 Retour", callback_data="admin")]
    ]

    await query.edit_message_text(
        "🎨 *Configuration du bot*\n\n"
        "Sélectionnez un élément à configurer :",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return CHOOSING

async def handle_normal_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestion des boutons normaux"""
    global paris_tz 
    query = update.callback_query
    await query.answer()
    await admin_features.register_user(update.effective_user)


    if query.data == "admin":
        if str(update.effective_user.id) in ADMIN_IDS:
            return await show_admin_menu(update, context)
        else:
            await query.edit_message_text("❌ Vous n'êtes pas autorisé à accéder au menu d'administration.")
            return CHOOSING

    elif query.data == "show_info_potato":
        text = (
            "🔒 <b>Ceci est un exemple de bouton avec texte</b>\n\n"
            "<code>Possible de mettre un id SESSION par exemple.</code>"
        )
        keyboard = [[InlineKeyboardButton("🔙 Retour aux réseaux", callback_data="show_networks")]]
        
        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        return CHOOSING

    elif query.data == "menu_catalog":
        if str(update.effective_user.id) not in ADMIN_IDS:
            await query.answer("❌ Accès non autorisé")
            return CHOOSING
        
        keyboard = [
            [InlineKeyboardButton("➕ Ajouter une catégorie", callback_data="add_category")],
            [InlineKeyboardButton("➕ Ajouter un produit", callback_data="add_product")],
            [InlineKeyboardButton("✏️ Modifier une catégorie", callback_data="edit_category")],
            [InlineKeyboardButton("✏️ Modifier un produit", callback_data="edit_product")],
            [InlineKeyboardButton("❌ Supprimer une catégorie", callback_data="delete_category")],
            [InlineKeyboardButton("❌ Supprimer un produit", callback_data="delete_product")],
            [InlineKeyboardButton("🔙 Retour", callback_data="admin")]
        ]
    
        await query.edit_message_text(
            "📦 *Gestion du catalogue*\n\n"
            "Que souhaitez-vous faire ?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return CHOOSING

    elif query.data == "manage_networks":
        return await manage_networks(update, context)

    elif query.data == "toggle_networks":
        return await toggle_networks(update, context)

    elif query.data == "add_network_button":
        return await add_network_button(update, context)

    elif query.data == "delete_network_button":
        return await delete_network_button(update, context)

    elif query.data == "edit_network_button":
        return await edit_network_button(update, context)

    elif query.data.startswith("del_network_"):
        network_id = query.data.replace("del_network_", "")
    
        with open('config/config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
    
        config['networks_buttons'] = [n for n in config.get('networks_buttons', []) if n['id'] != network_id]
    
        with open('config/config.json', 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
    
        await query.answer("✅ Réseau supprimé")
        return await manage_networks(update, context)

    elif query.data.startswith("edit_network_"):
        network_id = query.data.replace("edit_network_", "")
        context.user_data['editing_network_id'] = network_id
    
        with open('config/config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
    
        network = next((n for n in config.get('networks_buttons', []) if n['id'] == network_id), None)
        if network:
            keyboard = [
                [InlineKeyboardButton("✏️ Modifier le nom", callback_data=f"edit_network_name_{network_id}")],
                [InlineKeyboardButton("🔗 Modifier la valeur", callback_data=f"edit_network_value_{network_id}")],
                [InlineKeyboardButton("🔙 Retour", callback_data="edit_network_button")]
            ]
        
            await query.edit_message_text(
                f"Modification du réseau : {network['name']}\n"
                f"Type actuel : {network['type']}\n"
                f"Valeur actuelle : {network['value']}\n\n"
                "Que souhaitez-vous modifier ?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return CHOOSING

    elif query.data.startswith("edit_network_name_"):
        network_id = query.data.replace("edit_network_name_", "")
        context.user_data['editing_network_id'] = network_id
        context.user_data['is_network_button'] = True  # Indique que c'est un bouton réseau

        with open('config/config.json', 'r') as f:
            config = json.load(f)

        network = next((n for n in config.get('networks_buttons', []) if n['id'] == network_id), None)

        message = await query.edit_message_text(
            f"✏️ Modification du nom du réseau\n\n"
            f"Nom actuel : {network['name']}\n\n"
            "Envoyez le nouveau nom :",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Annuler", callback_data=f"edit_network_{network_id}")
            ]])
        )

        context.user_data['messages_to_delete'] = [message.message_id]
        return WAITING_BUTTON_NAME

    elif query.data.startswith("edit_network_value_"):
        network_id = query.data.replace("edit_network_value_", "")
        context.user_data['editing_network_id'] = network_id
        context.user_data['is_network_button'] = True  # Indique que c'est un bouton réseau

        with open('config/config.json', 'r') as f:
            config = json.load(f)

        network = next((n for n in config.get('networks_buttons', []) if n['id'] == network_id), None)

        message = await query.edit_message_text(
            f"✏️ Modification de la valeur du réseau\n\n"
            f"Valeur actuelle : {network['value']}\n\n"
            "Envoyez la nouvelle valeur :\n"
            "• Pour un lien : envoyez une URL commençant par http:// ou https://\n"
            "• Pour du texte : envoyez le texte à afficher",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Annuler", callback_data=f"edit_network_{network_id}")
            ]])
        )

        context.user_data['messages_to_delete'] = [message.message_id]
        return WAITING_BUTTON_VALUE

    elif query.data.startswith("network_text_"):
        network_id = query.data.replace("network_text_", "")
        with open('config/config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
    
        network = next((n for n in config.get('networks_buttons', []) if n['id'] == network_id), None)
        if network:
            await query.edit_message_text(
                network['value'],
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour", callback_data="show_networks")
                ]]),
                parse_mode='HTML'
            )
        return CHOOSING

    # Sous-menu Configuration
    elif query.data == "menu_config":
        if str(update.effective_user.id) not in ADMIN_IDS:
            await query.answer("❌ Accès non autorisé")
            return CHOOSING

        with open('config/config.json', 'r') as f:
            config = json.load(f)

        reviews_enabled = config.get('reviews_enabled', True)
        reviews_status = "✅ Activé" if reviews_enabled else "❌ Désactivé"

        keyboard = [
            [InlineKeyboardButton("🏠 Modifier message d'accueil", callback_data="edit_welcome")],
            [InlineKeyboardButton("🖼️ Modifier image bannière", callback_data="edit_banner_image")],
            [InlineKeyboardButton("🛒 Modifier bouton Commander", callback_data="edit_order_button")],
            [InlineKeyboardButton("🎯 Gérer boutons personnalisés", callback_data="show_custom_buttons")],
            [InlineKeyboardButton("🌐 Gérer catégorie Réseaux", callback_data="manage_networks")],
            [InlineKeyboardButton(f"📝 Avis: {reviews_status}", callback_data="manage_reviews_config")],
            [InlineKeyboardButton("🔙 Retour", callback_data="admin")]
        ]

        await query.edit_message_text(
            "🎨 *Configuration du bot*\n\n"
            "Sélectionnez un élément à configurer :",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return CHOOSING

    # Sous-menu Utilisateurs & Accès
    elif query.data == "menu_users":
        if str(update.effective_user.id) not in ADMIN_IDS:
            await query.answer("❌ Accès non autorisé")
            return CHOOSING
    
        is_enabled = access_manager.is_access_code_enabled()
        status_text = "✅ Activé" if is_enabled else "❌ Désactivé"
    
        keyboard = [
            [InlineKeyboardButton("👥 Gérer utilisateurs", callback_data="manage_users")],
            [InlineKeyboardButton(f"🔒 Code d'accès: {status_text}", callback_data="toggle_access_code")]
        ]
        
        keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="admin")])
    
        await query.edit_message_text(
            "👥 *Gestion des utilisateurs et accès*\n\n"
            f"Système de codes : {status_text}\n\n"
            "Que souhaitez-vous faire ?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return CHOOSING

    elif query.data.startswith("custom_text_"):
        button_id = query.data.replace("custom_text_", "")
        with open('config/config.json', 'r') as f:
            config = json.load(f)
        
        button = next((b for b in config.get('custom_buttons', []) if b['id'] == button_id), None)
        if button:
            await query.edit_message_text(
                button['value'],
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour", callback_data="back_to_home")
                ]]),
                parse_mode='HTML'
            )
        return CHOOSING

    elif query.data == "show_custom_buttons":
        if str(update.effective_user.id) not in ADMIN_IDS:
            await query.answer("❌ Vous n'êtes pas autorisé à accéder à cette fonction.")
            return CHOOSING

        keyboard = [
            [InlineKeyboardButton("➕ Ajouter un bouton", callback_data="add_custom_button")],
            [InlineKeyboardButton("❌ Supprimer un bouton", callback_data="list_buttons_delete")],
            [InlineKeyboardButton("✏️ Modifier un bouton", callback_data="list_buttons_edit")],
            [InlineKeyboardButton("🔙 Retour", callback_data="admin")]
        ]

        await query.edit_message_text(
            "🔧 Gestion des boutons personnalisés\n\n"
            "Choisissez une action :",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'  # Ajout du parse_mode
        )
        return CHOOSING

    elif query.data == "add_custom_button":
        if str(update.effective_user.id) not in ADMIN_IDS:
            await query.answer("Vous n'êtes pas autorisé à accéder à cette fonction.")
            return CHOOSING

        await query.edit_message_text(
            "Ajout d'un nouveau bouton\n\n"
            "Envoyez le nom du bouton (exemple: 'Mon Bouton') :",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Retour", callback_data="show_custom_buttons")
            ]])
        )
        return WAITING_BUTTON_NAME

    elif query.data == "list_buttons_delete":
        if str(update.effective_user.id) not in ADMIN_IDS:
            await query.answer("Vous n'êtes pas autorisé à accéder à cette fonction.")
            return CHOOSING
        
        with open('config/config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        buttons = config.get('custom_buttons', [])
        if not buttons:
            await query.edit_message_text(
                "Aucun bouton personnalise n'existe.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Retour", callback_data="show_custom_buttons")
                ]])
            )
            return CHOOSING
        
        keyboard = []
        for button in buttons:
            keyboard.append([InlineKeyboardButton(
                f"Supprimer {button['name']}", 
                callback_data=f"delete_button_{button['id']}"
            )])
        
        keyboard.append([InlineKeyboardButton("Retour", callback_data="show_custom_buttons")])
        
        await query.edit_message_text(
            "Selectionnez le bouton a supprimer :",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CHOOSING

    elif query.data.startswith("confirm_delete_broadcast_"):
        return await admin_features.delete_broadcast(update, context)

    elif query.data.startswith("delete_button_"):
        if str(update.effective_user.id) not in ADMIN_IDS:
            await query.answer("❌ Vous n'êtes pas autorisé à accéder à cette fonction.")
            return CHOOSING
        
        button_id = query.data.replace("delete_button_", "")
        
        with open('config/config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        config['custom_buttons'] = [b for b in config.get('custom_buttons', []) if b['id'] != button_id]
        
        with open('config/config.json', 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
        
        await query.edit_message_text(
            "✅ Bouton supprimé avec succès !",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Retour", callback_data="show_custom_buttons")
            ]])
        )
        return CHOOSING

    elif query.data == "list_buttons_edit":
        if str(update.effective_user.id) not in ADMIN_IDS:
            await query.answer("❌ Vous n'êtes pas autorisé à accéder à cette fonction.")
            return CHOOSING
        
        with open('config/config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        buttons = config.get('custom_buttons', [])
        if not buttons:
            await query.edit_message_text(
                "Aucun bouton personnalisé n'existe.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour", callback_data="show_custom_buttons")
                ]])
            )
            return CHOOSING
        
        keyboard = []
        for button in buttons:
            keyboard.append([InlineKeyboardButton(
                f"✏️ {button['name']}", 
                callback_data=f"edit_button_{button['id']}"
            )])
        
        keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="show_custom_buttons")])
        
        await query.edit_message_text(
            "Sélectionnez le bouton à modifier :",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CHOOSING

    elif query.data.startswith("edit_button_"):
        if str(update.effective_user.id) not in ADMIN_IDS:
            await query.answer("❌ Vous n'êtes pas autorisé à accéder à cette fonction.")
            return CHOOSING
        
        button_id = query.data.replace("edit_button_", "")
        context.user_data['editing_button_id'] = button_id
        
        with open('config/config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        button = next((b for b in config.get('custom_buttons', []) if b['id'] == button_id), None)
        if button:
            keyboard = [
                [InlineKeyboardButton("✏️ Modifier le nom", callback_data=f"edit_button_name_{button_id}")],
                [InlineKeyboardButton("🔗 Modifier la valeur", callback_data=f"edit_button_value_{button_id}")],
                [InlineKeyboardButton("🔙 Retour", callback_data="list_buttons_edit")]
            ]
            
            await query.edit_message_text(
                f"Modification du bouton : {button['name']}\n"
                f"Type actuel : {button['type']}\n"
                f"Valeur actuelle : {button['value']}\n\n"
                "Que souhaitez-vous modifier ?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return CHOOSING

    elif query.data.startswith("edit_button_name_"):
        button_id = query.data.replace("edit_button_name_", "")
        context.user_data['editing_button_id'] = button_id
        context.user_data['editing_button_field'] = 'name'
        
        await query.edit_message_text(
            "✏️ Envoyez le nouveau nom du bouton :",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Annuler", callback_data=f"edit_button_{button_id}")
            ]])
        )
        return WAITING_BUTTON_NAME

    elif query.data.startswith("edit_button_value_"):
        button_id = query.data.replace("edit_button_value_", "")
        context.user_data['editing_button_id'] = button_id
        context.user_data['editing_button_field'] = 'value'
        
        await query.edit_message_text(
            "✏️ Envoyez la nouvelle valeur du bouton :\n\n"
            "• Pour un bouton URL : envoyez un lien commençant par http:// ou https://\n"
            "• Pour un bouton texte : envoyez le texte à afficher",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Annuler", callback_data=f"edit_button_{button_id}")
            ]])
        )
        return WAITING_BUTTON_VALUE



    elif query.data == "edit_banner_image":
            msg = await query.message.edit_text(
                "📸 Veuillez envoyer la nouvelle image bannière :",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Annuler", callback_data="cancel_edit")
                ]])
            )
            context.user_data['banner_msg'] = msg
            return WAITING_BANNER_IMAGE

    elif query.data == "manage_users":
        return await admin_features.handle_user_management(update, context)

    elif query.data == "start_broadcast":
        return await admin_features.handle_broadcast(update, context)

    elif query.data == "add_category":
        await query.message.edit_text(
            "📝 Veuillez entrer le nom de la nouvelle catégorie:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Annuler", callback_data="cancel_add_category")
            ]])
        )
        return WAITING_CATEGORY_NAME

    elif query.data == "add_product":
        keyboard = []
        for category in CATALOG.keys():
            if category != 'stats':
                keyboard.append([InlineKeyboardButton(category, callback_data=f"select_category_{category}")])
        keyboard.append([InlineKeyboardButton("🔙 Annuler", callback_data="cancel_add_product")])
        
        await query.message.edit_text(
            "📝 Sélectionnez la catégorie pour le nouveau produit:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return SELECTING_CATEGORY

    elif query.data.startswith("select_category_"):
        # Ne traiter que si ce n'est PAS une action de suppression
        if not query.data.startswith("select_category_to_delete_"):
            category = query.data.replace("select_category_", "")
            context.user_data['temp_product_category'] = category
            
            await query.message.edit_text(
                "📝 Veuillez entrer le nom du nouveau produit:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Annuler", callback_data="cancel_add_product")
                ]])
            )
            return WAITING_PRODUCT_NAME

    elif query.data == "delete_product":
        try:
            keyboard = []
            for category in CATALOG.keys():
                if category != 'stats':
                    # Créer un callback_data sécurisé pour chaque catégorie
                    safe_callback = create_safe_callback_data(
                        "del_prod_cat",
                        category
                    )
                    # Stocker la correspondance
                    store_callback_mapping(safe_callback, category)
                    
                    keyboard.append([
                        InlineKeyboardButton(
                            category[:50],  # Limite l'affichage à 50 caractères
                            callback_data=safe_callback
                        )
                    ])
            keyboard.append([
                InlineKeyboardButton("🔙 Annuler", callback_data="cancel_delete_product")
            ])
            
            await query.message.edit_text(
                "⚠️ Sélectionnez la catégorie du produit à supprimer:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return SELECTING_CATEGORY_TO_DELETE
        except Exception as e:
            print(f"Erreur dans delete_product: {e}")
            await query.message.edit_text(
                "Une erreur s'est produite. Veuillez réessayer.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour au menu", callback_data="admin")
                ]])
            )
            return CHOOSING

    elif query.data.startswith("del_prod_cat_"):
        try:
            # Récupérer la catégorie originale
            category = get_original_data(query.data)
            if not category:
                raise ValueError("Catégorie non trouvée")
                
            products = CATALOG.get(category, [])
            keyboard = []
            
            for product in products:
                if isinstance(product, dict):
                    # Créer un callback_data sécurisé pour chaque produit
                    product_data = f"{category}|||{product['name']}"
                    safe_callback = create_safe_callback_data(
                        "confirm_del_prod",
                        product_data
                    )
                    # Stocker la correspondance
                    store_callback_mapping(safe_callback, product_data)
                    
                    keyboard.append([
                        InlineKeyboardButton(
                            product['name'][:50],  # Limite l'affichage à 50 caractères
                            callback_data=safe_callback
                        )
                    ])
                    
            keyboard.append([
                InlineKeyboardButton("🔙 Annuler", callback_data="cancel_delete_product")
            ])

            await query.message.edit_text(
                f"⚠️ Sélectionnez le produit à supprimer de *{category}* :",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return SELECTING_PRODUCT_TO_DELETE
        except Exception as e:
            print(f"Erreur dans delete_product_category: {e}")
            await query.message.edit_text(
                "Une erreur s'est produite. Veuillez réessayer.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour au menu", callback_data="admin")
                ]])
            )
            return CHOOSING

    elif query.data.startswith("confirm_del_prod_"):
        try:
            # Récupérer les données originales
            data = get_original_data(query.data)
            if not data:
                raise ValueError("Données non trouvées")
                
            category, product_name = data.split("|||")
            
            # Créer un nouveau callback sécurisé pour la confirmation finale
            safe_callback = create_safe_callback_data(
                "really_del_prod",
                f"{category}|||{product_name}"
            )
            store_callback_mapping(safe_callback, data)
            
            keyboard = [[
                InlineKeyboardButton(
                    "✅ Oui, supprimer",
                    callback_data=safe_callback
                ),
                InlineKeyboardButton(
                    "❌ Non, annuler",
                    callback_data="cancel_delete_product"
                )
            ]]
            
            await query.message.edit_text(
                f"⚠️ *Êtes-vous sûr de vouloir supprimer le produit* `{product_name}` *?*\n\n"
                f"Cette action est irréversible !",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return SELECTING_PRODUCT_TO_DELETE
        except Exception as e:
            print(f"Erreur lors de la confirmation de suppression: {e}")
            await query.message.edit_text(
                "Une erreur s'est produite. Veuillez réessayer.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour au menu", callback_data="admin")
                ]])
            )
            return CHOOSING

    elif query.data.startswith("really_del_prod_"):
        try:
            # Récupérer les données originales
            data = get_original_data(query.data)
            if not data:
                raise ValueError("Données non trouvées")
                
            category, product_name = data.split("|||")
            
            if category in CATALOG:
                CATALOG[category] = [p for p in CATALOG[category] if p['name'] != product_name]
                save_catalog(CATALOG)
                
                # Nettoyer le mapping
                CALLBACK_DATA_MAPPING.pop(query.data, None)
                
                await query.message.edit_text(
                    f"✅ Le produit *{product_name}* a été supprimé avec succès !",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Retour au menu", callback_data="admin")
                    ]])
                )
            else:
                raise ValueError("Catégorie non trouvée")
                
            return CHOOSING
        except Exception as e:
            print(f"Erreur lors de la suppression du produit: {e}")
            await query.message.edit_text(
                "Une erreur s'est produite lors de la suppression. Veuillez réessayer.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour au menu", callback_data="admin")
                ]])
            )
            return CHOOSING

    elif query.data == "delete_category":
        try:
            keyboard = []
            for category in CATALOG.keys():
                if category != 'stats':
                    # Créer un callback_data sécurisé pour chaque catégorie
                    safe_callback = create_safe_callback_data(
                        "del_cat",
                        category
                    )
                    # Stocker la correspondance
                    store_callback_mapping(safe_callback, category)
                    
                    keyboard.append([
                        InlineKeyboardButton(
                            category[:50],  # Limite l'affichage à 50 caractères
                            callback_data=safe_callback
                        )
                    ])
            
            keyboard.append([
                InlineKeyboardButton("🔙 Annuler", callback_data="cancel_delete_category")
            ])
            
            await query.message.edit_text(
                "⚠️ Sélectionnez la catégorie à supprimer:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return SELECTING_CATEGORY_TO_DELETE
            
        except Exception as e:
            print(f"Erreur dans delete_category: {e}")
            await query.message.edit_text(
                "Une erreur s'est produite. Veuillez réessayer.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour au menu", callback_data="admin")
                ]])
            )
            return CHOOSING

    elif query.data.startswith("del_cat_"):
        try:
            # Récupérer la catégorie originale à partir du mapping
            original_category = get_original_data(query.data)
            if not original_category:
                raise ValueError("Catégorie non trouvée")
                
            # Créer un nouveau callback sécurisé pour la confirmation
            confirm_callback = create_safe_callback_data(
                "confirm_del",
                original_category
            )
            store_callback_mapping(confirm_callback, original_category)
            
            keyboard = [[
                InlineKeyboardButton(
                    "✅ Oui, supprimer",
                    callback_data=confirm_callback
                ),
                InlineKeyboardButton(
                    "❌ Non, annuler",
                    callback_data="cancel_delete_category"
                )
            ]]
            
            await query.message.edit_text(
                f"⚠️ *Êtes-vous sûr de vouloir supprimer la catégorie* `{original_category}` *?*\n\n"
                f"Cette action supprimera également tous les produits de cette catégorie.\n"
                f"Cette action est irréversible !",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return SELECTING_CATEGORY_TO_DELETE
            
        except Exception as e:
            print(f"Erreur dans la confirmation de suppression: {e}")
            await query.message.edit_text(
                "Une erreur s'est produite. Veuillez réessayer.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour au menu", callback_data="admin")
                ]])
            )
            return CHOOSING

    elif query.data.startswith("confirm_del_"):
        try:
            # Récupérer la catégorie originale
            original_category = get_original_data(query.data)
            if not original_category or original_category not in CATALOG:
                raise ValueError("Catégorie invalide ou non trouvée")
                
            # Supprimer la catégorie
            del CATALOG[original_category]
            save_catalog(CATALOG)
            
            # Nettoyer le mapping
            CALLBACK_DATA_MAPPING.pop(query.data, None)
            
            await query.message.edit_text(
                f"✅ La catégorie a été supprimée avec succès !",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour au menu", callback_data="admin")
                ]])
            )
            return CHOOSING
            
        except Exception as e:
            print(f"Erreur lors de la suppression: {e}")
            await query.message.edit_text(
                "Une erreur s'est produite lors de la suppression. Veuillez réessayer.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour au menu", callback_data="admin")
                ]])
            )
            return CHOOSING


        try:
            encoded_data = query.data.replace("really_delete_product_", "")
            category, product_name = decode_callback_data(encoded_data)
        
            if category and product_name and category in CATALOG:
                CATALOG[category] = [p for p in CATALOG[category] if p['name'] != product_name]
                save_catalog(CATALOG)
                await query.message.edit_text(
                    f"✅ Le produit *{html.escape(product_name)}* a été supprimé avec succès !",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Retour au menu", callback_data="admin")
                    ]])
                )
            return CHOOSING
        except Exception as e:
            print(f"Erreur lors de la suppression du produit: {e}")
            return await show_admin_menu(update, context)

    elif query.data == "edit_category":
        if str(query.from_user.id) in ADMIN_IDS:
            keyboard = []
            for category in CATALOG.keys():
                if category != 'stats':  # <- AJOUTEZ CETTE LIGNE
                    keyboard.append([InlineKeyboardButton(
                        f"{category} {'(SOLD OUT ❌)' if not CATALOG[category] else ''}",
                        callback_data=f"edit_cat_{category}"
                    )])
            keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="admin")])
            await query.message.edit_text(
                "Choisissez une catégorie à modifier:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return CHOOSING

    elif query.data.startswith("edit_cat_"):
        if str(query.from_user.id) in ADMIN_IDS:
            if query.data.startswith("edit_cat_name_"):
                # Gestion de la modification du nom
                category = query.data.replace("edit_cat_name_", "")
                context.user_data['category_to_edit'] = category
                await query.message.edit_text(
                    f"📝 *Modification du nom de catégorie*\n\n"
                    f"Catégorie actuelle : *{category}*\n\n"
                    f"✍️ Envoyez le nouveau nom pour cette catégorie :",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Retour", callback_data=f"edit_cat_{category}")
                    ]]),
                    parse_mode='Markdown'
                )
                return WAITING_NEW_CATEGORY_NAME
            else:
                # Menu d'édition de catégorie
                category = query.data.replace("edit_cat_", "")
                keyboard = [
                    [InlineKeyboardButton("✏️ Modifier le nom", callback_data=f"edit_cat_name_{category}")],
                    [InlineKeyboardButton("➕ Ajouter SOLD OUT", callback_data=f"add_soldout_{category}")],
                    [InlineKeyboardButton("🔙 Retour", callback_data="edit_category")]
                ]
                await query.message.edit_text(
                    f"Que voulez-vous modifier pour la catégorie *{category}* ?",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                return CHOOSING

    elif query.data.startswith("edit_cat_name_"):
        if str(query.from_user.id) in ADMIN_IDS:
            category = query.data.replace("edit_cat_name_", "")
            context.user_data['category_to_edit'] = category
            await query.message.edit_text(
                f"📝 *Modification du nom de catégorie*\n\n"
                f"Catégorie actuelle : *{category}*\n\n"
                f"✍️ Envoyez le nouveau nom pour cette catégorie :",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Retour", callback_data=f"edit_cat_{category}")
                ]]),
                parse_mode='Markdown'
            )
            return WAITING_NEW_CATEGORY_NAME

    elif query.data.startswith("add_soldout_"):
        if str(query.from_user.id) in ADMIN_IDS:
            category = query.data.replace("add_soldout_", "")
            # Demander confirmation avant d'ajouter SOLD OUT
            keyboard = [
                [
                    InlineKeyboardButton("✅ Oui, mettre en SOLD OUT", callback_data=f"confirm_soldout_{category}"),
                    InlineKeyboardButton("❌ Non, annuler", callback_data=f"edit_cat_{category}")
                ]
            ]
            await query.message.edit_text(
                f"⚠️ *Attention!*\n\n"
                f"Vous êtes sur le point de mettre la catégorie *{category}* en SOLD OUT.\n\n"
                f"❗ *Cela supprimera tous les produits existants* dans cette catégorie.\n\n"
                f"Êtes-vous sûr de vouloir continuer?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return EDITING_CATEGORY

    elif query.data.startswith("confirm_soldout_"):
        if str(query.from_user.id) in ADMIN_IDS:
            category = query.data.replace("confirm_soldout_", "")
            # Vider la catégorie et ajouter le produit SOLD OUT
            CATALOG[category] = [{
                'name': 'SOLD OUT ! ❌',
                'price': 'Non disponible',
                'description': 'Cette catégorie est temporairement en rupture de stock.',
                'media': []
            }]
            save_catalog(CATALOG)
            await query.answer("✅ SOLD OUT ajouté avec succès!")
                
            # Retourner au menu d'édition des catégories
            keyboard = []
            for cat in CATALOG.keys():
                keyboard.append([InlineKeyboardButton(
                    f"{cat} {'(SOLD OUT ❌)' if not CATALOG[cat] or (len(CATALOG[cat]) == 1 and CATALOG[cat][0].get('name') == 'SOLD OUT ! ❌') else ''}",
                    callback_data=f"edit_cat_{cat}"
                )])
            keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="admin")])
            await query.message.edit_text(
                "Choisissez une catégorie à modifier:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return EDITING_CATEGORY

    elif query.data == "toggle_access_code":
            if str(update.effective_user.id) not in ADMIN_IDS:
                await query.answer("❌ Vous n'êtes pas autorisé à modifier ce paramètre.")
                return CHOOSING
            
            is_enabled = access_manager.toggle_access_code()
            status = "activé ✅" if is_enabled else "désactivé ❌"
        
            # Afficher un message temporaire
            await query.answer(f"Le système de code d'accès a été {status}")
        
            # Rafraîchir le menu admin
            return await show_admin_menu(update, context)

    elif query.data == "edit_order_button":
            # Gérer l'affichage des configurations actuelles
            if CONFIG.get('order_url'):
                current_config = CONFIG['order_url']
                config_type = "URL"
            elif CONFIG.get('order_text'):
                current_config = CONFIG['order_text']
                config_type = "Texte"
            else:
                current_config = 'Non configuré'
                config_type = "Aucune"

            message = await query.message.edit_text(
                "🛒 Configuration du bouton Commander 🛒\n\n"
                f"<b>Configuration actuelle</b> ({config_type}):\n"
                f"{current_config}\n\n"
                "Vous pouvez :\n"
                "• Envoyer un pseudo Telegram (avec ou sans @)\n\n"
                "• Envoyer un message avec formatage HTML (<b>gras</b>, <i>italique</i>, etc)\n\n"
                "• Envoyer une URL (commençant par http:// ou https://) pour rediriger vers un site",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Annuler", callback_data="cancel_edit_order")
                ]]),
                parse_mode='HTML'  # Ajout du support HTML
            )
            context.user_data['edit_order_button_message_id'] = message.message_id
            return WAITING_ORDER_BUTTON_CONFIG

    elif query.data == "show_order_text":
        try:
            # Récupérer le message de commande configuré
            order_text = CONFIG.get('order_text', "Aucun message configuré")
        
            # Extraire la catégorie du message précédent
            category = None
            for markup_row in query.message.reply_markup.inline_keyboard:
                for button in markup_row:
                    if button.callback_data and button.callback_data.startswith("view_"):
                        category = button.callback_data.replace("view_", "")
                        break
                if category:
                    break
        
            keyboard = [[
                InlineKeyboardButton("🔙 Retour aux produits", callback_data=f"view_{category}")
            ]]
        
            # Modifier le message existant au lieu d'en créer un nouveau
            # Utiliser parse_mode='HTML' au lieu de 'Markdown'
            await query.message.edit_text(
                text=order_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
            return CHOOSING
        
        except Exception as e:
            print(f"Erreur lors de l'affichage du message: {e}")
            await query.answer("Une erreur est survenue lors de l'affichage du message", show_alert=True)
            return CHOOSING


    elif query.data == "edit_welcome":
            current_message = CONFIG.get('welcome_message', "Message non configuré")
        
            message = await query.message.edit_text(
                "✏️ Configuration du message d'accueil\n\n"
                f"Message actuel :\n{current_message}\n\n"
                "Envoyez le nouveau message d'accueil.\n"
                "Vous pouvez utiliser le formatage HTML :\n"
                "• <b>texte</b> pour le gras\n"
                "• <i>texte</i> pour l'italique\n"
                "• <u>texte</u> pour le souligné",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Annuler", callback_data="cancel_edit_welcome")
                ]]),
                parse_mode='HTML'
            )
            context.user_data['edit_welcome_message_id'] = message.message_id
            return WAITING_WELCOME_MESSAGE

    elif query.data == "show_stats":
        # Configuration du fuseau horaire Paris
        paris_tz = pytz.timezone('Europe/Paris')
        utc_now = datetime.utcnow()
        paris_now = utc_now.replace(tzinfo=pytz.UTC).astimezone(paris_tz)

        # Initialisation des stats si nécessaire
        if 'stats' not in CATALOG:
            CATALOG['stats'] = {
                "total_views": 0,
                "category_views": {},
                "product_views": {},
                "last_updated": paris_now.strftime("%H:%M:%S"),
                "last_reset": paris_now.strftime("%Y-%m-%d")
            }
    
        # Nettoyer les stats avant l'affichage
        clean_stats()
    
        stats = CATALOG['stats']
        text = "📊 *Statistiques du catalogue*\n\n"
        text += f"👥 Vues totales: {stats.get('total_views', 0)}\n"
    
        # Conversion de l'heure en fuseau horaire Paris
        last_updated = stats.get('last_updated', 'Jamais')
        if last_updated != 'Jamais':
            try:
                if len(last_updated) > 8:  # Si format complet
                    dt = datetime.strptime(last_updated, "%Y-%m-%d %H:%M:%S")
                else:  # Si format HH:MM:SS
                    today = paris_now.strftime("%Y-%m-%d")
                    dt = datetime.strptime(f"{today} {last_updated}", "%Y-%m-%d %H:%M:%S")
            
                # Convertir en timezone Paris
                dt = dt.replace(tzinfo=pytz.UTC).astimezone(paris_tz)
                last_updated = dt.strftime("%H:%M:%S")
            except Exception as e:
                print(f"Erreur conversion heure: {e}")
            
        text += f"🕒 Dernière mise à jour: {last_updated}\n"
    
        if 'last_reset' in stats:
            text += f"🔄 Dernière réinitialisation: {stats.get('last_reset', 'Jamais')}\n"
        text += "\n"
    
        # Le reste du code reste identique
        text += "📈 *Vues par catégorie:*\n"
        category_views = stats.get('category_views', {})
        if category_views:
            sorted_categories = sorted(category_views.items(), key=lambda x: x[1], reverse=True)
            for category, views in sorted_categories:
                if category in CATALOG:
                    text += f"- {category}: {views} vues\n"
        else:
            text += "Aucune vue enregistrée.\n"

        text += "\n━━━━━━━━━━━━━━━\n\n"
    
        text += "🔥 *Produits les plus populaires:*\n"
        product_views = stats.get('product_views', {})
        if product_views:
            all_products = []
            for category, products in product_views.items():
                if category in CATALOG:
                    existing_products = [p['name'] for p in CATALOG[category]]
                    for product_name, views in products.items():
                        if product_name in existing_products:
                            all_products.append((category, product_name, views))
        
            sorted_products = sorted(all_products, key=lambda x: x[2], reverse=True)[:5]
            for category, product_name, views in sorted_products:
                text += f"- {product_name} ({category}): {views} vues\n"
        else:
            text += "Aucune vue enregistrée sur les produits.\n"
    
        keyboard = [
            [InlineKeyboardButton("🔄 Réinitialiser les statistiques", callback_data="confirm_reset_stats")],
            [InlineKeyboardButton("🔙 Retour", callback_data="admin")]
        ]
    
        await query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    elif query.data == "edit_contact":
            # Gérer l'affichage de la configuration actuelle
            if CONFIG.get('contact_username'):
                current_config = f"@{CONFIG['contact_username']}"
                config_type = "Pseudo Telegram"
            elif CONFIG.get('contact_url'):  # Ajout d'une nouvelle option pour l'URL
                current_config = CONFIG['contact_url']
                config_type = "URL"
            else:
                current_config = 'Non configuré'
                config_type = "Aucune"

            message = await query.message.edit_text(
                "📱 Configuration du contact\n\n"
                f"Configuration actuelle ({config_type}):\n"
                f"{current_config}\n\n"
                "Vous pouvez :\n"
                "• Envoyer un pseudo Telegram (avec ou sans @)\n"
                "• Envoyer une URL (commençant par http:// ou https://) pour rediriger vers un site",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Annuler", callback_data="cancel_edit_contact")
                ]]),
                parse_mode='HTML'
            )
            context.user_data['edit_contact_message_id'] = query.message.message_id
            return WAITING_CONTACT_USERNAME

    elif query.data in ["cancel_add_category", "cancel_add_product", "cancel_delete_category", 
                        "cancel_delete_product", "cancel_edit_contact", "cancel_edit_order", "cancel_edit_welcome"]:
        return await show_admin_menu(update, context)

    elif query.data == "back_to_categories":
        if 'category_message_id' in context.user_data:
            try:
                await context.bot.edit_message_text(
                    chat_id=query.message.chat_id,
                    message_id=context.user_data['category_message_id'],
                    text=context.user_data['category_message_text'],
                    reply_markup=InlineKeyboardMarkup(context.user_data['category_message_reply_markup']),
                    parse_mode='Markdown'
                )
            except Exception as e:
                print(f"Erreur lors de la mise à jour du message des catégories: {e}")
        else:
            # Si le message n'existe pas, recréez-le
            keyboard = []
            for category in CATALOG.keys():
                if category != 'stats':
                    keyboard.append([InlineKeyboardButton(category, callback_data=f"view_{category}")])

            keyboard.append([InlineKeyboardButton("🔙 Retour à l'accueil", callback_data="back_to_home")])

            await query.edit_message_text(
                "📋 *Menu*\n\n"
                "Choisissez une catégorie pour voir les produits :",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )

    elif query.data == "skip_media":
        category = context.user_data.get('temp_product_category')
        if category:
            new_product = {
                'name': context.user_data.get('temp_product_name'),
                'price': context.user_data.get('temp_product_price'),
                'description': context.user_data.get('temp_product_description')
            }
            
            if category not in CATALOG:
                CATALOG[category] = []
            CATALOG[category].append(new_product)
            save_catalog(CATALOG)
            
            context.user_data.clear()
            return await show_admin_menu(update, context)
            
    elif query.data.startswith("product_"):
        try:
            _, nav_id = query.data.split("_", 1)
            print(f"nav_id reçu: {nav_id}")
            product_info = context.user_data.get(f'nav_product_{nav_id}')
            print(f"product_info trouvé: {product_info}")

            if not product_info:
                await query.answer("Produit non trouvé")
                return

            category = product_info['category']
            product_name = product_info['name']
            print(f"Catégorie: {category}, Nom du produit: {product_name}")

            prev_product, next_product = get_sibling_products(category, product_name, query.from_user.id)
            print(f"Produit précédent: {prev_product['name'] if prev_product else None}")
            print(f"Produit suivant: {next_product['name'] if next_product else None}")

            product = next((p for p in CATALOG[category] if p['name'] == product_name), None)

            if product:
                caption = f"📱 <b>{get_promo_badge(product)}{product['name']}</b>\n\n"
                caption += f"💰 <b>Prix:</b>\n{get_product_display_price(product)}\n\n"
                caption += f"📝 <b>Description:</b>\n{product['description']}"

                keyboard = []
        
                # Navigation des médias (en premier)
                if 'media' in product and product['media']:
                    media_list = product['media']
                    media_list = sorted(media_list, key=lambda x: x.get('order_index', 0))
                    total_media = len(media_list)
                    context.user_data['current_media_index'] = 0
                    current_media = media_list[0]

                    if total_media > 1:
                        keyboard.append([
                            InlineKeyboardButton("⬅️ Vidéo précédente", callback_data=f"prev_{nav_id}"),
                            InlineKeyboardButton("Vidéo suivante ➡️", callback_data=f"next_{nav_id}")
                        ])

                # Navigation entre produits (en deuxième)
                if prev_product or next_product:
                    product_nav = []
                    if prev_product:
                        new_nav_id = str(abs(hash(prev_product['name'])) % 10000)
                        context.user_data[f'nav_product_{new_nav_id}'] = {
                            'category': category,
                            'name': prev_product['name']
                        }
                        product_nav.append(InlineKeyboardButton("◀️ Produit précédent", callback_data=f"product_{new_nav_id}"))
                    if next_product:
                        new_nav_id = str(abs(hash(next_product['name'])) % 10000)
                        context.user_data[f'nav_product_{new_nav_id}'] = {
                            'category': category,
                            'name': next_product['name']
                        }
                        product_nav.append(InlineKeyboardButton("Produit suivant ▶️", callback_data=f"product_{new_nav_id}"))
                    keyboard.append(product_nav)

                # Boutons Commander et Retour
                keyboard.append([
                    InlineKeyboardButton(
                        "🛒 Commander",
                        **({'url': CONFIG['order_url']} if CONFIG.get('order_url') 
                           else {'callback_data': "show_order_text"})
                    )
                ])
                keyboard.append([
                    InlineKeyboardButton("🔙 Retour à la catégorie", callback_data=f"view_{category}")
                ])

                # Gestion de l'affichage
                if 'media' in product and product['media']:
                    # Pour les produits avec média, on doit supprimer et recréer
                    try:
                        await query.message.delete()
                    except Exception as e:
                        print(f"Erreur lors de la suppression du message: {e}")

                    try:
                        if current_media['media_type'] == 'photo':
                            message = await context.bot.send_photo(
                                chat_id=query.message.chat_id,
                                photo=current_media['media_id'],
                                caption=caption,
                                reply_markup=InlineKeyboardMarkup(keyboard),
                                parse_mode='HTML'
                            )
                        else:  # video
                            message = await context.bot.send_video(
                                chat_id=query.message.chat_id,
                                video=current_media['media_id'],
                                caption=caption,
                                reply_markup=InlineKeyboardMarkup(keyboard),
                                parse_mode='HTML'
                            )
                        context.user_data['last_product_message_id'] = message.message_id
                    except Exception as e:
                        print(f"Erreur lors de l'envoi du média: {e}")
                        message = await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=f"{caption}\n\n⚠️ Le média n'a pas pu être chargé",
                            reply_markup=InlineKeyboardMarkup(keyboard),
                            parse_mode='HTML'
                        )
                        context.user_data['last_product_message_id'] = message.message_id
                else:
                    # Pour les produits sans média, on essaie d'abord d'éditer
                    try:
                        # Si on vient d'un produit sans média, on peut éditer
                        await query.message.edit_text(
                            text=caption,
                            reply_markup=InlineKeyboardMarkup(keyboard),
                            parse_mode='HTML'
                        )
                    except Exception as e:
                        print(f"Erreur lors de l'édition du message: {e}")
                        # Si l'édition échoue (probablement parce qu'on vient d'un produit avec média)
                        # On supprime l'ancien message s'il existe
                        try:
                            await query.message.delete()
                        except Exception as e:
                            print(f"Erreur lors de la suppression de l'ancien message: {e}")
                        
                        # Et on crée un nouveau message
                        message = await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=caption,
                            reply_markup=InlineKeyboardMarkup(keyboard),
                            parse_mode='HTML'
                        )
                        context.user_data['last_product_message_id'] = message.message_id

                await query.answer()

                # Incrémenter les stats
                if 'stats' not in CATALOG:
                    CATALOG['stats'] = {
                        "total_views": 0,
                        "category_views": {},
                        "product_views": {},
                        "last_updated": datetime.now(paris_tz).strftime("%H:%M:%S")
                    }

                if 'product_views' not in CATALOG['stats']:
                    CATALOG['stats']['product_views'] = {}
                if category not in CATALOG['stats']['product_views']:
                    CATALOG['stats']['product_views'][category] = {}
                if product['name'] not in CATALOG['stats']['product_views'][category]:
                    CATALOG['stats']['product_views'][category][product['name']] = 0

                CATALOG['stats']['product_views'][category][product['name']] += 1
                CATALOG['stats']['total_views'] += 1
                CATALOG['stats']['last_updated'] = datetime.now(paris_tz).strftime("%H:%M:%S")
                save_catalog(CATALOG)

        except Exception as e:
            print(f"Erreur lors de l'affichage du produit: {e}")
            await query.answer("Une erreur est survenue")
            
    elif query.data.startswith("view_"):
        category = query.data.replace("view_", "")
        if category in CATALOG:
            # Initialisation des stats si nécessaire
            if 'stats' not in CATALOG:
                CATALOG['stats'] = {
                    "total_views": 0,
                    "category_views": {},
                    "product_views": {},
                    "last_updated": datetime.now(paris_tz).strftime("%H:%M:%S")
                }

            if 'category_views' not in CATALOG['stats']:
                CATALOG['stats']['category_views'] = {}

            if category not in CATALOG['stats']['category_views']:
                CATALOG['stats']['category_views'][category] = 0

            # Mettre à jour les statistiques
            CATALOG['stats']['category_views'][category] += 1
            CATALOG['stats']['total_views'] += 1
            CATALOG['stats']['last_updated'] = datetime.now(paris_tz).strftime("%H:%M:%S")
            save_catalog(CATALOG)

            products = CATALOG[category]
            # Afficher la liste des produits
            text = f"*{category}*\n\n"
            keyboard = []
            for product in products:
                nav_id = str(random.randint(1000, 9999))
                context.user_data[f'nav_product_{nav_id}'] = {
                    'category': category,
                    'name': product['name']
                }
                # NOUVEAU: Ajouter le badge promo au nom du bouton
                product_display_name = f"{get_promo_badge(product)}{product['name']}"
                keyboard.append([InlineKeyboardButton(
                    product_display_name,
                    callback_data=f"product_{nav_id}"
                )])

            keyboard.append([InlineKeyboardButton("🔙 Retour au menu", callback_data="show_categories")])

            try:
                # Suppression du dernier message de produit (photo ou vidéo) si existe
                if 'last_product_message_id' in context.user_data:
                    try:
                        await context.bot.delete_message(
                            chat_id=query.message.chat_id,
                            message_id=context.user_data['last_product_message_id']
                        )
                        del context.user_data['last_product_message_id']
                    except:
                        pass

                print(f"Texte du message : {text}")
                print(f"Clavier : {keyboard}")

                # Éditer le message existant au lieu de le supprimer et recréer
                await query.message.edit_text(
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
        
                context.user_data['category_message_id'] = query.message.message_id
                context.user_data['category_message_text'] = text
                context.user_data['category_message_reply_markup'] = keyboard

            except Exception as e:
                print(f"Erreur lors de la mise à jour du message des produits: {e}")
                # Si l'édition échoue, on crée un nouveau message
                message = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                context.user_data['category_message_id'] = message.message_id

            # Mettre à jour les stats des produits seulement s'il y en a
            if products:
                if 'stats' not in CATALOG:
                    CATALOG['stats'] = {
                        "total_views": 0,
                        "category_views": {},
                        "product_views": {},
                        "last_updated": datetime.now(paris_tz).strftime("%H:%M:%S"),
                        "last_reset": datetime.now(paris_tz).strftime("%Y-%m-%d")
                    }

                if 'product_views' not in CATALOG['stats']:
                    CATALOG['stats']['product_views'] = {}
                if category not in CATALOG['stats']['product_views']:
                    CATALOG['stats']['product_views'][category] = {}

                # Mettre à jour les stats pour chaque produit dans la catégorie
                for product in products:
                    if product['name'] not in CATALOG['stats']['product_views'][category]:
                        CATALOG['stats']['product_views'][category][product['name']] = 0
                    CATALOG['stats']['product_views'][category][product['name']] += 1

                save_catalog(CATALOG)
                
    elif query.data.startswith(("next_", "prev_")):
        try:
            direction, nav_id = query.data.split("_")
            # Récupérer les informations du produit
            product_info = context.user_data.get(f'nav_product_{nav_id}')
            if not product_info:
                await query.answer("Navigation expirée")
                return
        
            category = product_info['category']
            product_name = product_info['name']
        
            # Récupérer le produit
            product = next((p for p in CATALOG[category] if p['name'] == product_name), None)

            if product and 'media' in product:
                media_list = sorted(product['media'], key=lambda x: x.get('order_index', 0))
                total_media = len(media_list)
                current_index = context.user_data.get('current_media_index', 0)

                # Navigation simple
                if direction == "next":
                    current_index = current_index + 1
                    if current_index >= total_media:
                        current_index = 0
                else:  # prev
                    current_index = current_index - 1
                    if current_index < 0:
                        current_index = total_media - 1

                context.user_data['current_media_index'] = current_index
                current_media = media_list[current_index]

                caption = f"📱 <b>{product['name']}</b>\n\n"
                caption += f"💰 <b>Prix:</b>\n{product['price']}\n\n"
                caption += f"📝 <b>Description:</b>\n{product['description']}"

                # Création des boutons
                keyboard = []
            
                # Navigation des médias (en premier)
                if total_media > 1:
                    keyboard.append([
                        InlineKeyboardButton("⬅️ Vidéo précédente", callback_data=f"prev_{nav_id}"),
                        InlineKeyboardButton("Vidéo suivante ➡️", callback_data=f"next_{nav_id}")
                    ])
            
                # Navigation entre produits (en deuxième)
                prev_product, next_product = get_sibling_products(category, product['name'])
                if prev_product or next_product:
                    product_nav = []
                    if prev_product:
                        # Au lieu de générer un nouvel ID aléatoire, utilisez un ID basé sur le nom du produit
                        prev_nav_id = str(abs(hash(prev_product['name'])) % 10000)
                        context.user_data[f'nav_product_{prev_nav_id}'] = {
                            'category': category,
                            'name': prev_product['name']
                        }
                        product_nav.append(InlineKeyboardButton("◀️ Produit précédent", callback_data=f"product_{prev_nav_id}"))
    
                    if next_product:
                        # Même chose pour le produit suivant
                        next_nav_id = str(abs(hash(next_product['name'])) % 10000)
                        context.user_data[f'nav_product_{next_nav_id}'] = {
                            'category': category,
                            'name': next_product['name']
                        }
                        product_nav.append(InlineKeyboardButton("Produit suivant ▶️", callback_data=f"product_{next_nav_id}"))
                    keyboard.append(product_nav)

                # Bouton Commander et Retour (en dernier)
                keyboard.append([
                    InlineKeyboardButton(
                        "🛒 Commander",
                        **({'url': CONFIG.get('order_url')} if CONFIG.get('order_url') else {'callback_data': "show_order_text"})
                    )
                ])
                keyboard.append([
                    InlineKeyboardButton("🔙 Retour à la catégorie", callback_data=f"view_{category}")
                ])

                try:
                    await query.message.delete()
                except Exception as e:
                    print(f"Erreur lors de la suppression du message: {e}")

                try:
                    if current_media['media_type'] == 'photo':
                        try:
                            message = await context.bot.send_photo(
                                chat_id=query.message.chat_id,
                                photo=current_media['media_id'],
                                caption=caption,
                                reply_markup=InlineKeyboardMarkup(keyboard),
                                parse_mode='HTML'
                            )
                        except Exception as e:
                            print(f"Erreur d'envoi de photo: {e}")
                            message = await context.bot.send_message(
                                chat_id=query.message.chat_id,
                                text=f"{caption}\n\n⚠️ L'image n'a pas pu être chargée",
                                reply_markup=InlineKeyboardMarkup(keyboard),
                                parse_mode='HTML'
                            )
                    else:  # video
                        try:
                            message = await context.bot.send_video(
                                chat_id=query.message.chat_id,
                                video=current_media['media_id'],
                                caption=caption,
                                reply_markup=InlineKeyboardMarkup(keyboard),
                                parse_mode='HTML'
                            )
                        except Exception as e:
                            print(f"Erreur d'envoi de vidéo: {e}")
                            message = await context.bot.send_message(
                                chat_id=query.message.chat_id,
                                text=f"{caption}\n\n⚠️ La vidéo n'a pas pu être chargée",
                                reply_markup=InlineKeyboardMarkup(keyboard),
                                parse_mode='HTML'
                            )
                    context.user_data['last_product_message_id'] = message.message_id
                except Exception as e:
                    print(f"Erreur lors de l'envoi du média: {e}")
                    await query.answer("Une erreur est survenue lors de l'affichage du média")

        except Exception as e:
            print(f"Erreur lors de la navigation des médias: {e}")
            await query.answer("Une erreur est survenue")
            
    elif query.data == "edit_product":
        keyboard = []
        for category in CATALOG.keys():
            if category != 'stats':
                keyboard.append([
                    InlineKeyboardButton(
                        category, 
                        callback_data=f"editcat_{category}"  # Raccourci ici
                    )
                ])
        keyboard.append([InlineKeyboardButton("🔙 Annuler", callback_data="cancel_edit")])
        
        await query.message.edit_text(
            "✏️ Sélectionnez la catégorie du produit à modifier:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return SELECTING_CATEGORY

    elif query.data.startswith("editcat_"):  # Nouveau gestionnaire avec nom plus court
        category = query.data.replace("editcat_", "")
        products = CATALOG.get(category, [])
        
        keyboard = []
        for product in products:
            if isinstance(product, dict):
                # Créer un callback_data plus court
                callback_data = f"editp_{category[:10]}_{product['name'][:20]}"
                keyboard.append([
                    InlineKeyboardButton(product['name'], callback_data=callback_data)
                ])
        keyboard.append([InlineKeyboardButton("🔙 Annuler", callback_data="cancel_edit")])
        
        await query.message.edit_text(
            f"✏️ Sélectionnez le produit à modifier dans {category}:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return SELECTING_PRODUCT_TO_EDIT

    elif query.data.startswith("editp_"):
        try:
            _, short_category, short_product = query.data.split("_", 2)
            
            # Trouver la vraie catégorie et le vrai produit
            category = next((cat for cat in CATALOG.keys() if cat.startswith(short_category) or short_category.startswith(cat)), None)
            if category:
                product_name = next((p['name'] for p in CATALOG[category] if p['name'].startswith(short_product) or short_product.startswith(p['name'])), None)
                if product_name:
                    context.user_data['editing_category'] = category
                    context.user_data['editing_product'] = product_name

                    keyboard = [
                        [InlineKeyboardButton("📝 Nom", callback_data="edit_name")],
                        [InlineKeyboardButton("💰 Prix", callback_data="edit_price")],
                        [InlineKeyboardButton("📝 Description", callback_data="edit_desc")],
                        [InlineKeyboardButton("📸 Médias", callback_data="edit_media")],
                        [InlineKeyboardButton("🔙 Annuler", callback_data="cancel_edit")]
                    ]

                    await query.message.edit_text(
                        f"✏️ Que souhaitez-vous modifier pour *{product_name}* ?\n"
                        "Sélectionnez un champ à modifier:",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode='Markdown'
                    )
                    return EDITING_PRODUCT_FIELD
            
            return await show_admin_menu(update, context)
        except Exception as e:
            print(f"Erreur dans editp_: {e}")
            return await show_admin_menu(update, context)

    elif query.data in ["edit_name", "edit_price", "edit_desc", "edit_media"]:
        field_mapping = {
            "edit_name": "name",
            "edit_price": "price",
            "edit_desc": "description",
            "edit_media": "media",
            "edit_promo": "promo" 
        }
        field = field_mapping[query.data]
        context.user_data['editing_field'] = field
    
        category = context.user_data.get('editing_category')
        product_name = context.user_data.get('editing_product')
    
        product = next((p for p in CATALOG[category] if p['name'] == product_name), None)
    
        if product:

            if field == 'promo':
                return await show_promo_menu(update, context, product)

            if field == 'media':
                # Stocker les informations du produit en cours d'édition
                context.user_data['temp_product_category'] = category
                context.user_data['temp_product_name'] = product_name
                context.user_data['temp_product_price'] = product.get('price')
                context.user_data['temp_product_description'] = product.get('description')
                context.user_data['temp_product_media'] = []
                context.user_data['media_count'] = 0
            
                # Envoyer le message d'invitation pour les médias
                message = await query.message.edit_text(
                    "📸 Envoyez les photos ou vidéos du produit (plusieurs possibles)\n\n"
                    "*Si vous ne voulez pas en envoyer, cliquez sur ignorer* \n\n"
                    "*📌ATTENTION : Modifier les images écrase celles déjà existantes*",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Annuler", callback_data="cancel_edit")]
                    ]),
                    parse_mode='Markdown'
                )
                context.user_data['media_invitation_message_id'] = message.message_id
                return WAITING_PRODUCT_MEDIA
            else:
                # Votre code existant pour les autres champs
                current_value = product.get(field, "Non défini")
                field_names = {
                    'name': 'nom',
                    'price': 'prix',
                    'description': 'description'
                }
                await query.message.edit_text(
                    f"✏️ Modification du {field_names.get(field, field)}\n"
                    f"Valeur actuelle : {current_value}\n\n"
                    "Envoyez la nouvelle valeur :",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Annuler", callback_data="cancel_edit")
                    ]])
                )
                return WAITING_NEW_VALUE

    elif query.data == "cancel_edit":
        return await show_admin_menu(update, context)

    elif query.data == "confirm_reset_stats":
        # Réinitialiser les statistiques
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        CATALOG['stats'] = {
            "total_views": 0,
            "category_views": {},
            "product_views": {},
            "last_updated": now.split(" ")[1],  # Juste l'heure
            "last_reset": now.split(" ")[0]  # Juste la date
        }
        save_catalog(CATALOG)
        
        # Afficher un message de confirmation
        keyboard = [[InlineKeyboardButton("🔙 Retour au menu", callback_data="admin")]]
        await query.message.edit_text(
            "✅ *Les statistiques ont été réinitialisées avec succès!*\n\n"
            f"Date de réinitialisation : {CATALOG['stats']['last_reset']}\n\n"
            "Toutes les statistiques sont maintenant à zéro.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
               
    elif query.data == "show_categories":
        keyboard = []
        # Créer uniquement les boutons de catégories avec indication SOLD OUT
        for category in CATALOG.keys():
            if category != 'stats':
                # Vérifier si la catégorie est vide ou contient seulement SOLD OUT
                is_sold_out = (not CATALOG[category] or 
                              (len(CATALOG[category]) == 1 and 
                               CATALOG[category][0].get('name') == 'SOLD OUT ! ❌'))
            
                category_display = f"{category} (SOLD OUT ❌)" if is_sold_out else category
                keyboard.append([InlineKeyboardButton(category_display, callback_data=f"view_{category}")])

        # Ajouter uniquement le bouton retour à l'accueil
        keyboard.append([InlineKeyboardButton("🔙 Retour à l'accueil", callback_data="back_to_home")])

        try:
            message = await query.edit_message_text(
                "📋 *Menu*\n\n"
                "Choisissez une catégorie pour voir les produits :",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            context.user_data['menu_message_id'] = message.message_id
        except Exception as e:
            print(f"Erreur lors de la mise à jour du message des catégories: {e}")
            # Si la mise à jour échoue, recréez le message
            message = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="📋 *Menu*\n\n"
                     "Choisissez une catégorie pour voir les produits :",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            context.user_data['menu_message_id'] = message.message_id

    elif query.data == "back_to_home":  
        chat_id = update.effective_chat.id

        welcome_text = CONFIG.get('welcome_message', 
            "🌿 <b>Bienvenue sur votre bot !</b> 🌿\n\n"
            "<b>Pour changer ce message d accueil, rendez vous dans l onglet admin.</b>\n"
            "📋 Cliquez sur MENU pour voir les catégories"
        )

        keyboard = [
            [InlineKeyboardButton("📋 MENU", callback_data="show_categories")]
        ]

        with open('config/config.json', 'r') as f:
            config = json.load(f)

        for button in config.get('custom_buttons', []):
            if button['type'] == 'url':
                keyboard.append([InlineKeyboardButton(button['name'], url=button['value'])])
            elif button['type'] == 'text':
                keyboard.append([InlineKeyboardButton(button['name'], callback_data=f"custom_text_{button['id']}")])

        # Vérifier si la catégorie Réseaux est activée
        if config.get('networks_enabled', True):
            keyboard.append([InlineKeyboardButton("📱 Réseaux", callback_data="show_networks")])

        if config.get('reviews_enabled', True):
            keyboard.append([InlineKeyboardButton("📝 Avis", callback_data="show_reviews")])

        if str(update.effective_user.id) in ADMIN_IDS and access_manager.is_access_code_enabled():
            keyboard.extend([
                [InlineKeyboardButton("🎫 Générer des codes d'accès", callback_data="generate_multiple_codes")],
                [InlineKeyboardButton("📜 Historique codes", callback_data="show_codes_history")]
            ])

        if str(update.effective_user.id) in ADMIN_IDS:
            keyboard.append([InlineKeyboardButton("🔧 Menu Admin", callback_data="admin")])

        await query.message.edit_text(
            text=welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'  
        )
        return CHOOSING

async def manage_networks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère le menu de configuration des réseaux"""
    query = update.callback_query
    await query.answer()

    with open('config/config.json', 'r') as f:
        config = json.load(f)

    is_enabled = config.get('networks_enabled', True)
    status = "✅ Activée" if is_enabled else "❌ Désactivée"

    keyboard = [
        [InlineKeyboardButton(f"Catégorie Réseaux: {status}", callback_data="toggle_networks")],
        [InlineKeyboardButton("➕ Ajouter un bouton", callback_data="add_network_button")],
        [InlineKeyboardButton("✏️ Modifier un bouton", callback_data="edit_network_button")],
        [InlineKeyboardButton("❌ Supprimer un bouton", callback_data="delete_network_button")],
        [InlineKeyboardButton("🔙 Retour", callback_data="menu_config")]
    ]

    await query.edit_message_text(
        "🌐 *Configuration de la catégorie Réseaux*\n\n"
        "Gérez ici l'affichage et les boutons de la catégorie Réseaux.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return CHOOSING

async def toggle_networks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Active/désactive la catégorie Réseaux"""
    query = update.callback_query
    await query.answer()

    with open('config/config.json', 'r') as f:
        config = json.load(f)

    config['networks_enabled'] = not config.get('networks_enabled', True)

    with open('config/config.json', 'w') as f:
        json.dump(config, f, indent=4)

    return await manage_networks(update, context)

async def add_network_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commence le processus d'ajout d'un bouton réseau"""
    query = update.callback_query
    await query.answer()

    context.user_data['is_network_button'] = True  

    message = await query.edit_message_text(
        "➕ Ajout d'un nouveau bouton réseau\n\n"
        "Envoyez le nom du bouton (exemple: '🌟 Mon Réseau') :",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Annuler", callback_data="manage_networks")
        ]])
    )
    
    context.user_data['messages_to_delete'] = [message.message_id]
    return WAITING_BUTTON_NAME

async def edit_network_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liste les boutons réseaux pour modification"""
    query = update.callback_query
    await query.answer()

    with open('config/config.json', 'r') as f:
        config = json.load(f)

    networks = config.get('networks_buttons', [])
    if not networks:
        await query.edit_message_text(
            "Aucun bouton réseau n'existe.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Retour", callback_data="manage_networks")
            ]])
        )
        return CHOOSING

    keyboard = []
    for network in networks:
        keyboard.append([InlineKeyboardButton(
            f"✏️ {network['name']}", 
            callback_data=f"edit_network_{network['id']}"
        )])

    keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="manage_networks")])

    await query.edit_message_text(
        "Sélectionnez le bouton à modifier :",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING

async def delete_network_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liste les boutons réseaux pour suppression"""
    query = update.callback_query
    await query.answer()

    with open('config/config.json', 'r') as f:
        config = json.load(f)

    networks = config.get('networks_buttons', [])
    if not networks:
        await query.edit_message_text(
            "Aucun bouton réseau n'existe.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Retour", callback_data="manage_networks")
            ]])
        )
        return CHOOSING

    keyboard = []
    for network in networks:
        keyboard.append([InlineKeyboardButton(
            f"❌ {network['name']}", 
            callback_data=f"del_network_{network['id']}"
        )])

    keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="manage_networks")])

    await query.edit_message_text(
        "Sélectionnez le bouton à supprimer :",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING

async def get_file_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler temporaire pour obtenir le file_id de l'image banner"""
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        CONFIG['banner_image'] = file_id
        # Sauvegarder dans config.json
        with open('config/config.json', 'w', encoding='utf-8') as f:
            json.dump(CONFIG, f, indent=4)
        await update.message.reply_text(
            f"✅ Image banner enregistrée!\nFile ID: {file_id}"
        )
    else:
        await update.message.reply_text(
            "❌ Veuillez envoyer une photo pour l'utiliser comme bannière."
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if isinstance(context.error, NetworkError):
            print(f"Erreur réseau: {context.error}")
            if update and update.callback_query:
                await update.callback_query.answer("Erreur de connexion, veuillez réessayer.")
            await asyncio.sleep(1)  # Attendre avant de réessayer
        elif isinstance(context.error, TimedOut):
            print(f"Timeout: {context.error}")
            if update and update.callback_query:
                await update.callback_query.answer("La requête a pris trop de temps, veuillez réessayer.")
            await asyncio.sleep(1)
        else:
            print(f"Une erreur s'est produite: {context.error}")
    except Exception as e:
        print(f"Erreur dans le gestionnaire d'erreurs: {e}")
        
def main():
    """Fonction principale du bot"""
    try:
        # Créer l'application avec les timeouts personnalisés
        global admin_features
        application = (
            Application.builder()
            .token(TOKEN)
            .connect_timeout(30.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
            .get_updates_read_timeout(30.0)
            .get_updates_write_timeout(30.0)
            .get_updates_connect_timeout(30.0)
            .build()
        )
        admin_features = AdminFeatures()

        # Initialiser l'access manager
        global access_manager
        access_manager = AccessManager()

        # Ajouter le gestionnaire d'erreurs
        application.add_error_handler(error_handler)
        
        # Gestionnaire de conversation principal
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('start', start),
                CommandHandler('admin', admin),
                CallbackQueryHandler(handle_normal_buttons, pattern='^(show_categories|back_to_home|admin)$'),
                CallbackQueryHandler(show_custom_buttons_menu, pattern="^show_custom_buttons$"),
            ],
            states={
                CHOOSING: [
                    CallbackQueryHandler(show_quick_promos, pattern="^quick_promos$"),
                    CallbackQueryHandler(apply_quick_promo, pattern="^quick_promo_(10|20|30|50)$"),
                    CallbackQueryHandler(show_promo_menu, pattern="^edit_promo$"),
                    CallbackQueryHandler(admin_features.handle_user_management, pattern="^user_page_[0-9]+$"),
                    CallbackQueryHandler(list_buttons_for_editing, pattern="^list_buttons_edit$"),
                    CallbackQueryHandler(handle_button_editing, pattern="^edit_button_[^_]+$"),
                    CallbackQueryHandler(start_edit_button_name, pattern="^edit_button_name_"),
                    CallbackQueryHandler(start_edit_button_value, pattern="^edit_button_value_"),
                    CallbackQueryHandler(start_add_custom_button, pattern="^add_custom_button$"),
                    CallbackQueryHandler(handle_network_button_name, pattern="^edit_network_name_"),
                    CallbackQueryHandler(handle_network_button_value, pattern="^edit_network_value_"),
                    CallbackQueryHandler(list_buttons_for_deletion, pattern="^list_buttons_delete$"),
                    CallbackQueryHandler(handle_button_deletion, pattern="^delete_button_"),
                    CallbackQueryHandler(admin_features.manage_broadcasts, pattern="^manage_broadcasts$"),
                    CallbackQueryHandler(admin_features.edit_broadcast_content, pattern="^edit_broadcast_content_"),
                    CallbackQueryHandler(admin_features.edit_broadcast, pattern="^edit_broadcast_"),
                    CallbackQueryHandler(admin_features.resend_broadcast, pattern="^resend_broadcast_"),
                    CallbackQueryHandler(admin_features.delete_broadcast, pattern="^delete_broadcast_"),
                    CallbackQueryHandler(admin_features.handle_user_management, pattern="^manage_users$"),
                    CallbackQueryHandler(admin_features.handle_generate_multiple_codes, pattern="^generate_multiple_codes$"),
                    CallbackQueryHandler(admin_features.show_codes_history, pattern="^show_codes_history$"),
                    CallbackQueryHandler(admin_features.handle_custom_code_number, pattern="^gen_code_custom$"),
                    CallbackQueryHandler(lambda u, c: admin_features.generate_codes(u, c, 1), pattern="^gen_code_1$"),
                    CallbackQueryHandler(lambda u, c: admin_features.generate_codes(u, c, 5), pattern="^gen_code_5$"),
                    CallbackQueryHandler(admin_features.toggle_codes_view, pattern="^show_(active|used)_codes$"),
                    CallbackQueryHandler(admin_features.show_codes_history, pattern="^refresh_codes$"),
                    CallbackQueryHandler(admin_features.handle_codes_pagination, pattern="^(prev|next)_codes_page$"),
                    CallbackQueryHandler(admin_features.show_ban_user_menu, pattern="^ban_user_menu$"),
                    CallbackQueryHandler(admin_features.show_unban_user_menu, pattern="^unban_user_menu$"),
                    CallbackQueryHandler(admin_features.handle_unban_callback, pattern="^unban_[0-9]+$"),
                    CallbackQueryHandler(post_review, pattern="^post_review$"),
                    CallbackQueryHandler(handle_review_navigation, pattern="^(prev|next)_review_page$"),
                    CallbackQueryHandler(manage_pending_reviews, pattern="^manage_pending_reviews$"),
                    CallbackQueryHandler(show_reviews_menu, pattern="^show_reviews$"),
                    CallbackQueryHandler(view_reviews, pattern="^view_reviews$"),
                    CallbackQueryHandler(handle_review_decision, pattern="^(approve|reject)_review_[0-9]+$"),
                    CallbackQueryHandler(manage_reviews_config, pattern="^manage_reviews_config$"),

                    CallbackQueryHandler(
                        lambda u, c: admin_features.show_user_list(u, c, user_type=u.callback_query.data.split('_')[2]), 
                        pattern=r"^user_list_(validated|pending|banned)_[0-9]+$"
                    ),
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_CODE_NUMBER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, admin_features.handle_code_number_input),
                    CallbackQueryHandler(admin_features.handle_generate_multiple_codes, pattern="^generate_multiple_codes$"),
                    CallbackQueryHandler(admin_features.back_to_generate_codes, pattern="^back_to_generate_codes$"),
                ],
                WAITING_CATEGORY_NAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category_name),
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_PRODUCT_NAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_name),
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_PRODUCT_PRICE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_price),
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_PRODUCT_DESCRIPTION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_description),
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_PRODUCT_MEDIA: [
                    MessageHandler(filters.PHOTO | filters.VIDEO, handle_product_media),
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                SELECTING_CATEGORY: [
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_BUTTON_NAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button_name),
                    CallbackQueryHandler(handle_normal_buttons)
                ],
                WAITING_BUTTON_VALUE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button_value),
                    CallbackQueryHandler(handle_normal_buttons)
                ],
                SELECTING_CATEGORY_TO_DELETE: [
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_REVIEW_TEXT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_review_text),
                    CallbackQueryHandler(show_reviews_menu, pattern="^show_reviews$"),
                ],
                SELECTING_PRODUCT_TO_DELETE: [
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_CONTACT_USERNAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_contact_username),
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                SELECTING_PRODUCT_TO_EDIT: [
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                EDITING_PRODUCT_FIELD: [
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_NEW_VALUE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_value),
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_BANNER_IMAGE: [
                    MessageHandler(filters.PHOTO, handle_banner_image),
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_WELCOME_MESSAGE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_welcome_message),
                    CallbackQueryHandler(handle_normal_buttons)
                ],
                WAITING_BAN_INPUT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, admin_features.handle_ban_input),
                    CallbackQueryHandler(admin_features.handle_user_management, pattern="^manage_users$"),
                ],
                WAITING_UNBAN_INPUT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, admin_features.handle_unban_input),
                    CallbackQueryHandler(admin_features.handle_user_management, pattern="^manage_users$"),
                ],
                WAITING_ORDER_BUTTON_CONFIG: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_button_config),
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_PRODUCT_MEDIA: [
                    MessageHandler(filters.PHOTO | filters.VIDEO, handle_product_media),
                    CallbackQueryHandler(finish_product_media, pattern="^finish_media$"),
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_NEW_CATEGORY_NAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_category_name),
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                EDITING_CATEGORY: [
                    CallbackQueryHandler(handle_normal_buttons),
                ],
                WAITING_FOR_ACCESS_CODE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_access_code),
                    CallbackQueryHandler(start, pattern="^cancel_access$"),
                ],
                WAITING_BROADCAST_MESSAGE: [
                    MessageHandler(
                        (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Sticker.ALL) & ~filters.COMMAND,  # Même correction ici
                        admin_features.send_broadcast_message
                    ),
                    CallbackQueryHandler(handle_normal_buttons)
                ],
                WAITING_BROADCAST_EDIT: [
                    MessageHandler(
                        (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Sticker.ALL) & ~filters.COMMAND,  # Notez le S majuscule et le .ALL
                        admin_features.handle_broadcast_edit
                    ),
                    CallbackQueryHandler(handle_normal_buttons)
                ],
            },
            fallbacks=[
                CommandHandler('start', start),
                CommandHandler('admin', admin),
            ],
            name="main_conversation",
            persistent=False,
        )
        
        application.add_handler(CommandHandler("ban", admin_features.handle_ban_command))        
        application.add_handler(CallbackQueryHandler(show_networks, pattern="^show_networks$"))
        application.add_handler(CallbackQueryHandler(start, pattern="^start_cmd$"))
        application.add_handler(CommandHandler("gencode", admin_generate_code))
        application.add_handler(CommandHandler("listecodes", admin_list_codes))
        application.add_handler(conv_handler)

        # Démarrer le bot avec les paramètres optimisés
        print("Bot démarré...")
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY],
            pool_timeout=30.0,
            read_timeout=30.0,
            write_timeout=30.0,
            connect_timeout=30.0
        )

    except Exception as e:
        print(f"Erreur lors du démarrage du bot: {e}")

if __name__ == '__main__':
    main()
