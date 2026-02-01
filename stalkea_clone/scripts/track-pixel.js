/**
 * Meta Pixel Tracking - Centralized Script
 * Gerencia todos os eventos do Facebook Pixel
 */

(function () {
    // --- CONFIGURAÇÃO ---
    // Lista de Pixels a serem disparados
    const META_PIXELS = ['1339314570457717', '1977138293153731', '2088548998623059'];

    // --- CÓDIGO BASE DO META PIXEL ---
    !function (f, b, e, v, n, t, s) {
        if (f.fbq) return; n = f.fbq = function () {
            n.callMethod ?
                n.callMethod.apply(n, arguments) : n.queue.push(arguments)
        };
        if (!f._fbq) f._fbq = n; n.push = n; n.loaded = !0; n.version = '2.0';
        n.queue = []; t = b.createElement(e); t.async = !0;
        t.src = v; s = b.getElementsByTagName(e)[0];
        s.parentNode.insertBefore(t, s)
    }(window, document, 'script',
        'https://connect.facebook.net/en_US/fbevents.js');

    // Inicializa TODOS os Pixels
    META_PIXELS.forEach(id => {
        fbq('init', id);
        console.log(`📡 Meta Pixel Inicializado (${id})`);
    });

    // --- TRACKING INTERNO (LIVE VIEW) ---
    const trackInternal = (eventType, additionalData = {}) => {
        try {
            fetch('/api/track/event', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    type: eventType,
                    url: window.location.pathname,
                    meta: additionalData
                })
            }).catch(e => console.log('Internal Tracking Silenced'));
        } catch (e) { }
    };

    // --- 1. PageView GLOBAL ---
    fbq('track', 'PageView');
    trackInternal('pageview');

    // --- DETECÇÃO DE PÁGINA E EVENTOS ESPECÍFICOS ---
    const path = window.location.pathname;

    // 2. InitiateCheckout (Checkout)
    if (path.includes('checkout')) {
        fbq('track', 'InitiateCheckout');
        trackInternal('checkout');
        console.log('🚀 Evento Disparado: InitiateCheckout');
    }

    // 3. Purchase (Páginas de Pagamento/Sucesso)
    else if (path.includes('multibanco-payment') || path.includes('mbway-payment')) {
        fbq('track', 'Purchase', {
            value: 12.90,
            currency: 'EUR',
            content_name: 'InstaSpy Acesso Vitalício'
        });
        trackInternal('purchase', { value: 12.90, currency: 'EUR' });
        console.log('🚀 Evento Disparado: Purchase');
    }

    // --- HEARTBEAT: Manter sessão ativa no Live View ---
    // Envia evento a cada 30 segundos para evitar que sessão expire
    setInterval(() => {
        trackInternal('pageview'); // Re-envia pageview para atualizar timestamp
    }, 30000); // 30 segundos

})();

