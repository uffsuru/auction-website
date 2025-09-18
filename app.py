from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
import json
import os
from werkzeug.utils import secure_filename
import random
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import errorcode, pooling
import sys
load_dotenv()

app = Flask(__name__)
socketio = SocketIO(app, async_mode='eventlet')
app.secret_key = os.environ.get('SECRET_KEY', 'a-default-secret-key-for-dev-only')

# Serve static files
from whitenoise import WhiteNoise
app.wsgi_app = WhiteNoise(app.wsgi_app, root='static/')

# Define the path for file uploads. In production, this points to a persistent disk.
# In development, it points to a local folder inside 'static'.
if os.environ.get('RENDER') == 'true':
    UPLOAD_FOLDER = '/var/data/uploads'
    # In production, also serve uploaded files from the persistent disk
    app.wsgi_app.add_files(UPLOAD_FOLDER, prefix='uploads/')
else:
    UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')

# --- MySQL Configuration ---
# Reads credentials from a .env file for local development.
db_config = {
   'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD'),
    'database': os.environ.get('DB_DATABASE', 'auction_db')
}

# --- Database Connection Pool ---
try:
    print("ℹ️  Attempting to create MySQL connection pool...")
    print(f"    Host: {db_config.get('host')}")
    print(f"    User: {db_config.get('user')}")
    print(f"    Database: {db_config.get('database')}")
    # For security, the password is not printed to the log.

    db_pool = mysql.connector.pooling.MySQLConnectionPool(
        pool_name="auction_pool",
        pool_size=5,  # Reduced pool size for PythonAnywhere's environment
        **db_config
    )
    print("✅ MySQL Connection Pool created successfully.")
except mysql.connector.Error as err:
    # This block provides detailed debugging information in your PythonAnywhere server log.
    print("❌❌❌ CRITICAL: FAILED TO CREATE MYSQL CONNECTION POOL ❌❌❌")
    print(f"MySQL Error Code: {err.errno}")
    print(f"MySQL Error Message: {err.msg}")
    print("-----------------------------------------------------------------")
    print("👉 COMMON FIXES FOR LOCAL DEVELOPMENT:")
    print("   1. Is your MySQL server running?")
    print("   2. Did you create the database? (e.g., CREATE DATABASE auction_db;)")
    print("   3. Do the 'user' and 'password' in db_config match your MySQL credentials?")
    print("-----------------------------------------------------------------")
    db_pool = None

# --- Database Connection Function ---
def get_db_connection():
    """Gets a connection from the pool."""
    if not db_pool:
        return None
    try:
        return db_pool.get_connection()
    except mysql.connector.Error as err:
        print(f"❌ Error getting connection from pool: {err}")
        return None

