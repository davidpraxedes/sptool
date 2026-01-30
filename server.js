const express = require('express');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 8000;

// Middleware para JSON
app.use(express.json());

// API Endpoints Mock
app.get('/api/get-ip.php', (req, res) => {
    res.json({ ip: req.ip || '127.0.0.1' });
});

app.get('/api/config.php', (req, res) => {
    res.json({
        api_enabled: true,
        instagram_api_url: '/api/instagram.php',
        leads_api_url: '/api/leads.php'
    });
});

app.get('/api/instagram.php', (req, res) => {
    const username = req.query.username || 'joao_silva';

    // Retornar perfil mock
    res.json({
        success: true,
        data: {
            username: username,
            full_name: username.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase()),
            profile_pic_url: '/assets/images/avatars/perfil-espionado.jpeg',
            biography: 'Perfil de demonstração',
            follower_count: Math.floor(Math.random() * 10000),
            following_count: Math.floor(Math.random() * 1000),
            is_private: false,
            is_verified: false
        }
    });
});

app.get('/api/leads.php', (req, res) => {
    const action = req.query.action;

    if (action === 'check_status') {
        res.json({
            success: true,
            status: 'active',
            searches_remaining: 999
        });
    } else {
        res.json({ success: true });
    }
});

app.post('/api/leads.php', (req, res) => {
    res.json({ success: true, lead_id: 'demo_' + Date.now() });
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
});
