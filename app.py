from flask import Flask, render_template, request, redirect, url_for
from bs4 import BeautifulSoup
import requests
import datetime
import json
import os
import threading
import time
from dotenv import load_dotenv
import uuid

# ================== CONFIG ==================
load_dotenv()

PRICE_DATA_FILE = "products.json"
CHECK_INTERVAL = 60 * 30  # 30 minutes

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/116.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

app = Flask(__name__)
# ============================================

# ---------- Helper Functions ----------
def load_products():
    try:
        with open(PRICE_DATA_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_products(products):
    with open(PRICE_DATA_FILE, "w") as f:
        json.dump(products, f, indent=4)

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, data=payload, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Telegram error: {e}")

def scrape_product_details(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        print(response)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return None, None

    soup = BeautifulSoup(response.text, "html.parser")
    if "captcha" in response.text.lower():
        print("Captcha detected. Blocked by Amazon.")
        return None, None

    # Price scraping
    price_text = None
    price_tag = soup.find("span", class_="a-offscreen")
    if price_tag:
        price_text = price_tag.get_text()
    else:
        price_tag = soup.find("span", id="priceblock_ourprice") \
                    or soup.find("span", id="priceblock_dealprice") \
                    or soup.find("span", class_="a-price-whole")
        if price_tag:
            price_text = price_tag.get_text()

    if price_text:
        try:
            clean_price = price_text.replace("₹", "").replace(",", "").strip()
            price_float = float(clean_price)
        except ValueError:
            price_float = None
    else:
        price_float = None

    # Product name
    name_tag = soup.find("span", id="productTitle")
    product_name = name_tag.get_text().strip() if name_tag else "Unknown Product"

    return price_float, product_name

def update_all_products():
    """Background function to check prices for all products."""
    while True:
        products = load_products()
        for product in products:
            final_price, product_name = scrape_product_details(product["url"])
            if final_price is not None:
                old_price = product.get("current_price")
                product["product_name"] = product_name
                product["current_price"] = final_price
                product["last_check"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Safely track lowest price
                current_lowest = product.get("lowest_price")
                if current_lowest is None or final_price < current_lowest:
                    product["lowest_price"] = final_price
                    # Telegram alert on price drop
                    if old_price is not None and final_price < old_price:
                        drop_amount = old_price - final_price
                        message = (
                            f"🚨 *PRICE DROP ALERT!* 🚨\n\n"
                            f"{product_name} dropped from ₹{old_price:.2f} to ₹{final_price:.2f} "
                            f"(saving ₹{drop_amount:.2f}).\n\n[View Product]({product['url']})"
                        )
                        send_telegram(message)
        save_products(products)
        print(f"Background check complete at {datetime.datetime.now()}. Sleeping {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)


# ---------- Flask Routes ----------
@app.route("/")
def dashboard():
    products = load_products()
    return render_template("dashboard.html", products=products)

@app.route("/add", methods=["POST"])
def add_product():
    url = request.form.get("url")
    if not url:
        return redirect(url_for("dashboard"))

    # Scrape immediately
    price, name = scrape_product_details(url)
    product_name = name if name else "Unknown Product"

    new_product = {
        "id": str(uuid.uuid4()),
        "url": url,
        "product_name": product_name,
        "current_price": price,
        "lowest_price": price,
        "last_check": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") if price else None
    }

    products = load_products()
    products.append(new_product)
    save_products(products)

    # Send Telegram notification
    if price is not None:
        message = (
            f"✅ *NEW PRODUCT ADDED!* ✅\n\n"
            f"Tracking *{product_name}* at ₹{price:.2f}.\n"
            f"[View Product]({url})"
        )
        send_telegram(message)

    return redirect(url_for("dashboard"))


@app.route("/delete/<product_id>", methods=["POST"])
def delete_product(product_id):
    products = load_products()
    products = [p for p in products if p["id"] != product_id]
    save_products(products)
    return redirect(url_for("dashboard"))

# ---------- Start Background Thread ----------
if __name__ == "__main__":
    threading.Thread(target=update_all_products, daemon=True).start()
    app.run(debug=True, port=5000)