# --- Database Initialization ---
def init_db():
    conn = get_db_connection()
    if not conn:
        print("Could not connect to MySQL. Aborting DB initialization.")
        return
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (id INT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(255), email VARCHAR(255) UNIQUE, password TEXT, created_at DATETIME, email_verified BOOLEAN DEFAULT 0, is_admin BOOLEAN DEFAULT 0)''')
    try:
        # Add is_admin column for existing databases
        c.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0")
    except mysql.connector.Error as err:
        if err.errno != 1060: # Ignore "Duplicate column name" error
            raise
    c.execute('''CREATE TABLE IF NOT EXISTS auctions (id INT PRIMARY KEY AUTO_INCREMENT, title VARCHAR(255), description TEXT, starting_price DECIMAL(10, 2), current_price DECIMAL(10, 2), end_time DATETIME, seller_id INT, category VARCHAR(255), image_url TEXT, created_at DATETIME, history_link TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS bids (id INT PRIMARY KEY AUTO_INCREMENT, auction_id INT, user_id INT, amount DECIMAL(10, 2), bid_time DATETIME)''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders (id INT PRIMARY KEY AUTO_INCREMENT, auction_id INT, user_id INT, address TEXT, payment_status VARCHAR(50), order_status VARCHAR(50) DEFAULT 'Ordered', created_at DATETIME)''')
    c.execute('''CREATE TABLE IF NOT EXISTS notifications (id INT PRIMARY KEY AUTO_INCREMENT, user_id INT, message TEXT, is_read BOOLEAN DEFAULT 0, created_at DATETIME, link TEXT, FOREIGN KEY(user_id) REFERENCES users(id))''')

    # --- Add Indexes for Performance ---
    # These operations are idempotent; they will only add indexes if they don't exist.
    def execute_alter(command):
        try:
            c.execute(command)
        except mysql.connector.Error as err:
            if err.errno != 1061: # Ignore "Duplicate key name" error
                raise
    
    execute_alter("ALTER TABLE auctions ADD INDEX idx_created_at (created_at DESC)")
    execute_alter("ALTER TABLE auctions ADD INDEX idx_seller_id (seller_id)")
    execute_alter("ALTER TABLE auctions ADD INDEX idx_end_time (end_time)")
    execute_alter("ALTER TABLE auctions ADD INDEX idx_category (category)")
    execute_alter("ALTER TABLE bids ADD INDEX idx_auction_bid_time (auction_id, bid_time DESC)")
    execute_alter("ALTER TABLE bids ADD INDEX idx_user_bids_sorted (user_id, amount DESC, bid_time DESC)")
    execute_alter("ALTER TABLE bids ADD INDEX idx_user_id (user_id)")
    execute_alter("ALTER TABLE orders ADD INDEX idx_user_id (user_id)")
    # --- End of Index Addition ---

    conn.commit()
    c.close()
    conn.close()

# --- Notification Helper ---
def create_notification(cursor, user_id, message, link):
    """Creates a notification using an existing database cursor. Does not commit."""
    created_at = datetime.now()
    cursor.execute("INSERT INTO notifications (user_id, message, link, created_at) VALUES (%s, %s, %s, %s)",
                   (user_id, message, link, created_at.isoformat()))

    # Build the notification object to send over SocketIO without another DB query
    notification_data = {
        'id': cursor.lastrowid,
        'user_id': user_id,
        'message': message,
        'is_read': False,
        'created_at': created_at.isoformat(),
        'link': link
    }
    socketio.emit('new_notification', notification_data, room=str(user_id))

@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        join_room(str(session['user_id']))

@socketio.on('join')
def handle_join(data):
    if 'user_id' in session and str(session['user_id']) == data['room']:
        join_room(data['room'])

# --- Add get_time_left helper and register as Jinja2 global ---
def get_time_left(end_time_str):
    """Calculate time left for an auction with more precision."""
    try:
        if isinstance(end_time_str, str):
            # Handles ISO format strings from JSON/DB
            end_time = datetime.fromisoformat(end_time_str)
        else:
            # Handles datetime objects directly
            end_time = end_time_str
        
        now = datetime.now()
        if end_time <= now:
            return "Ended"
            
        time_diff = end_time - now
        days = time_diff.days
        seconds_left = time_diff.seconds
        
        hours = seconds_left // 3600
        minutes = (seconds_left % 3600) // 60
        seconds = seconds_left % 60

        if days > 0:
            return f"{days}d {hours}h left"
        elif hours > 0:
            return f"{hours}h {minutes}m left"
        elif minutes > 0:
            return f"{minutes}m {seconds}s left"
        else:
            return f"{seconds}s left"
            
    except (ValueError, TypeError):
        # Catches parsing errors or if end_time_str is None
        return "Unknown"

def get_delivery_date(order_date_str):
    """Calculate expected delivery date (7 days after order)."""
    try:
        # Handle if the input is already a datetime object from the DB
        if isinstance(order_date_str, datetime):
            order_date = order_date_str
        else:
            order_date = datetime.fromisoformat(order_date_str)
        
        delivery_date = order_date + timedelta(days=7)
        return delivery_date.strftime('%A, %b %d')
    except (ValueError, TypeError, AttributeError):
        return "Not available"

app.jinja_env.globals.update(get_time_left=get_time_left, get_delivery_date=get_delivery_date)

# --- Admin Decorator ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            # Redirect non-admins to the homepage
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# ...existing code...

# Add order/payment route after app is defined
# Debug print to confirm route registration
print("Registering /order/<int:auction_id> route")
@app.route('/order/<int:auction_id>', methods=['GET', 'POST'])
def order(auction_id):
    print(f"/order route accessed with auction_id={auction_id}")
    if 'user_id' not in session:
        return redirect(url_for('index'))
    conn = get_db_connection()
    if not conn: return "Database connection failed", 500
    c = conn.cursor(dictionary=True, buffered=True)
    # Get auction details
    c.execute('SELECT end_time FROM auctions WHERE id = %s', (auction_id,))
    auction = c.fetchone()
    if not auction:
        conn.close()
        return "Auction not found", 404
    # Check if auction ended
    end_time = auction['end_time']
    if not end_time or end_time > datetime.now():
        conn.close()
        return "Auction not ended yet", 403
    # Get highest bid (winner)
    c.execute('SELECT user_id, amount FROM bids WHERE auction_id = %s ORDER BY amount DESC, bid_time ASC LIMIT 1', (auction_id,))
    winner = c.fetchone()
    if not winner or winner['user_id'] != session['user_id']:
        c.close()
        conn.close()
        return "You are not the winner of this auction.", 403
    # Check if order already exists
    c.execute('SELECT id FROM orders WHERE auction_id = %s AND user_id = %s', (auction_id, session['user_id']))
    if c.fetchone():
        c.close()
        conn.close()
        return "Order already placed for this auction.", 400
    if request.method == 'POST':
        address = request.form.get('address')
        payment = request.form.get('payment')
        if address and payment:
            created_at_iso = datetime.now().isoformat()
            # For demo, payment is always successful
            c.execute('INSERT INTO orders (auction_id, user_id, address, payment_status, order_status, created_at) VALUES (%s, %s, %s, %s, %s, %s)',
                      (auction_id, session['user_id'], address, 'paid', 'Ordered', created_at_iso))
            conn.commit()
            c.close()
            conn.close()
            delivery_date = get_delivery_date(created_at_iso)
            return render_template('order-success.html', delivery_date=delivery_date)
        else:
            c.close()
            conn.close()
            return render_template('order.html', error='All fields are required.')
    c.close()
    conn.close()
    return render_template('order.html')


    # ...existing code...

@app.route('/')
def index():
    category = request.args.get('category')
    conn = get_db_connection()
    if not conn: return "Database connection failed", 500
    c = conn.cursor(dictionary=True)
    if category:
        c.execute('''SELECT * FROM auctions WHERE end_time > %s AND category = %s ORDER BY created_at DESC''', (datetime.now().isoformat(), category))
    else:
        c.execute('''SELECT * FROM auctions WHERE end_time > %s ORDER BY created_at DESC''', (datetime.now().isoformat(),))
    auctions = c.fetchall()
    c.close()
    conn.close()
    return render_template('index.html', auctions=auctions)

@app.route('/auction/<int:auction_id>')
def auction_detail(auction_id):
    conn = get_db_connection()
    if not conn: return "Database connection failed", 500
    c = conn.cursor(dictionary=True)
    
    # Get auction details
    c.execute('SELECT * FROM auctions WHERE id = %s', (auction_id,))
    auction = c.fetchone()
    
    if not auction:
        return "Auction not found", 404
    
    # Get bid history
    c.execute('''SELECT b.amount, b.bid_time, u.name FROM bids b 
                JOIN users u ON b.user_id = u.id 
                WHERE b.auction_id = %s ORDER BY b.bid_time DESC LIMIT 10''', (auction_id,))
    bids = c.fetchall()
    
    c.close()
    conn.close()
    return render_template('auction-detail.html', auction=auction, bids=bids)

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    if not conn: return "Database connection failed", 500
    c = conn.cursor(dictionary=True)
    
    # Get user's auctions
    c.execute('''SELECT a.*, COUNT(b.id) as bid_count
                 FROM auctions a
                 LEFT JOIN bids b ON a.id = b.auction_id
                 WHERE a.seller_id = %s
                 GROUP BY a.id
                 ORDER BY a.created_at DESC''', (session['user_id'],))
    my_auctions = c.fetchall()
    
    # Get user's bids (include auction id)
    c.execute('''SELECT a.id, a.title, b.amount, b.bid_time, a.current_price, a.end_time 
                FROM bids b JOIN auctions a ON b.auction_id = a.id 
                WHERE b.user_id = %s ORDER BY b.amount DESC, b.bid_time DESC''', (session['user_id'],))
    all_bids = c.fetchall()
    # Get all orders for this user (auction_id set)
    c.execute('SELECT auction_id FROM orders WHERE user_id = %s', (session['user_id'],))
    ordered_auction_ids = set(row['auction_id'] for row in c.fetchall())
    # Keep only the highest bid per auction, and mark if ordered
    seen_auctions = set()
    my_bids = []
    for bid in all_bids:
        auction_id = bid['id']
        if auction_id not in seen_auctions:
            is_ordered = auction_id in ordered_auction_ids
            bid['is_ordered'] = is_ordered # Add as a new key
            my_bids.append(bid)
            seen_auctions.add(auction_id)
    
    # Get user's orders (with auction info)
    c.execute('''SELECT o.id, a.title, o.address, o.payment_status, o.order_status, o.created_at, a.image_url, a.id as auction_id
                 FROM orders o JOIN auctions a ON o.auction_id = a.id
                 WHERE o.user_id = %s ORDER BY o.created_at DESC''', (session['user_id'],))
    my_orders = c.fetchall()
    c.close()
    conn.close()
    return render_template('dashboard.html', my_auctions=my_auctions, my_bids=my_bids, my_orders=my_orders)

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')
    
    if not all([name, email, password]):
        return jsonify({'success': False, 'message': 'All fields required'})
    
    conn = get_db_connection()
    if not conn: return jsonify({'success': False, 'message': 'Database connection failed'})
    c = conn.cursor(buffered=True)
    
    # Check if user exists
    c.execute('SELECT id FROM users WHERE email = %s', (email,))
    if c.fetchone():
        c.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Email already registered'})
    
    # Create user (email_verified=0 by default)
    hashed_password = generate_password_hash(password)
    c.execute('INSERT INTO users (name, email, password, created_at, email_verified) VALUES (%s, %s, %s, %s, %s)',
              (name, email, hashed_password, datetime.now().isoformat(), 0))
    conn.commit()
    c.close()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Registration successful'})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    
    conn = get_db_connection()
    if not conn: return jsonify({'success': False, 'message': 'Database connection failed'})
    c = conn.cursor(dictionary=True)
    c.execute('SELECT id, name, password, is_admin FROM users WHERE email = %s', (email,))
    user = c.fetchone()
    c.close()
    conn.close()
    
    if user and check_password_hash(user['password'], password):
        session['user_id'] = user['id']
        session['user_name'] = user['name']
        # Store admin status in session for easy access
        session['is_admin'] = user['is_admin']
        return jsonify({'success': True, 'message': 'Login successful'})
    
    return jsonify({'success': False, 'message': 'Invalid credentials'})

@app.route('/api/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/api/bid', methods=['POST'])
def place_bid():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please login first'})

    conn = get_db_connection()
    if not conn: return jsonify({'success': False, 'message': 'Database connection failed'})
    c = conn.cursor(dictionary=True)

    try:
        # Check if user is verified
        c.execute('SELECT email_verified FROM users WHERE id = %s', (session['user_id'],))
        user = c.fetchone()
        if not user or not user['email_verified']:
            return jsonify({'success': False, 'message': 'You must verify your email before bidding.'})

        data = request.get_json()
        auction_id = data.get('auction_id')
        bid_amount = float(data.get('amount'))

        # --- Transaction Start ---
        # Use a transaction with FOR UPDATE to prevent race conditions
        conn.start_transaction()
        
        # Lock the auction row and get current details
        c.execute('SELECT title, current_price, end_time, seller_id FROM auctions WHERE id = %s FOR UPDATE', (auction_id,))
        auction = c.fetchone()

        if not auction:
            conn.rollback()
            return jsonify({'success': False, 'message': 'Auction not found'})

        # Check if the bidder is the seller
        if auction['seller_id'] == session['user_id']:
            conn.rollback()
            return jsonify({'success': False, 'message': 'You cannot bid on your own auction.'})

        # Check if auction has ended
        if auction['end_time'] < datetime.now():
            conn.rollback()
            return jsonify({'success': False, 'message': 'Auction has ended'})

        if bid_amount <= auction['current_price']:
            conn.rollback()
            return jsonify({'success': False, 'message': 'Bid must be higher than current price'})

        # Get previous highest bidder to notify them
        c.execute("SELECT user_id FROM bids WHERE auction_id = %s ORDER BY amount DESC LIMIT 1", (auction_id,))
        highest_bidder = c.fetchone()

        bid_time = datetime.now()
        # Place bid
        c.execute('INSERT INTO bids (auction_id, user_id, amount, bid_time) VALUES (%s, %s, %s, %s)',
                  (auction_id, session['user_id'], bid_amount, bid_time.isoformat()))

        # Update auction current price
        c.execute('UPDATE auctions SET current_price = %s WHERE id = %s', (bid_amount, auction_id))

        # Notify previous highest bidder
        if highest_bidder and highest_bidder['user_id'] != session['user_id']:
            create_notification(c, highest_bidder['user_id'], f"You have been outbid on {auction['title']}.", f"/auction/{auction_id}")

        conn.commit() # Commit bid, price update, and notification insert together

        # --- Emit real-time update to all clients after successful commit ---
        bid_data = {
            'auction_id': auction_id,
            'amount': f'{bid_amount:.2f}',
            'user_name': session['user_name'], # The name of the user who just bid
            'bid_time': 'Just now'
        }
        # Broadcast to all clients; the client-side JS will filter by auction ID.
        socketio.emit('new_bid', bid_data)

        return jsonify({'success': True, 'message': 'Bid placed successfully'})

    except mysql.connector.Error as err:
        if conn.in_transaction: conn.rollback()
        print(f"Database error during bid placement: {err}")
        return jsonify({'success': False, 'message': 'A database error occurred. Please try again.'})
    except Exception as e:
        if conn.in_transaction: conn.rollback()
        print(f"An unexpected error occurred during bid placement: {e}")
        return jsonify({'success': False, 'message': 'An unexpected error occurred. Please try again.'})
    finally:
        c.close()
        conn.close()

def create_sample_data():
    """Create sample auction data if database is empty"""
    conn = get_db_connection()
    if not conn: return
    c = conn.cursor(buffered=True)
    
    # Check if auctions exist
    c.execute('SELECT COUNT(*) FROM auctions')
    if c.fetchone()[0] == 0:
        # Create a default user for sample auctions
        hashed_password = generate_password_hash('demo')
        c.execute('INSERT IGNORE INTO users (id, name, email, password, created_at, email_verified, is_admin) VALUES (%s, %s, %s, %s, %s, %s, %s)',
                  (1, 'Demo Seller', 'demo@example.com', hashed_password, datetime.now().isoformat(), 1, 0))
        
        sample_auctions = [
            ("Vintage Rolex Watch", "Authentic vintage Rolex Submariner from 1978. In excellent condition.", 2000, 2500, 
             (datetime.now() + timedelta(days=2)).isoformat(), 1, "Watches", "⌚", datetime.now().isoformat(), "https://en.wikipedia.org/wiki/Rolex_Submariner"),
            ("Rare Pokemon Cards Set", "Complete first edition Pokemon card collection", 300, 450, 
             (datetime.now() + timedelta(days=1)).isoformat(), 1, "Collectibles", "🎮", datetime.now().isoformat(), None),
            ("Antique Painting", "18th century oil painting by renowned artist", 1000, 1200, 
             (datetime.now() + timedelta(days=5)).isoformat(), 1, "Art", "🎨", datetime.now().isoformat(), None),
            ("Classic Guitar", "1960s Martin D-28 acoustic guitar in pristine condition", 600, 800, 
             (datetime.now() + timedelta(days=3)).isoformat(), 1, "Music", "🎸", datetime.now().isoformat(), None),
            ("Designer Handbag", "Limited edition Chanel bag, never used", 500, 600, 
             (datetime.now() + timedelta(days=4)).isoformat(), 1, "Fashion", "👜", datetime.now().isoformat(), None),
            ("Sports Memorabilia", "Signed baseball by legendary player", 200, 300, 
             (datetime.now() + timedelta(days=6)).isoformat(), 1, "Sports", "⚾", datetime.now().isoformat(), None)
        ]
        
        for auction in sample_auctions:
            c.execute('''INSERT INTO auctions (title, description, starting_price, current_price, 
                        end_time, seller_id, category, image_url, created_at, history_link) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''', auction)
    
    conn.commit()
    c.close()
    conn.close()


# Route for creating a new auction
@app.route('/create-auction', methods=['GET', 'POST'])
def create_auction():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        starting_price = request.form.get('starting_price')
        end_time = request.form.get('end_time')
        category = request.form.get('category')
        history_link = request.form.get('history_link')
        file = request.files.get('image_file')
        allowed_exts = {'jpg', 'jpeg', 'png', 'gif', 'pdf', 'webp', 'bmp', 'tiff', 'svg'}
        # Add a file size limit (e.g., 5MB)
        if file and len(file.read()) > 5 * 1024 * 1024:
            return render_template('create-auction.html', error='File is too large. The limit is 5MB.')
        file.seek(0) # Reset file pointer after reading

        image_url = ''
        if not all([title, description, starting_price, end_time, category]):
            return render_template('create-auction.html', error='All fields except image are required.')
        if file and file.filename:
            ext = file.filename.rsplit('.', 1)[-1].lower()
            if ext in allowed_exts:
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                file_path = os.path.join(UPLOAD_FOLDER, filename)
                file.save(file_path)
                # Store a direct URL path for the uploaded file
                image_url = f"/uploads/{filename}"
            else:
                return render_template('create-auction.html', error='Invalid file type. Allowed: jpg, png, gif, pdf,')
        try:
            conn = get_db_connection()
            if not conn: return render_template('create-auction.html', error='Database connection failed')
            c = conn.cursor()
            c.execute('''INSERT INTO auctions (title, description, starting_price, current_price, end_time, seller_id, category, image_url, created_at, history_link) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                (title, description, float(starting_price), float(starting_price), end_time, session['user_id'], category, image_url, datetime.now().isoformat(), history_link))
            conn.commit()
            c.close()
            conn.close()
            return redirect(url_for('dashboard'))
        except Exception as e:
            return render_template('create-auction.html', error='Error creating auction: ' + str(e))
    return render_template('create-auction.html')

