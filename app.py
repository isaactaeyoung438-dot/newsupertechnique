from flask import Flask, request, redirect, url_for, flash, render_template_string, jsonify, make_response, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text, func
from datetime import datetime, timedelta
from functools import wraps
from collections import defaultdict
import json
import os
import hashlib
import secrets
import re
import csv

# Import reportlab with error handling
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    print("⚠️ ReportLab not installed. PDF generation disabled.")

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# ==================== CONFIGURATION ====================
TAX_RATE = float(os.environ.get('VAT_RATE', 0.16))  # 16% VAT

# Database setup - Creates a separate .db file in the instance folder
basedir = os.path.abspath(os.path.dirname(__file__))

# Create instance folder if it doesn't exist
instance_path = os.path.join(basedir, 'instance')
if not os.path.exists(instance_path):
    os.makedirs(instance_path)
    print(f"✅ Created instance folder at: {instance_path}")

# Database file path - this creates a separate .db file
db_path = os.path.join(instance_path, 'supermarket.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

print(f"📂 Database will be stored at: {db_path}")

# ==================== USER SESSION ====================

current_session = {}


def login_required(role=None):
    """Decorator to check if user is logged in"""

    def decorator(func):
        @wraps(func)
        def decorated_function(*args, **kwargs):
            if not current_session.get('logged_in'):
                flash('Please login first!', 'danger')
                return redirect(url_for('login'))
            if role and current_session.get('role') != role and current_session.get('role') != 'admin':
                flash('Access denied! Insufficient permissions.', 'danger')
                return redirect(url_for('index'))
            return func(*args, **kwargs)

        return decorated_function

    return decorator


# ==================== HELPER FUNCTIONS ====================

def remove_emoji(text):
    """Remove emojis from text for clean printing"""
    if not text:
        return text
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001F900-\U0001F9FF"
        "\U0001FA70-\U0001FAFF"
        "]+",
        flags=re.UNICODE
    )
    return emoji_pattern.sub('', text).strip()


