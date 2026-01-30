const express = require('express');
const path = require('path');
const axios = require('axios');

const app = express();
const PORT = process.env.PORT || 8000;

// Middleware para JSON
app.use(express.json());

// Proxy para API do Stalkea.ai
const STALKEA_BASE = 'https://stalkea.ai/api';

// API: Get IP
app.get('/api/get-ip.php', async (req, res) => {
    try {
        const response = await axios.get(`${STALKEA_BASE}/get-ip.php`, {
            headers: {
                'Referer': 'https://stalkea.ai/',
                'User-Agent': req.headers['user-agent']
            }
        });
        res.json(response.data);
    } catch (error) {
        res.json({ ip: req.ip || '127.0.0.1' });
    }
});

// API: Config
app.get('/api/config.php', async (req, res) => {
    try {
        const response = await axios.get(`${STALKEA_BASE}/config.php`, {
            headers: {
                'Referer': 'https://stalkea.ai/',
                'User-Agent': req.headers['user-agent']
            }
        });
        res.json(response.data);
    } catch (error) {
        res.json({
            status: 'success',
            data: {
                pixel_fb: '',
                gtm_id: '',
                checkout_url: 'cta.html'
            }
        });
    }
});

// API: Instagram
app.get('/api/instagram.php', async (req, res) => {
    try {
        const queryString = new URLSearchParams(req.query).toString();
        const url = `${STALKEA_BASE}/instagram.php${queryString ? '?' + queryString : ''}`;

        const response = await axios.get(url, {
            headers: {
                'Referer': 'https://stalkea.ai/',
                'User-Agent': req.headers['user-agent']
            }
        });

        res.json(response.data);
    } catch (error) {
        res.status(error.response?.status || 500).json({
            status: 'error',
            message: `Erro ao conectar com API original. Código: ${error.response?.status || 500}`
        });
    }
});

// ==================== WAYMB PAYMENT API ====================

// Sanitização de dados
function sanitizePhone(phone) {
    // Remover tudo exceto dígitos
    let cleaned = String(phone).replace(/\D/g, '');

    // Remover prefixo +351
    if (cleaned.startsWith('351') && cleaned.length > 9) {
        cleaned = cleaned.substring(3);
    }

    // Pegar últimos 9 dígitos
    if (cleaned.length > 9) {
        cleaned = cleaned.slice(-9);
    }

    // Validar prefixo
    if (!cleaned.match(/^(91|92|93|96)/)) {
        throw new Error('Número de telemóvel inválido. Deve começar com 91, 92, 93 ou 96.');
    }

    return cleaned;
}

function sanitizeNIF(nif) {
    // Remover tudo exceto dígitos
    let cleaned = String(nif).replace(/\D/g, '');

    // Pegar últimos 9 dígitos
    if (cleaned.length > 9) {
        cleaned = cleaned.slice(-9);
    }

    // Validar tamanho
    if (cleaned.length !== 9) {
        throw new Error('NIF inválido. Deve ter 9 dígitos.');
    }

    return cleaned;
}

function sanitizeName(name) {
    return String(name).trim().substring(0, 50);
}

// API: Criar Pagamento
app.post('/api/payment', async (req, res) => {
    try {
        const { amount, method, payer } = req.body;

        // Validar dados obrigatórios
        if (!amount || !method || !payer) {
            return res.status(400).json({
                success: false,
                error: 'Dados incompletos'
            });
        }

        // Sanitizar dados
        const sanitizedPhone = sanitizePhone(payer.phone);
        const sanitizedNIF = sanitizeNIF(payer.document);
        const sanitizedName = sanitizeName(payer.name);

        // Payload para WayMB
        const payload = {
            client_id: process.env.WAYMB_CLIENT_ID || 'modderstore_c18577a3',
            client_secret: process.env.WAYMB_CLIENT_SECRET || '850304b9-8f36-4b3d-880f-36ed75514cc7',
            account_email: process.env.WAYMB_ACCOUNT_EMAIL || 'modderstore@gmail.com',
            amount: parseFloat(amount),
            method: method,
            payer: {
                name: sanitizedName,
                document: sanitizedNIF,
                phone: sanitizedPhone
            }
        };

        console.log('📤 Criando transação WayMB:', { method, amount, phone: sanitizedPhone });

        // Chamar API WayMB
        const response = await axios.post(
            'https://api.waymb.com/transactions/create',
            payload,
            {
                headers: {
                    'Content-Type': 'application/json'
                },
                timeout: 30000
            }
        );

        console.log('✅ Transação criada:', response.data.id);

        // Retornar sucesso
        res.json({
            success: true,
            data: response.data
        });

    } catch (error) {
        console.error('❌ Erro ao criar pagamento:', error.message);
        res.status(500).json({
            success: false,
            error: error.message || 'Erro ao processar pagamento'
        });
    }
});