@app.route('/auction/<int:auction_id>/edit', methods=['GET', 'POST'])
def edit_auction(auction_id):
    if 'user_id' not in session:
        return redirect(url_for('index'))

    conn = get_db_connection()
    if not conn: return "Database connection failed", 500
    c = conn.cursor(dictionary=True, buffered=True)

    # Fetch auction and check ownership and bid status
    c.execute('SELECT a.*, (SELECT COUNT(*) FROM bids WHERE auction_id = a.id) as bid_count FROM auctions a WHERE a.id = %s', (auction_id,))
    auction = c.fetchone()

    if not auction:
        c.close()
        conn.close()
        return "Auction not found", 404

    # Authorization check: must be seller OR admin
    if auction['seller_id'] != session.get('user_id') and not session.get('is_admin'):
        c.close()
        conn.close()
        return "You are not authorized to edit this auction.", 403

    # Prevent editing if bids have been placed (for non-admins)
    # Admins are allowed to edit, which can be useful for moderation.
    if auction['bid_count'] > 0 and not session.get('is_admin'):
        c.close()
        conn.close()
        return render_template('edit-auction.html', auction=auction, error='Cannot edit an auction that already has bids.')

    # Prevent editing if auction has ended
    if auction['end_time'] < datetime.now():
        c.close()
        conn.close()
        # Redirect to the appropriate dashboard
        redirect_url = url_for('admin_auctions') if session.get('is_admin') else url_for('dashboard')
        return redirect(redirect_url)

    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        end_time = request.form.get('end_time')
        category = request.form.get('category')
        history_link = request.form.get('history_link')
        file = request.files.get('image_file')
        # Add a file size limit (e.g., 5MB)
        if file and len(file.read()) > 5 * 1024 * 1024:
            c.close(); conn.close()
            return render_template('edit-auction.html', auction=auction, error='File is too large. The limit is 5MB.')
        file.seek(0) # Reset file pointer after reading

        if not all([title, description, end_time, category]):
            c.close()
            conn.close()
            return render_template('edit-auction.html', auction=auction, error='All fields except image are required.')

        image_url = auction['image_url']  # Keep old image by default
        if file and file.filename:
            allowed_exts = {'jpg', 'jpeg', 'png', 'gif', 'pdf', 'webp', 'bmp', 'tiff', 'svg'}
            ext = file.filename.rsplit('.', 1)[-1].lower()
            if ext in allowed_exts:
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                file_path = os.path.join(UPLOAD_FOLDER, filename)
                file.save(file_path)
                # Store a direct URL path for the uploaded file
                image_url = f"/uploads/{filename}"

        c.execute('''UPDATE auctions SET title = %s, description = %s, end_time = %s, category = %s, history_link = %s, image_url = %s WHERE id = %s''', (title, description, end_time, category, history_link, image_url, auction_id))
        conn.commit()
        c.close()
        conn.close()
        redirect_url = url_for('admin_auctions') if session.get('is_admin') else url_for('dashboard')
        return redirect(redirect_url)

    # For GET request
    c.close()
    conn.close()
    return render_template('edit-auction.html', auction=auction)
