from playwright.sync_api import sync_playwright
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pathlib import Path
import requests
import json
import psycopg2
from zoneinfo import ZoneInfo
import logging

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Configuration ---
load_dotenv()

# Variables d'environnement avec valeurs par défaut
USER = os.getenv("user", "")
DB_PASSWORD = os.getenv("password", "")
HOST = os.getenv("host", "localhost") 
PORT = os.getenv("port", "5432")
DBNAME = os.getenv("dbname", "")

EMAIL = os.getenv("DIGIFOOD_EMAIL", "")
PASSWORD = os.getenv("DIGIFOOD_PASSWORD", "")
DOWNLOAD_FOLDER = Path("exports").absolute()

def get_db_connection():
    """Établit et retourne une connexion à la base de données"""
    try:
        logger.info("Tentative de connexion à la base de données...")
        connection = psycopg2.connect(
            user=USER,
            password=DB_PASSWORD,
            host=HOST,
            port=PORT,
            dbname=DBNAME
        )
        logger.info("Connexion établie avec succès")
        return connection
    except Exception as e:
        logger.error(f"Échec de la connexion : {e}")
        raise

# Test initial de la connexion
try:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT NOW();")
            result = cur.fetchone()
            logger.info(f"Test de connexion réussi. Heure serveur : {result[0]}")
except Exception as e:
    logger.error("Échec du test de connexion initial")
    logger.error(f"Veuillez vérifier vos informations de connexion dans le fichier .env : {e}")

def envoyer_donnees_vers_api(sales, api_url, api_key=None):
    """Envoie les données de ventes vers une API externe"""
    headers = {
        "Content-Type": "application/json",
        **({"Authorization": f"Bearer {api_key}"} if api_key else {})
    }

    for sale in sales:
        try:
            payload = {
                "id": sale.get("id"),
                "date": sale.get("date"),
                "total": sale.get("total"),
                "payment_method": sale.get("payment_method"),
                "location": sale.get("location")
            }

            response = requests.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"✅ Vente {sale.get('id')} envoyée avec succès")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Erreur lors de l'envoi de la vente {sale.get('id')}: {e}")

def attendre_et_cliquer(page, selector, timeout=5000):
    """Attend qu'un élément soit visible et clique dessus"""
    try:
        page.wait_for_selector(selector, timeout=timeout, state="visible")
        page.click(selector)
    except Exception as e:
        logger.error(f"Erreur lors du clic sur {selector}: {e}")
        raise

def inserer_ventes_dans_bdd(data, export_date):
    """Insère les données de ventes dans la base de données"""
    if not isinstance(data, dict) or "Global" not in data:
        logger.error("Structure de données invalide: clé 'Global' manquante")
        logger.debug(f"Structure reçue: {data}")
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Calcul des totaux
                shops = data["Global"].get("Shops", [])
                totals = {
                    "shops": len(shops),
                    "ht": sum(shop.get("total_ht", 0) for shop in shops),
                    "ttc": sum(shop.get("total", 0) for shop in shops),
                    "volume": sum(shop.get("volume", 0) for shop in shops),
                    "orders": sum(shop.get("order_count", 0) for shop in shops)
                }

                # S'assurer que la date est en UTC
                if export_date.tzinfo is None:
                    export_date = export_date.replace(tzinfo=ZoneInfo("UTC"))
                elif export_date.tzinfo != ZoneInfo("UTC"):
                    export_date = export_date.astimezone(ZoneInfo("UTC"))

                # Insertion des données
                cur.execute("""
                    INSERT INTO exports (
                        export_date, total_shops, global_total_ht,
                        global_total, total_volume, total_orders, raw_data
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    export_date,
                    totals["shops"],
                    totals["ht"],
                    totals["ttc"],
                    totals["volume"],
                    totals["orders"],
                    json.dumps(data)
                ))
                
                conn.commit()
                logger.info("✅ Données insérées avec succès")
                logger.info(f"Statistiques: {totals}")

    except Exception as e:
        logger.error(f"❌ Erreur lors de l'insertion: {e}")
        raise

def download_file(file_url):
    """Télécharge et traite un fichier depuis une URL"""
    try:
        filename = file_url.split("?")[0].split("/")[-1]
        file_path = DOWNLOAD_FOLDER / filename

        response = requests.get(file_url)
        response.raise_for_status()

        with open(file_path, "wb") as file:
            file.write(response.content)

        logger.info(f"Rapport téléchargé: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Extraction de la date du nom de fichier
        export_date = extraire_date_du_fichier(filename)
        
        # Traitement des données
        export_date_naive = export_date.replace(tzinfo=None, microsecond=0)
        inserer_ventes_dans_bdd(data, export_date_naive)

        return file_path

    except Exception as e:
        logger.error(f"Erreur lors du téléchargement/traitement du fichier: {e}")
        raise

def extraire_date_du_fichier(filename):
    """Extrait la date d'un nom de fichier"""
    try:
        date_parts = filename.split("_")[-1].split(".")[0].split("-")
        date_str = f"{date_parts[3]}-{date_parts[4]}-{date_parts[5]}"
        
        # Créer la date en UTC
        date_part = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo("UTC"))
        now_utc = datetime.now(ZoneInfo("UTC"))
        
        export_date = date_part.replace(
            hour=now_utc.hour,
            minute=now_utc.minute,
            second=now_utc.second,
            microsecond=now_utc.microsecond
        )
        logger.info(f"Date d'export extraite (UTC): {export_date}")
        return export_date
    
    except Exception as e:
        logger.warning(f"Erreur lors de l'extraction de la date: {e}")
        export_date = datetime.now(ZoneInfo("UTC"))
        logger.info(f"Utilisation de la date actuelle comme fallback (UTC): {export_date}")
        return export_date