def get_client_ip():
    """Get client IP address"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0]
    return request.remote_addr


def log_activity(action_message, user=None):
    """Log user activity"""
    log = ActivityLog(
        user=user or current_session.get('username', 'system'),
        action=action_message,
        ip=get_client_ip()
    )
    db.session.add(log)
    db.session.commit()


def get_total_stock():
    """Get total stock quantity"""
    result = db.session.query(func.sum(Product.quantity)).scalar()
    return result or 0


def get_total_sales():
    """Get total sales amount"""
    result = db.session.query(func.sum(SaleTransaction.total)).scalar()
    return result or 0


def get_total_profit():
    """Calculate total profit"""
    total_profit = 0
    for trans in SaleTransaction.query.all():
        trans_profit = trans.total
        for item in trans.items:
            product = Product.query.filter_by(name=item.product_name).first()
            if product:
                trans_profit -= product.cost_price * item.quantity
        total_profit += trans_profit
    return int(total_profit)


def get_daily_sales_last_7_days():
    """Get daily sales for the last 7 days"""
    sales_by_day = defaultdict(float)
    today = datetime.now().date()
    for i in range(7):
        date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        sales_by_day[date_str] = 0
    for trans in SaleTransaction.query.all():
        trans_date = trans.timestamp.date()
        if trans_date >= today - timedelta(days=6):
            date_str = trans_date.strftime("%Y-%m-%d")
            sales_by_day[date_str] += trans.total
    return dict(sorted(sales_by_day.items()))


# ==================== DATABASE MODELS ====================

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), default='staff')
    name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_ip = db.Column(db.String(45))
    last_login_time = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()

    def check_password(self, password):
        return self.password_hash == hashlib.sha256(password.encode()).hexdigest()


class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    price = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    quality = db.Column(db.String(20), default='medium')
    category = db.Column(db.String(50), default='general')
    cost_price = db.Column(db.Integer, default=0)
    min_stock = db.Column(db.Integer, default=5)
    barcode = db.Column(db.String(50), unique=True)
    popularity = db.Column(db.Integer, default=80)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Customer(db.Model):
    __tablename__ = 'customers'
    phone = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    total_spent = db.Column(db.Integer, default=0)
    visits = db.Column(db.Integer, default=0)
    last_purchase = db.Column(db.DateTime)
    registered_at = db.Column(db.DateTime, default=datetime.utcnow)
    registered_ip = db.Column(db.String(45))


class SaleTransaction(db.Model):
    __tablename__ = 'sales_transactions'
    id = db.Column(db.String(50), primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    subtotal = db.Column(db.Integer, nullable=False, default=0)
    discount_amount = db.Column(db.Integer, default=0)
    discount_type = db.Column(db.String(10), default='fixed')
    discount_value = db.Column(db.Integer, default=0)
    tax_amount = db.Column(db.Integer, default=0)
    total = db.Column(db.Integer, nullable=False)
    customer_phone = db.Column(db.String(20), db.ForeignKey('customers.phone'))
    customer_name = db.Column(db.String(100))
    customer_email = db.Column(db.String(120))
    ip_address = db.Column(db.String(45))
    payment_method = db.Column(db.String(20), default='Cash')
    payment_reference = db.Column(db.String(100))
    items = db.relationship('SaleItem', backref='transaction', lazy=True, cascade='all, delete-orphan')


class SaleItem(db.Model):
    __tablename__ = 'sale_items'
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String(50), db.ForeignKey('sales_transactions.id'), nullable=False)
    product_name = db.Column(db.String(150), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Integer, nullable=False)
    total_cost = db.Column(db.Integer, nullable=False)


class ActivityLog(db.Model):
    __tablename__ = 'activity_log'
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.Column(db.String(80))
    action = db.Column(db.String(255))
    ip = db.Column(db.String(45))


# ==================== INITIAL PRODUCT DATA ====================

INITIAL_PRODUCTS = {
    "Cooking Oil": {"price": 250, "quantity": 45, "quality": "high", "category": "cooking", "cost_price": 180,
                    "min_stock": 8, "barcode": "890123456001", "popularity": 95},
    "Maize Flour": {"price": 120, "quantity": 65, "quality": "medium", "category": "cooking", "cost_price": 85,
                    "min_stock": 15, "barcode": "890123456002", "popularity": 98},
    "Wheat Flour": {"price": 110, "quantity": 58, "quality": "high", "category": "cooking", "cost_price": 75,
                    "min_stock": 12, "barcode": "890123456003", "popularity": 97},
    "Rice (5kg)": {"price": 600, "quantity": 32, "quality": "premium", "category": "cooking", "cost_price": 450,
                   "min_stock": 5, "barcode": "890123456004", "popularity": 96},
    "Pasta": {"price": 80, "quantity": 48, "quality": "medium", "category": "cooking", "cost_price": 50,
              "min_stock": 10, "barcode": "890123456005", "popularity": 85},
    "Cooking Fat": {"price": 180, "quantity": 38, "quality": "high", "category": "cooking", "cost_price": 130,
                    "min_stock": 8, "barcode": "890123456006", "popularity": 88},
    "Salt": {"price": 20, "quantity": 105, "quality": "low", "category": "cooking", "cost_price": 10, "min_stock": 20,
             "barcode": "890123456007", "popularity": 99},
    "Sugar": {"price": 140, "quantity": 55, "quality": "medium", "category": "cooking", "cost_price": 100,
              "min_stock": 12, "barcode": "890123456008", "popularity": 98},
    "Tomato Paste": {"price": 65, "quantity": 42, "quality": "medium", "category": "cooking", "cost_price": 42,
                     "min_stock": 10, "barcode": "890123456009", "popularity": 90},
    "Garlic Paste": {"price": 90, "quantity": 33, "quality": "high", "category": "cooking", "cost_price": 60,
                     "min_stock": 6, "barcode": "890123456010", "popularity": 87},
    "Honey": {"price": 350, "quantity": 25, "quality": "premium", "category": "cooking", "cost_price": 250,
              "min_stock": 5, "barcode": "890123456059", "popularity": 89},
    "Peanut Butter": {"price": 280, "quantity": 30, "quality": "high", "category": "cooking", "cost_price": 200,
                      "min_stock": 8, "barcode": "890123456060", "popularity": 92},
    "Hot Sauce": {"price": 95, "quantity": 40, "quality": "medium", "category": "cooking", "cost_price": 60,
                  "min_stock": 10, "barcode": "890123456061", "popularity": 83},
    "Baked Beans": {"price": 110, "quantity": 35, "quality": "medium", "category": "cooking", "cost_price": 75,
                    "min_stock": 8, "barcode": "890123456062", "popularity": 86},
    "Soup Mix": {"price": 55, "quantity": 50, "quality": "low", "category": "cooking", "cost_price": 35,
                 "min_stock": 12, "barcode": "890123456063", "popularity": 81},
    "Apple": {"price": 30, "quantity": 55, "quality": "high", "category": "fruits", "cost_price": 20, "min_stock": 10,
              "barcode": "890123456011", "popularity": 95},
    "Banana": {"price": 20, "quantity": 105, "quality": "medium", "category": "fruits", "cost_price": 12,
               "min_stock": 15, "barcode": "890123456012", "popularity": 98},
    "Orange": {"price": 25, "quantity": 48, "quality": "high", "category": "fruits", "cost_price": 15, "min_stock": 10,
               "barcode": "890123456013", "popularity": 92},
    "Strawberry": {"price": 60, "quantity": 33, "quality": "high", "category": "fruits", "cost_price": 35,
                   "min_stock": 5, "barcode": "890123456014", "popularity": 88},
    "Kiwi": {"price": 40, "quantity": 28, "quality": "high", "category": "fruits", "cost_price": 25, "min_stock": 5,
             "barcode": "890123456015", "popularity": 85},
    "Pineapple": {"price": 80, "quantity": 18, "quality": "high", "category": "fruits", "cost_price": 50,
                  "min_stock": 3, "barcode": "890123456016", "popularity": 90},
    "Mango": {"price": 45, "quantity": 38, "quality": "high", "category": "fruits", "cost_price": 28, "min_stock": 8,
              "barcode": "890123456017", "popularity": 91},
    "Grapes": {"price": 55, "quantity": 43, "quality": "high", "category": "fruits", "cost_price": 32, "min_stock": 8,
               "barcode": "890123456018", "popularity": 89},
    "Cherry": {"price": 90, "quantity": 23, "quality": "premium", "category": "fruits", "cost_price": 60,
               "min_stock": 3, "barcode": "890123456019", "popularity": 86},
    "Watermelon": {"price": 120, "quantity": 13, "quality": "high", "category": "fruits", "cost_price": 80,
                   "min_stock": 2, "barcode": "890123456020", "popularity": 93},
    "Pear": {"price": 35, "quantity": 40, "quality": "high", "category": "fruits", "cost_price": 22, "min_stock": 8,
             "barcode": "890123456064", "popularity": 87},
    "Peach": {"price": 50, "quantity": 30, "quality": "premium", "category": "fruits", "cost_price": 32, "min_stock": 5,
              "barcode": "890123456065", "popularity": 84},
    "Lettuce": {"price": 35, "quantity": 33, "quality": "medium", "category": "vegetables", "cost_price": 20,
                "min_stock": 8, "barcode": "890123456021", "popularity": 84},
    "Tomato": {"price": 40, "quantity": 65, "quality": "medium", "category": "vegetables", "cost_price": 25,
               "min_stock": 12, "barcode": "890123456022", "popularity": 96},
    "Carrot": {"price": 25, "quantity": 85, "quality": "high", "category": "vegetables", "cost_price": 15,
               "min_stock": 15, "barcode": "890123456023", "popularity": 94},
    "Broccoli": {"price": 50, "quantity": 28, "quality": "high", "category": "vegetables", "cost_price": 30,
                 "min_stock": 5, "barcode": "890123456024", "popularity": 82},
    "Corn": {"price": 30, "quantity": 48, "quality": "medium", "category": "vegetables", "cost_price": 18,
             "min_stock": 10, "barcode": "890123456025", "popularity": 86},
    "Bell Pepper": {"price": 45, "quantity": 38, "quality": "high", "category": "vegetables", "cost_price": 28,
                    "min_stock": 8, "barcode": "890123456026", "popularity": 83},
    "Potato": {"price": 20, "quantity": 125, "quality": "low", "category": "vegetables", "cost_price": 12,
               "min_stock": 20, "barcode": "890123456027", "popularity": 97},
    "Onion": {"price": 25, "quantity": 95, "quality": "medium", "category": "vegetables", "cost_price": 15,
              "min_stock": 15, "barcode": "890123456028", "popularity": 98},
    "Garlic": {"price": 15, "quantity": 105, "quality": "high", "category": "vegetables", "cost_price": 8,
               "min_stock": 20, "barcode": "890123456029", "popularity": 95},
    "Cucumber": {"price": 30, "quantity": 58, "quality": "medium", "category": "vegetables", "cost_price": 18,
                 "min_stock": 10, "barcode": "890123456030", "popularity": 88},
    "Eggplant": {"price": 45, "quantity": 35, "quality": "medium", "category": "vegetables", "cost_price": 28,
                 "min_stock": 8, "barcode": "890123456066", "popularity": 82},
    "Chili Pepper": {"price": 25, "quantity": 60, "quality": "medium", "category": "vegetables", "cost_price": 15,
                     "min_stock": 12, "barcode": "890123456067", "popularity": 85},
    "Milk": {"price": 50, "quantity": 25, "quality": "high", "category": "dairy", "cost_price": 35, "min_stock": 5,
             "barcode": "890123456031", "popularity": 96},
    "Cheese": {"price": 120, "quantity": 18, "quality": "premium", "category": "dairy", "cost_price": 85,
               "min_stock": 3, "barcode": "890123456032", "popularity": 89},
    "Butter": {"price": 90, "quantity": 28, "quality": "high", "category": "dairy", "cost_price": 60, "min_stock": 5,
               "barcode": "890123456033", "popularity": 91},
    "Eggs (tray)": {"price": 300, "quantity": 45, "quality": "medium", "category": "dairy", "cost_price": 220,
                    "min_stock": 8, "barcode": "890123456034", "popularity": 94},
    "Ice Cream": {"price": 80, "quantity": 43, "quality": "high", "category": "dairy", "cost_price": 50, "min_stock": 8,
                  "barcode": "890123456035", "popularity": 92},
    "Yogurt": {"price": 45, "quantity": 38, "quality": "high", "category": "dairy", "cost_price": 28, "min_stock": 8,
               "barcode": "890123456036", "popularity": 90},
    "Cream": {"price": 70, "quantity": 25, "quality": "high", "category": "dairy", "cost_price": 48, "min_stock": 5,
              "barcode": "890123456068", "popularity": 86},
    "Cottage Cheese": {"price": 95, "quantity": 20, "quality": "medium", "category": "dairy", "cost_price": 65,
                       "min_stock": 4, "barcode": "890123456069", "popularity": 83},
    "Bread": {"price": 40, "quantity": 20, "quality": "low", "category": "bakery", "cost_price": 25, "min_stock": 5,
              "barcode": "890123456037", "popularity": 98},
    "Croissant": {"price": 60, "quantity": 24, "quality": "high", "category": "bakery", "cost_price": 35,
                  "min_stock": 5, "barcode": "890123456038", "popularity": 87},
    "Baguette": {"price": 45, "quantity": 22, "quality": "medium", "category": "bakery", "cost_price": 28,
                 "min_stock": 4, "barcode": "890123456039", "popularity": 85},
    "Cookies": {"price": 35, "quantity": 55, "quality": "medium", "category": "bakery", "cost_price": 20,
                "min_stock": 10, "barcode": "890123456040", "popularity": 93},
    "Cake": {"price": 250, "quantity": 12, "quality": "premium", "category": "bakery", "cost_price": 150,
             "min_stock": 2, "barcode": "890123456041", "popularity": 88},
    "Pretzel": {"price": 30, "quantity": 35, "quality": "medium", "category": "bakery", "cost_price": 18,
                "min_stock": 8, "barcode": "890123456070", "popularity": 82},
    "Donut": {"price": 50, "quantity": 28, "quality": "medium", "category": "bakery", "cost_price": 30, "min_stock": 6,
              "barcode": "890123456071", "popularity": 90},
    "Coffee": {"price": 150, "quantity": 35, "quality": "premium", "category": "beverages", "cost_price": 90,
               "min_stock": 8, "barcode": "890123456042", "popularity": 94},
    "Tea": {"price": 80, "quantity": 45, "quality": "high", "category": "beverages", "cost_price": 50, "min_stock": 10,
            "barcode": "890123456043", "popularity": 96},
    "Juice": {"price": 60, "quantity": 50, "quality": "medium", "category": "beverages", "cost_price": 38,
              "min_stock": 10, "barcode": "890123456044", "popularity": 91},
    "Soda": {"price": 40, "quantity": 105, "quality": "low", "category": "beverages", "cost_price": 25, "min_stock": 20,
             "barcode": "890123456045", "popularity": 97},
    "Water (6pack)": {"price": 120, "quantity": 85, "quality": "medium", "category": "beverages", "cost_price": 80,
                      "min_stock": 15, "barcode": "890123456046", "popularity": 99},
    "Energy Drink": {"price": 100, "quantity": 40, "quality": "medium", "category": "beverages", "cost_price": 65,
                     "min_stock": 8, "barcode": "890123456072", "popularity": 88},
    "Milkshake": {"price": 120, "quantity": 30, "quality": "high", "category": "beverages", "cost_price": 80,
                  "min_stock": 5, "barcode": "890123456073", "popularity": 86},
    "Smoothie": {"price": 150, "quantity": 25, "quality": "premium", "category": "beverages", "cost_price": 100,
                 "min_stock": 4, "barcode": "890123456074", "popularity": 87},
    "Popcorn": {"price": 55, "quantity": 38, "quality": "medium", "category": "snacks", "cost_price": 35,
                "min_stock": 8, "barcode": "890123456047", "popularity": 89},
    "Chocolate": {"price": 75, "quantity": 55, "quality": "high", "category": "snacks", "cost_price": 45,
                  "min_stock": 12, "barcode": "890123456048", "popularity": 95},
    "Candy": {"price": 10, "quantity": 310, "quality": "low", "category": "snacks", "cost_price": 5, "min_stock": 50,
              "barcode": "890123456049", "popularity": 98},
    "Biscuits": {"price": 45, "quantity": 60, "quality": "medium", "category": "snacks", "cost_price": 28,
                 "min_stock": 12, "barcode": "890123456075", "popularity": 91},
    "Nuts Mix": {"price": 120, "quantity": 35, "quality": "high", "category": "snacks", "cost_price": 80,
                 "min_stock": 8, "barcode": "890123456076", "popularity": 88},
    "Potato Chips": {"price": 65, "quantity": 70, "quality": "medium", "category": "snacks", "cost_price": 40,
                     "min_stock": 15, "barcode": "890123456077", "popularity": 94},
    "Detergent": {"price": 180, "quantity": 35, "quality": "high", "category": "household", "cost_price": 120,
                  "min_stock": 8, "barcode": "890123456052", "popularity": 94},
    "Dish Soap": {"price": 90, "quantity": 45, "quality": "medium", "category": "household", "cost_price": 60,
                  "min_stock": 10, "barcode": "890123456053", "popularity": 92},
    "Sponge": {"price": 25, "quantity": 65, "quality": "low", "category": "household", "cost_price": 15,
               "min_stock": 15, "barcode": "890123456054", "popularity": 88},
    "Broom": {"price": 150, "quantity": 25, "quality": "medium", "category": "household", "cost_price": 100,
              "min_stock": 5, "barcode": "890123456055", "popularity": 85},
    "Laundry Basket": {"price": 250, "quantity": 20, "quality": "medium", "category": "household", "cost_price": 180,
                       "min_stock": 5, "barcode": "890123456078", "popularity": 82},
    "Fabric Softener": {"price": 120, "quantity": 30, "quality": "high", "category": "household", "cost_price": 80,
                        "min_stock": 8, "barcode": "890123456079", "popularity": 87},
    "Shampoo": {"price": 200, "quantity": 40, "quality": "high", "category": "personal", "cost_price": 140,
                "min_stock": 10, "barcode": "890123456080", "popularity": 91},
    "Body Soap": {"price": 80, "quantity": 60, "quality": "medium", "category": "personal", "cost_price": 50,
                  "min_stock": 15, "barcode": "890123456081", "popularity": 93},
    "Razor": {"price": 150, "quantity": 35, "quality": "high", "category": "personal", "cost_price": 100,
              "min_stock": 8, "barcode": "890123456082", "popularity": 86},
    "Toothpaste": {"price": 120, "quantity": 50, "quality": "medium", "category": "personal", "cost_price": 80,
                   "min_stock": 12, "barcode": "890123456083", "popularity": 94},
    "Toothbrush": {"price": 80, "quantity": 45, "quality": "medium", "category": "personal", "cost_price": 50,
                   "min_stock": 10, "barcode": "890123456084", "popularity": 89},
    "Lotion": {"price": 180, "quantity": 30, "quality": "premium", "category": "personal", "cost_price": 120,
               "min_stock": 6, "barcode": "890123456085", "popularity": 88},
    "Sushi": {"price": 350, "quantity": 15, "quality": "premium", "category": "international", "cost_price": 220,
              "min_stock": 3, "barcode": "890123456056", "popularity": 87},
    "Pizza": {"price": 280, "quantity": 14, "quality": "premium", "category": "international", "cost_price": 180,
              "min_stock": 2, "barcode": "890123456057", "popularity": 92},
    "Tacos": {"price": 150, "quantity": 22, "quality": "high", "category": "international", "cost_price": 95,
              "min_stock": 4, "barcode": "890123456058", "popularity": 88},
    "Curry Paste": {"price": 120, "quantity": 30, "quality": "high", "category": "international", "cost_price": 80,
                    "min_stock": 6, "barcode": "890123456086", "popularity": 85},
    "Noodles": {"price": 65, "quantity": 50, "quality": "medium", "category": "international", "cost_price": 40,
                "min_stock": 10, "barcode": "890123456087", "popularity": 89}
}


# ==================== DATABASE INITIALIZATION ====================

def init_database():
    """Initialize database with tables and sample data"""
    with app.app_context():
        db.create_all()
        print(f"✅ Database tables created successfully at: {db_path}")

        admin = User.query.filter_by(username='admin').first()
        if not admin:
            admin = User(username='admin', name='Isaac Manager', email='isaac@supermarket.com', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("✅ Admin user created")
        else:
            admin.set_password('admin123')
            db.session.commit()
            print("✅ Admin password set to 'admin123'")

        if Product.query.count() == 0:
            print("🌱 Seeding products...")
            for name, details in INITIAL_PRODUCTS.items():
                product = Product(
                    name=name, price=details['price'], quantity=details['quantity'],
                    quality=details['quality'], category=details['category'],
                    cost_price=details['cost_price'], min_stock=details['min_stock'],
                    barcode=details['barcode'], popularity=details['popularity']
                )
                db.session.add(product)
            db.session.commit()
            print(f"✅ Seeded {len(INITIAL_PRODUCTS)} products")
        else:
            print(f"✅ Database already has {Product.query.count()} products")


# ==================== LOGIN HTML ====================

LOGIN_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Isaac Supermarket - Login</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
        }
        .login-container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            width: 100%;
            max-width: 420px;
            padding: 50px 40px;
            animation: slideUp 0.5s ease;
        }
        @keyframes slideUp {
            from { transform: translateY(30px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }
        .login-header {
            text-align: center;
            margin-bottom: 35px;
        }
        .login-header .logo { font-size: 48px; margin-bottom: 10px; }
        .login-header h1 { font-size: 28px; color: #1a1a2e; font-weight: 700; }
        .login-header p { color: #666; font-size: 14px; margin-top: 5px; }
        .form-group { margin-bottom: 20px; position: relative; }
        .form-group label { display: block; font-size: 14px; font-weight: 600; color: #333; margin-bottom: 6px; }
        .form-group .input-wrapper { position: relative; }
        .form-group .input-wrapper i { position: absolute; left: 15px; top: 50%; transform: translateY(-50%); color: #999; font-size: 16px; }
        .form-group input {
            width: 100%; padding: 14px 15px 14px 45px;
            border: 2px solid #e0e0e0; border-radius: 12px;
            font-size: 16px; transition: all 0.3s; background: #f8f9fa;
        }
        .form-group input:focus { border-color: #667eea; outline: none; background: white; box-shadow: 0 0 0 4px rgba(102,126,234,0.1); }
        .form-group .toggle-password {
            position: absolute; right: 15px; top: 50%; transform: translateY(-50%);
            cursor: pointer; color: #999; transition: 0.3s;
        }
        .form-group .toggle-password:hover { color: #667eea; }
        .remember-forgot {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 25px; font-size: 14px;
        }
        .remember-forgot label { display: flex; align-items: center; gap: 8px; color: #666; cursor: pointer; }
        .remember-forgot label input[type="checkbox"] { width: 16px; height: 16px; accent-color: #667eea; cursor: pointer; }
        .remember-forgot a { color: #667eea; text-decoration: none; font-weight: 600; }
        .remember-forgot a:hover { text-decoration: underline; }
        .btn-login {
            width: 100%; padding: 14px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; border: none; border-radius: 12px;
            font-size: 16px; font-weight: 600; cursor: pointer;
            transition: all 0.3s;
            display: flex; align-items: center; justify-content: center; gap: 10px;
        }
        .btn-login:hover { transform: translateY(-2px); box-shadow: 0 10px 30px rgba(102,126,234,0.4); }
        .login-footer { text-align: center; margin-top: 25px; color: #666; font-size: 14px; }
        .login-footer a { color: #667eea; text-decoration: none; font-weight: 600; }
        .flash-messages { margin-bottom: 20px; }
        .flash-message {
            padding: 12px 15px; border-radius: 10px; font-size: 14px;
            display: flex; align-items: center; gap: 10px; animation: slideIn 0.3s ease;
        }
        .flash-message.success { background: #d4edda; color: #155724; border-left: 4px solid #28a745; }
        .flash-message.danger { background: #f8d7da; color: #721c24; border-left: 4px solid #dc3545; }
        @keyframes slideIn {
            from { transform: translateX(-20px); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        @media (max-width: 480px) {
            .login-container { padding: 30px 20px; }
            .login-header h1 { font-size: 24px; }
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="login-header">
            <div class="logo">🏪</div>
            <h1>Isaac Supermarket</h1>
            <p>Management System Login</p>
        </div>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                <div class="flash-messages">
                    {% for category, message in messages %}
                        <div class="flash-message {{ category }}">
                            <i class="fas fa-{% if category == 'success' %}check-circle{% else %}exclamation-circle{% endif %}"></i>
                            {{ message }}
                        </div>
                    {% endfor %}
                </div>
            {% endif %}
        {% endwith %}
        <form method="POST" action="{{ url_for('login') }}">
            <div class="form-group">
                <label><i class="fas fa-user" style="color: #667eea;"></i> Username</label>
                <div class="input-wrapper">
                    <i class="fas fa-user"></i>
                    <input type="text" name="username" placeholder="Enter your username" required autofocus>
                </div>
            </div>
            <div class="form-group">
                <label><i class="fas fa-lock" style="color: #667eea;"></i> Password</label>
                <div class="input-wrapper">
                    <i class="fas fa-lock"></i>
                    <input type="password" name="password" placeholder="Enter your password" required>
                </div>
            </div>
            <button type="submit" class="btn-login">
                <i class="fas fa-sign-in-alt"></i> Login
            </button>
        </form>
        <div class="login-footer">
            <p>Don't have an account? <a href="{{ url_for('register') }}">Register here</a></p>
        </div>
    </div>
</body>
</html>
'''

REGISTER_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Isaac Supermarket - Register</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
        }
        .register-container {
            background: white; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            width: 100%; max-width: 440px; padding: 40px 35px;
        }
        .register-header { text-align: center; margin-bottom: 30px; }
        .register-header .logo { font-size: 40px; }
        .register-header h1 { font-size: 24px; color: #1a1a2e; }
        .register-header p { color: #666; font-size: 14px; }
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; font-size: 13px; font-weight: 600; color: #333; margin-bottom: 5px; }
        .form-group .input-wrapper { position: relative; }
        .form-group .input-wrapper i { position: absolute; left: 14px; top: 50%; transform: translateY(-50%); color: #999; font-size: 15px; }
        .form-group input {
            width: 100%; padding: 12px 14px 12px 42px;
            border: 2px solid #e0e0e0; border-radius: 10px;
            font-size: 15px; transition: all 0.3s; background: #f8f9fa;
        }
        .form-group input:focus { border-color: #667eea; outline: none; background: white; }
        .btn-register {
            width: 100%; padding: 14px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; border: none; border-radius: 10px;
            font-size: 16px; font-weight: 600; cursor: pointer; transition: all 0.3s; margin-top: 10px;
        }
        .btn-register:hover { transform: translateY(-2px); box-shadow: 0 10px 30px rgba(102,126,234,0.4); }
        .flash-messages { margin-bottom: 18px; }
        .flash-message {
            padding: 10px 14px; border-radius: 8px; font-size: 13px;
            display: flex; align-items: center; gap: 8px;
        }
        .flash-message.success { background: #d4edda; color: #155724; border-left: 4px solid #28a745; }
        .flash-message.danger { background: #f8d7da; color: #721c24; border-left: 4px solid #dc3545; }
        .register-footer { text-align: center; margin-top: 20px; color: #666; font-size: 14px; }
        .register-footer a { color: #667eea; text-decoration: none; font-weight: 600; }
    </style>
</head>
<body>
    <div class="register-container">
        <div class="register-header">
            <div class="logo">🏪</div>
            <h1>Create Account</h1>
            <p>Join Isaac Supermarket Management</p>
        </div>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                <div class="flash-messages">
                    {% for category, message in messages %}
                        <div class="flash-message {{ category }}">
                            <i class="fas fa-{% if category == 'success' %}check-circle{% else %}exclamation-circle{% endif %}"></i>
                            {{ message }}
                        </div>
                    {% endfor %}
                </div>
            {% endif %}
        {% endwith %}
        <form method="POST">
            <div class="form-group">
                <label><i class="fas fa-user" style="color: #667eea;"></i> Username</label>
                <div class="input-wrapper">
                    <i class="fas fa-user"></i>
                    <input type="text" name="username" placeholder="Choose a username" required>
                </div>
            </div>
            <div class="form-group">
                <label><i class="fas fa-envelope" style="color: #667eea;"></i> Email</label>
                <div class="input-wrapper">
                    <i class="fas fa-envelope"></i>
                    <input type="email" name="email" placeholder="Enter your email" required>
                </div>
            </div>
            <div class="form-group">
                <label><i class="fas fa-user-circle" style="color: #667eea;"></i> Full Name</label>
                <div class="input-wrapper">
                    <i class="fas fa-user-circle"></i>
                    <input type="text" name="fullname" placeholder="Enter your full name" required>
                </div>
            </div>
            <div class="form-group">
                <label><i class="fas fa-lock" style="color: #667eea;"></i> Password</label>
                <div class="input-wrapper">
                    <i class="fas fa-lock"></i>
                    <input type="password" name="password" placeholder="Min 4 characters" required>
                </div>
            </div>
            <div class="form-group">
                <label><i class="fas fa-check-circle" style="color: #667eea;"></i> Confirm Password</label>
                <div class="input-wrapper">
                    <i class="fas fa-check-circle"></i>
                    <input type="password" name="confirm_password" placeholder="Confirm your password" required>
                </div>
            </div>
            <button type="submit" class="btn-register">
                <i class="fas fa-user-plus"></i> Register
            </button>
        </form>
        <div class="register-footer">
            <p>Already have an account? <a href="{{ url_for('login') }}">Login here</a></p>
        </div>
    </div>
</body>
</html>
'''


# ==================== AUTH ROUTES ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip().lower()
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            user.last_login_ip = get_client_ip()
            user.last_login_time = datetime.now()
            db.session.commit()
            current_session['logged_in'] = True
            current_session['username'] = user.username
            current_session['role'] = user.role
            current_session['name'] = user.name or user.username
            log_activity(f"User {username} logged in", username)
            flash(f'Welcome back, {user.name or username}!', 'success')
            return redirect(url_for('index'))
        flash('Invalid username or password!', 'danger')
    return render_template_string(LOGIN_HTML)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip().lower()
        email = request.form['email'].strip()
        fullname = request.form['fullname'].strip()
        password = request.form['password']
        confirm = request.form['confirm_password']
        if not all([username, email, fullname, password]):
            flash('All fields are required!', 'danger')
            return redirect(url_for('register'))
        if password != confirm:
            flash('Passwords do not match!', 'danger')
            return redirect(url_for('register'))
        if len(password) < 4:
            flash('Password must be at least 4 characters!', 'danger')
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('Username already exists!', 'danger')
            return redirect(url_for('register'))
        new_user = User(username=username, email=email, name=fullname, role='staff')
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        log_activity(f"New user registered: {username}")
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    return render_template_string(REGISTER_HTML)


@app.route('/logout')
def logout():
    log_activity(f"User {current_session.get('username', 'unknown')} logged out")
    current_session.clear()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('login'))


@app.route('/change_password', methods=['GET', 'POST'])
@login_required()
def change_password():
    if request.method == 'POST':
        current = request.form['current_password']
        new = request.form['new_password']
        confirm = request.form['confirm_password']
        username = current_session.get('username')
        user = User.query.filter_by(username=username).first()
        if not user:
            flash('User not found!', 'danger')
            return redirect(url_for('change_password'))
        if not user.check_password(current):
            flash('Current password is incorrect!', 'danger')
            return redirect(url_for('change_password'))
        if new != confirm:
            flash('New passwords do not match!', 'danger')
            return redirect(url_for('change_password'))
        if len(new) < 4:
            flash('Password must be at least 4 characters!', 'danger')
            return redirect(url_for('change_password'))
        user.set_password(new)
        db.session.commit()
        log_activity(f"User {username} changed password")
        flash('Password changed! Please login again.', 'success')
        current_session.clear()
        return redirect(url_for('login'))

    CHANGE_PASSWORD_HTML = '''
    <!DOCTYPE html>
    <html>
    <head><title>Change Password</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; display: flex; justify-content: center; align-items: center; }
        .container { background: white; padding: 40px; border-radius: 20px; width: 420px; }
        h2 { text-align: center; color: #667eea; margin-bottom: 20px; }
        input { width: 100%; padding: 12px; margin: 10px 0; border: 2px solid #e0e0e0; border-radius: 10px; }
        input:focus { border-color: #667eea; outline: none; }
        button { width: 100%; padding: 12px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; border-radius: 10px; cursor: pointer; font-weight: bold; }
        .flash { background: #dc2626; color: white; padding: 10px; border-radius: 10px; margin-bottom: 15px; }
        .flash.success { background: #10b981; }
        .footer { text-align: center; margin-top: 15px; }
        .footer a { color: #667eea; text-decoration: none; }
    </style>
    </head>
    <body>
        <div class="container">
            <h2>🔑 Change Password</h2>
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="flash {{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            <form method="POST">
                <input type="password" name="current_password" placeholder="Current Password" required>
                <input type="password" name="new_password" placeholder="New Password" required>
                <input type="password" name="confirm_password" placeholder="Confirm Password" required>
                <button type="submit">Change Password</button>
            </form>
            <div class="footer"><a href="/">← Back to Dashboard</a></div>
        </div>
    </body>
    </html>
    '''
    return render_template_string(CHANGE_PASSWORD_HTML)


# ==================== MASTER TEMPLATE WITH FIXED SIDEBAR AND FULL TOP HEADER ====================

MASTER_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Isaac Supermarket - {{ title }}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html, body { 
            height: 100%; 
            overflow: hidden; 
        }
        body { 
            font-family: 'Segoe UI', Tahoma, sans-serif; 
            background: #f0f2f5; 
        }

        /* ===== SIDEBAR - FIXED AND UNSCROLLABLE ===== */
        .sidebar {
            position: fixed; 
            left: 0; 
            top: 0; 
            width: 280px; 
            height: 100vh; 
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: white; 
            z-index: 1000; 
            overflow: hidden;
            box-shadow: 2px 0 10px rgba(0,0,0,0.1);
            display: flex;
            flex-direction: column;
        }

        /* ===== SIDEBAR HEADER - FIXED AT TOP ===== */
        .sidebar-header {
            padding: 30px 20px;
            text-align: center;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            flex-shrink: 0;
        }
        .sidebar-header h2 {
            font-size: 22px;
            font-weight: bold;
            color: white;
            text-shadow: 0 2px 10px rgba(0,0,0,0.2);
        }
        .sidebar-header .subtitle {
            font-size: 13px;
            color: rgba(255,255,255,0.95);
            margin-top: 8px;
            font-weight: 600;
            background: rgba(0,0,0,0.15);
            padding: 8px 15px;
            border-radius: 20px;
            display: inline-block;
            letter-spacing: 1px;
        }
        .sidebar-header .subtitle i {
            margin-right: 6px;
        }

        /* ===== NAV MENU - SCROLLABLE INSIDE ===== */
        .nav-menu {
            flex: 1;
            overflow-y: auto;
            padding: 20px 0;
        }
        .nav-menu::-webkit-scrollbar {
            width: 5px;
        }
        .nav-menu::-webkit-scrollbar-track {
            background: rgba(255,255,255,0.05);
        }
        .nav-menu::-webkit-scrollbar-thumb {
            background: rgba(255,255,255,0.2);
            border-radius: 10px;
        }
        .nav-menu::-webkit-scrollbar-thumb:hover {
            background: rgba(255,255,255,0.3);
        }

        .nav-item {
            padding: 14px 25px; 
            margin: 5px 0; 
            display: flex; 
            align-items: center; 
            gap: 15px;
            cursor: pointer; 
            transition: all 0.3s; 
            color: rgba(255,255,255,0.8); 
            text-decoration: none;
        }
        .nav-item:hover { background: rgba(255,255,255,0.1); color: white; padding-left: 30px; }
        .nav-item.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; 
            border-radius: 10px; 
            margin: 5px 15px; 
            padding: 12px 15px;
        }
        .nav-item i { width: 24px; font-size: 18px; }
        .nav-item .badge { background: #dc2626; padding: 2px 8px; border-radius: 20px; font-size: 11px; margin-left: auto; }

        /* ===== MAIN CONTENT ===== */
        .main-content { 
            margin-left: 280px; 
            padding: 0;
            height: 100vh;
            overflow-y: auto;
            background: #f0f2f5;
        }
        .main-content::-webkit-scrollbar {
            width: 8px;
        }
        .main-content::-webkit-scrollbar-track {
            background: #f0f2f5;
        }
        .main-content::-webkit-scrollbar-thumb {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 10px;
        }
        .main-content::-webkit-scrollbar-thumb:hover {
            background: linear-gradient(135deg, #764ba2 0%, #667eea 100%);
        }

        /* ===== CONTENT WRAPPER ===== */
        .content-wrapper {
            padding: 20px;
        }

        /* ===== TOP HEADER - FULL WIDTH COVERING TOP MARGIN ===== */
        .top-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 15px 25px;
            display: flex; 
            justify-content: space-between; 
            align-items: center;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15); 
            color: white;
            position: sticky; 
            top: 0; 
            z-index: 100;
            margin: 0;
            width: 100%;
        }
        .top-header .page-title h1 { font-size: 24px; }
        .top-header .page-title p { color: rgba(255,255,255,0.8); font-size: 14px; margin-top: 5px; }
        .top-header .user-info { display: flex; align-items: center; gap: 20px; }
        .top-header .user-profile { display: flex; align-items: center; gap: 10px; cursor: pointer; }
        .top-header .user-profile .avatar {
            width: 40px; height: 40px; background: rgba(255,255,255,0.2);
            border-radius: 50%; display: flex; align-items: center; justify-content: center;
        }
        .top-header .user-profile .name { font-weight: 600; }
        .top-header .user-profile .role { font-size: 12px; color: rgba(255,255,255,0.8); }

        /* ===== STATS GRID ===== */
        .stats-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px; margin-bottom: 30px;
        }
        .stat-card {
            background: white; padding: 20px; border-radius: 15px;
            display: flex; justify-content: space-between; align-items: center;
            transition: all 0.3s; cursor: pointer; box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }
        .stat-card:hover { transform: translateY(-5px); box-shadow: 0 5px 20px rgba(0,0,0,0.1); }
        .stat-info h3 { font-size: 28px; font-weight: bold; color: #1a1a2e; }
        .stat-info p { color: #666; font-size: 14px; margin-top: 5px; }
        .stat-icon {
            width: 50px; height: 50px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 12px; display: flex; align-items: center; justify-content: center;
            color: white; font-size: 24px;
        }

        .chart-container { background: white; padding: 20px; border-radius: 15px; margin-bottom: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }
        .chart-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .products-section {
            background: white; border-radius: 15px; padding: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }
        .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 10px; }
        .section-header h3 { color: #1a1a2e; }
        .search-box { display: flex; gap: 10px; flex-wrap: wrap; }
        .search-box input { padding: 10px 15px; border: 1px solid #ddd; border-radius: 10px; width: 250px; }
        .btn-add {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; padding: 10px 20px; border-radius: 10px; text-decoration: none;
            display: inline-flex; align-items: center; gap: 8px;
        }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #eee; }
        th { background: #f8f9fa; color: #1a1a2e; font-weight: 600; }
        .low-stock { color: #dc2626; font-weight: bold; }
        .status-badge { padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; display: inline-block; }
        .status-high { background: #d4edda; color: #155724; }
        .status-low { background: #f8d7da; color: #721c24; }
        .btn-edit { background: #667eea; color: white; padding: 5px 10px; border-radius: 5px; text-decoration: none; font-size: 12px; }
        .btn-delete { background: #dc2626; color: white; padding: 5px 10px; border-radius: 5px; text-decoration: none; font-size: 12px; margin-left: 5px; }
        .restock-form { display: inline-block; margin-left: 5px; }
        .restock-input { width: 60px; padding: 5px; border: 1px solid #ddd; border-radius: 5px; }
        .restock-btn { background: #10b981; color: white; border: none; padding: 5px 10px; border-radius: 5px; cursor: pointer; }
        .flash {
            position: fixed; top: 20px; right: 20px; padding: 15px 20px; border-radius: 10px;
            z-index: 1100; animation: slideIn 0.3s ease;
        }
        .flash.success { background: #10b981; color: white; }
        .flash.danger { background: #dc2626; color: white; }
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }

        /* ===== CUSTOMER SECTION ===== */
        .customer-section {
            background: white; padding: 20px; border-radius: 15px; margin-bottom: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }
        .customer-section h3 { margin-bottom: 15px; color: #1a1a2e; }
        .customer-section input {
            padding: 10px; border: 1px solid #ddd; border-radius: 8px; width: 100%;
        }
        .transaction {
            background: white; padding: 15px; margin-bottom: 10px; border-radius: 10px;
            border-left: 4px solid #667eea;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }
        .customer-card {
            background: white; padding: 20px; margin-bottom: 10px; border-radius: 10px;
            border-left: 4px solid #10b981;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }
        .log-entry {
            background: white; padding: 10px; margin-bottom: 5px; border-radius: 10px;
            border-left: 4px solid #f59e0b;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }
        .alert-item {
            background: #fff3cd; border-left: 4px solid #dc2626; padding: 15px;
            margin-bottom: 15px; border-radius: 10px;
        }
        .product-item {
            background: white; padding: 15px; margin-bottom: 10px; border-radius: 10px;
            display: flex; justify-content: space-between; align-items: center;
            border: 1px solid #e0e0e0;
        }

        @media (max-width: 768px) {
            .sidebar { left: -280px; }
            .main-content { margin-left: 0; }
            .stats-grid { grid-template-columns: 1fr; }
            .search-box input { width: 100%; }
            .top-header { flex-direction: column; text-align: center; gap: 10px; }
        }
    </style>
</head>
<body>
    <div class="sidebar">
        <div class="sidebar-header">
            <h2>🏪 ISAAC MARKET</h2>
            <div class="subtitle">
                <i class="fas fa-rocket"></i> Start Your Business to Make a Living
            </div>
        </div>
        <div class="nav-menu">
            <a href="/" class="nav-item {% if active_page == 'dashboard' %}active{% endif %}">
                <i class="fas fa-tachometer-alt"></i><span>Dashboard</span>
            </a>
            <a href="/products" class="nav-item {% if active_page == 'products' %}active{% endif %}">
                <i class="fas fa-boxes"></i><span>Inventory</span><span class="badge">{{ total_products }}</span>
            </a>
            <a href="/sale" class="nav-item {% if active_page == 'sale' %}active{% endif %}">
                <i class="fas fa-shopping-cart"></i><span>Sales</span>
            </a>
            <a href="/invoices" class="nav-item {% if active_page == 'invoices' %}active{% endif %}">
                <i class="fas fa-file-invoice"></i><span>Invoices</span>
            </a>
            <a href="/customers" class="nav-item {% if active_page == 'customers' %}active{% endif %}">
                <i class="fas fa-users"></i><span>Customers</span>
            </a>
            <a href="/report" class="nav-item {% if active_page == 'report' %}active{% endif %}">
                <i class="fas fa-chart-line"></i><span>Reports</span>
            </a>
            <a href="/low_stock" class="nav-item {% if active_page == 'low_stock' %}active{% endif %}">
                <i class="fas fa-exclamation-triangle"></i><span>Low Stock</span><span class="badge">{{ low_stock_count }}</span>
            </a>
            <a href="/best_sellers" class="nav-item {% if active_page == 'best_sellers' %}active{% endif %}">
                <i class="fas fa-trophy"></i><span>Best Sellers</span>
            </a>
            <a href="/activity" class="nav-item {% if active_page == 'activity' %}active{% endif %}">
                <i class="fas fa-history"></i><span>Activity Log</span>
            </a>
            <a href="/database-info" class="nav-item {% if active_page == 'database' %}active{% endif %}">
                <i class="fas fa-database"></i><span>Database</span>
            </a>
            {% if session_role == 'admin' %}
            <a href="/users" class="nav-item {% if active_page == 'users' %}active{% endif %}">
                <i class="fas fa-user-shield"></i><span>Users</span>
            </a>
            {% endif %}
            <a href="/change_password" class="nav-item">
                <i class="fas fa-key"></i><span>Change Password</span>
            </a>
            <a href="/logout" class="nav-item">
                <i class="fas fa-sign-out-alt"></i><span>Logout</span>
            </a>
        </div>
    </div>
    <div class="main-content">
        <div class="top-header">
            <div class="page-title">
                <h1>{{ page_title }}</h1>
                <p>{{ page_subtitle }}</p>
            </div>
            <div class="user-info">
                <div class="user-profile">
                    <div class="avatar"><i class="fas fa-user"></i></div>
                    <div>
                        <div class="name">{{ session_name }}</div>
                        <div class="role">{{ session_role|capitalize }}</div>
                    </div>
                </div>
            </div>
        </div>
        <div class="content-wrapper">
            {{ content|safe }}
        </div>
    </div>
    {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
            {% for category, message in messages %}
                <div class="flash {{ category }}">{{ message }}</div>
            {% endfor %}
        {% endif %}
    {% endwith %}
    <script>
        setTimeout(function() {
            document.querySelectorAll('.flash').forEach(function(el) { el.remove(); });
        }, 5000);
    </script>
</body>
</html>
'''


# ==================== DASHBOARD ====================

@app.route('/')
@login_required()
def index():
    daily_sales_data = get_daily_sales_last_7_days()
    products_list = Product.query.all()
    total_products = len(products_list)
    low_stock_count = Product.query.filter(Product.quantity < Product.min_stock).count()
    total_stock = get_total_stock()
    total_sales = get_total_sales()
    total_profit = get_total_profit()

    content = f'''
    <div class="stats-grid">
        <div class="stat-card" onclick="location.href='/products'">
            <div class="stat-info"><h3>{total_products}</h3><p>Total Products</p></div>
            <div class="stat-icon"><i class="fas fa-box"></i></div>
        </div>
        <div class="stat-card" onclick="location.href='/low_stock'">
            <div class="stat-info"><h3>{total_stock}</h3><p>Total Stock</p></div>
            <div class="stat-icon"><i class="fas fa-cubes"></i></div>
        </div>
        <div class="stat-card" onclick="location.href='/report'">
            <div class="stat-info"><h3>{total_sales:,} KES</h3><p>Total Sales</p></div>
            <div class="stat-icon"><i class="fas fa-currency-dollar"></i></div>
        </div>
        <div class="stat-card">
            <div class="stat-info"><h3>{total_profit:,} KES</h3><p>Total Profit</p></div>
            <div class="stat-icon"><i class="fas fa-chart-line"></i></div>
        </div>
    </div>
    <div class="chart-container">
        <div class="chart-header"><h3>📊 Sales Overview (Last 7 Days)</h3></div>
        <canvas id="salesChart" style="max-height:300px;width:100%;"></canvas>
    </div>
    <div class="products-section">
        <div class="section-header">
            <h3>🛒 Recent Products</h3>
            <div class="search-box">
                <input type="text" id="searchInput" placeholder="Search products..." onkeyup="searchProducts()">
                <a href="/add_product" class="btn-add"><i class="fas fa-plus"></i> Add Product</a>
            </div>
        </div>
        <div style="overflow-x:auto;">
            <table>
                <thead><tr><th>Product</th><th>Price</th><th>Quantity</th><th>Quality</th><th>Category</th><th>Status</th></tr></thead>
                <tbody>
    '''
    for p in products_list[:10]:
        low_class = 'low-stock' if p.quantity < p.min_stock else ''
        status_badge = '<span class="status-badge status-low">⚠️ Low Stock</span>' if p.quantity < p.min_stock else '<span class="status-badge status-high">✓ In Stock</span>'
        content += f'<tr class="product-row"><td><strong>{p.name}</strong></td><td>{p.price} KES</td><td class="{low_class}">{p.quantity} / {p.min_stock}</td><td><span class="status-badge status-{"high" if p.quality == "high" else "low"}">{p.quality.capitalize()}</span></td><td>{p.category.capitalize()}</td><td>{status_badge}</td></tr>'

    content += '''
                </tbody>
            </table>
        </div>
    </div>
    <script>
        function searchProducts() {
            var input = document.getElementById('searchInput').value.toLowerCase();
            var rows = document.getElementsByClassName('product-row');
            for (var i = 0; i < rows.length; i++) {
                rows[i].style.display = rows[i].textContent.toLowerCase().includes(input) ? '' : 'none';
            }
        }
        var ctx = document.getElementById('salesChart').getContext('2d');
        new Chart(ctx, {
            type: 'line',
            data: {
                labels: ''' + str(list(daily_sales_data.keys())) + ''',
                datasets: [{
                    label: 'Daily Sales (KES)',
                    data: ''' + str(list(daily_sales_data.values())) + ''',
                    borderColor: '#667eea',
                    backgroundColor: 'rgba(102,126,234,0.1)',
                    tension: 0.4,
                    fill: true,
                    pointBackgroundColor: '#667eea',
                    pointBorderColor: 'white',
                    pointRadius: 5
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: { legend: { position: 'top' } },
                scales: { y: { beginAtZero: true, ticks: { callback: function(value) { return value + ' KES'; } } } }
            }
        });
    </script>
    '''

    return render_template_string(MASTER_TEMPLATE,
                                  title="Dashboard",
                                  page_title=f"Welcome back, {current_session.get('name', 'User')}!",
                                  page_subtitle="Here's what's happening with your store today.",
                                  content=content,
                                  active_page="dashboard",
                                  total_products=total_products,
                                  low_stock_count=low_stock_count,
                                  session_name=current_session.get('name', 'User'),
                                  session_role=current_session.get('role', 'staff')
                                  )


# ==================== PRODUCT ROUTES ====================

@app.route('/products')
@login_required()
def view_products():
    products_list = Product.query.all()
    total_products = len(products_list)
    total_stock = get_total_stock()
    low_stock_count = Product.query.filter(Product.quantity < Product.min_stock).count()
    is_admin = current_session.get('role') == 'admin'

    content = f'''
    <div class="stats-grid">
        <div class="stat-card"><div class="stat-info"><h3>{total_products}</h3><p>Total Products</p></div><div class="stat-icon"><i class="fas fa-box"></i></div></div>
        <div class="stat-card"><div class="stat-info"><h3>{total_stock}</h3><p>Total Stock</p></div><div class="stat-icon"><i class="fas fa-cubes"></i></div></div>
    </div>
    <div class="products-section">
        <div class="section-header">
            <h3>📦 All Products ({total_products} items)</h3>
            '''
    if is_admin:
        content += '<a href="/add_product" class="btn-add"><i class="fas fa-plus"></i> Add Product</a>'
    content += '''
        </div>
        <div style="overflow-x:auto;">
            <table>
                <thead><tr><th>Product</th><th>Price</th><th>Quantity</th><th>Quality</th><th>Category</th><th>Cost Price</th><th>Profit</th><th>Actions</th></tr></thead>
                <tbody>
    '''
    for p in products_list:
        low_class = 'low-stock' if p.quantity < p.min_stock else ''
        profit = p.price - p.cost_price
        restock_form = ''
        if is_admin:
            restock_form = f'<form action="/restock/{p.name}" method="POST" class="restock-form"><input type="number" name="quantity" class="restock-input" placeholder="Qty" min="1"><button type="submit" class="restock-btn">Restock</button></form>'
        content += f'''
        <tr>
            <td><strong>{p.name}</strong></td>
            <td>{p.price} KES</td>
            <td class="{low_class}">{p.quantity} / {p.min_stock}</td>
            <td><span class="status-badge status-{"high" if p.quality == "high" else "low"}">{p.quality.capitalize()}</span></td>
            <td>{p.category.capitalize()}</td>
            <td>{p.cost_price} KES</td>
            <td>{profit} KES</td>
            <td>
        '''
        if is_admin:
            content += f'<a href="/edit_product/{p.name}" class="btn-edit">Edit</a> <a href="/delete_product/{p.name}" class="btn-delete" onclick="return confirm(\'Delete {p.name}?\')">Delete</a> {restock_form}'
        else:
            content += '<span style="color:#999;">Read only</span>'
        content += '</td></tr>'

    content += '''
                </tbody>
            </table>
        </div>
    </div>
    '''

    return render_template_string(MASTER_TEMPLATE,
                                  title="Inventory",
                                  page_title="📦 Inventory Management",
                                  page_subtitle=f"Manage your {total_products} products, track stock levels, and update prices.",
                                  content=content,
                                  active_page="products",
                                  total_products=total_products,
                                  low_stock_count=low_stock_count,
                                  session_name=current_session.get('name', 'User'),
                                  session_role=current_session.get('role', 'staff')
                                  )


@app.route('/add_product', methods=['GET', 'POST'])
@login_required(role='admin')
def add_product():
    if request.method == 'POST':
        name = request.form['name'].strip()
        if Product.query.filter_by(name=name).first():
            flash('Product already exists!', 'danger')
            return redirect(url_for('add_product'))
        qty = int(request.form['quantity'])
        if qty <= 0:
            flash('Quantity must be greater than 0!', 'danger')
            return redirect(url_for('add_product'))
        product = Product(
            name=name, price=int(request.form['price']), quantity=qty,
            quality=request.form['quality'], category=request.form['category'],
            cost_price=int(request.form['cost_price']), min_stock=int(request.form['min_stock']),
            barcode=request.form.get('barcode', f"BAR{int(datetime.now().timestamp())}")
        )
        db.session.add(product)
        db.session.commit()
        log_activity(f"Added new product: {name}")
        flash(f'Product "{name}" added successfully!', 'success')
        return redirect(url_for('view_products'))

    ADD_FORM = '''
    <div class="products-section">
        <div class="section-header"><h3>➕ Add New Product</h3></div>
        <form method="POST" style="max-width:600px;margin:0 auto;">
            <div style="margin-bottom:15px;"><label>Product Name</label><input type="text" name="name" required style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;"></div>
            <div style="margin-bottom:15px;"><label>Barcode (Optional)</label><input type="text" name="barcode" placeholder="Optional" style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;"></div>
            <div style="margin-bottom:15px;"><label>Price (KES)</label><input type="number" name="price" required style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;"></div>
            <div style="margin-bottom:15px;"><label>Cost Price (KES)</label><input type="number" name="cost_price" required style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;"></div>
            <div style="margin-bottom:15px;"><label>Quantity</label><input type="number" name="quantity" required min="1" style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;"></div>
            <div style="margin-bottom:15px;"><label>Minimum Stock Level</label><input type="number" name="min_stock" value="5" style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;"></div>
            <div style="margin-bottom:15px;"><label>Quality</label>
                <select name="quality" style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;">
                    <option>low</option><option selected>medium</option><option>high</option><option>premium</option>
                </select>
            </div>
            <div style="margin-bottom:15px;"><label>Category</label>
                <select name="category" style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;">
                    <option>cooking</option><option>fruits</option><option>vegetables</option><option>dairy</option>
                    <option>bakery</option><option>beverages</option><option>snacks</option><option>household</option>
                    <option>personal</option><option>international</option>
                </select>
            </div>
            <button type="submit" style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;padding:12px 30px;border:none;border-radius:8px;cursor:pointer;width:100%;">Add Product</button>
        </form>
    </div>
    '''

    total_products = Product.query.count()
    low_stock_count = Product.query.filter(Product.quantity < Product.min_stock).count()
    return render_template_string(MASTER_TEMPLATE,
                                  title="Add Product",
                                  page_title="➕ Add New Product",
                                  page_subtitle="Add a new product to your inventory.",
                                  content=ADD_FORM,
                                  active_page="products",
                                  total_products=total_products,
                                  low_stock_count=low_stock_count,
                                  session_name=current_session.get('name', 'User'),
                                  session_role=current_session.get('role', 'staff')
                                  )


@app.route('/edit_product/<name>', methods=['GET', 'POST'])
@login_required(role='admin')
def edit_product(name):
    product = Product.query.filter_by(name=name).first()
    if not product:
        flash('Product not found!', 'danger')
        return redirect(url_for('view_products'))
    if request.method == 'POST':
        qty = int(request.form['quantity'])
        if qty < 0:
            flash('Quantity cannot be negative!', 'danger')
            return redirect(url_for('edit_product', name=name))
        product.price = int(request.form['price'])
        product.quantity = qty
        product.quality = request.form['quality']
        product.category = request.form['category']
        product.cost_price = int(request.form['cost_price'])
        product.min_stock = int(request.form['min_stock'])
        product.barcode = request.form.get('barcode', product.barcode)
        db.session.commit()
        flash(f'Product "{name}" updated!', 'success')
        return redirect(url_for('view_products'))

    EDIT_FORM = f'''
    <div class="products-section">
        <div class="section-header"><h3>✏️ Edit Product: {name}</h3></div>
        <form method="POST" style="max-width:600px;margin:0 auto;">
            <div style="margin-bottom:15px;"><label>Barcode</label><input type="text" name="barcode" value="{product.barcode or ''}" style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;"></div>
            <div style="margin-bottom:15px;"><label>Price (KES)</label><input type="number" name="price" value="{product.price}" required style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;"></div>
            <div style="margin-bottom:15px;"><label>Cost Price (KES)</label><input type="number" name="cost_price" value="{product.cost_price}" required style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;"></div>
            <div style="margin-bottom:15px;"><label>Quantity</label><input type="number" name="quantity" value="{product.quantity}" required min="0" style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;"></div>
            <div style="margin-bottom:15px;"><label>Minimum Stock Level</label><input type="number" name="min_stock" value="{product.min_stock}" style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;"></div>
            <div style="margin-bottom:15px;"><label>Quality</label>
                <select name="quality" style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;">
                    <option {"selected" if product.quality == 'low' else ""}>low</option>
                    <option {"selected" if product.quality == 'medium' else ""}>medium</option>
                    <option {"selected" if product.quality == 'high' else ""}>high</option>
                    <option {"selected" if product.quality == 'premium' else ""}>premium</option>
                </select>
            </div>
            <div style="margin-bottom:15px;"><label>Category</label>
                <select name="category" style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;">
                    <option {"selected" if product.category == 'cooking' else ""}>cooking</option>
                    <option {"selected" if product.category == 'fruits' else ""}>fruits</option>
                    <option {"selected" if product.category == 'vegetables' else ""}>vegetables</option>
                    <option {"selected" if product.category == 'dairy' else ""}>dairy</option>
                    <option {"selected" if product.category == 'bakery' else ""}>bakery</option>
                    <option {"selected" if product.category == 'beverages' else ""}>beverages</option>
                    <option {"selected" if product.category == 'snacks' else ""}>snacks</option>
                    <option {"selected" if product.category == 'household' else ""}>household</option>
                    <option {"selected" if product.category == 'personal' else ""}>personal</option>
                    <option {"selected" if product.category == 'international' else ""}>international</option>
                </select>
            </div>
            <button type="submit" style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;padding:12px 30px;border:none;border-radius:8px;cursor:pointer;width:100%;">Update Product</button>
        </form>
    </div>
    '''

    total_products = Product.query.count()
    low_stock_count = Product.query.filter(Product.quantity < Product.min_stock).count()
    return render_template_string(MASTER_TEMPLATE,
                                  title="Edit Product",
                                  page_title="✏️ Edit Product",
                                  page_subtitle=f"Editing {name}",
                                  content=EDIT_FORM,
                                  active_page="products",
                                  total_products=total_products,
                                  low_stock_count=low_stock_count,
                                  session_name=current_session.get('name', 'User'),
                                  session_role=current_session.get('role', 'staff')
                                  )


@app.route('/delete_product/<name>')
@login_required(role='admin')
def delete_product(name):
    product = Product.query.filter_by(name=name).first()
    if product:
        db.session.delete(product)
        db.session.commit()
        log_activity(f"Deleted product: {name}")
        flash(f'Product "{name}" deleted!', 'success')
    return redirect(url_for('view_products'))


@app.route('/restock/<name>', methods=['POST'])
@login_required(role='admin')
def restock_product(name):
    product = Product.query.filter_by(name=name).first()
    if product:
        qty = int(request.form['quantity'])
        if qty > 0:
            product.quantity += qty
            db.session.commit()
            log_activity(f"Restocked {qty} units of {name}")
            flash(f'Restocked {qty} units of {name}!', 'success')
        else:
            flash('Quantity must be positive!', 'danger')
    return redirect(request.referrer or url_for('view_products'))


# ==================== SALE ROUTE ====================

@app.route('/sale', methods=['GET', 'POST'])
@login_required()
def record_sale():
    if request.method == 'POST':
        cart = []
        total_before_discount = 0
        items = request.form.getlist('cart_items[]')
        quantities = request.form.getlist('quantities[]')
        customer_phone = request.form.get('customer_phone', '')
        customer_name = request.form.get('customer_name', '')
        customer_email = request.form.get('customer_email', '')
        payment_method = request.form.get('payment_method', 'Cash')
        payment_reference = request.form.get('payment_reference', '')
        discount_type = request.form.get('discount_type', 'fixed')
        discount_value = int(request.form.get('discount_value', 0))

        if payment_method in ['M-Pesa', 'Paybill'] and not payment_reference:
            flash(f'Please provide a transaction reference for {payment_method} payment.', 'danger')
            return redirect(url_for('record_sale'))

        if customer_phone:
            customer = Customer.query.get(customer_phone)
            if not customer:
                customer = Customer(phone=customer_phone, name=customer_name or 'Unknown', email=customer_email or '',
                                    registered_ip=get_client_ip())
                db.session.add(customer)
                db.session.commit()

        for i, name in enumerate(items):
            if name:
                product = Product.query.filter_by(name=name).first()
                if product:
                    qty = int(quantities[i]) if i < len(quantities) and quantities[i] else 0
                    if qty > 0 and qty <= product.quantity:
                        cost = product.price * qty
                        cart.append({'name': name, 'quantity': qty, 'cost': cost, 'price': product.price})
                        total_before_discount += cost
                        product.quantity -= qty

        if cart:
            discount_amount = 0
            if discount_type == 'percent' and discount_value > 0:
                discount_amount = int(total_before_discount * discount_value / 100)
            elif discount_type == 'fixed':
                discount_amount = min(discount_value, total_before_discount)
            after_discount = total_before_discount - discount_amount
            tax_amount = int(after_discount * TAX_RATE)
            total_after_tax = after_discount + tax_amount

            transaction_id = f"INV-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            transaction = SaleTransaction(
                id=transaction_id, timestamp=datetime.now(), subtotal=total_before_discount,
                discount_amount=discount_amount, discount_type=discount_type, discount_value=discount_value,
                tax_amount=tax_amount, total=total_after_tax,
                customer_phone=customer_phone, customer_name=customer_name, customer_email=customer_email,
                ip_address=get_client_ip(), payment_method=payment_method,
                payment_reference=payment_reference if payment_reference else None
            )
            db.session.add(transaction)
            for item in cart:
                sale_item = SaleItem(transaction_id=transaction_id, product_name=item['name'],
                                     quantity=item['quantity'], unit_price=item['price'], total_cost=item['cost'])
                db.session.add(sale_item)
            if customer_phone:
                customer = Customer.query.get(customer_phone)
                if customer:
                    customer.total_spent += total_after_tax
                    customer.visits += 1
                    customer.last_purchase = datetime.now()
            db.session.commit()
            log_activity(f"Sale recorded: {len(cart)} items, total {total_after_tax} KES, payment: {payment_method}")

            receipt_items = ''.join([
                                        f'<div class="item"><span>{item["quantity"]} x {remove_emoji(item["name"])}</span><span>{item["cost"]} KES</span></div>'
                                        for item in cart])
            discount_line = f'<div class="item"><span>Discount ({discount_type} {discount_value}):</span><span>-{discount_amount} KES</span></div>' if discount_amount > 0 else ''
            content = f'''
            <div style="max-width:500px;margin:0 auto;background:white;padding:30px;border-radius:20px;box-shadow:0 2px 10px rgba(0,0,0,0.1);">
                <h2 style="text-align:center;">🏪 ISAAC SUPERMARKET</h2>
                <p style="text-align:center;color:#666;">{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
                <p style="text-align:center;"><strong>Payment:</strong> {payment_method} {f'({payment_reference})' if payment_reference else ''}</p>
                <hr style="margin:15px 0;">
                {receipt_items}
                <hr style="margin:10px 0;">
                <div class="item" style="display:flex;justify-content:space-between;padding:5px 0;"><span>Subtotal:</span><span>{total_before_discount} KES</span></div>
                {discount_line}
                <div class="item" style="display:flex;justify-content:space-between;padding:5px 0;"><span>VAT ({int(TAX_RATE * 100)}%):</span><span>{tax_amount} KES</span></div>
                <div class="total" style="font-size:24px;font-weight:bold;margin-top:10px;padding-top:10px;border-top:2px solid #667eea;display:flex;justify-content:space-between;">
                    <span>TOTAL:</span><span>{total_after_tax} KES</span>
                </div>
                <div style="margin-top:20px;">
                    <button onclick="window.print()" style="background:#667eea;color:white;padding:12px;border:none;border-radius:10px;cursor:pointer;width:100%;">🖨️ Print Receipt</button>
                    <a href="/" style="display:block;text-align:center;margin-top:10px;color:#667eea;">Back to Dashboard</a>
                </div>
            </div>
            '''

            total_products = Product.query.count()
            low_stock_count = Product.query.filter(Product.quantity < Product.min_stock).count()
            return render_template_string(MASTER_TEMPLATE,
                                          title="Receipt",
                                          page_title="🧾 Sales Receipt",
                                          page_subtitle="Thank you for shopping with us!",
                                          content=content,
                                          active_page="sale",
                                          total_products=total_products,
                                          low_stock_count=low_stock_count,
                                          session_name=current_session.get('name', 'User'),
                                          session_role=current_session.get('role', 'staff')
                                          )
        else:
            flash('No valid items selected!', 'danger')

    products_list = Product.query.order_by(Product.name).all()
    product_items_html = ''
    for p in products_list:
        product_items_html += f'''
        <div class="product-item" data-name="{p.name.lower()}">
            <div><strong>{p.name}</strong><br>{p.price} KES | Stock: {p.quantity}<br><small>Barcode: {p.barcode or 'N/A'}</small></div>
            <div>
                <input type="number" class="qty-input" name="quantities[]" placeholder="Qty" min="0" max="{p.quantity}" value="0" style="width:100px;padding:8px;border:1px solid #ddd;border-radius:8px;">
                <input type="hidden" name="cart_items[]" value="{p.name}">
            </div>
        </div>
        '''

    content = f'''
    <div class="customer-section">
        <h3>👤 Customer Information</h3>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;">
            <input type="text" id="customerPhone" placeholder="📞 Phone Number">
            <input type="text" id="customerName" placeholder="👤 Customer Name">
            <input type="email" id="customerEmail" placeholder="📧 Email Address">
        </div>
    </div>

    <div class="customer-section">
        <h3>💳 Payment Method</h3>
        <div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap;">
            <label><input type="radio" name="payment_method" value="Cash" checked> Cash</label>
            <label><input type="radio" name="payment_method" value="M-Pesa"> M-Pesa</label>
            <label><input type="radio" name="payment_method" value="Paybill"> Paybill</label>
            <div id="refDiv" style="display:none; margin-left:20px;">
                <input type="text" id="paymentReference" placeholder="M-Pesa: Phone / Paybill: Account" style="padding:8px;border:1px solid #ddd;border-radius:8px;width:250px;">
            </div>
        </div>
    </div>

    <div class="customer-section">
        <h3>💰 Discount</h3>
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
            <select id="discountType" style="padding:8px;border-radius:8px;border:1px solid #ddd;">
                <option value="fixed">Fixed Amount (KES)</option>
                <option value="percent">Percentage (%)</option>
            </select>
            <input type="number" id="discountValue" placeholder="Amount" value="0" style="padding:8px;border-radius:8px;border:1px solid #ddd;width:150px;">
        </div>
    </div>

    <div class="customer-section">
        <h3>🔍 Search Products</h3>
        <input type="text" id="searchBox" placeholder="Type product name..." style="width:100%;padding:10px;border-radius:8px;border:1px solid #ddd;">
    </div>

    <form method="POST" id="saleForm">
        <input type="hidden" name="customer_phone" id="customerPhoneInput">
        <input type="hidden" name="customer_name" id="customerNameInput">
        <input type="hidden" name="customer_email" id="customerEmailInput">
        <input type="hidden" name="payment_method" id="paymentMethodInput">
        <input type="hidden" name="payment_reference" id="paymentRefInput">
        <input type="hidden" name="discount_type" id="discountTypeInput">
        <input type="hidden" name="discount_value" id="discountValueInput">
        <div id="productsContainer">
            {product_items_html}
        </div>
        <button type="submit" style="margin-top:20px;width:100%;">💳 Complete Purchase</button>
    </form>

    <script>
        const searchBox = document.getElementById('searchBox');
        const productItems = document.querySelectorAll('.product-item');

        searchBox.addEventListener('input', function() {{
            const term = this.value.toLowerCase();
            productItems.forEach(item => {{
                const name = item.getAttribute('data-name');
                item.style.display = name.includes(term) ? 'flex' : 'none';
            }});
        }});

        function togglePaymentRef() {{
            var radios = document.getElementsByName('payment_method');
            var refDiv = document.getElementById('refDiv');
            var selected = null;
            for (var i = 0; i < radios.length; i++) {{
                if (radios[i].checked) {{ selected = radios[i].value; break; }}
            }}
            refDiv.style.display = (selected === 'M-Pesa' || selected === 'Paybill') ? 'inline-block' : 'none';
        }}
        document.querySelectorAll('input[name="payment_method"]').forEach(el => el.addEventListener('change', togglePaymentRef));
        togglePaymentRef();

        document.getElementById('saleForm').onsubmit = function() {{
            document.getElementById('customerPhoneInput').value = document.getElementById('customerPhone').value;
            document.getElementById('customerNameInput').value = document.getElementById('customerName').value;
            document.getElementById('customerEmailInput').value = document.getElementById('customerEmail').value;
            var radios = document.getElementsByName('payment_method');
            for (var i = 0; i < radios.length; i++) {{
                if (radios[i].checked) {{
                    document.getElementById('paymentMethodInput').value = radios[i].value;
                    break;
                }}
            }}
            document.getElementById('paymentRefInput').value = document.getElementById('paymentReference').value;
            document.getElementById('discountTypeInput').value = document.getElementById('discountType').value;
            document.getElementById('discountValueInput').value = document.getElementById('discountValue').value;
            return true;
        }};
    </script>
    '''

    total_products = len(products_list)
    low_stock_count = Product.query.filter(Product.quantity < Product.min_stock).count()
    return render_template_string(MASTER_TEMPLATE,
                                  title="Record Sale",
                                  page_title="🛒 Record Sale",
                                  page_subtitle="Select products, apply discount, choose payment method, and complete sale.",
                                  content=content,
                                  active_page="sale",
                                  total_products=total_products,
                                  low_stock_count=low_stock_count,
                                  session_name=current_session.get('name', 'User'),
                                  session_role=current_session.get('role', 'staff')
                                  )


# ==================== INVOICES ====================

@app.route('/invoices')
@login_required()
def invoices_list():
    transactions = SaleTransaction.query.order_by(SaleTransaction.timestamp.desc()).all()
    transactions_html = ''
    for trans in transactions:
        items_html = ''.join([f'{item.quantity} x {remove_emoji(item.product_name)}<br>' for item in trans.items])
        transactions_html += f'''
        <div class="transaction">
            <strong>🧾 {trans.id}</strong> | 📅 {trans.timestamp.strftime("%Y-%m-%d %H:%M:%S")}
            <br>{items_html}
            <strong>Subtotal: {trans.subtotal} KES</strong>
            <br>{"Discount: -" + str(trans.discount_amount) + " KES<br>" if trans.discount_amount > 0 else ""}
            <strong>Tax: {trans.tax_amount} KES</strong>
            <br><strong>Total: {trans.total} KES</strong>
            {f"<br>👤 {trans.customer_name}" if trans.customer_name else ""}
            <br><strong>Payment:</strong> {trans.payment_method} {f"({trans.payment_reference})" if trans.payment_reference else ""}
        </div>
        '''

    content = f'''
    <div class="stats-grid">
        <div class="stat-card"><div class="stat-info"><h3>{SaleTransaction.query.count()}</h3><p>Total Invoices</p></div><div class="stat-icon"><i class="fas fa-file-invoice"></i></div></div>
        <div class="stat-card"><div class="stat-info"><h3>{get_total_sales():,} KES</h3><p>Total Revenue</p></div><div class="stat-icon"><i class="fas fa-currency-dollar"></i></div></div>
    </div>
    <div class="products-section">
        <div class="section-header"><h3>📄 All Invoices</h3></div>
        {transactions_html if transactions_html else "<p>No invoices found.</p>"}
    </div>
    '''

    total_products = Product.query.count()
    low_stock_count = Product.query.filter(Product.quantity < Product.min_stock).count()
    return render_template_string(MASTER_TEMPLATE,
                                  title="Invoices",
                                  page_title="📄 Invoice History",
                                  page_subtitle="View all sales invoices",
                                  content=content,
                                  active_page="invoices",
                                  total_products=total_products,
                                  low_stock_count=low_stock_count,
                                  session_name=current_session.get('name', 'User'),
                                  session_role=current_session.get('role', 'staff')
                                  )


# ==================== CUSTOMERS ====================

@app.route('/customers')
@login_required()
def view_customers():
    customers_list = Customer.query.all()
    total_revenue = sum(c.total_spent for c in customers_list)
    total_visits = sum(c.visits for c in customers_list)
    loyal_count = sum(1 for c in customers_list if c.visits > 5)

    customers_html = ''.join([f'''
    <div class="customer-card">
        <strong>👤 {c.name or 'Unknown'}</strong>
        <br>📞 {c.phone}
        <br>{"📧 " + c.email + "<br>" if c.email else ""}
        💰 Total Spent: <strong>{c.total_spent:,} KES</strong>
        <br>🔄 Visits: {c.visits}
        <br>📅 Last Purchase: {c.last_purchase.strftime("%Y-%m-%d %H:%M:%S") if c.last_purchase else "Never"}
    </div>
    ''' for c in customers_list])

    content = f'''
    <div class="stats-grid">
        <div class="stat-card"><div class="stat-info"><h3>{len(customers_list)}</h3><p>Total Customers</p></div><div class="stat-icon"><i class="fas fa-users"></i></div></div>
        <div class="stat-card"><div class="stat-info"><h3>{total_revenue:,} KES</h3><p>Total Revenue</p></div><div class="stat-icon"><i class="fas fa-currency-dollar"></i></div></div>
        <div class="stat-card"><div class="stat-info"><h3>{total_visits}</h3><p>Total Visits</p></div><div class="stat-icon"><i class="fas fa-calendar-check"></i></div></div>
        <div class="stat-card"><div class="stat-info"><h3>{loyal_count}</h3><p>Loyal Customers (5+ visits)</p></div><div class="stat-icon"><i class="fas fa-star"></i></div></div>
    </div>
    <div class="products-section">
        <div class="section-header"><h3>📇 Customer Database</h3></div>
        {customers_html if customers_html else "<p>No customers yet.</p>"}
    </div>
    '''

    total_products = Product.query.count()
    low_stock_count = Product.query.filter(Product.quantity < Product.min_stock).count()
    return render_template_string(MASTER_TEMPLATE,
                                  title="Customers",
                                  page_title="👥 Customer Database",
                                  page_subtitle="View and manage your customer information.",
                                  content=content,
                                  active_page="customers",
                                  total_products=total_products,
                                  low_stock_count=low_stock_count,
                                  session_name=current_session.get('name', 'User'),
                                  session_role=current_session.get('role', 'staff')
                                  )


# ==================== REPORT ====================

@app.route('/report')
@login_required()
def daily_report():
    transactions = SaleTransaction.query.order_by(SaleTransaction.timestamp.desc()).limit(50).all()
    total_transactions = SaleTransaction.query.count()
    total_sales = get_total_sales()
    average_sale = total_sales / total_transactions if total_transactions else 0

    transactions_html = ''
    for trans in transactions:
        items_html = ''.join(
            [f'{item.quantity} x {remove_emoji(item.product_name)} = {item.total_cost} KES<br>' for item in
             trans.items])
        transactions_html += f'''
        <div class="transaction">
            <strong>🧾 {trans.id}</strong> | 📅 {trans.timestamp.strftime("%Y-%m-%d %H:%M:%S")}
            <br>{items_html}
            <strong>Subtotal: {trans.subtotal} KES</strong>
            <br>{"Discount: -" + str(trans.discount_amount) + " KES<br>" if trans.discount_amount > 0 else ""}
            <strong>Tax: {trans.tax_amount} KES</strong>
            <br><strong>Total: {trans.total} KES</strong>
            {f"<br>👤 {trans.customer_name}" if trans.customer_name else ""}
            <br><strong>Payment:</strong> {trans.payment_method} {f"({trans.payment_reference})" if trans.payment_reference else ""}
        </div>
        '''

    content = f'''
    <div class="stats-grid">
        <div class="stat-card"><div class="stat-info"><h3>{total_transactions}</h3><p>Transactions</p></div><div class="stat-icon"><i class="fas fa-receipt"></i></div></div>
        <div class="stat-card"><div class="stat-info"><h3>{total_sales:,} KES</h3><p>Total Sales</p></div><div class="stat-icon"><i class="fas fa-currency-dollar"></i></div></div>
        <div class="stat-card"><div class="stat-info"><h3>{average_sale:,.2f} KES</h3><p>Average Sale</p></div><div class="stat-icon"><i class="fas fa-chart-line"></i></div></div>
    </div>
    <div class="products-section">
        <div class="section-header">
            <h3>📋 Recent Transactions</h3>
            <a href="/export/csv" class="btn-add"><i class="fas fa-download"></i> Export CSV</a>
        </div>
        {transactions_html if transactions_html else "<p>No transactions yet.</p>"}
    </div>
    '''

    total_products = Product.query.count()
    low_stock_count = Product.query.filter(Product.quantity < Product.min_stock).count()
    return render_template_string(MASTER_TEMPLATE,
                                  title="Sales Report",
                                  page_title="📊 Sales Report",
                                  page_subtitle="View your sales performance and transaction history.",
                                  content=content,
                                  active_page="report",
                                  total_products=total_products,
                                  low_stock_count=low_stock_count,
                                  session_name=current_session.get('name', 'User'),
                                  session_role=current_session.get('role', 'staff')
                                  )


# ==================== LOW STOCK ====================

@app.route('/low_stock')
@login_required()
def low_stock_alert():
    low_products = Product.query.filter(Product.quantity < Product.min_stock).all()
    is_admin = current_session.get('role') == 'admin'

    items_html = ''
    for p in low_products:
        restock_form = f'''
        <form action="/restock/{p.name}" method="POST" style="display:flex;gap:10px;align-items:center;margin-top:10px;">
            <input type="number" name="quantity" placeholder="Quantity to add" required min="1" style="padding:8px;border:1px solid #ddd;border-radius:5px;width:150px;">
            <button type="submit" style="background:#10b981;color:white;border:none;padding:8px 20px;border-radius:5px;cursor:pointer;">📦 Restock Now</button>
        </form>''' if is_admin else ''
        items_html += f'''
        <div class="alert-item">
            <h3>📦 {p.name}</h3>
            <p>Current Stock: <strong>{p.quantity}</strong> | Minimum Required: <strong>{p.min_stock}</strong></p>
            {restock_form}
        </div>
        '''

    if not low_products:
        items_html = '''
        <div style="background:white;padding:40px;text-align:center;border-radius:10px;">
            <h2 style="color:#10b981;">✅ All Stock Levels Are Healthy!</h2>
            <p>No items are below minimum stock levels.</p>
        </div>
        '''

    content = f'''
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-info"><h3>{len(low_products)}</h3><p>Low Stock Items</p></div>
            <div class="stat-icon"><i class="fas fa-exclamation-triangle"></i></div>
        </div>
    </div>
    {items_html}
    '''

    total_products = Product.query.count()
    low_stock_count = len(low_products)
    return render_template_string(MASTER_TEMPLATE,
                                  title="Low Stock Alert",
                                  page_title="⚠️ Low Stock Alert",
                                  page_subtitle="Items that need immediate restocking attention.",
                                  content=content,
                                  active_page="low_stock",
                                  total_products=total_products,
                                  low_stock_count=low_stock_count,
                                  session_name=current_session.get('name', 'User'),
                                  session_role=current_session.get('role', 'staff')
                                  )


# ==================== BEST SELLERS ====================

@app.route('/best_sellers')
@login_required()
def best_sellers():
    qty_sales = db.session.query(SaleItem.product_name, func.sum(SaleItem.quantity)).group_by(
        SaleItem.product_name).order_by(func.sum(SaleItem.quantity).desc()).limit(10).all()
    rev_sales = db.session.query(SaleItem.product_name, func.sum(SaleItem.total_cost)).group_by(
        SaleItem.product_name).order_by(func.sum(SaleItem.total_cost).desc()).limit(10).all()

    by_qty = ''.join(
        [f'<div class="transaction"><strong>{i + 1}.</strong> {remove_emoji(name)} - {qty} units sold</div>' for
         i, (name, qty) in enumerate(qty_sales)])
    by_rev = ''.join([f'<div class="transaction"><strong>{i + 1}.</strong> {remove_emoji(name)} - {rev:,} KES</div>' for
                      i, (name, rev) in enumerate(rev_sales)])

    content = f'''
    <div class="products-section">
        <div class="section-header"><h3>🏆 Top 10 Products by Quantity Sold</h3></div>
        {by_qty if by_qty else "<p>No sales data yet.</p>"}
    </div>
    <div class="products-section" style="margin-top:20px;">
        <div class="section-header"><h3>💰 Top 10 Products by Revenue Generated</h3></div>
        {by_rev if by_rev else "<p>No sales data yet.</p>"}
    </div>
    '''

    total_products = Product.query.count()
    low_stock_count = Product.query.filter(Product.quantity < Product.min_stock).count()
    return render_template_string(MASTER_TEMPLATE,
                                  title="Best Sellers",
                                  page_title="🏆 Best Sellers Report",
                                  page_subtitle="Most popular products by quantity and revenue.",
                                  content=content,
                                  active_page="best_sellers",
                                  total_products=total_products,
                                  low_stock_count=low_stock_count,
                                  session_name=current_session.get('name', 'User'),
                                  session_role=current_session.get('role', 'staff')
                                  )


# ==================== ACTIVITY LOG ====================

@app.route('/activity')
@login_required(role='admin')
def view_activity():
    logs = ActivityLog.query.order_by(ActivityLog.timestamp.desc()).limit(100).all()
    logs_html = ''.join([f'''
    <div class="log-entry">
        <strong>{log.timestamp.strftime("%Y-%m-%d %H:%M:%S")}</strong> | 👤 {log.user}: {log.action}
        <br><span style="font-size:11px;color:#666;">🌐 IP: {log.ip or "Unknown"}</span>
    </div>
    ''' for log in logs])

    content = f'''
    <div class="products-section">
        <div class="section-header"><h3>📋 Activity Log (Last 100 entries)</h3></div>
        {logs_html if logs_html else "<p>No activity logged yet.</p>"}
    </div>
    '''

    total_products = Product.query.count()
    low_stock_count = Product.query.filter(Product.quantity < Product.min_stock).count()
    return render_template_string(MASTER_TEMPLATE,
                                  title="Activity Log",
                                  page_title="📋 Activity Log",
                                  page_subtitle="Track all user activities and system events.",
                                  content=content,
                                  active_page="activity",
                                  total_products=total_products,
                                  low_stock_count=low_stock_count,
                                  session_name=current_session.get('name', 'User'),
                                  session_role=current_session.get('role', 'staff')
                                  )


# ==================== USERS (Admin Only) ====================

@app.route('/users')
@login_required(role='admin')
def view_users():
    users_list = User.query.all()
    admin_count = sum(1 for u in users_list if u.role == 'admin')
    staff_count = sum(1 for u in users_list if u.role == 'staff')

    users_html = ''
    for u in users_list:
        users_html += f'''
        <tr>
            <td><strong>{u.username}</strong></td>
            <td>{u.name or ""}</td>
            <td>{u.email or ""}</td>
            <td><span class="status-badge status-{"high" if u.role == "admin" else "low"}">{u.role.upper()}</span></td>
            <td>{u.created_at.strftime("%Y-%m-%d %H:%M:%S") if u.created_at else "N/A"}</td>
            <td>
                {"<span style=\"color:#999;\">Protected</span>" if u.username == "admin" else f"<form action=\"/delete_user/{u.username}\" method=\"POST\" onsubmit=\"return confirm('Delete {u.username}?');\"><button type=\"submit\" style=\"background:#dc2626;color:white;padding:5px 10px;border:none;border-radius:5px;cursor:pointer;\">Delete</button></form>"}
            </td>
        </tr>
        '''

    content = f'''
    <div class="stats-grid">
        <div class="stat-card"><div class="stat-info"><h3>{len(users_list)}</h3><p>Total Users</p></div><div class="stat-icon"><i class="fas fa-users"></i></div></div>
        <div class="stat-card"><div class="stat-info"><h3>{admin_count}</h3><p>Admins</p></div><div class="stat-icon"><i class="fas fa-user-shield"></i></div></div>
        <div class="stat-card"><div class="stat-info"><h3>{staff_count}</h3><p>Staff</p></div><div class="stat-icon"><i class="fas fa-user"></i></div></div>
    </div>
    <div class="products-section">
        <div class="section-header"><h3>👥 System Users</h3></div>
        <div style="overflow-x:auto;">
            <table>
                <thead><tr><th>Username</th><th>Name</th><th>Email</th><th>Role</th><th>Created</th><th>Actions</th></tr></thead>
                <tbody>{users_html}</tbody>
            </table>
        </div>
    </div>
    '''

    total_products = Product.query.count()
    low_stock_count = Product.query.filter(Product.quantity < Product.min_stock).count()
    return render_template_string(MASTER_TEMPLATE,
                                  title="User Management",
                                  page_title="👤 User Management (Admin Only)",
                                  page_subtitle="Manage system users and their permissions.",
                                  content=content,
                                  active_page="users",
                                  total_products=total_products,
                                  low_stock_count=low_stock_count,
                                  session_name=current_session.get('name', 'User'),
                                  session_role=current_session.get('role', 'staff')
                                  )


@app.route('/delete_user/<username>', methods=['POST'])
@login_required(role='admin')
def delete_user(username):
    if username == 'admin':
        flash('Cannot delete main admin!', 'danger')
        return redirect(url_for('view_users'))
    user = User.query.filter_by(username=username).first()
    if user:
        db.session.delete(user)
        db.session.commit()
        log_activity(f"Deleted user: {username}")
        flash(f'User {username} deleted!', 'success')
    return redirect(url_for('view_users'))


# ==================== DATABASE INFO ROUTE ====================

@app.route('/database-info')
@login_required(role='admin')
def database_info():
    """Display database information and statistics"""
    try:
        total_users = User.query.count()
        total_products = Product.query.count()
        total_customers = Customer.query.count()
        total_transactions = SaleTransaction.query.count()
        total_sales = get_total_sales()
        total_stock = get_total_stock()
        total_profit = get_total_profit()

        recent_activities = ActivityLog.query.order_by(ActivityLog.timestamp.desc()).limit(10).all()

        tables = {
            'Users': User.query.count(),
            'Products': Product.query.count(),
            'Customers': Customer.query.count(),
            'Sales Transactions': SaleTransaction.query.count(),
            'Sale Items': SaleItem.query.count(),
            'Activity Logs': ActivityLog.query.count()
        }

        activities_html = ''
        for log in recent_activities:
            activities_html += f'''
            <tr>
                <td>{log.timestamp.strftime("%Y-%m-%d %H:%M:%S")}</td>
                <td><strong>{log.user}</strong></td>
                <td>{log.action}</td>
                <td><span style="font-size:11px;color:#666;">{log.ip or 'N/A'}</span></td>
            </tr>
            '''

        content = f'''
        <style>
            .db-stats-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 15px;
                margin-bottom: 30px;
            }}
            .db-stat-card {{
                background: white; padding: 20px; border-radius: 15px; text-align: center;
                box-shadow: 0 2px 10px rgba(0,0,0,0.05); transition: all 0.3s;
            }}
            .db-stat-card:hover {{ transform: translateY(-5px); box-shadow: 0 5px 20px rgba(0,0,0,0.1); }}
            .db-stat-card .icon {{ font-size: 32px; margin-bottom: 8px; }}
            .db-stat-card h3 {{ font-size: 28px; font-weight: bold; color: #1a1a2e; }}
            .db-stat-card p {{ color: #666; font-size: 13px; margin-top: 4px; }}
            .db-stat-card .status-badge {{
                display: inline-block; padding: 3px 12px; border-radius: 20px;
                font-size: 12px; font-weight: 600; margin-top: 5px;
            }}
            .db-stat-card .status-badge.success {{ background: #d4edda; color: #155724; }}
            .db-stat-card .status-badge.warning {{ background: #fff3cd; color: #856404; }}
            .db-section {{
                background: white; border-radius: 15px; padding: 20px;
                margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.05);
            }}
            .db-section h3 {{ color: #1a1a2e; margin-bottom: 15px; display: flex; align-items: center; gap: 10px; }}
            .db-section h3 i {{ color: #667eea; }}
            .db-actions {{ display: flex; gap: 15px; flex-wrap: wrap; margin-top: 15px; }}
            .db-actions .btn {{
                padding: 10px 20px; border-radius: 10px; text-decoration: none; font-weight: 600;
                display: inline-flex; align-items: center; gap: 8px; transition: all 0.3s;
            }}
            .db-actions .btn-primary {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }}
            .db-actions .btn-primary:hover {{ transform: translateY(-2px); box-shadow: 0 5px 20px rgba(102,126,234,0.3); }}
            .db-actions .btn-success {{ background: #10b981; color: white; }}
            .db-actions .btn-success:hover {{ transform: translateY(-2px); box-shadow: 0 5px 20px rgba(16,185,129,0.3); }}
            .db-actions .btn-warning {{ background: #f59e0b; color: white; }}
            .db-actions .btn-warning:hover {{ transform: translateY(-2px); box-shadow: 0 5px 20px rgba(245,158,11,0.3); }}
            .db-file-info {{
                background: #f8f9fa; padding: 15px; border-radius: 10px; margin-top: 10px;
                font-family: monospace; font-size: 13px; color: #333; border: 1px solid #e0e0e0;
            }}
        </style>

        <div class="db-stats-grid">
            <div class="db-stat-card">
                <div class="icon">🗄️</div>
                <h3>{total_products}</h3>
                <p>Total Products</p>
                <span class="status-badge success">Active</span>
            </div>
            <div class="db-stat-card">
                <div class="icon">👥</div>
                <h3>{total_users}</h3>
                <p>Total Users</p>
                <span class="status-badge success">Active</span>
            </div>
            <div class="db-stat-card">
                <div class="icon">💰</div>
                <h3>{total_sales:,}</h3>
                <p>Total Sales (KES)</p>
                <span class="status-badge success">Revenue</span>
            </div>
            <div class="db-stat-card">
                <div class="icon">📊</div>
                <h3>{total_profit:,}</h3>
                <p>Total Profit (KES)</p>
                <span class="status-badge success">Profit</span>
            </div>
            <div class="db-stat-card">
                <div class="icon">📦</div>
                <h3>{total_stock}</h3>
                <p>Total Stock</p>
                <span class="status-badge {'warning' if total_stock < 100 else 'success'}">{'Low' if total_stock < 100 else 'Healthy'}</span>
            </div>
            <div class="db-stat-card">
                <div class="icon">🧾</div>
                <h3>{total_transactions}</h3>
                <p>Total Transactions</p>
                <span class="status-badge success">Complete</span>
            </div>
        </div>

        <div class="db-section">
            <h3><i class="fas fa-database"></i> Database Tables</h3>
            <div style="overflow-x:auto;">
                <table>
                    <thead><tr><th>Table Name</th><th>Records</th><th>Status</th></tr></thead>
                    <tbody>
        '''

        for table_name, count in tables.items():
            status = '✅ Healthy' if count > 0 else '⚠️ Empty'
            color = '#155724' if count > 0 else '#856404'
            bg_color = '#d4edda' if count > 0 else '#fff3cd'
            content += f'''
                        <tr>
                            <td><strong>{table_name}</strong></td>
                            <td>{count:,}</td>
                            <td><span style="background:{bg_color};color:{color};padding:3px 12px;border-radius:20px;font-size:12px;font-weight:600;">{status}</span></td>
                        </tr>
            '''

        content += f'''
                    </tbody>
                </table>
            </div>
        </div>

        <div class="db-section">
            <h3><i class="fas fa-file-alt"></i> Database File Information</h3>
            <div class="db-file-info">
                📁 Database Path: {db_path}<br>
                📊 Database Size: {os.path.getsize(db_path) / 1024:.2f} KB<br>
                🗄️ Database Type: SQLite 3<br>
                📅 Created: {datetime.fromtimestamp(os.path.getctime(db_path)).strftime("%Y-%m-%d %H:%M:%S") if os.path.exists(db_path) else "N/A"}<br>
                📝 Last Modified: {datetime.fromtimestamp(os.path.getmtime(db_path)).strftime("%Y-%m-%d %H:%M:%S") if os.path.exists(db_path) else "N/A"}
            </div>
        </div>

        <div class="db-section">
            <h3><i class="fas fa-clock"></i> Recent Activity</h3>
            <div style="overflow-x:auto;">
                <table>
                    <thead><tr><th>Timestamp</th><th>User</th><th>Action</th><th>IP Address</th></tr></thead>
                    <tbody>
                        {activities_html if activities_html else '<tr><td colspan="4" style="text-align:center;color:#666;">No recent activity</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="db-section">
            <h3><i class="fas fa-tools"></i> Database Actions</h3>
            <div class="db-actions">
                <a href="/export_db" class="btn btn-success"><i class="fas fa-download"></i> Export Database</a>
                <a href="/products" class="btn btn-primary"><i class="fas fa-boxes"></i> View Products</a>
                <a href="/customers" class="btn btn-primary"><i class="fas fa-users"></i> View Customers</a>
                <a href="/report" class="btn btn-primary"><i class="fas fa-chart-line"></i> View Reports</a>
                <a href="/backup_db" class="btn btn-warning"><i class="fas fa-copy"></i> Backup Database</a>
                <a href="/optimize_db" class="btn btn-primary"><i class="fas fa-hammer"></i> Optimize Database</a>
            </div>
        </div>
        '''

        total_products = Product.query.count()
        low_stock_count = Product.query.filter(Product.quantity < Product.min_stock).count()

        return render_template_string(MASTER_TEMPLATE,
                                      title="Database Information",
                                      page_title="🗄️ Database Management",
                                      page_subtitle="View database statistics, tables, and perform maintenance operations.",
                                      content=content,
                                      active_page="database",
                                      total_products=total_products,
                                      low_stock_count=low_stock_count,
                                      session_name=current_session.get('name', 'User'),
                                      session_role=current_session.get('role', 'staff')
                                      )
    except Exception as e:
        flash(f'Error loading database info: {str(e)}', 'danger')
        return redirect(url_for('index'))


@app.route('/export_db')
@login_required(role='admin')
def export_database():
    try:
        if os.path.exists(db_path):
            return send_file(db_path, as_attachment=True, download_name="supermarket.db")
        else:
            flash('Database file not found!', 'danger')
            return redirect(url_for('database_info'))
    except Exception as e:
        flash(f'Error exporting database: {str(e)}', 'danger')
        return redirect(url_for('database_info'))


@app.route('/backup_db')
@login_required(role='admin')
def backup_database():
    try:
        if os.path.exists(db_path):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(instance_path, "backups")
            os.makedirs(backup_path, exist_ok=True)
            import shutil
            backup_file = os.path.join(backup_path, f"supermarket_{timestamp}.db")
            shutil.copy2(db_path, backup_file)
            log_activity(f"Database backed up: {backup_file}")
            flash(f'✅ Database backed up successfully!', 'success')
        else:
            flash('Database file not found!', 'danger')
    except Exception as e:
        flash(f'Error backing up database: {str(e)}', 'danger')
    return redirect(url_for('database_info'))


@app.route('/optimize_db')
@login_required(role='admin')
def optimize_database():
    try:
        db.session.execute(text('VACUUM;'))
        db.session.execute(text('REINDEX;'))
        db.session.commit()
        log_activity("Database optimized (VACUUM and REINDEX performed)")
        flash('✅ Database optimized successfully!', 'success')
    except Exception as e:
        flash(f'Error optimizing database: {str(e)}', 'danger')
    return redirect(url_for('database_info'))


# ==================== EXPORT CSV ====================

@app.route('/export/csv')
@login_required()
def export_csv():
    transactions = SaleTransaction.query.all()
    if not transactions:
        flash('No sales to export!', 'warning')
        return redirect(url_for('daily_report'))

    filename = f"sales_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(filename, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Timestamp', 'Transaction ID', 'Product', 'Quantity', 'Unit Price', 'Total',
                         'Subtotal', 'Discount', 'Tax', 'Grand Total', 'Customer', 'Phone', 'Email',
                         'Payment Method', 'Payment Reference', 'IP Address'])
        for trans in transactions:
            for item in trans.items:
                writer.writerow([
                    trans.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    trans.id,
                    remove_emoji(item.product_name),
                    item.quantity,
                    f"{item.unit_price:.2f}",
                    item.total_cost,
                    trans.subtotal,
                    trans.discount_amount,
                    trans.tax_amount,
                    trans.total,
                    trans.customer_name or 'Walk-in',
                    trans.customer_phone or 'N/A',
                    trans.customer_email or 'N/A',
                    trans.payment_method,
                    trans.payment_reference or 'N/A',
                    trans.ip_address or 'N/A'
                ])

    flash(f'Exported to {filename}', 'success')
    return redirect(url_for('daily_report'))


# ==================== RUN APPLICATION ====================

if __name__ == '__main__':
    with app.app_context():
        init_database()

        total_products = Product.query.count()
        total_stock = get_total_stock()
        total_users = User.query.count()
        total_sales = get_total_sales()

        print("\n" + "=" * 70)
        print("🏪 ISAAC SUPERMARKET - MANAGEMENT SYSTEM")
        print("=" * 70)
        print(f"✅ Database: {db_path}")
        print(f"📦 Products: {total_products}")
        print(f"📊 Total Stock: {total_stock} units")
        print(f"👥 Users: {total_users}")
        print(f"💰 Total Sales: {total_sales:,} KES")
        print("\n🔐 LOGIN CREDENTIALS:")
        print("   • Username: admin")
        print("   • Password: admin123")
        print("\n🌐 ACCESS URL:")
        print("   • http://127.0.0.1:5000")
        print("=" * 70 + "\n")

        if not REPORTLAB_AVAILABLE:
            print("⚠️  ReportLab not installed. PDF receipt generation disabled.\n")

    app.run(debug=True, host='0.0.0.0', port=5000)