@app.route('/profile')
def profile():                              
    if 'user_id' not in session:
        return redirect(url_for('index'))
    conn = get_db_connection()
    if not conn: return "Database connection failed", 500
    c = conn.cursor(dictionary=True)
    c.execute('SELECT name, email, created_at, email_verified FROM users WHERE id = %s', (session['user_id'],))
    user = c.fetchone()
    c.close()
    conn.close()
    if not user:
        return "User not found", 404
    return render_template('profile.html', user=user)
@app.route('/profile/edit', methods=['GET', 'POST'])
def edit_profile():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    conn = get_db_connection()
    if not conn: return "Database connection failed", 500
    c = conn.cursor(dictionary=True, buffered=True)
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        otp = request.form.get('otp')
        # Optionally, add password change logic here
        if not name or not email:
            c.execute('SELECT name, email FROM users WHERE id = %s', (session['user_id'],))
            user = c.fetchone()
            c.close()
            conn.close()
            return render_template('edit-profile.html', user=user, error='All fields are required.')
        # If email is being changed, require OTP
        c.execute('SELECT email FROM users WHERE id = %s', (session['user_id'],))
        current_email = c.fetchone()['email']
        if email != current_email:
            # Check OTP
            if 'email_change_otp' not in session or 'email_change_new' not in session or session['email_change_new'] != email:
                c.execute('SELECT name, email FROM users WHERE id = %s', (session['user_id'],))
                user = c.fetchone()
                c.close()
                conn.close()
                return render_template('edit-profile.html', user=user, error='Please request OTP for your current email before changing.')
            if not otp or str(otp) != str(session['email_change_otp']):
                c.execute('SELECT name, email FROM users WHERE id = %s', (session['user_id'],))
                user = c.fetchone()
                c.close()
                conn.close()
                return render_template('edit-profile.html', user=user, error='Invalid OTP for email change.')
            # Check if new email is taken
            c.execute('SELECT id FROM users WHERE email = %s AND id != %s', (email, session['user_id']))
            if c.fetchone():
                c.execute('SELECT name, email FROM users WHERE id = %s', (session['user_id'],))
                user = c.fetchone()
                c.close()
                conn.close()
                return render_template('edit-profile.html', user=user, error='Email already in use.')
            # OTP correct, update email
            c.execute('UPDATE users SET name = %s, email = %s WHERE id = %s', (name, email, session['user_id']))
            conn.commit()
            c.close()
            conn.close()
            session['user_name'] = name
            session.pop('email_change_otp', None)
            session.pop('email_change_new', None)
            return redirect(url_for('profile'))
        else:
            # Name change only
            c.execute('UPDATE users SET name = %s WHERE id = %s', (name, session['user_id']))
            conn.commit()
            c.close()
            conn.close()
            session['user_name'] = name
            return redirect(url_for('profile'))
    else:
        c.execute('SELECT name, email FROM users WHERE id = %s', (session['user_id'],))
        user = c.fetchone()
        c.close()
        conn.close()
        return render_template('edit-profile.html', user=user)

