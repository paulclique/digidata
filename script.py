from playwright.sync_api import sync_playwright
import os
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
import requests
import json
import psycopg2

# --- Configuration ---
load_dotenv()

USER = os.getenv("user")
DB_PASSWORD = os.getenv("password")
HOST = os.getenv("host")
PORT = os.getenv("port")
DBNAME = os.getenv("dbname")

EMAIL = os.getenv("DIGIFOOD_EMAIL")
PASSWORD = os.getenv("DIGIFOOD_PASSWORD")
DOWNLOAD_FOLDER = Path("exports").absolute()

# Connect to the database
try:
    print(f"Tentative de connexion à la base de données avec les paramètres suivants :")
    print(f"Utilisateur : {USER}")
    print(f"Mot de passe : {DB_PASSWORD}")
    print(f"Hôte : {HOST}")
    print(f"Port : {PORT}")
    print(f"Base de données : {DBNAME}")
    
    connection = psycopg2.connect(
        user=USER,
        password=DB_PASSWORD,
        host=HOST,
        port=PORT,
        dbname=DBNAME
    )
    print("Connection successful!")
    
    cursor = connection.cursor()
    cursor.execute("SELECT NOW();")
    result = cursor.fetchone()
    print("Current Time:", result)

    cursor.close()
    connection.close()
    print("Connection closed.")

except Exception as e:
    print(f"Échec de la connexion : {e}")
    print("Veuillez vérifier vos informations de connexion dans le fichier .env")

def envoyer_donnees_vers_api(sales, api_url, api_key=None):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    for sale in sales:
        payload = {
            "id": sale.get("id"),
            "date": sale.get("date"),
            "total": sale.get("total"),
            "payment_method": sale.get("payment_method"),
            "location": sale.get("location")
        }

        response = requests.post(api_url, json=payload, headers=headers)

        if response.status_code == 201:
            print(f"✅ Vente {sale.get('id')} envoyée avec succès.")
        else:
            print(f"❌ Erreur {response.status_code} pour {sale.get('id')} : {response.text}")

def attendre_et_cliquer(page, selector, timeout=5000):
    page.wait_for_selector(selector, timeout=timeout)
    page.click(selector)