def download_report_from_network(page):
    """Télécharge le rapport depuis la réponse réseau"""
    logger.info("Attente de la réponse réseau vers l'endpoint '/tasks'")

    try:
        with page.expect_response(
            lambda response: "tasks" in response.url and response.status == 200
        ) as response_info:
            response = response_info.value
            json_data = response.json()
            
            logger.info("Réponse JSON reçue")
            logger.debug(f"Structure de la réponse: {json_data}")

            if not isinstance(json_data, dict) or "data" not in json_data:
                raise ValueError("Structure de réponse invalide")

            data = json_data["data"]
            if not data or not isinstance(data, list):
                raise ValueError("Données vides ou invalides")

            last_task = data[-1]
            logger.debug(f"Dernière tâche: {last_task}")

            if not isinstance(last_task, dict) or "response" not in last_task:
                raise ValueError("Structure de tâche invalide")

            response_data = last_task["response"]
            if not isinstance(response_data, dict) or "type" not in response_data or "file" not in response_data:
                raise ValueError("Structure de réponse_data invalide")

            if response_data["type"] != "file":
                raise ValueError("Type de réponse incorrect")

            file_url = response_data["file"]
            logger.info(f"URL du fichier trouvée: {file_url}")
            return download_file(file_url)

    except Exception as e:
        logger.error(f"Erreur lors de l'analyse de la réponse: {e}")
        logger.debug(f"Contenu de la réponse: {json_data if 'json_data' in locals() else 'Non disponible'}")
        return None

def telecharger_rapport():
    """Fonction principale de téléchargement du rapport"""
    DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        logger.info("Lancement du navigateur en mode headless...")
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            ignore_https_errors=True,
            accept_downloads=True,
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()

        try:
            # Configuration initiale
            logger.info("Configuration de la langue en français...")
            page.goto("https://app.digifood.fr")
            page.evaluate("localStorage.setItem('lang', 'fr')")
            
            # Navigation et connexion
            logger.info("Connexion à Digifood...")
            page.goto("https://app.digifood.fr/location_OUZSMG1QVkt2MWlRT1ZwR3ZCbUVkdz09/reports", wait_until="networkidle")
            
            # Processus de connexion
            gerer_connexion(page)

            # Configuration et génération du rapport
            configurer_et_generer_rapport(page)

            # Téléchargement final
            logger.info("Téléchargement du rapport")
            download_report_from_network(page)

        except Exception as e:
            logger.error(f"Erreur lors du téléchargement: {e}")
            page.screenshot(path="error_screenshot.png")
            raise
        finally:
            browser.close()

def gerer_connexion(page):
    """Gère le processus de connexion"""
    selectors = [
        'button:has-text("Continue")',
        'button:has-text("Continuer")',
        'button[type="submit"]',
        'button.continue-button'
    ]

    # Première étape: email
    logger.info("Saisie de l'email...")
    page.wait_for_selector('input[name="username"]', timeout=30000)
    page.fill('input[name="username"]', EMAIL)
    cliquer_bouton_avec_retry(page, selectors, "Continuer (email)")

    # Deuxième étape: mot de passe
    logger.info("Saisie du mot de passe...")
    page.wait_for_selector('input[type="password"]', timeout=30000)
    page.fill('input[type="password"]', PASSWORD)
    cliquer_bouton_avec_retry(page, selectors, "Continuer (mot de passe)")

    page.wait_for_load_state("networkidle", timeout=30000)

