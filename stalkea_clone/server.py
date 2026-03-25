from flask import Flask, request, jsonify, session, send_from_directory, redirect, url_for, make_response
import os
import time
import json
import requests
import psycopg2
from urllib.parse import urlparse
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta

# Inicializa Flask
# REMOVIDO static_url_path='' pois causa conflito com rotas explícitas em alguns ambientes
app = Flask(__name__) 
app.secret_key = 'HORNET600_SECRET_KEY_PRODUCTION' # Chave secreta para sessões

@app.before_request
def log_request_info():
    # Filtra logs para reduzir ruído
    ignored_prefixes = ['/static', '/api/auth/check', '/api/admin/live', '/api/admin/orders', '/api/status']
    should_log = True
    for prefix in ignored_prefixes:
        if request.path.startswith(prefix):
            should_log = False
            break
            
    if should_log:
        real_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ',' in real_ip: real_ip = real_ip.split(',')[0].strip()
        print(f"📡 Request: {request.method} {request.path} | Remote: {real_ip}")
        
        # --- IP BLOCKING (Dynamic DB + Static) ---
        BLOCKED_IPS = ['31.22.201.99', '87.196.72.7']
        if real_ip in BLOCKED_IPS:
            print(f"🚫 BLOCKED IP ATTEMPT (Static): {real_ip}")
            return "Acesso Negado / Access Denied", 403
            
        # Check DB for banned IP
        try:
            conn = get_db_connection()
            if conn:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM blocked_ips WHERE ip = %s", (real_ip,))
                is_banned = cur.fetchone()
                cur.close()
                conn.close()
                if is_banned:
                    print(f"🚫 BLOCKED IP ATTEMPT (DB): {real_ip}")
                    return "Acesso Negado / Access Denied", 403
        except Exception as e:
            print(f"⚠️ DB Block Check Error: {e}")

# --- CONFIGURAÇÃO E DADOS ---
# Define diretório base absoluto para evitar erros de CWD no Railway
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STALKEA_BASE = 'https://stalkea.ai/api'
ALLOWED_IMAGE_PROXY_HOSTS = (
    'instagram.com',
    'cdninstagram.com',
    'fbcdn.net'
)

# DATABASE URL (Suporte para Vercel Postgres: POSTGRES_URL)
DATABASE_URL = os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL') or 'postgresql://postgres:ZciydaCzmAgnGnzrztdzmMONpqHEPNxK@yamabiko.proxy.rlwy.net:32069/railway'

