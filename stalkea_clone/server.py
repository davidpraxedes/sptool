from flask import Flask, request, jsonify, session, send_from_directory, redirect, url_for, make_response
import os
import time
import json
import requests
from datetime import datetime, timedelta

# Inicializa Flask
# REMOVIDO static_url_path='' pois causa conflito com rotas explícitas em alguns ambientes
app = Flask(__name__) 
app.secret_key = 'HORNET600_SECRET_KEY_PRODUCTION' # Chave secreta para sessões

@app.before_request
def log_request_info():
    if request.path != '/api/auth/check' and not request.path.startswith('/static'):
        real_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ',' in real_ip: real_ip = real_ip.split(',')[0].strip()
        print(f"📡 Request: {request.method} {request.path} | Remote: {real_ip}")

# --- CONFIGURAÇÃO E DADOS ---
# Define diretório base absoluto para evitar erros de CWD no Railway
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
ORDERS_FILE = os.path.join(DATA_DIR, 'orders.json')
STALKEA_BASE = 'https://stalkea.ai/api'

# Garante que diretório de dados existe
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)


# Inicializa arquivo de pedidos se não existir
if not os.path.exists(ORDERS_FILE):
    with open(ORDERS_FILE, 'w') as f:
        json.dump([], f)

# --- IN-MEMORY STORAGE (LIVE VIEW) ---
# Armazena sessões ativas: { session_id: { last_seen, ip, page, meta_data } }
active_sessions = {}

# --- FUNÇÕES AUXILIARES ---
def load_orders():
    try:
        with open(ORDERS_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_order(order_data):
    orders = load_orders()
    # Adiciona Metadata
    order_data['id'] = len(orders) + 1
    order_data['created_at'] = datetime.now().isoformat()
    orders.append(order_data)
    
    with open(ORDERS_FILE, 'w') as f:
        json.dump(orders, f, indent=2)

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
    
    # Detecção de IP Real
    real_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ',' in real_ip:
        real_ip = real_ip.split(',')[0].strip()

    # Identificação da Sessão (Cookie ou IP)
    sid = request.cookies.get('session_id')
    
    # Lógica de Merge: Se temos SID (Cookie) mas existe uma sessão prévia por IP, mesclar.
    if sid and real_ip in active_sessions and sid not in active_sessions:
        # Encontrou sessão órfã por IP. Migrar para SID.
        ip_session = active_sessions[real_ip]
        active_sessions[sid] = ip_session # Copia dados
        del active_sessions[real_ip]      # Remove sessão antiga
        print(f"🔄 Merged Session: IP {real_ip} -> UUID {sid}")
        
    if not sid:
        sid = real_ip # Fallback p/ IP

    event_type = data.get('type') # pageview, search, checkout, purchase
    
    # Dados do Evento (Base)
    event_data = {
        'ip': real_ip,
        'user_agent': request.headers.get('User-Agent'),
        'timestamp': time.time(),
        'last_seen': datetime.now().isoformat(),
        'page': data.get('url'),
        'type': event_type,
        'meta': data.get('meta', {})
    }
    
    # Atualiza Sessão Ativa
    if sid in active_sessions:
        current_session = active_sessions[sid]
        
        # Preserva Metadata
        current_meta = current_session.get('meta', {})
        new_meta = event_data.get('meta', {})
        
        # Preserva campos importantes se o novo evento não os trouxer
        if 'searched_profile' in current_meta and 'searched_profile' not in new_meta:
             new_meta['searched_profile'] = current_meta['searched_profile']
        if 'location' in current_meta:
            new_meta['location'] = current_meta['location']
             
        event_data['meta'] = {**current_meta, **new_meta}
        
    else:
        # Nova sessão (ou acabou de ser criada pelo Merge acima, mas vamos garantir GeoIP)
        # Tentar resolver GeoIP apenas se não tiver Location
        if 'location' not in event_data['meta']:
            try:
                 if real_ip and len(real_ip) > 7 and not real_ip.startswith('127') and not real_ip.startswith('10.'):
                     geo_url = f"http://ip-api.com/json/{real_ip}?fields=status,countryCode,city"
                     geo_resp = requests.get(geo_url, timeout=2).json()
                     if geo_resp.get('status') == 'success':
                          location = f"{geo_resp.get('countryCode')} ({geo_resp.get('city')})"
                          event_data['meta']['location'] = location
            except Exception as e:
                print(f"GeoIP Error: {e}")
             
    active_sessions[sid] = event_data
    
    # Se for compra, salva no histórico permanente
    if event_type == 'purchase':
        save_order(event_data)
        
    return jsonify({'status': 'ok'})

# --- API: WAYMB PAYMENT ---

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
        
        if waymb_data.get('success'):
            tx_id = waymb_data.get('data', {}).get('id')
            print(f"✅ Transação criada: {tx_id}")
            
            # 🔔 DISPARAR PUSHCUT "PEDIDO GERADO"
            try:
                pushcut_url = "https://api.pushcut.io/XPTr5Kloj05Rr37Saz0D1/notifications/Aprovado%20delivery"
                pushcut_payload = {
                    "title": "InstaSpy - Pedido Gerado",
                    "text": f"Novo pedido {method.upper()}\nValor: {amount}€\nID: {tx_id}",
                    "isTimeSensitive": True
                }
                requests.post(pushcut_url, json=pushcut_payload, timeout=4)
                print(f"📲 Pushcut 'Pedido Gerado' enviado")
            except Exception as e:
                print(f"⚠️ Erro ao enviar Pushcut: {e}")
            
            return jsonify({
                'success': True,
                'data': waymb_data.get('data')
            })
        else:
            return jsonify({
                'success': False,
                'error': waymb_data.get('error', 'Erro desconhecido')
            }), 400
            
    except Exception as e:
        print(f"❌ Erro ao criar pagamento: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/status', methods=['POST'])
def check_payment_status():
    """Consulta status de transação WayMB"""
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
        
        if waymb_data.get('success'):
            return jsonify({
                'success': True,
                'data': waymb_data.get('data')
            })
        else:
            return jsonify({
                'success': False,
                'error': waymb_data.get('error', 'Erro ao consultar status')
            }), 400
            
    except Exception as e:
        print(f"❌ Erro ao consultar status: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# --- API: ADMIN DATA ---

@app.route('/api/admin/live', methods=['GET'])
def get_live_view():
    """Retorna usuários ativos nos últimos 5 minutos"""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    
    now = time.time()
    # Filtra sessões ativas (últimos 300 segundos)
    active = []
    
    # Cleanup de sessões antigas
    to_remove = []
    
    for sid, data in active_sessions.items():
        if now - data['timestamp'] < 300: # 5 minutos
            # Adiciona ID para o frontend
            user_data = data.copy()
            user_data['session_id'] = sid
            active.append(user_data)
        else:
            to_remove.append(sid)
            
    # Remove inativos da memória
    for sid in to_remove:
        del active_sessions[sid]
        
    return jsonify({
        'count': len(active),
        'users': active
    })

@app.route('/api/admin/orders', methods=['GET'])
def get_orders():
    """Retorna lista de pedidos"""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(load_orders())

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