def cliquer_bouton_avec_retry(page, selectors, action_name):
    """Essaie de cliquer sur un bouton avec plusieurs sélecteurs"""
    for selector in selectors:
        try:
            logger.debug(f"Essai du sélecteur: {selector}")
            page.wait_for_selector(selector, timeout=5000, state="visible")
            page.click(selector)
            logger.info(f"Action {action_name} réussie")
            return
        except Exception as e:
            logger.debug(f"Échec du sélecteur {selector}: {e}")
            continue
    
    raise Exception(f"Échec de l'action {action_name}: aucun sélecteur n'a fonctionné")

def configurer_et_generer_rapport(page):
    """Configure et génère le rapport"""
    logger.info("Configuration du rapport...")
    
    # Clic sur le bouton de génération
    selectors_rapport = [
        'button:has-text("Générer un rapport")',
        'button:has-text("Generate report")'
    ]
    cliquer_bouton_avec_retry(page, selectors_rapport, "Générer rapport")

    # Configuration des options
    page.wait_for_selector('.mat-mdc-dialog-container')
    attendre_et_cliquer(page, 'mat-select[id="mat-select-3"]')
    attendre_et_cliquer(page, 'mat-option:has-text("Ventes")')
    attendre_et_cliquer(page, 'mat-select[id="mat-select-4"]')
    attendre_et_cliquer(page, 'mat-option:has-text("Fichier JSON (json)")')

    # Configuration de la période avec gestion explicite du fuseau horaire
    logger.info("Configuration de la période")
    paris_tz = ZoneInfo("Europe/Paris")
    now_paris = datetime.now(paris_tz)
    
    # Si l'heure actuelle est avant 22h, on utilise la date d'hier
    if now_paris.hour < 22:
        date_debut = (now_paris - timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
        date_fin = now_paris.replace(hour=21, minute=59, second=59, microsecond=999999)
    else:
        # Si l'heure actuelle est après 22h, on utilise la date d'aujourd'hui
        date_debut = now_paris.replace(hour=22, minute=0, second=0, microsecond=0)
        date_fin = (now_paris + timedelta(days=1)).replace(hour=21, minute=59, second=59, microsecond=999999)

    logger.info(f"Période d'export (Paris): {date_debut} - {date_fin}")
    
    # Convertir en format ISO pour l'input
    date_debut_iso = date_debut.strftime("%Y-%m-%dT%H:%M")
    date_fin_iso = date_fin.strftime("%Y-%m-%dT%H:%M")
    
    logger.info(f"Dates formatées pour l'input: {date_debut_iso} - {date_fin_iso}")
    
    page.fill('input[id="mat-input-2"]', date_debut_iso)
    page.fill('input[id="mat-input-3"]', date_fin_iso)

    # Vérification des dates
    verifier_dates_saisies(page)

    # Génération finale
    logger.info("Lancement de la génération")
    page.locator('button').nth(11).click()
    page.wait_for_timeout(5000)

def verifier_dates_saisies(page):
    """Vérifie que les dates ont été correctement saisies"""
    date_debut = page.input_value('input[id="mat-input-2"]')
    date_fin = page.input_value('input[id="mat-input-3"]')
    logger.info(f"Dates saisies dans l'interface - Début: {date_debut}, Fin: {date_fin}")
    
    # Vérification supplémentaire des fuseaux horaires
    paris_tz = ZoneInfo("Europe/Paris")
    now_paris = datetime.now(paris_tz)
    logger.info(f"Heure actuelle à Paris: {now_paris}")
    logger.info(f"Heure actuelle en UTC: {datetime.now(ZoneInfo('UTC'))}")
    
    # Vérification que la date de fin est après la date de début
    try:
        debut = datetime.strptime(date_debut, "%Y-%m-%dT%H:%M")
        fin = datetime.strptime(date_fin, "%Y-%m-%dT%H:%M")
        if fin <= debut:
            logger.warning("ATTENTION: La date de fin est avant ou égale à la date de début!")
    except Exception as e:
        logger.error(f"Erreur lors de la vérification des dates: {e}")

if __name__ == "__main__":
    logger.info("\n=== Export de la journée (00:00-23:59) ===")
    telecharger_rapport()
    logger.info("=== Export terminé ===\n")