# --- DB HELPERS ---

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"❌ DB Connection Error: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            # Tabela de Pedidos
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    transaction_id VARCHAR(50) UNIQUE,
                    method VARCHAR(20),
                    amount FLOAT,
                    status VARCHAR(20) DEFAULT 'PENDING',
                    payer_json TEXT,
                    reference_data_json TEXT,
                    waymb_data_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # Tabela de Configurações
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key VARCHAR(50) PRIMARY KEY,
                    value TEXT
                );
            """)
            # Tabela Active Sessions (Live View)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS active_sessions (
                    session_id TEXT PRIMARY KEY,
                    ip TEXT,
                    user_agent TEXT,
                    page TEXT,
                    type TEXT,
                    meta_json TEXT,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    session_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Tabela Daily Visits (Contador de Visitas Únicas)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_visits (
                    id SERIAL PRIMARY KEY,
                    ip TEXT,
                    user_agent TEXT,
                    visit_date DATE DEFAULT CURRENT_DATE
                );
            """)

            # Tabela Blocked IPs (Banimento Dinâmico)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS blocked_ips (
                    ip TEXT PRIMARY KEY,
                    reason TEXT,
                    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # MIGRATION: Ensure session_start exists (ALTER TABLE IF NOT EXISTS approach)
            try:
                cur.execute("ALTER TABLE active_sessions ADD COLUMN IF NOT EXISTS session_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
            except Exception as e:
                print(f"⚠️ Migration warning (session_start): {e}")
                conn.rollback() # Rollback em caso de erro para não travar o commit principal

            conn.commit()
            print("✅ Tabelas 'orders', 'active_sessions' e 'daily_visits' verificadas/criadas com sucesso.")
            cur.close()
            conn.close()
        except Exception as e:
            print(f"❌ Erro ao criar tabelas: {e}")

# Inicializa DB no startup
try:
    init_db()
except:
    pass

# --- IN-MEMORY STORAGE (REMOVIDO - Migrado para SQL) ---
# active_sessions agora é uma tabela no PostgreSQL


# --- FUNÇÕES DE PEDIDOS (MIGRADAS PARA SQL) ---

def load_orders():
    conn = get_db_connection()
    if not conn: return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM orders ORDER BY created_at DESC")
        rows = cur.fetchall()
        
        orders = []
        for row in rows:
            # Reconstrói objeto parecido com o JSON original
            order = dict(row)
            order['payer'] = json.loads(row['payer_json']) if row['payer_json'] else {}
            order['reference_data'] = json.loads(row['reference_data_json']) if row['reference_data_json'] else {}
            order['waymb_data'] = json.loads(row['waymb_data_json']) if row['waymb_data_json'] else {}
            
            # FIX: Copiar reference_data para meta para compatibilidade com Admin Frontend
            order['meta'] = order['reference_data']
            
            # Formata data para string ISO se necessário, ou deixa datetime
            if isinstance(order['created_at'], datetime):
                order['created_at'] = order['created_at'].isoformat()
            orders.append(order)
            
        cur.close()
        conn.close()
        return orders
    except Exception as e:
        print(f"❌ Erro ao carregar orders: {e}")
        return []

def save_order(order_data):
    conn = get_db_connection()
    if not conn: 
        print("❌ DB indisponível para salvar ordem")
        return
        
    try:
        cur = conn.cursor()
        
        payer_json = json.dumps(order_data.get('payer', {}))
        ref_json = json.dumps(order_data.get('reference_data', {}))
        waymb_json = json.dumps(order_data.get('waymb_data', {}))
        tx_id = order_data.get('transaction_id')
        method = order_data.get('method')
        amount = order_data.get('amount')
        status = order_data.get('status')
        
        # Tentar recuperar dados da sessão (Live View) para enriquecer o pedido
        session_data = {}
        try:
            # Busca sessão pelo IP do request ou cookie se viesse (aqui pegamos o payer ip ou tentamos linkar)
            # Como create_payment vem do backend as vezes, o IP pode ser do server. 
            # Mas vamos tentar pelo IP salvo no tracking se tiver match recente??
            # Simplificando: Vamos tentar pegar o SEARCHED_PROFILE de active_sessions pelo active_session mais recente deste IP
            pass # TODO: Melhorar correlação
        except: pass

        cur.execute("""
            INSERT INTO orders (transaction_id, method, amount, status, payer_json, reference_data_json, waymb_data_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            RETURNING id;
        """, (tx_id, method, amount, status, payer_json, ref_json, waymb_json))
        
        new_id = cur.fetchone()[0]
        conn.commit()
        print(f"💾 Pedido salvo no PostgreSQL: ID {new_id}")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Erro ao salvar ordem no DB: {e}")

# --- ROTAS DE SERVIÇO DE ARQUIVOS (FRONTEND) ---

@app.route('/')
def root():
    templates_dir = os.path.join(BASE_DIR, 'templates')
    return send_from_directory(templates_dir, 'home.html')

# Rotas do Admin (Frontend)
@app.route('/admin/login')
def admin_login_page():
    templates_dir = os.path.join(BASE_DIR, 'templates')
    return send_from_directory(templates_dir, 'admin_login.html')

@app.route('/admin')
@app.route('/admin/')
@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('logged_in'):
        return redirect('/admin/login')
    templates_dir = os.path.join(BASE_DIR, 'templates')
    return send_from_directory(templates_dir, 'admin_index.html')


# --- API MIGRATION (PHP -> PYTHON COMPATIBILITY) ---

@app.route('/api/get-ip.php', methods=['GET'])
def api_get_ip():
    try:
        headers = {
            'Referer': 'https://stalkea.ai/',
            'User-Agent': request.headers.get('User-Agent')
        }
        resp = requests.get(f"{STALKEA_BASE}/get-ip.php", headers=headers, timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        print(f"Error fetching IP: {e}")
        return jsonify({'ip': request.remote_addr or '127.0.0.1'})

@app.route('/api/config.php', methods=['GET'])
def api_config():
    try:
        headers = {
            'Referer': 'https://stalkea.ai/',
            'User-Agent': request.headers.get('User-Agent')
        }
        resp = requests.get(f"{STALKEA_BASE}/config.php", headers=headers, timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        print(f"Error fetching config: {e}")
        # Default config fallback
        return jsonify({
            'status': 'success',
            'data': {
                'pixel_fb': '',
                'gtm_id': '',
                'checkout_url': 'cta.html'
            }
        })

@app.route('/api/instagram.php', methods=['GET'])
def api_instagram():
    try:
        query_string = request.query_string.decode('utf-8')
        url = f"{STALKEA_BASE}/instagram.php"
        if query_string:
            url += f"?{query_string}"
            
        headers = {
            'Referer': 'https://stalkea.ai/',
            'User-Agent': request.headers.get('User-Agent')
        }
        resp = requests.get(url, headers=headers, timeout=10)
        return jsonify(resp.json())
    except Exception as e:
        print(f"Error in instagram proxy: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/image-proxy', methods=['GET'])
def api_image_proxy():
    """
    Proxy same-origin para imagens externas (especialmente Instagram),
    evitando bloqueios de CORP/COEP no navegador.
    """
    raw_url = (request.args.get('url') or '').strip()
    if not raw_url:
        return jsonify({'status': 'error', 'message': 'missing url'}), 400

    if raw_url.startswith('//'):
        raw_url = f"https:{raw_url}"
    elif not raw_url.startswith('http://') and not raw_url.startswith('https://'):
        raw_url = f"https://{raw_url.lstrip('/')}"

    try:
        parsed = urlparse(raw_url)
    except Exception:
        return jsonify({'status': 'error', 'message': 'invalid url'}), 400

    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return jsonify({'status': 'error', 'message': 'invalid scheme/host'}), 400

    host = parsed.hostname.lower()
    allowed_host = any(host == d or host.endswith(f".{d}") or d in host for d in ALLOWED_IMAGE_PROXY_HOSTS)
    if not allowed_host:
        return jsonify({'status': 'error', 'message': 'host not allowed'}), 403

    try:
        upstream_headers = {
            'User-Agent': request.headers.get('User-Agent', 'Mozilla/5.0'),
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            'Referer': 'https://www.instagram.com/'
        }
        upstream = requests.get(raw_url, headers=upstream_headers, timeout=15, allow_redirects=True)
    except Exception as e:
        print(f"Error in image proxy fetch: {e}")
        return jsonify({'status': 'error', 'message': 'upstream fetch failed'}), 502

    if upstream.status_code >= 400:
        return jsonify({'status': 'error', 'message': f'upstream status {upstream.status_code}'}), 502

    content_type = upstream.headers.get('Content-Type', 'image/jpeg')
    if not content_type.startswith('image/'):
        return jsonify({'status': 'error', 'message': 'upstream did not return image'}), 502

    response = make_response(upstream.content)
    response.headers['Content-Type'] = content_type
    response.headers['Cache-Control'] = 'public, max-age=300'
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Cross-Origin-Resource-Policy'] = 'cross-origin'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

@app.route('/api/leads.php', methods=['GET', 'POST'])
@app.route('/api/instagram.php/leads.php', methods=['GET', 'POST'])
def api_leads():
    try:
        headers = {
            'Referer': 'https://stalkea.ai/',
            'User-Agent': request.headers.get('User-Agent')
        }
        
        if request.method == 'POST':
            resp = requests.post(f"{STALKEA_BASE}/leads.php", json=request.json, headers=headers, timeout=10)
            return jsonify(resp.json())
        else:
            query_string = request.query_string.decode('utf-8')
            url = f"{STALKEA_BASE}/leads.php"
            if query_string:
                url += f"?{query_string}"
            resp = requests.get(url, headers=headers, timeout=10)
            return jsonify(resp.json())
            
    except Exception as e:
        print(f"Error in leads proxy: {e}")
        if request.method == 'GET':
             return jsonify({'success': True, 'searched_remaining': 999})
        return jsonify({'success': True, 'lead_id': f"demo_{int(time.time())}"})

# --- API: AUTENTICAÇÃO ---

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if username == 'admin' and password == 'Hornet600':
        session['logged_in'] = True
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'message': 'Credenciais inválidas'}), 401

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    session.pop('logged_in', None)
    return jsonify({'success': True})

@app.route('/api/auth/check', methods=['GET'])
def api_auth_check():
    return jsonify({'logged_in': session.get('logged_in', False)})

# Helper para verificar autenticação em rotas de admin
def check_auth():
    return session.get('logged_in', False)

# --- API: TRACKING & LIVE VIEW ---

@app.route('/api/track/event', methods=['POST'])
def track_event():
    """Recebe eventos do frontend para Live View e Analytics"""
    data = request.json
    
    # IGNORAR ADMIN do Tracking
    page_url = data.get('url', '')
    if '/admin' in page_url or 'admin_index' in page_url:
        return jsonify({'status': 'ignored_admin'})
        
    # IGNORAR BOTS (Apenas os óbvios)
    user_agent = request.headers.get('User-Agent', '').lower()
    bot_keywords = ['bot', 'crawl', 'spider', 'slurp', 'bing']
    if any(keyword in user_agent for keyword in bot_keywords):
        return jsonify({'status': 'ignored_bot'})
    
    # Detecção de IP Real
    real_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ',' in real_ip:
        real_ip = real_ip.split(',')[0].strip()

    # Identificação da Sessão (Cookie ou IP)
    sid = request.cookies.get('session_id')
    if not sid: sid = real_ip

    event_type = data.get('type')
    page_url = data.get('url')
    new_meta = data.get('meta', {})
    
    conn = get_db_connection()
    if not conn: return jsonify({'status': 'db_unavailable'})

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. Tentar pegar sessão existente para merge de metadados
        cur.execute("SELECT * FROM active_sessions WHERE session_id = %s", (sid,))
        current_session = cur.fetchone()

        final_meta = new_meta
        if current_session:
            # Merge JSON
            current_meta_json = current_session['meta_json']
            current_meta = json.loads(current_meta_json) if current_meta_json else {}
            
            # Preserva campos importantes
            if 'searched_profile' in current_meta and 'searched_profile' not in new_meta:
                 new_meta['searched_profile'] = current_meta['searched_profile']
            if 'location' in current_meta:
                new_meta['location'] = current_meta['location']
            
            final_meta = {**current_meta, **new_meta}
        else:
            # Nova Sessão - GeoIP apenas se não tiver
            if 'location' not in final_meta:
                try:
                     if real_ip and len(real_ip) > 7 and not real_ip.startswith('127') and not real_ip.startswith('10.'):
                         geo_url = f"http://ip-api.com/json/{real_ip}?fields=status,countryCode,city"
                         # Pequeno timeout para não travar a thread
                         geo_resp = requests.get(geo_url, timeout=1.5).json()
                         if geo_resp.get('status') == 'success':
                              location = f"{geo_resp.get('countryCode')} ({geo_resp.get('city')})"
                              final_meta['location'] = location
                except: pass

        # 2. UPSERT (Insert or Update)
        meta_json_str = json.dumps(final_meta)
        
        user_agent = request.headers.get('User-Agent') or 'Unknown'

        cur.execute("""
            INSERT INTO active_sessions (session_id, ip, user_agent, page, type, meta_json, last_seen, session_start)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (session_id) DO UPDATE 
            SET ip = EXCLUDED.ip,
                user_agent = EXCLUDED.user_agent,
                page = EXCLUDED.page,
                type = EXCLUDED.type,
                meta_json = EXCLUDED.meta_json,
                last_seen = NOW();
        """, (sid, real_ip, user_agent, page_url, event_type, meta_json_str))

        # 3. Registrar Visita Diária Única (Daily Visits)
        # Verifica se já existe visita deste IP hoje
        cur.execute("""
            INSERT INTO daily_visits (ip, user_agent, visit_date)
            SELECT %s, %s, CURRENT_DATE
            WHERE NOT EXISTS (
                SELECT 1 FROM daily_visits 
                WHERE ip = %s AND visit_date = CURRENT_DATE
            )
        """, (real_ip, user_agent, real_ip))
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"✅ Session Tracked (SQL): {sid[:10]}...")

    except Exception as e:
        print(f"Tracking Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)})
        
    return jsonify({'status': 'ok'})

# --- COMUNICAÇÃO (EMAIL / PUSHCUT) ---

def get_config(key, default=None):
    """Busca configuração no DB (prioridade) ou ENV"""
    try:
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
            res = cur.fetchone()
            cur.close()
            conn.close()
            if res and res[0]:
                return res[0]
    except Exception as e:
        print(f"⚠️ Erro ao buscar config {key}: {e}")
        
    return os.environ.get(key, default)

def send_email_via_sendgrid(to_email, subject, content_html):
    """Envia email via SendGrid API V3"""
    api_key = get_config('SENDGRID_API_KEY')
    
    # Email verificado fornecido pelo usuário
    # ou configurado no ENV 'SENDGRID_FROM_EMAIL'
    from_email = get_config('SENDGRID_FROM_EMAIL', 'support@brasilconectasolucoes.shop')
    
    if not api_key:
        print("⚠️ SendGrid Key não configurada. Email não enviado.")
        return False
        
    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "personalizations": [{
            "to": [{"email": to_email}]
        }],
        "from": {"email": from_email, "name": "InstaSpy Support"},
        "subject": subject,
        "content": [{
            "type": "text/html",
            "value": content_html
        }]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=5)
        if response.status_code in [200, 201, 202]:
            print(f"✅ Email enviado para {to_email} (via SendGrid)")
            return True
        else:
            print(f"❌ Erro SendGrid: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Erro ao conectar SendGrid: {e}")
        return False

def send_order_created_email(order_data, method, amount, payment_details=None):
    """
    Envia email de 'Pedido Criado' com templates distintos para MBWAY (Urgência) e Multibanco.
    """
    payer = order_data.get('payer', {})
    email = payer.get('email')
    
    if not email or '@' not in email: return
    
    name = payer.get('name', 'Cliente')
    order_id = order_data.get('id')
    
    # URL Base para imagens (Railway)
    BASE_URL = "https://sptool.vercel.app"
    
    # Common Styles
    common_style = """
        <style>
            @keyframes pulse-green {
                0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); transform: scale(1); }
                70% { box-shadow: 0 0 0 10px rgba(16, 185, 129, 0); transform: scale(1.02); }
                100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); transform: scale(1); }
            }
            .btn-pulse { animation: pulse-green 2s infinite; }
        </style>
    """
    
    if method == 'mbway':
        subject = "🔥 SÓ FALTAM 5 MINUTOS! Finaliza o teu acesso"
        # Link para instruções/status (se houver, senao vai pro checkout? mbway ja foi gerado, entao status)
        action_link = f"{BASE_URL}/pages/mbway-payment.html?amount={amount}&phone={payer.get('phone')}&order_id={order_id}"

        html_content = f"""
        {common_style}
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; background: #e5e7eb; padding: 40px;">
            <div style="background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);">
                <div style="background: #111827; padding: 16px; text-align: center;">
                    <span style="color: white; font-weight: bold;">Pedido Criado (MBWAY)</span>
                </div>
                <div style="padding: 40px; background: #f3f4f6;">
                    <div style="max-width: 600px; margin: 0 auto; background: #18181b; border: 1px solid #27272a; border-radius: 12px; overflow: hidden; color: #ececec;">
                        <div style="padding: 24px; text-align: center; border-bottom: 1px solid #27272a;">
                            <img src="{BASE_URL}/assets/images/logos/logocheckout.png" alt="InstaSpy" style="height: 32px;">
                        </div>
                        <div style="padding: 32px; line-height: 1.6; color: #d4d4d8;">
                            <div style="text-align: center; margin-bottom: 24px;">
                                <div style="background-color: white; padding: 8px; border-radius: 8px; display: inline-block;">
                                    <img src="{BASE_URL}/assets/images/payment/mbway-logo.png" alt="MBWAY" style="height: 40px; display:block;">
                                </div>
                            </div>
                            
                            <p>Olá, <strong>{name}</strong>!</p>
                            <p>O teu pedido de acesso ao Painel Espião foi gerado.</p>
                            
                            <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.2); padding: 16px; border-radius: 8px; margin: 20px 0;">
                                <h3 style="margin:0 0 8px; color:#ef4444; font-size:18px;">🔥 SÓ FALTAM 5 MINUTOS!</h3>
                                <p style="margin:0; color:#fca5a5;">O pagamento via MBWAY só é válido por este curto período. Se não aprovares agora na app, o desconto será cancelado.</p>
                            </div>
                            
                            <p>Acede à tua aplicação MBWAY e confirma a transação de <strong style="color:white">{amount}€</strong> agora mesmo.</p>
                            
                            <div style="text-align: center; margin-top: 32px;">
                                <a href="{action_link}" style="background:#10B981; color:white; display:inline-block; padding: 16px 32px; text-decoration: none; border-radius: 8px; font-weight: bold; border:1px solid #059669; box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);">Confirmar na App MBWAY</a>
                                <p style="font-size:12px; color:#71717a; margin-top: 12px;">Esta oferta expira em instantes.</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        """
        
    elif method == 'multibanco':
        subject = "⏳ Garante o teu Preço Agora - Dados de Pagamento"
        ent = payment_details.get('entity', 'N/A')
        ref = payment_details.get('reference', 'N/A')
        
        # Link para página de instrucoes, onde copiar funciona
        action_link = f"{BASE_URL}/pages/multibanco-payment.html?entity={ent}&reference={ref}&amount={amount}"
        
        html_content = f"""
        {common_style}
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; background: #e5e7eb; padding: 40px;">
            <div style="background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);">
                <div style="background: #111827; padding: 16px; text-align: center;">
                    <span style="color: white; font-weight: bold;">Pedido Criado (Multibanco)</span>
                </div>
                <div style="padding: 40px; background: #f3f4f6;">
                    <div style="max-width: 600px; margin: 0 auto; background: #18181b; border: 1px solid #27272a; border-radius: 12px; overflow: hidden; color: #ececec;">
                        <div style="padding: 24px; text-align: center; border-bottom: 1px solid #27272a;">
                            <img src="{BASE_URL}/assets/images/logos/logocheckout.png" alt="InstaSpy" style="height: 32px;">
                        </div>
                        <div style="padding: 32px; line-height: 1.6; color: #d4d4d8;">
                            <div style="text-align: center; margin-bottom: 24px;">
                                <img src="{BASE_URL}/assets/images/payment/multibanco-logo.png" alt="Multibanco" style="height: 40px; background: white; padding: 4px; border-radius: 4px;">
                            </div>

                            <p>Olá, <strong>{name}</strong>!</p>
                            <p>A tua referência Multibanco foi gerada com sucesso.</p>
                            <p>Para garantir que o valor promocional de <strong style="color:white">{amount}€</strong> se mantém, recomendamos o pagamento imediato.</p>
                            
                            <div style="background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.2); padding: 16px; border-radius: 8px; margin: 20px 0; text-align:center;">
                                <p style="margin:0 0 4px; font-size:12px; text-transform:uppercase; color:#93c5fd;">Entidade</p>
                                <div style="margin-bottom: 16px;">
                                    <span style="font-weight:bold; font-size:20px; color:white; letter-spacing: 1px; vertical-align:middle;">{ent}</span>
                                    <a href="{action_link}" style="text-decoration:none; color:#60a5fa; font-size:12px; border:1px solid #60a5fa; padding:2px 6px; border-radius:4px; margin-left:8px;">COPIAR</a>
                                </div>
                                
                                <p style="margin:0 0 4px; font-size:12px; text-transform:uppercase; color:#93c5fd;">Referência</p>
                                <div style="margin-bottom: 16px;">
                                    <span style="font-weight:bold; font-size:20px; color:white; letter-spacing: 2px; vertical-align:middle;">{ref}</span>
                                    <a href="{action_link}" style="text-decoration:none; color:#60a5fa; font-size:12px; border:1px solid #60a5fa; padding:2px 6px; border-radius:4px; margin-left:8px;">COPIAR</a>
                                </div>
                                
                                <p style="margin:0 0 8px; font-size:12px; text-transform:uppercase; color:#93c5fd;">Valor</p>
                                <p style="margin:0; font-weight:bold; font-size:20px; color:#10B981;">{amount} €</p>
                            </div>

                            <p style="font-size: 14px; text-align: center; color: #a1a1aa;">O acesso será libertado automaticamente após o pagamento.</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        """
    else:
        return

    try:
        threading.Thread(target=send_email_via_sendgrid, args=(email, subject, html_content)).start()
    except Exception as e:
        print(f"⚠️ Erro ao iniciar thread de email criacao: {e}")

def send_payment_approved_email(order_data, amount):
    """Envia email de Pagamento Aprovado"""
    payer = order_data.get('payer') or order_data.get('payer_json') or {}
    if isinstance(payer, str): 
        try: payer = json.loads(payer)
        except: payer = {}
        
    email = payer.get('email')
    
    if not email or '@' not in email: return

    subject = "✅ Pagamento Confirmado! O teu acesso está a ser preparado"
    BASE_URL = "https://sptool.vercel.app"
    
    html_content = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; background: #e5e7eb; padding: 40px;">
        <div style="background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);">
            <div style="background: #111827; padding: 16px; text-align: center;">
                <span style="color: white; font-weight: bold;">Pagamento Confirmado</span>
            </div>
            <div style="padding: 40px; background: #f3f4f6;">
                <div style="max-width: 600px; margin: 0 auto; background: #18181b; border: 1px solid #27272a; border-radius: 12px; overflow: hidden; color: #ececec;">
                    <div style="padding: 24px; text-align: center; border-bottom: 1px solid #27272a;">
                        <img src="{BASE_URL}/assets/images/logos/logocheckout.png" alt="InstaSpy" style="height: 32px;">
                    </div>
                    <div style="padding: 32px; line-height: 1.6; color: #d4d4d8;">
                        <div style="text-align:center; margin-bottom:20px;">
                             <div style="background:#10B981; width:60px; height:60px; border-radius:50%; display:inline-flex; align-items:center; justify-content:center;">
                                <!-- Check Icon (Unicode for Email Compatibility) -->
                                <span style="color:white; font-size:32px; font-weight:bold; line-height:1;">✓</span>
                             </div>
                        </div>

                        <h2 style="text-align:center; color:white; margin-top:0;">Pagamento Confirmado!</h2>
                        
                        <p>Parabéns, <strong>{payer.get('name', 'Cliente')}</strong>!</p>
                        <p>O teu pagamento de <strong style="color:white">{amount}€</strong> foi validado com sucesso.</p>
                        
                        <div style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.2); padding: 20px; border-radius: 8px; margin: 20px 0;">
                            <h3 style="margin:0 0 10px; color:#10B981; font-size:16px;">O que acontece agora?</h3>
                            <p style="margin:0; color:#d1fae5;">A nossa equipa já iniciou a configuração do teu painel e a recolha dos dados solicitados. Por tratar-se de um processo minucioso, pedimos um prazo de <strong>até 24 horas</strong>.</p>
                        </div>

                        <p>Não te preocupes! Assim que o acesso estiver 100% pronto, receberás um novo email com o teu link exclusivo e a senha.</p>
                        
                        <div style="border-top: 1px solid #27272a; margin-top: 32px; padding-top: 20px; font-size: 13px; color: #52525b; text-align: center;">
                            ID do Pedido: #{order_data.get('id', 'N/A')}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """
    
    try:
        threading.Thread(target=send_email_via_sendgrid, args=(email, subject, html_content)).start()
    except Exception as e:
        print(f"⚠️ Erro ao iniciar thread de email aprovado: {e}")

