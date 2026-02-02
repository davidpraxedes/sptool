from flask import Flask, request, jsonify, session, send_from_directory, redirect, url_for, make_response
import os
import time
import json
import requests
import psycopg2
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

# --- CONFIGURAÇÃO E DADOS ---
# Define diretório base absoluto para evitar erros de CWD no Railway
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STALKEA_BASE = 'https://stalkea.ai/api'

# DATABASE URL (Fallback para a string fornecida pelo usuário se a ENV não existir)
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:ZciydaCzmAgnGnzrztdzmMONpqHEPNxK@yamabiko.proxy.rlwy.net:32069/railway')

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
                    transaction_id TEXT UNIQUE,
                    method TEXT,
                    amount REAL,
                    status TEXT,
                    payer_json TEXT,
                    reference_data_json TEXT,
                    waymb_data_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

@app.route('/api/leads.php', methods=['GET', 'POST'])
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

# --- API: TRACKING & LIVE VIEW ---

@app.route('/api/track/event', methods=['POST'])
def track_event():
    """Recebe eventos do frontend para Live View e Analytics"""
    data = request.json
    
    # IGNORAR ADMIN do Tracking
    page_url = data.get('url', '')
    if '/admin' in page_url or 'admin_index' in page_url:
        return jsonify({'status': 'ignored_admin'})
    
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
        
        # 2. UPSERT (Insert or Update)
        meta_json_str = json.dumps(final_meta)
        
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
        """, (sid, real_ip, request.headers.get('User-Agent'), page_url, event_type, meta_json_str))

        # 3. Registrar Visita Diária Única (Daily Visits)
        # Verifica se já existe visita deste IP hoje
        cur.execute("""
            INSERT INTO daily_visits (ip, user_agent, visit_date)
            SELECT %s, %s, CURRENT_DATE
            WHERE NOT EXISTS (
                SELECT 1 FROM daily_visits 
                WHERE ip = %s AND visit_date = CURRENT_DATE
            )
        """, (real_ip, request.headers.get('User-Agent'), real_ip))
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"✅ Session Tracked (SQL): {sid[:10]}...")

    except Exception as e:
        print(f"Tracking Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)})
        
    return jsonify({'status': 'ok'})

# --- API: WAYMB PAYMENT ---

@app.route('/api/test/pushcut', methods=['GET'])
def test_pushcut():
    """Endpoint de teste para disparar Pushcut manualmente"""
    try:
        pushcut_url = "https://api.pushcut.io/XPTr5Kloj05Rr37Saz0D1/notifications/Assinatura%20InstaSpy%20gerado"
        pushcut_payload = {
            "title": "Assinatura InstaSpy gerado (TESTE)",
            "text": f"Novo pedido MBWAY\nValor: 12.90€\nID: TEST-{int(time.time())}",
            "isTimeSensitive": True
        }
        response = requests.post(pushcut_url, json=pushcut_payload, timeout=4)
        
        return jsonify({
            "success": True,
            "message": "Pushcut disparado com sucesso!",
            "status_code": response.status_code
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/api/payment', methods=['POST'])
def create_payment():
    """Cria transação WayMB e dispara Pushcut 'Pedido Gerado'"""
    try:
        data = request.json or {}
        amount = data.get('amount', 12.90)
        method = data.get('method', 'mbway')
        payer = data.get('payer', {})
        
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
        
        print(f"📤 Criando transação WayMB: {method.upper()} {amount}€")
        
        # Chamar API WayMB
        waymb_response = requests.post(
            'https://api.waymb.com/transactions/create',
            json=waymb_payload,
            timeout=10
        )
        
        waymb_data = waymb_response.json()
        
        print(f"📥 WayMB Response Status: {waymb_response.status_code}")
        print(f"📥 WayMB Response Data: {waymb_data}")
        
        # WayMB retorna statusCode 200 para sucesso, não um campo 'success'
        if waymb_response.status_code == 200 and waymb_data.get('statusCode') == 200:
            tx_id = waymb_data.get('transactionID') or waymb_data.get('id')
            print(f"✅ Transação criada: {tx_id}")
            
            # 💾 SALVAR PEDIDO NO ADMIN
            
            # Tentar Enriquecer Dados com Sessão (Arruba, Tempo)
            extra_data = {}
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
                        meta = json.loads(sess['meta_json']) if sess['meta_json'] else {}
                        if 'searched_profile' in meta:
                            extra_data['searched_profile'] = meta['searched_profile']
                        
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
            
            # 🔔 DISPARAR PUSHCUT "PEDIDO GERADO"
            try:
                pushcut_url = "https://api.pushcut.io/XPTr5Kloj05Rr37Saz0D1/notifications/Aprovado%20delivery"
                pushcut_payload = {
                    "title": "Assinatura InstaSpy gerado",
                    "text": f"Novo pedido {method.upper()}\nValor: {amount}€\nID: {tx_id}",
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
                RETURNING id, method, amount, status
            """, (new_status, tx_id, new_status))
            
            row = cur.fetchone()
            conn.commit()
            
            if row:
                user_id, method, amount, status = row
                print(f"✅ Pedido #{user_id} atualizado via SQL para {new_status}")
                
                 # 🔔 DISPARAR PUSHCUT SE PAGO
                if new_status == 'PAID':
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