# Route to request OTP for email change
@app.route('/profile/request-email-change-otp', methods=['POST'])
def request_email_change_otp():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    new_email = request.form.get('new_email')
    if not new_email:
        return redirect(url_for('edit_profile'))
    # Generate OTP and store in session
    otp = random.randint(100000, 999999)
    session['email_change_otp'] = otp
    session['email_change_new'] = new_email
    print(f"[DEMO] OTP for changing email to {new_email}: {otp}")
    return redirect(url_for('edit_profile'))
# Profile page route
@app.route('/users')
def list_users():
    conn = get_db_connection()
    if not conn: return "Database connection failed", 500
    c = conn.cursor(dictionary=True)
    c.execute('SELECT id, name, email, created_at FROM users ORDER BY created_at ASC')
    users = c.fetchall()
    c.close()
    conn.close()
    return render_template('users.html', users=users)


# --- OTP Email Verification Demo ---
# Route to request email verification (send OTP)
@app.route('/profile/request-verify', methods=['POST'])
def request_email_verification():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    # Generate a 6-digit OTP
    otp = random.randint(100000, 999999)
    session['otp'] = otp
    session['otp_user_id'] = session['user_id']
    print(f"[DEMO] OTP for user {session['user_id']}: {otp}")  # In real app, send via email
    return redirect(url_for('verify_otp'))