def send_discount_recovery_email(email, name):
    """Envia email com Desconto de Recuperação (7.99€)"""
    if not email or '@' not in email: return
    
    subject = "🎁 Presente Especial: Termina o teu acesso por apenas €7,99"
    BASE_URL = "https://sptool.vercel.app"
    link = f"{BASE_URL}/pages/checkout.html?discount=true&email={email}&utm_source=email_recovery&coupon=RECUPERACAO_799"
    
    common_style = """
        <style>
            @keyframes pulse-green {
                0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); transform: scale(1); }
                70% { box-shadow: 0 0 0 10px rgba(16, 185, 129, 0); transform: scale(1.02); }
                100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); transform: scale(1); }
            }
        </style>
    """
    
    html_content = f"""
    {common_style}
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; background: #e5e7eb; padding: 40px;">
        <div style="background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);">
            <div style="background: #111827; padding: 16px; text-align: center;">
                <span style="color: white; font-weight: bold;">Recuperação com Desconto</span>
            </div>
            <div style="padding: 40px; background: #f3f4f6;">
                <div style="max-width: 600px; margin: 0 auto; background: #18181b; border: 1px solid #27272a; border-radius: 12px; overflow: hidden; color: #ececec;">
                    <div style="padding: 24px; text-align: center; border-bottom: 1px solid #27272a;">
                        <img src="{BASE_URL}/assets/images/logos/logocheckout.png" alt="InstaSpy" style="height: 32px;">
                    </div>
                    <div style="padding: 32px; line-height: 1.6; color: #d4d4d8;">
                        <p>Olá, <strong>{name}</strong>!</p>
                        <p>Vimos que não concluíste o teu acesso. Queremos muito ajudar-te.</p>
                        <p>Exclusivamente através deste email, libertámos um desconto para finalizares agora:</p>
                        
                        <div style="text-align:center; margin:40px 0;">
                            <p style="text-decoration:line-through; color:#71717a; margin-bottom:8px;">De 12,90€</p>
                            <h2 style="color:#10B981; margin:0 0 24px; font-size: 32px;">Por 7,99€</h2>
                            <a href="{link}" style="background:#10B981; color:white; display:inline-block; padding: 16px 32px; text-decoration: none; border-radius: 8px; font-weight: bold; box-shadow:0 0 20px rgba(16,185,129,0.3); animation: pulse-green 2s infinite;">RESGATAR DESCONTO</a>
                        </div>
                        
                        <p style="font-size:12px; color:#52525b; text-align:center;">Link seguro com cupão ativado.</p>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """
    
    try:
        # Envio síncrono aqui pois será chamado pelo Cron Job
        send_email_via_sendgrid(email, subject, html_content)
    except Exception as e:
        print(f"⚠️ Erro ao enviar email desconto: {e}")