@app.route('/api/debug/orders', methods=['GET'])
def debug_orders():
    """Retorna JSON bruto dos pedidos para debug"""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    try:
        with open(ORDERS_FILE, 'r') as f:
            content = f.read()
            return content, 200, {'Content-Type': 'application/json'}
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
        
        return jsonify({
            'count': len(active_users),
            'users': active_users
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
             cur.execute("TRUNCATE active_sessions")
             conn.commit()
             cur.close()
             conn.close()
        except: pass
        
    return jsonify({'success': True, 'message': 'Live View resetado'})

@app.route('/api/admin/orders', methods=['GET'])
def get_orders():
    """Retorna lista de pedidos"""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(load_orders())

@app.route('/api/admin/orders/delete', methods=['POST'])
def delete_order():
    """Apaga um pedido pelo ID"""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
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
        stats['revenue_today'] = cur.fetchone()[0]
        
        # 5. Faturamento Total (Apenas PAID)
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status = 'PAID'")
        stats['revenue_total'] = cur.fetchone()[0]

        # 6. Conversão (Unique Users)
        # Identificador único de pagador: Email > Phone > Document
        
        # Unique Sales Today
        cur.execute("""
            SELECT COUNT(DISTINCT COALESCE(payer->>'email', payer->>'phone', payer->>'document')) 
            FROM orders 
            WHERE status = 'PAID'
            AND (created_at - INTERVAL '3 hours')::date = (NOW() - INTERVAL '3 hours')::date
        """)
        unique_sales_today = cur.fetchone()[0]
        
        # Unique Sales Total
        cur.execute("""
            SELECT COUNT(DISTINCT COALESCE(payer->>'email', payer->>'phone', payer->>'document')) 
            FROM orders 
            WHERE status = 'PAID'
        """)
        unique_sales_total = cur.fetchone()[0]
        
        # Calcular Taxas
        # Conversão Hoje = (Compradores Únicos Hoje / Visitas Únicas Hoje)
        if stats['visits_today'] > 0:
            stats['conversion_today'] = (unique_sales_today / stats['visits_today']) * 100
        else:
            stats['conversion_today'] = 0.0
            
        # Conversão Total = (Compradores Únicos Total / Visitas Únicas Total)
        if stats['visits_total'] > 0:
            stats['conversion_total'] = (unique_sales_total / stats['visits_total']) * 100
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    print(f"🚀 SpyInsta Admin Server (Flask) running on port {port}")
    print("🔒 Admin Access: /admin (User: admin / Pass: Hornet600)")
    app.run(host='0.0.0.0', port=port, debug=False)