// API: Consultar Status
app.post('/api/status', async (req, res) => {
    try {
        const { id } = req.body;

        if (!id) {
            return res.status(400).json({
                success: false,
                error: 'ID da transação não fornecido'
            });
        }

        // Consultar status na WayMB
        const response = await axios.post(
            'https://api.waymb.com/transactions/info',
            { id },
            {
                headers: {
                    'Content-Type': 'application/json'
                },
                timeout: 10000
            }
        );

        res.json({
            success: true,
            data: response.data
        });

    } catch (error) {
        console.error('❌ Erro ao consultar status:', error.message);
        res.status(500).json({
            success: false,
            error: error.message || 'Erro ao consultar status'
        });
    }
});

// API: Webhook MBWay
app.post('/api/webhook/mbway', async (req, res) => {
    try {
        const data = req.body || {};

        const txId = data.id || data.transaction_id;
        const status = data.status;
        const amount = parseFloat(data.amount || data.valor || 0);

        console.log('🔔 Webhook recebido:', { txId, status, amount });

        if (status === 'COMPLETED' || status === 'PAID') {
            console.log('✅ Pagamento confirmado:', txId);

            // TODO: Atualizar banco de dados
            // TODO: Enviar notificação Pushcut

            // Exemplo de notificação (se tiver Pushcut configurado)
            if (process.env.PUSHCUT_SECRET) {
                try {
                    await axios.post(
                        `https://api.pushcut.io/${process.env.PUSHCUT_SECRET}/notifications/${process.env.PUSHCUT_NOTIFICATION_NAME || 'Payment'}`,
                        {
                            title: '💰 Pagamento Confirmado',
                            text: `Valor: ${amount}€\nID: ${txId}`,
                            isTimeSensitive: true
                        }
                    );
                } catch (err) {
                    console.error('⚠️ Erro ao enviar notificação:', err.message);
                }
            }
        }

        res.json({ status: 'received' });

    } catch (error) {
        console.error('❌ Erro no webhook:', error.message);
        res.status(500).json({ error: error.message });
    }
});

// ==================== END WAYMB API ====================

// API: Leads (GET)
app.get('/api/leads.php', async (req, res) => {
    try {
        const queryString = new URLSearchParams(req.query).toString();
        const url = `${STALKEA_BASE}/leads.php${queryString ? '?' + queryString : ''}`;

        const response = await axios.get(url, {
            headers: {
                'Referer': 'https://stalkea.ai/',
                'User-Agent': req.headers['user-agent']
            }
        });

        res.json(response.data);
    } catch (error) {
        res.json({ success: true, searches_remaining: 999 });
    }
});

// API: Leads (POST)
app.post('/api/leads.php', async (req, res) => {
    try {
        const response = await axios.post(`${STALKEA_BASE}/leads.php`, req.body, {
            headers: {
                'Referer': 'https://stalkea.ai/',
                'User-Agent': req.headers['user-agent'],
                'Content-Type': 'application/json'
            }
        });

        res.json(response.data);
    } catch (error) {
        res.json({ success: true, lead_id: 'demo_' + Date.now() });
    }
});

// Servir arquivos estáticos da pasta stalkea_clone
app.use(express.static(path.join(__dirname, 'stalkea_clone')));

// Rota principal
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'stalkea_clone', 'index.html'));
});

// Iniciar servidor
app.listen(PORT, '0.0.0.0', () => {
    console.log(`✅ Servidor rodando na porta ${PORT}`);
    console.log(`🚀 Acesse: http://localhost:${PORT}`);
    console.log(`📡 Proxy para: ${STALKEA_BASE}`);
});