@app.route('/api/payment', methods=['POST'])
def create_payment():
    """Cria transação WayMB e dispara Pushcut 'Pedido Gerado'"""
    try:
        data = request.json or {}
        amount = data.get('amount', 12.90)
        method = data.get('method', 'mbway')
        payer = data.get('payer', {})
        
        # VALIDAÇÃO DE EMAIL OBRIGATÓRIA
        email = payer.get('email')
        if not email or '@' not in email or '.' not in email:
             return jsonify({
                'success': False, 
                'error': 'Por favor, insira um email válido para receber o acesso.'
            }), 400

        
        # Identificar IP do Cliente
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ',' in client_ip: client_ip = client_ip.split(',')[0].strip()
        
        # --- RATE LIMIT CHECK (Anti-Spam) ---
        # Regra: Máx 1 pedido MBWAY e 1 Multibanco por IP nas últimas 24h (para evitar spam de pedidos não pagos)
        try:
            conn_chk = get_db_connection()
            if conn_chk:
                cur_chk = conn_chk.cursor()
                # Verifica pedidos feitos por este IP para este método nas últimas 24h
                # Como guardamos o IP no json reference_data, fazemos uma busca textual simples por eficiência e compatibilidade
                # O formato salvo será "client_ip": "x.x.x.x"
                search_pattern = f'%"client_ip": "{client_ip}"%'
                cur_chk.execute("""
                    SELECT count(*) FROM orders 
                    WHERE method = %s 
                    AND reference_data_json LIKE %s 
                    AND created_at > NOW() - INTERVAL '24 hours'
                """, (method.upper(), search_pattern))
                
                count = cur_chk.fetchone()[0]
                cur_chk.close()
                conn_chk.close()
                
                if count >= 1:
                    print(f"🚫 Bloqueio de Spam: IP {client_ip} já tem {count} pedido(s) de {method} hoje.")
                    return jsonify({
                        'success': False, 
                        'error': f'Você já gerou um pedido de {method.upper()} hoje. Realize o pagamento do anterior ou aguarde.'
                    }), 429
        except Exception as e:
            print(f"⚠️ Erro ao verificar rate limit: {e}")

        # Preparar payload para WayMB
        waymb_payload = {
            'client_id': os.environ.get('WAYMB_CLIENT_ID', 'modderstore_c18577a3'),
            'client_secret': os.environ.get('WAYMB_CLIENT_SECRET', '850304b9-8f36-4b3d-880f-36ed75514cc7'),
            'account_email': os.environ.get('WAYMB_ACCOUNT_EMAIL', 'modderstore@gmail.com'),
            'amount': amount,
            'method': method,
            'payer': {
                'name': payer.get('name', ''),
                'document': payer.get('document', ''),
                'phone': payer.get('phone', '')
            }
        }
        
        print(f"📤 Criando transação WayMB: {method.upper()} {amount}€ (IP: {client_ip})")
        
        # Chamar API WayMB
        waymb_response = requests.post(
            'https://api.waymb.com/transactions/create',
            json=waymb_payload,
            timeout=10
        )
        
        waymb_data = waymb_response.json()
        
        print(f"📥 WayMB Response Status: {waymb_response.status_code}")
        print(f"📥 WayMB Response Data: {json.dumps(waymb_data, indent=2)}")
        
        # WayMB retorna statusCode 200 para sucesso, não um campo 'success'
        if waymb_response.status_code == 200 and waymb_data.get('statusCode') == 200:
            tx_id = waymb_data.get('transactionID') or waymb_data.get('id')
            print(f"✅ Transação criada: {tx_id}")
            
            # 💾 SALVAR PEDIDO NO ADMIN
            
            # Tentar Enriquecer Dados com Sessão (Arruba, Tempo)
            extra_data = {}
            extra_data['client_ip'] = client_ip # SALVAR IP PARA RATE LIMIT
            
            # 1. Dados vindos do front (Ex: Bumps)
            if 'meta' in data:
                 extra_data = {**extra_data, **data['meta']}

            try:
                conn_sess = get_db_connection()
                if conn_sess:
                    cur_sess = conn_sess.cursor(cursor_factory=RealDictCursor)
                    # Busca sessão pelo telefone (às vezes salvo no meta) ou pelo IP recente
                    # Como aqui não temos o IP do cliente (request vem do back ou do cliente?), 
                    # create_payment é chamado pelo front, então request.remote_addr funciona!
                    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
                    if ',' in client_ip: client_ip = client_ip.split(',')[0].strip()

                    cur_sess.execute("""
                        SELECT meta_json, session_start 
                        FROM active_sessions 
                        WHERE ip = %s 
                        ORDER BY last_seen DESC LIMIT 1
                    """, (client_ip,))
                    sess = cur_sess.fetchone()
                    if sess:
                        meta_session = json.loads(sess['meta_json']) if sess['meta_json'] else {}
                        if 'searched_profile' in meta_session:
                            extra_data['searched_profile'] = meta_session['searched_profile']
                        
                        # Calcular Duração
                        if sess['session_start']:
                            duration = datetime.now() - sess['session_start']
                            # Formata duração hh:mm:ss
                            total_seconds = int(duration.total_seconds())
                            hours, remainder = divmod(total_seconds, 3600)
                            minutes, seconds = divmod(remainder, 60)
                            extra_data['duration_formatted'] = f"{hours}h {minutes}m {seconds}s"
                            extra_data['duration_seconds'] = total_seconds

                    cur_sess.close()
                    conn_sess.close()
            except Exception as e:
                print(f"⚠️ Erro ao vincular sessão ao pedido: {e}")

            # Merge WayMB data + Extra
            ref_data = waymb_data.get('referenceData', {}) or {}
            full_ref_data = {**ref_data, **extra_data} # Salva contexto no reference_data (banco)

            order_data = {
                'transaction_id': tx_id,
                'method': method.upper(),
                'amount': amount,
                'status': 'PENDING',
                'payer': payer,
                'reference_data': full_ref_data,
                'waymb_data': waymb_data
            }
            save_order(order_data)
            print(f"💾 Pedido salvo no admin: #{order_data.get('id')}")
            
            # 🔔 DISPARAR EMAILS DE CRIAÇÃO (MBWAY/MULTIBANCO)
            # FIX: WayMB retorna dados de Multibanco em 'referenceData', não 'paymentDetails'
            payment_details = waymb_data.get('referenceData') or waymb_data.get('paymentDetails') or {}
            send_order_created_email(order_data, method, amount, payment_details)

            # 🔔 DISPARAR PUSHCUT "PEDIDO GERADO"
            try:
                pushcut_url = "https://api.pushcut.io/XPTr5Kloj05Rr37Saz0D1/notifications/Aprovado%20delivery"
                pushcut_payload = {
                    "title": "Assinatura InstaSpy gerado",
                    "text": f"Novo pedido {method.upper()}\nValor: {amount}€\nID: {tx_id}\nEmail: {email}",
                    "isTimeSensitive": True
                }
                pushcut_response = requests.post(pushcut_url, json=pushcut_payload, timeout=4)
                print(f"📲 Pushcut 'Pedido Gerado' enviado - Status: {pushcut_response.status_code}")
                print(f"📲 Pushcut Response: {pushcut_response.text}")
            except Exception as e:
                print(f"⚠️ Erro ao enviar Pushcut: {e}")
                import traceback
                traceback.print_exc()
            
            return jsonify({
                'success': True,
                'data': waymb_data
            })
        else:
            error_msg = waymb_data.get('error', waymb_data.get('message', 'Erro desconhecido'))
            print(f"❌ WayMB retornou erro: {error_msg}")
            
            # [NEW] SALVAR FALHA NO ADMIN
            # Tentar Enriquecer Dados com Sessão (Arruba, Tempo)
            extra_data = {}
            extra_data['client_ip'] = client_ip # SALVAR IP
            
            # 1. Dados vindos do front
            if 'meta' in data:
                 extra_data = {**extra_data, **data['meta']}
            
            # Adicionar razão da falha
            extra_data['failure_reason'] = error_msg

            try:
                conn_sess = get_db_connection()
                if conn_sess:
                    cur_sess = conn_sess.cursor(cursor_factory=RealDictCursor)
                    # Busca sessão
                    client_ip_lookup = request.headers.get('X-Forwarded-For', request.remote_addr)
                    if ',' in client_ip_lookup: client_ip_lookup = client_ip_lookup.split(',')[0].strip()

                    cur_sess.execute("""
                        SELECT meta_json, session_start 
                        FROM active_sessions 
                        WHERE ip = %s 
                        ORDER BY last_seen DESC LIMIT 1
                    """, (client_ip_lookup,))
                    sess = cur_sess.fetchone()
                    if sess:
                        meta_session = json.loads(sess['meta_json']) if sess['meta_json'] else {}
                        if 'searched_profile' in meta_session:
                            extra_data['searched_profile'] = meta_session['searched_profile']
                        
                        if sess['session_start']:
                            duration = datetime.now() - sess['session_start']
                            total_seconds = int(duration.total_seconds())
                            hours, remainder = divmod(total_seconds, 3600)
                            minutes, seconds = divmod(remainder, 60)
                            extra_data['duration_formatted'] = f"{hours}h {minutes}m {seconds}s"
                            extra_data['duration_seconds'] = total_seconds

                    cur_sess.close()
                    conn_sess.close()
            except Exception as e:
                print(f"⚠️ Erro ao vincular sessão ao pedido (Falha): {e}")

            # Salvar pedido como FAILED
            order_data = {
                'transaction_id': f"FAILED-{int(time.time())}", # ID Fictício para não quebrar Unique Constraint se houver
                'method': method.upper(),
                'amount': amount,
                'status': 'FAILED',
                'payer': payer,
                'reference_data': extra_data, # Já contem failure_reason
                'waymb_data': waymb_data
            }
            save_order(order_data)
            print(f"💾 Pedido FALHO salvo no admin: {error_msg}")

            return jsonify({
                'success': False,
                'error': error_msg
            }), 400
            
    except Exception as e:
        print(f"❌ Erro ao criar pagamento: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/order/update-status', methods=['POST'])
def update_order_status():
    """Atualiza status do pedido (chamado pelo frontend após polling bem sucedido)"""
    try:
        data = request.json or {}
        tx_id = data.get('transaction_id')
        new_status = data.get('status')
        
        if not tx_id or not new_status:
            return jsonify({'success': False, 'error': 'Missing transaction_id or status'}), 400
            
        # ATUALIZAÇÃO VIA DB
        conn = get_db_connection()
        if not conn:
             return jsonify({'error': 'Database unavailable'}), 500
             
        try:
            cur = conn.cursor()
            
            # Atualiza status e pega dados para Pushcut APENAS SE mudou
            cur.execute("""
                UPDATE orders 
                SET status = %s 
                WHERE transaction_id = %s AND status IS DISTINCT FROM %s
                RETURNING id, method, amount, status, payer_json
            """, (new_status, tx_id, new_status))
            
            row = cur.fetchone()
            conn.commit()
            
            if row:
                user_id, method, amount, status, payer_json = row
                print(f"✅ Pedido #{user_id} atualizado via SQL para {new_status}")
                
                 # 🔔 DISPARAR EMAILS APROVADO
                if new_status == 'PAID':
                     order_data_mock = {'id': user_id, 'payer': payer_json}
                     send_payment_approved_email(order_data_mock, amount)
                
                 # 🔔 DISPARAR PUSHCUT SE PAGO
                if new_status == 'PAID':
                    # 🔔 DISPARAR EMAIL DE APROVADO
                    # row = (id, method, amount, status, payer_json...) -> precisamos ajustar o select RETURNING
                    # O fetchone retorna tuple based on RETURNING order
                    # Atualizar query para retornar payer_json
                    
                     # DISPARAR PUSHCUT (Mantido)
                    try:
                        pushcut_url = "https://api.pushcut.io/XPTr5Kloj05Rr37Saz0D1/notifications/Aprovado%20delivery"
                        pushcut_payload = {
                            "title": "🟢💸 Venda Aprovada 🟢",
                            "text": f"Pagamento confirmado {method}\nValor: {amount}€\nID: {tx_id}",
                            "isTimeSensitive": True
                        }
                        requests.post(pushcut_url, json=pushcut_payload, timeout=4)
                        print(f"📲 Pushcut 'Venda Aprovada' enviado")
                    except Exception as e:
                        print(f"⚠️ Erro ao enviar Pushcut de Venda: {e}")
            else:
                 print(f"⚠️ Pedido não encontrado para TX_ID {tx_id}")

            cur.close()
            conn.close()
            
        except Exception as db_err:
             print(f"Database error: {db_err}")
             return jsonify({'error': str(db_err)}), 500
             
        return jsonify({'success': True})
            
    except Exception as e:
        print(f"❌ Erro ao atualizar pedido: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/status', methods=['POST'])
def check_payment_status():
    """Consulta status de transação WayMB"""
    # ... resto da função existente

    try:
        data = request.json or {}
        tx_id = data.get('id')
        
        if not tx_id:
            return jsonify({'success': False, 'error': 'ID obrigatório'}), 400
        
        # Consultar WayMB
        waymb_response = requests.post(
            'https://api.waymb.com/transactions/info',
            json={
                'client_id': os.environ.get('WAYMB_CLIENT_ID', 'modderstore_c18577a3'),
                'client_secret': os.environ.get('WAYMB_CLIENT_SECRET', '850304b9-8f36-4b3d-880f-36ed75514cc7'),
                'id': tx_id
            },
            timeout=10
        )
        
        waymb_data = waymb_response.json()
        print(f"🔄 Polling Status: {waymb_response.status_code} | Data: {waymb_data.get('status', 'UNKNOWN')}")
        
        # WayMB retorna statusCode 200 para sucesso
        # CORREÇÃO: Aceitar 200 OK HTTP, mesmo sem statusCode no body (para endpoints tipo /info)
        is_success = waymb_response.status_code == 200
        if is_success and 'error' in waymb_data:
             is_success = False
             
        if is_success:
            # Pegar dados reais da transação
            tx_data = waymb_data
            
            # Se tiver 'data' dentro, usar
            if 'data' in waymb_data and isinstance(waymb_data['data'], dict):
                 tx_data = waymb_data['data']
                 
            # Fallback para transactionID
            if 'id' not in tx_data and 'transactionID' in waymb_data:
                 tx_data['id'] = waymb_data['transactionID']
            
            return jsonify({
                'success': True,
                'data': tx_data
            })
        else:
            error_msg = waymb_data.get('error', waymb_data.get('message', 'Erro ao consultar status'))
            print(f"❌ Polling Error: {error_msg}")
            return jsonify({
                'success': False,
                'error': error_msg
            }), 400
            
    except Exception as e:
        print(f"❌ Erro ao consultar status: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/webhook/waymb', methods=['POST'])
def webhook_waymb():
    """Recebe notificação de pagamento do Gateway (WayMB)"""
    try:
        data = request.json or {}
        print(f"🔔 Webhook WayMB recebido: {json.dumps(data)}")
        
        # WayMB envia: { "transactionID": "...", "amount": 12.9, "status": "COMPLETED", ... }
        # Ou formato simplificado dependendo da config.
        # Vamos ser tolerantes.
        
        tx_id = data.get('transactionID') or data.get('id')
        status = data.get('status', 'PAID') # Se chamou webhook, geralmente é sucesso
        
        if not tx_id:
             return jsonify({'error': 'Missing ID'}), 400

        # --- FILTRO PROZIS (OUTRO PROJETO) ---
        # Se for 12.99, mandar notificação para outro Pushcut e parar.
        try:
            val_str = str(data.get('amount', '0')).replace(',', '.')
            amount_val = float(val_str)
            
            # Comparar float com margem de erro
            if abs(amount_val - 12.99) < 0.01:
                print(f"👟 Detetada Venda PROZIS (12.99€) - ID: {tx_id}")
                try:
                    # URL Específica solicitada
                    prozis_url = "https://api.pushcut.io/ZJtFapxqtRs_gYalo0G8Z/notifications/MinhaNotifica%C3%A7%C3%A3o"
                    requests.post(prozis_url, json={
                        "title": "👟 Venda Aprovada Prozis",
                        "text": f"Valor: 12.99€\nID: {tx_id}",
                        "isTimeSensitive": True
                    }, timeout=4)
                    print("📲 Pushcut PROZIS enviado com sucesso")
                except Exception as ep:
                    print(f"⚠️ Erro ao enviar Pushcut Prozis: {ep}")
                    
                # Retorna sucesso para o Gateway parar de tentar
                return jsonify({'success': True, 'message': 'Prozis Handled'})
        except Exception as e_filter:
            print(f"⚠️ Erro no filtro de valor: {e_filter}")
            # Continua o fluxo normal se der erro no parse
            
        # --- FIM FILTRO PROZIS ---
             
        # Atualizar status no DB (Fluxo SpyInsta)
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            
            # Buscar pedido para pegar email e enviar notificação
            cur.execute("SELECT payer_json, amount, status, id, method FROM orders WHERE transaction_id = %s", (tx_id,))
            row = cur.fetchone()
            
            if row:
                current_status = row[2]
                
                # Evitar duplicar email se já estiver pago
                if current_status == 'PAID':
                    print(f"ℹ️ Pedido {tx_id} já processado anteriormente.")
                    cur.close()
                    conn.close()
                    return jsonify({'success': True, 'message': 'Already processed'})
                
                # Mudar para PAID
                cur.execute("UPDATE orders SET status = 'PAID', updated_at = NOW() WHERE transaction_id = %s", (tx_id,))
                conn.commit()
                print(f"✅ Pedido {tx_id} marcado como PAID via Webhook")
                
                # Enviar Email
                payer_json = row[0]
                amount = row[1]
                method = row[4] or 'N/A'
                
                # Reconstruir objeto order_data minimo para a função de email
                order_data = {'payer_json': payer_json}
                try:
                    send_payment_approved_email(order_data, amount)
                    print(f"📧 Email de aprovação disparado para pedido {tx_id}")
                except Exception as e:
                     print(f"⚠️ Erro ao disparar email aprovado no webhook: {e}")
                     
                # 🔔 DISPARAR PUSHCUT (Venda Aprovada)
                try:
                    pushcut_url = "https://api.pushcut.io/XPTr5Kloj05Rr37Saz0D1/notifications/Aprovado%20delivery"
                    pushcut_payload = {
                        "title": "🟢💸 Venda Aprovada 🟢",
                        "text": f"Pagamento confirmado {method.upper()}\nValor: {amount}€\nID: {tx_id}",
                        "isTimeSensitive": True
                    }
                    requests.post(pushcut_url, json=pushcut_payload, timeout=4)
                    print(f"📲 Pushcut 'Venda Aprovada' enviado via Webhook")
                except Exception as e:
                    print(f"⚠️ Erro ao enviar Pushcut de Venda (Webhook): {e}")

            else:
                print(f"⚠️ Webhook: Pedido {tx_id} não encontrado no DB")
                
            cur.close()
            conn.close()
            
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"❌ Erro no Webhook: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/debug/orders', methods=['GET'])
def debug_orders():
    """Retorna JSON bruto dos pedidos para debug"""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    try:
        # This ORDERS_FILE is not defined, assuming it was removed or is a placeholder.
        # For now, returning an empty list or a mock.
        # If it was meant to read from DB, load_orders() should be used.
        # Given the context, it's likely a remnant from a file-based storage.
        # Returning an empty list for now to avoid NameError.
        return jsonify([])
    except Exception as e:
        return jsonify({'error': str(e)})

# --- ERROR HANDLERS & DIAGNOSTICS ---
@app.errorhandler(404)
def page_not_found(e):
    return f"PYTHON SERVER 404: Path {request.path} not found. BASE_DIR: {BASE_DIR}", 404


@app.route('/api/admin/live', methods=['GET'])
def get_live_view():
    """Retorna usuários ativos nos últimos 5 minutos"""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    if not conn: return jsonify({'count':0, 'users':[]})
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Buscar sessões ativas nos últimos 2 minutos (para dar margem)
        cur.execute("""
            SELECT * FROM active_sessions 
            WHERE last_seen > (NOW() - INTERVAL '2 minutes')
            ORDER BY last_seen DESC
        """)
        rows = cur.fetchall()
        
        active_users = []
        for row in rows:
            user = dict(row)
            user['meta'] = json.loads(row['meta_json']) if row['meta_json'] else {}
            # Timestamp para o frontend (Unix)
            user['timestamp'] = row['last_seen'].timestamp()
            active_users.append(user)
            
        cur.close()
        conn.close()
        
        # Deduplicação por IP (Server-Side Logic)
        unique_sessions_map = {}
        
        for user in active_users:
            ip = user['ip'] or user['session_id'] # Fallback
            
            # Se IP já existe, comparar para ver qual manter
            if ip in unique_sessions_map:
                existing = unique_sessions_map[ip]
                
                # Critério 1: Ter Searched Profile (Prioridade Máxima)
                idx_existing_profile = 1 if (existing.get('meta') and existing['meta'].get('searched_profile')) else 0
                idx_new_profile = 1 if (user.get('meta') and user['meta'].get('searched_profile')) else 0
                
                if idx_new_profile > idx_existing_profile:
                     unique_sessions_map[ip] = user
                     continue
                elif idx_existing_profile > idx_new_profile:
                     continue # Mantém o existente
                     
                # Critério 2: Ser Checkout/Pagamento (Prioridade Média)
                idx_existing_page = 1 if ('checkout' in existing['page'] or 'payment' in existing['page']) else 0
                idx_new_page = 1 if ('checkout' in user['page'] or 'payment' in user['page']) else 0
                
                if idx_new_page > idx_existing_page:
                    unique_sessions_map[ip] = user
                    continue
                elif idx_existing_page > idx_new_page:
                    continue
                    
                # Critério 3: Mais Recente (Timestamp)
                if user['timestamp'] > existing['timestamp']:
                    unique_sessions_map[ip] = user
            else:
                unique_sessions_map[ip] = user
        
        final_users_list = list(unique_sessions_map.values())
        
        # Sort by timestamp desc
        final_users_list.sort(key=lambda x: x['timestamp'], reverse=True)

        return jsonify({
            'count': len(final_users_list),
            'users': final_users_list
        })
    except Exception as e:
        print(f"Live View Error: {e}")
        return jsonify({'count':0, 'users':[]})

@app.route('/api/admin/purge-live', methods=['POST'])
def purge_live_view():
    """Limpa sessões ativas do Live View manually"""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    if conn:
        try:
             cur = conn.cursor()
             cur.execute("DELETE FROM active_sessions")
             conn.commit()
             cur.close()
             return jsonify({'success': True})
        except: return jsonify({'error': 'DB Error'}), 500
    return jsonify({'status': 'ok'})

@app.route('/api/admin/ban-ip', methods=['POST'])
def ban_ip():
    """Banir um IP dinamicamente"""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json or {}
    ip_to_ban = data.get('ip')
    reason = data.get('reason', 'Manual Ban via Admin')
    
    if not ip_to_ban: return jsonify({'error': 'Missing IP'}), 400
    
    # Previne auto-ban (opcional, mas bom)
    current_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ',' in current_ip: current_ip = current_ip.split(',')[0].strip()
    
    if ip_to_ban == current_ip:
        return jsonify({'error': 'Você não pode banir seu próprio IP!'}), 400

    conn = get_db_connection()
    if conn:
        try:
             cur = conn.cursor()
             cur.execute("""
                INSERT INTO blocked_ips (ip, reason) VALUES (%s, %s)
                ON CONFLICT (ip) DO NOTHING
             """, (ip_to_ban, reason))
             
             # Opcional: Limpar sessões desse IP
             cur.execute("DELETE FROM active_sessions WHERE ip = %s", (ip_to_ban,))
             
             conn.commit()
             cur.close()
             conn.close()
             print(f"🚫 IP BANNED via Admin: {ip_to_ban}")
             return jsonify({'success': True})
        except Exception as e:
             return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'DB Connection Failed'}), 500


@app.route('/api/admin/orders', methods=['GET'])
def get_orders():
    """Retorna lista de pedidos"""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(load_orders())

@app.route('/api/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    if not check_auth(): return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    
    if request.method == 'GET':
        try:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM settings")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            
            settings = {row[0]: row[1] for row in rows}
            
            # Mask sensitive data just for display? Or let admin see it?
            # User wants to manage it, so let's show it (maybe in password field in frontend)
            return jsonify(settings)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
            
    elif request.method == 'POST':
        try:
            data = request.json
            cur = conn.cursor()
            
            for key, value in data.items():
                cur.execute("""
                    INSERT INTO settings (key, value) 
                    VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (key, value))
            
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500


@app.route('/assets/<path:filename>')
def serve_assets(filename):
    """Serve arquivos da pasta assets (imagens para emails, etc)"""
    return send_from_directory(os.path.join(BASE_DIR, 'assets'), filename)

@app.route('/api/admin/orders/delete', methods=['POST'])
def delete_order():
    """Apaga um pedido pelo ID"""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json or {}
    order_id = data.get('id')
    if not order_id: return jsonify({'error': 'Missing ID'}), 400
    
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM orders WHERE id = %s", (order_id,))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({'success': True})
        except Exception as e:
            print(f"❌ Erro ao apagar pedido {order_id}: {e}")
            return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'DB Error'}), 500

@app.route('/api/admin/stats', methods=['GET'])
def get_admin_stats():
    """Retorna métricas do painel"""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'DB Unavailable'}), 500
    
    stats = {
        'visits_today': 0,
        'orders_today': 0,
        'orders_total': 0,
        'revenue_today': 0.0,
        'revenue_total': 0.0
    }
    
    try:
        cur = conn.cursor()
        
        # Ajuste de timezone: Brasilia (UTC-3)
        # Se servidor for UTC -> NOW() - 3 hours
        
        
        # 1. Visitas Hoje
        cur.execute("""
            SELECT COUNT(*) FROM daily_visits 
            WHERE visit_date = (NOW() - INTERVAL '3 hours')::date
        """)
        stats['visits_today'] = cur.fetchone()[0]
        
        # 1.1 Visitas Total (Total de registros em daily_visits)
        cur.execute("SELECT COUNT(*) FROM daily_visits")
        stats['visits_total'] = cur.fetchone()[0]
        
        # 2. Pedidos Hoje (Total Count)
        cur.execute("""
            SELECT COUNT(*) FROM orders 
            WHERE (created_at - INTERVAL '3 hours')::date = (NOW() - INTERVAL '3 hours')::date
        """)
        stats['orders_today'] = cur.fetchone()[0]
        
        # 3. Pedidos Total
        cur.execute("SELECT COUNT(*) FROM orders")
        stats['orders_total'] = cur.fetchone()[0]
        
        # 4. Faturamento Hoje (Apenas PAID)
        cur.execute("""
            SELECT COALESCE(SUM(amount), 0) FROM orders 
            WHERE status = 'PAID' 
            AND (created_at - INTERVAL '3 hours')::date = (NOW() - INTERVAL '3 hours')::date
        """)
        res_rev_today = cur.fetchone()[0]
        stats['revenue_today'] = float(res_rev_today) if res_rev_today is not None else 0.0
        
        # 5. Faturamento Total (Apenas PAID)
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status = 'PAID'")
        res_rev_total = cur.fetchone()[0]
        stats['revenue_total'] = float(res_rev_total) if res_rev_total is not None else 0.0

        # 6. Conversão (Baseada APENAS nos pedidos existentes no banco)
        # Fórmula: (Pedidos Pagos Hoje / Total Pedidos Hoje) * 100
        # Se eu apagar pedidos pendentes, eles somem do denominador, aumentando a conversão.
        
        # Conversão Hoje
        if stats['orders_today'] > 0:
            stats['conversion_today'] = (stats['visits_today'] / stats['visits_today']) * 100 if stats['visits_today'] > 0 else 0 # Placeholder para não quebrar front se esperar este campo
            # NOVA LÓGICA: Pedidos Pagos / Pedidos Totais (Hoje)
            paid_today = 0
            cur.execute("""
                SELECT COUNT(*) FROM orders 
                WHERE status = 'PAID'
                AND (created_at - INTERVAL '3 hours')::date = (NOW() - INTERVAL '3 hours')::date
            """)
            paid_today = cur.fetchone()[0]
            stats['conversion_today'] = (paid_today / stats['orders_today']) * 100
        else:
            stats['conversion_today'] = 0.0
            
        # Conversão Total
        if stats['orders_total'] > 0:
            cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'PAID'")
            paid_total = cur.fetchone()[0]
            stats['conversion_total'] = (paid_total / stats['orders_total']) * 100
        else:
            stats['conversion_total'] = 0.0
        
        cur.close()
        conn.close()
        
        return jsonify(stats)
    except Exception as e:
        print(f"Stats Error: {e}")
        return jsonify({'error': str(e)}), 500

# --- ERROR HANDLERS & DIAGNOSTICS ---
@app.errorhandler(404)
def page_not_found(e):
    return f"PYTHON SERVER 404: Path {request.path} not found. BASE_DIR: {BASE_DIR}", 404

@app.route('/health')
def health_check():
    # Diagnostics: List files in templates and root
    templates_dir = os.path.join(BASE_DIR, 'templates')
    templates_list = os.listdir(templates_dir) if os.path.exists(templates_dir) else ['ERROR: templates dir not found']
    
    root_list = os.listdir(BASE_DIR)
    
    return jsonify({
        'status': 'ok',
        'server': 'python-flask',
        'base_dir': BASE_DIR,
        'cwd': os.getcwd(),
        'templates_files': templates_list,
        'root_files': root_list,
        'templates_exists': os.path.exists(templates_dir),
        'admin_template_exists': os.path.exists(os.path.join(templates_dir, 'admin_index.html'))
    })

# Servir arquivos estáticos genéricos (CSS, JS, Images, outras páginas HTML)
# IMPORTANTE: Esta rota deve ser a ÚLTIMA, pois captura tudo.
@app.route('/<path:path>')
def static_proxy(path):
    return send_from_directory(BASE_DIR, path)


# --- PHISHING DETECTOR & SITE STATUS ---
import threading

# Global Status (In-memory is fine for this simple check)
SITE_STATUS = {
    'status': 'safe', # safe | error
    'message': 'Seguro (Online)',
    'last_check': None
}

@app.route('/api/admin/site-status', methods=['GET'])
def get_site_status():
    return jsonify(SITE_STATUS)

@app.route('/api/cron/phishing-check', methods=['GET'])
def run_phishing_check():
    """Endpoint chamado pelo Vercel Cron a cada 15 min"""
    global SITE_STATUS
    print("🕵️ Executando Phishing Check via Cron...")
    
    target_url = "https://sptool.vercel.app/" # UPDATED: Vercel URL
    
    try:
        r = requests.get(target_url, timeout=10)
        
        # Se retornar conteúdo de bloqueio (heurística simples)
        if "Deceptive Site Ahead" in r.text or "Phishing" in r.text or "Suspected Phishing" in r.text:
                raise Exception("Google Red Screen Detected (Content Match)")
        
        if r.status_code != 200:
            raise Exception(f"Status Code {r.status_code}")

        # Se chegou aqui, tá safe
        SITE_STATUS = {
            'status': 'safe',
            'message': 'Seguro (Online)',
            'last_check': time.time()
        }
        return jsonify({'success': True, 'status': 'safe'})
                
    except Exception as ex:
        print(f"⚠️ Phishing/Down Detected: {ex}")
        SITE_STATUS = {
            'status': 'error',
            'message': 'ALERTA: Phishing/Down',
            'last_check': time.time()
        }
        
        # Disparar Pushcut (Nome Diferente para Alertar)
        # Usando 'ErrorAlert' para diferenciar de 'Assinatura Gerada'
        pushcut_url = "https://api.pushcut.io/XPTr5Kloj05Rr37Saz0D1/notifications/ErrorAlert" 
        payload = {
            "title": "🚨 ALERTA DE SERVIDOR/PHISHING",
            "text": f"O site apresentou problemas!\nErro: {str(ex)}\nVerifique IMEDIATAMENTE.",
            "isTimeSensitive": True
        }
        try:
            requests.post(pushcut_url, json=payload, timeout=5)
        except: pass
        
        return jsonify({'success': False, 'error': str(ex)})

@app.route('/api/cron/recovery-check', methods=['GET'])
def cron_recovery_check():
    """CRON: Verifica pedidos pendentes há 15min e envia desconto"""
    print("⏰ CRON TRIGGERED: Checking abandoned orders...")
    
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'DB Down'}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Selecionar pedidos PENDENTES, criados entre 15 e 60 min atrás
        # E que NÃO tenham flag 'recovery_sent' no reference_data (limit 20)
        cur.execute("""
            SELECT id, payer_json, reference_data_json 
            FROM orders 
            WHERE status = 'PENDING'
            AND created_at < NOW() - INTERVAL '15 minutes'
            AND created_at > NOW() - INTERVAL '60 minutes'
            AND (reference_data_json::json->>'recovery_sent') IS NULL
            LIMIT 20
        """)
        
        orders_to_recover = cur.fetchall()
        print(f"found {len(orders_to_recover)} potential recoveries")
        
        count = 0
        for order in orders_to_recover:
            try:
                # Parse Data
                payer = json.loads(order['payer_json']) if order['payer_json'] else {}
                ref_data = json.loads(order['reference_data_json']) if order['reference_data_json'] else {}
                
                email = payer.get('email')
                name = payer.get('name', 'Cliente')
                
                if email and '@' in email:
                    # Enviar Email
                    send_discount_recovery_email(email, name)
                    
                    # Marcar como enviado no DB para não enviar de novo
                    ref_data['recovery_sent'] = True
                    new_ref_json = json.dumps(ref_data)
                    
                    cur_update = conn.cursor()
                    cur_update.execute("""
                        UPDATE orders 
                        SET reference_data_json = %s 
                        WHERE id = %s
                    """, (new_ref_json, order['id']))
                    conn.commit() # Commit a cada um para garantir
                    cur_update.close()
                    
                    print(f"📩 Recuperação enviada para Pedido #{order['id']} ({email})")
                    count += 1
            except Exception as e:
                print(f"Erro ao processar recuperação order {order.get('id')}: {e}")
                
        cur.close()
        conn.close()
        
        return jsonify({'success': True, 'recovered_count': count})
        
    except Exception as e:
        print(f"Cron Error: {e}")
        return jsonify({'error': str(e)}), 500

# --- LEGACY API SHIMS (PHP Compatibility) ---
@app.route('/api/instagram.php', methods=['GET', 'POST'])
@app.route('/api/instagram', methods=['GET', 'POST'])
def legacy_instagram_api():
    """Simula a API do Instagram/PHP para o script obfuscado"""
    action = request.args.get('action') or request.form.get('action')
    print(f"⚠️ Legacy API Call: {action}")
    
    # Always return success to satisfy the frontend simulation
    return jsonify({
        'status': 'success',
        'exists': True, 
        'canSearch': True,
        'leadId': 12345,
        'searchCount': 10,
        'data': {'username': 'simulated', 'full_name': 'Simulated User', 'follower_count': 1000},
        'ip': request.remote_addr
    })

# Para compatibilidade com Vercel, 'app' deve ser exposto globalmente.
# O bloco if __name__ == '__main__' abaixo só roda localmente.

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    print(f"🚀 SpyInsta Admin Server (Flask) running on port {port}")
    print("🔒 Admin Access: /admin (User: admin / Pass: Hornet600)")
    app.run(host='0.0.0.0', port=port, debug=True)

# FORCE DEPLOY FIX SYNTAX