# Route to show OTP entry form
@app.route('/profile/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    if 'user_id' not in session or 'otp' not in session or session.get('otp_user_id') != session['user_id']:
        return redirect(url_for('profile'))
    error = None
    if request.method == 'POST':
        entered_otp = request.form.get('otp')
        if entered_otp and str(entered_otp) == str(session['otp']):
            # Mark email as verified
            conn = get_db_connection()
            if not conn: return render_template('verify-otp.html', error='Database connection failed.')
            c = conn.cursor()
            c.execute('UPDATE users SET email_verified = 1 WHERE id = %s', (session['user_id'],))
            conn.commit()
            c.close()
            conn.close()
            session.pop('otp', None)
            session.pop('otp_user_id', None)
            return redirect(url_for('profile'))
        else:
            error = 'Invalid OTP. Please try again.'
    return render_template('verify-otp.html', error=error)

# --- Admin Panel Routes ---

@app.route('/admin')
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    user_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM auctions")
    auction_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders")
    order_count = c.fetchone()[0]
    c.close()
    conn.close()
    return render_template('admin/dashboard.html', user_count=user_count, auction_count=auction_count, order_count=order_count)

@app.route('/admin/users')
@admin_required
def admin_users():
    conn = get_db_connection()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT * FROM users ORDER BY created_at DESC")
    users = c.fetchall()
    c.close()
    conn.close()
    return render_template('admin/users.html', users=users)

@app.route('/admin/user/<int:user_id>/toggle-admin', methods=['POST'])
@admin_required
def toggle_admin_status(user_id):
    # Prevent an admin from demoting themselves to avoid getting locked out
    if user_id == session.get('user_id'):
        return redirect(url_for('admin_users'))

    conn = get_db_connection()
    c = conn.cursor()
    # Get current status to flip it
    c.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
    user = c.fetchone()
    if user:
        new_status = not user[0]  # Flips 0 to True, and 1 to False
        c.execute("UPDATE users SET is_admin = %s WHERE id = %s", (new_status, user_id))
        conn.commit()
    c.close()
    conn.close()
    return redirect(url_for('admin_users'))

@app.route('/admin/auctions')
@admin_required
def admin_auctions():
    conn = get_db_connection()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT a.*, u.name as seller_name FROM auctions a JOIN users u ON a.seller_id = u.id ORDER BY a.created_at DESC")
    auctions = c.fetchall()
    c.close()
    conn.close()
    return render_template('admin/auctions.html', auctions=auctions)

@app.route('/admin/auction/<int:auction_id>/delete', methods=['POST'])
@admin_required
def delete_auction(auction_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM bids WHERE auction_id = %s", (auction_id,))
    c.execute("DELETE FROM auctions WHERE id = %s", (auction_id,))
    conn.commit()
    c.close()
    conn.close()
    return redirect(url_for('admin_auctions'))

@app.route('/admin/orders')
@admin_required
def admin_orders():
    conn = get_db_connection()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT o.*, a.title as auction_title, u.name as buyer_name FROM orders o JOIN auctions a ON o.auction_id = a.id JOIN users u ON o.user_id = u.id ORDER BY o.created_at DESC")
    orders = c.fetchall()
    c.close()
    conn.close()
    order_statuses = ['Ordered', 'Picked', 'Shipped', 'Delivered', 'Cancelled']
    return render_template('admin/orders.html', orders=orders, statuses=order_statuses)

@app.route('/admin/order/<int:order_id>/update_status', methods=['POST'])
@admin_required
def update_order_status(order_id):
    new_status = request.form.get('status')
    conn = get_db_connection()
    c = conn.cursor(dictionary=True)
    # Get user_id for notification
    c.execute("SELECT user_id FROM orders WHERE id = %s", (order_id,))
    order = c.fetchone()

    c.execute("UPDATE orders SET order_status = %s WHERE id = %s", (new_status, order_id))
    if order:
        create_notification(c, order['user_id'], f"Your order #{order_id} has been updated to {new_status}.", f"/dashboard")

    c.close()
    conn.commit()
    conn.close()
    socketio.emit('status_update', {'order_id': order_id, 'status': new_status})
    return redirect(url_for('admin_orders'))

@app.route('/api/notifications/mark-read', methods=['POST'])
def mark_notifications_as_read():
    if 'user_id' not in session:
        return jsonify({'success': False}), 401
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE notifications SET is_read = 1 WHERE user_id = %s", (session['user_id'],))
    conn.commit()
    c.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/notifications/summary')
def notifications_summary():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        
    c = conn.cursor(dictionary=True)
    
    # Get unread count
    c.execute("SELECT COUNT(*) as count FROM notifications WHERE user_id = %s AND is_read = 0", (session['user_id'],))
    unread_count = c.fetchone()['count']
    
    # Get recent notifications
    c.execute("SELECT * FROM notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT 10", (session['user_id'],))
    notifications = c.fetchall()
    
    c.close()
    conn.close()
    
    # Ensure datetime objects are JSON serializable
    for notification in notifications:
        if isinstance(notification.get('created_at'), datetime):
            notification['created_at'] = notification['created_at'].isoformat()
            
    return jsonify({'success': True, 'unread_count': unread_count, 'notifications': notifications})
# The block below is for local development only.
if __name__ == '__main__':
    # This block allows for command-line actions like initializing the database.
    # This is a robust pattern for both development and production deployment.
    if len(sys.argv) > 1 and sys.argv[1] == 'init':
        print("🚀 Initializing database and creating sample data...")
        init_db()
        create_sample_data()
        print("✅ Database initialized successfully.")
    else:
        print("🚀 Starting Flask development server...")
        # Note: In development, run `python app.py init` once to set up the database.
        # The server no longer does this automatically on every start.
        socketio.run(app, host='127.0.0.1', port=5000, debug=True)