def inserer_ventes_dans_bdd(data, export_date):
    try:
        connection = psycopg2.connect(
            user=USER,
            password=DB_PASSWORD,
            host=HOST,
            port=PORT,
            dbname=DBNAME
        )
        cursor = connection.cursor()
        
        # Vérifier si les données contiennent la clé "Global"
        if isinstance(data, dict) and "Global" in data:
            # Calculer les totaux globaux
            total_shops = len(data["Global"].get("Shops", []))
            global_total_ht = sum(shop.get("total_ht", 0) for shop in data["Global"].get("Shops", []))
            global_total = sum(shop.get("total", 0) for shop in data["Global"].get("Shops", []))
            total_volume = sum(shop.get("volume", 0) for shop in data["Global"].get("Shops", []))
            total_orders = sum(shop.get("order_count", 0) for shop in data["Global"].get("Shops", []))
            
            # Insérer les données dans la table exports
            cursor.execute("""
                INSERT INTO exports (
                    export_date, total_shops, global_total_ht, 
                    global_total, total_volume, total_orders, raw_data
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                export_date,
                total_shops,
                global_total_ht,
                global_total,
                total_volume,
                total_orders,
                json.dumps(data)
            ))
            
            connection.commit()
            print("✅ Données insérées avec succès")
            print(f"Nombre de boutiques : {total_shops}")
            print(f"Total HT : {global_total_ht}")
            print(f"Total TTC : {global_total}")
            print(f"Total volume : {total_volume}")
            print(f"Total commandes : {total_orders}")
        else:
            print("❌ Structure de données invalide : clé 'Global' manquante")
            print(f"Structure des données reçues : {data}")
        
    except Exception as e:
        print(f"❌ Erreur : {e}")
        if connection:
            connection.rollback()
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

def download_file(file_url):
    filename = file_url.split("?")[0].split("/")[-1]
    file_path = DOWNLOAD_FOLDER / filename

    response = requests.get(file_url)
    with open(file_path, "wb") as file:
        file.write(response.content)

    print(f"Rapport téléchargé : {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Extraire la date du nom du fichier
    # Le format est global_items_PuyduFouFrance_2025-04-23-2025-04-24.json
    # On prend la dernière date comme date d'export
    try:
        date_parts = filename.split("_")[-1].split(".")[0].split("-")
        # On reconstruit la date au format YYYY-MM-DD
        date_str = f"{date_parts[3]}-{date_parts[4]}-{date_parts[5]}"
        # On combine avec l'heure actuelle
        export_date = datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=datetime.now().hour,
            minute=datetime.now().minute,
            second=datetime.now().second
        )
        print(f"Date d'export extraite : {export_date}")
    except Exception as e:
        print(f"Erreur lors de l'extraction de la date : {e}")
        # Utiliser la date et heure actuelles comme fallback
        export_date = datetime.now()
        print(f"Utilisation de la date actuelle comme fallback : {export_date}")

    # Traiter les données
    inserer_ventes_dans_bdd(data, export_date)

    return file_path

def download_report_from_network(page):
    print("Attente de la réponse réseau vers l'endpoint '/tasks'")

    with page.expect_response(lambda response: "tasks" in response.url and response.status == 200) as response_info:
        response = response_info.value

    try:
        json_data = response.json()
        print("Réponse JSON reçue.")
        print(f"Structure de la réponse : {json_data}")

        if isinstance(json_data, dict) and "data" in json_data:
            data = json_data["data"]
            if isinstance(data, list) and len(data) > 0:
                last_task = data[-1]
                print(f"Dernière tâche : {last_task}")
                
                if isinstance(last_task, dict) and "response" in last_task:
                    response_data = last_task["response"]
                    if isinstance(response_data, dict) and "type" in response_data and "file" in response_data:
                        if response_data["type"] == "file":
                            file_url = response_data["file"]
                            print(f"URL du fichier trouvée : {file_url}")
                            return download_file(file_url)
                    else:
                        print("Structure de réponse inattendue dans response_data")
                else:
                    print("Structure de tâche inattendue")
            else:
                print("La liste data est vide ou n'est pas une liste")
        else:
            print("Structure de réponse inattendue au niveau racine")
            
        print("Aucun fichier valide trouvé dans la réponse.")
    except Exception as e:
        print(f"Erreur lors de l'analyse de la réponse JSON : {e}")
        print(f"Type de la réponse : {type(json_data)}")
        print(f"Contenu de la réponse : {json_data}")
    
    return None

def telecharger_rapport():
    DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            ignore_https_errors=True,
            accept_downloads=True
        )
        page = context.new_page()

        try:
            print("Connexion à Digifood")
            page.goto("https://app.digifood.fr/location_OUZSMG1QVkt2MWlRT1ZwR3ZCbUVkdz09/reports")
            page.fill('input[name="username"]', EMAIL)
            attendre_et_cliquer(page, 'button:has-text("Continuer")')

            page.wait_for_selector('input[type="password"]')
            page.fill('input[type="password"]', PASSWORD)
            attendre_et_cliquer(page, 'button:has-text("Continuer")')

            print("Configuration du rapport")
            page.wait_for_load_state("networkidle")

            attendre_et_cliquer(page, 'button:has-text("Générer un rapport")')

            page.wait_for_selector('.mat-mdc-dialog-container')
            attendre_et_cliquer(page, 'mat-select[id="mat-select-3"]')
            attendre_et_cliquer(page, 'mat-option:has-text("Ventes")')

            attendre_et_cliquer(page, 'mat-select[id="mat-select-4"]')
            attendre_et_cliquer(page, 'mat-option:has-text("Fichier JSON (json)")')

            print("Configuration de la période")
            today = datetime.now()
            
            # Utiliser la période complète de la journée
            date_debut = today.replace(hour=0, minute=0, second=0)
            date_fin = today.replace(hour=23, minute=59, second=59)

            print(f"\nDates d'export :")
            print(f"Date de début : {date_debut}")
            print(f"Date de fin : {date_fin}")

            page.fill('input[id="mat-input-2"]', date_debut.strftime("%Y-%m-%dT%H:%M"))
            page.fill('input[id="mat-input-3"]', date_fin.strftime("%Y-%m-%dT%H:%M"))

            # Vérifier les dates saisies
            date_debut_saisie = page.input_value('input[id="mat-input-2"]')
            date_fin_saisie = page.input_value('input[id="mat-input-3"]')
            print(f"\nDates saisies dans le formulaire :")
            print(f"Date de début saisie : {date_debut_saisie}")
            print(f"Date de fin saisie : {date_fin_saisie}")

            boutons = page.locator('button')
            print("Cliquer sur le bouton de génération")
            boutons.nth(11).click()

            print("Génération du rapport")
            page.wait_for_timeout(5000)

            print("Téléchargement du rapport")
            download_report_from_network(page)

        except Exception as e:
            print(f"Erreur lors du téléchargement: {str(e)}")
        finally:
            browser.close()

if __name__ == "__main__":
    print("\n=== Export de la journée (00:00-23:59) ===")
    telecharger_rapport()
    print("=== Export terminé ===\n")
